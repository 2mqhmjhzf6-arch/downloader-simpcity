"""High-level run loop.

Wires together:

    cookies   -> HttpClient
    HttpClient -> ThreadScraper -> Posts -> PostLinks
    PostLink   -> Resolver.resolve() -> [Resource]
    Resource   -> state dedup -> downloader -> disk
    failures   -> _meta/{failed_downloads,unsupported_hosts}.txt
    new hosts  -> global new_unsupported_hosts.log

Concurrency: thread pages are fetched sequentially (politeness), resolution
fans out across an asyncio task group bounded by ``Config.concurrency``,
downloads share that same semaphore.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from .auth import load_cookiejar
from .config import Config
from .downloader import download_resource
from .errors import DeadLink, ResolverError, TransientError, UnsupportedHost
from .forum.xenforo import ThreadScraper
from .http_client import HttpClient
from .models import Kind, Post, PostLink, Resource
from .naming import build_filename, ensure_dir, run_dir, thread_dir
from .resolvers import base as resolver_base  # noqa: F401 -- triggers registration
from .resolvers.base import ResolveContext, find_resolver
from .state import State
from .tracking import (
    log_new_host,
    split_failures,
    write_failed_downloads,
    write_unsupported_hosts,
)
from .ui import console, progress_count_bar


_ALBUM_PATH_RX = re.compile(r"/(?:a|album|folder)/", re.IGNORECASE)


def _looks_like_album(url: str) -> bool:
    return bool(_ALBUM_PATH_RX.search(url))

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._jar = load_cookiejar(
            cookies_file=cfg.cookies_file,
            cookies_browser=cfg.cookies_browser,
            xf_user=cfg.xf_user,
            xf_session=cfg.xf_session,
        )

    async def run(self, thread_url: str) -> int:
        """Returns the number of files actually downloaded (0 in dry-run)."""
        http = HttpClient(
            user_agent=self.cfg.user_agent,
            cookies=self._jar,
            timeout_s=self.cfg.timeout_s,
            retries=self.cfg.retries,
            use_curl_cffi=self.cfg.use_curl_cffi,
        )
        try:
            return await self._run_with(http, thread_url)
        finally:
            await http.aclose()

    async def _run_with(self, http: HttpClient, thread_url: str) -> int:
        scraper = ThreadScraper(http)
        thread, posts = await scraper.fetch_thread(
            thread_url, pages=self.cfg.pages, posts=self.cfg.posts
        )
        console.print(
            f"[bold green]Thread:[/] {thread.title}  "
            f"[dim]({thread.thread_id}, {len(posts)} posts, {thread.pages} pages)[/]"
        )

        run_root = ensure_dir(run_dir(self.cfg.out_dir, date.today()))
        tdir = ensure_dir(thread_dir(run_root, thread.title, thread.thread_id))
        meta = ensure_dir(tdir / "_meta")
        state = State(meta / "state.sqlite")
        state.upsert_thread(thread.thread_id, thread.url, thread.title)

        forum_origin = f"{urlparse(thread_url).scheme}://{urlparse(thread_url).netloc}"

        # 1. Extract links from every post.
        all_links: list[PostLink] = []
        for p in posts:
            all_links.extend(scraper.extract_links(p, forum_origin))
        console.print(f"  {len(all_links)} candidate URLs extracted")

        # 2. Group by resolver (so we can show host-level progress).
        unresolved: list[tuple[PostLink, type]] = []
        unknown: list[PostLink] = []
        for link in all_links:
            cls = find_resolver(link.url)
            if cls is None:
                unknown.append(link)
            else:
                link.resolver_name = cls.name
                unresolved.append((link, cls))
        if unknown:
            console.print(f"  [yellow]{len(unknown)} URLs with no matching resolver — skipping[/]")
            for u in unknown:
                state.record_failure(thread.thread_id, u.url, "no resolver matched")
                log_new_host(u.url)

        # 3. Resolve + download under a shared Rich Progress.
        disable_ui = self.cfg.quiet or not sys.stderr.isatty()
        sem = asyncio.Semaphore(self.cfg.concurrency)
        resolved: list[tuple[Post, Resource]] = []

        with progress_count_bar(disable=disable_ui) as progress:
            resolve_task = progress.add_task(
                "resolving", host="all", total=len(unresolved),
            )

            async def _resolve_one(link: PostLink, cls) -> None:
                async with sem:
                    resolver = cls()
                    # Merge CLI-supplied passwords with whatever the post body
                    # advertised in spoilers / "pw: ..." hints. Preserve order,
                    # dedup.
                    merged_pws = list(dict.fromkeys(
                        [*self.cfg.passwords, *link.post.passwords]
                    ))
                    ctx = ResolveContext(
                        http=http,
                        max_height=self.cfg.max_height,
                        exclude_4k=self.cfg.exclude_4k,
                        passwords=merged_pws,
                        post_html=link.post.raw_html,
                        gofile_token=self.cfg.gofile_token,
                    )
                    try:
                        items = await resolver.resolve(link.url, ctx)
                    except UnsupportedHost as e:
                        state.record_failure(thread.thread_id, link.url, f"UnsupportedHost: {e}")
                        return
                    except (DeadLink,) as e:
                        state.record_failure(thread.thread_id, link.url, f"dead: {e}")
                        return
                    except (TransientError, ResolverError, Exception) as e:
                        state.record_failure(thread.thread_id, link.url, f"{type(e).__name__}: {e}")
                        return
                    finally:
                        progress.advance(resolve_task, 1)
                    if not items and not _looks_like_album(link.url):
                        state.record_failure(
                            thread.thread_id,
                            link.url,
                            f"resolver '{cls.name}' returned no resources",
                        )
                        return
                    for r in items:
                        if _resource_excluded(r, self.cfg):
                            continue
                        resolved.append((link.post, r))

            await asyncio.gather(*[_resolve_one(l, c) for l, c in unresolved])

            # 3b. Type filter (videos / photos / both).
            if self.cfg.only_kind != "both":
                want = Kind.VIDEO if self.cfg.only_kind == "video" else Kind.IMAGE
                before = len(resolved)
                resolved = [(p, r) for (p, r) in resolved if r.kind == want]
                if before != len(resolved):
                    console.print(
                        f"  [dim]--only {self.cfg.only_kind}: "
                        f"{before - len(resolved)} resources filtered out, "
                        f"{len(resolved)} kept[/]"
                    )

            # 4. Dedup against state + filename for THIS run.
            new_pairs: list[tuple[Post, Resource]] = []
            seen_keys: set[str] = set()
            for post, r in resolved:
                key = r.dedup_key or r.url
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                if state.is_downloaded(thread.thread_id, key):
                    continue
                new_pairs.append((post, r))

            console.print(
                f"  [cyan]{len(new_pairs)}[/] new files to download  "
                f"[dim]({len(resolved) - len(new_pairs)} dedup/skipped)[/]"
            )

            # 5. Either dry-run report or actually download.
            if self.cfg.dry_run:
                for post, r in new_pairs[:30]:
                    fn = build_filename(r.filename or "file", post.post_date)
                    console.print(f"    [dim](dry)[/] {fn}  <- {r.url}")
                if len(new_pairs) > 30:
                    console.print(f"    [dim](dry) ... {len(new_pairs) - 30} more[/]")
                _write_tracking_files(tdir, state, thread.thread_id)
                state.close()
                return 0

            download_task = progress.add_task(
                "downloading", host="all", total=len(new_pairs),
            )
            downloaded = 0

            async def _dl(post: Post, r: Resource) -> None:
                nonlocal downloaded
                async with sem:
                    fn = build_filename(r.filename or "file", post.post_date)
                    try:
                        res = await download_resource(http, r, tdir, fn)
                    except DeadLink as e:
                        state.record_failure(thread.thread_id, r.url, f"dead: {e}")
                        return
                    except Exception as e:
                        state.record_failure(thread.thread_id, r.url, f"{type(e).__name__}: {e}")
                        return
                    finally:
                        progress.advance(download_task, 1)
                    if state.has_sha(thread.thread_id, res.sha256):
                        res.path.unlink(missing_ok=True)
                        return
                    state.record_download(
                        thread.thread_id,
                        r.dedup_key or r.url,
                        r.url,
                        res.path.name,
                        res.sha256,
                        res.size,
                    )
                    downloaded += 1

            await asyncio.gather(*[_dl(p, r) for p, r in new_pairs])

        _write_tracking_files(tdir, state, thread.thread_id)
        state.close()
        console.print(f"[bold green]Done.[/] {downloaded} new files in {tdir}")
        return downloaded


def _resource_excluded(r: Resource, cfg: Config) -> bool:
    if r.kind == Kind.VIDEO and r.height:
        if cfg.exclude_4k and r.height >= 2000:
            return True
        if cfg.max_height and r.height > cfg.max_height:
            return True
    return False


def _write_tracking_files(thread_dir_: Path, state: State, thread_id: str) -> None:
    fails = state.failures_for(thread_id)
    if not fails:
        return
    unsupported, other = split_failures(fails)
    meta = thread_dir_ / "_meta"
    write_unsupported_hosts(meta, unsupported)
    write_failed_downloads(meta, other)

    # Back-compat: keep the legacy combined file too. Some users have scripts
    # parsing it.
    legacy = meta / "failed_links.txt"
    legacy.write_text(
        "# url\treason\n" + "\n".join(f"{u}\t{r}" for u, r in fails) + "\n",
        encoding="utf-8",
    )
