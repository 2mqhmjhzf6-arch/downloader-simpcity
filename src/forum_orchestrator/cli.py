from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

import typer

from .config import Config
from .orchestrator import Orchestrator
from .resolvers import base as _resolver_base  # noqa: F401 -- register all
from .resolvers.base import all_resolvers
from .ui import console

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Forum Media Orchestrator — scrape & download media from XenForo threads.",
)


def _pages(raw: Optional[str]) -> Optional[tuple[int, int]]:
    if not raw:
        return None
    if "-" in raw:
        a, b = raw.split("-", 1)
        return int(a), int(b)
    n = int(raw)
    return n, n


def _normalize_only(raw: str) -> str:
    raw = (raw or "both").strip().lower()
    if raw in ("video", "videos", "v"):
        return "video"
    if raw in ("photo", "photos", "image", "images", "p", "i"):
        return "photo"
    if raw in ("both", "all", "any", ""):
        return "both"
    raise typer.BadParameter(f"--only must be one of: videos, photos, both (got {raw!r})")


def _posts(raw: Optional[str]) -> Optional[set[str]]:
    if not raw:
        return None
    return {p.strip() for p in raw.split(",") if p.strip()}


@app.command()
def download(
    url: str = typer.Argument(..., help="XenForo thread URL"),
    out: Path = typer.Option(Path("./downloads"), "--out", "-o", help="Output root"),
    cookies: Optional[Path] = typer.Option(None, help="Path to a Netscape cookies.txt"),
    cookies_from_browser: Optional[str] = typer.Option(
        None, "--cookies-from-browser",
        help="Read cookies live from this browser (firefox/chrome/edge/...)",
    ),
    xf_user: Optional[str] = typer.Option(None, help="xf_user cookie value"),
    xf_session: Optional[str] = typer.Option(None, help="xf_session cookie value"),
    pages: Optional[str] = typer.Option(None, help="Page range, e.g. '1-3' or just '4'"),
    posts: Optional[str] = typer.Option(None, help="Comma-separated post IDs to keep"),
    max_height: int = typer.Option(1080, help="Max video height; 4K always excluded"),
    concurrency: int = typer.Option(6, help="Parallel resolves/downloads"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only, no files written"),
    no_curl_cffi: bool = typer.Option(
        False, help="Disable curl-cffi (TLS-impersonating) requests"
    ),
    only: str = typer.Option(
        "both", "--only",
        help="Filter resource kinds: 'videos', 'photos', or 'both' (default).",
    ),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress progress bars"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Scrape a XenForo thread and download all resolvable media."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    only_norm = _normalize_only(only)
    cfg = Config(
        out_dir=out.resolve(),
        cookies_file=cookies,
        cookies_browser=cookies_from_browser,
        xf_user=xf_user,
        xf_session=xf_session,
        pages=_pages(pages),
        posts=_posts(posts),
        max_height=max_height,
        concurrency=concurrency,
        dry_run=dry_run,
        use_curl_cffi=not no_curl_cffi,
        only_kind=only_norm,
        quiet=quiet,
    )
    orc = Orchestrator(cfg)
    asyncio.run(orc.run(url))


@app.command("list-resolvers")
def list_resolvers() -> None:
    """List every registered host resolver."""
    for cls in sorted(all_resolvers(), key=lambda c: c.name):
        kind = "stub" if cls.__name__.endswith("Stub") else "ok"
        console.print(f"  [bold]{cls.name:<14}[/] [dim]{kind}[/]   "
                      f"patterns={len(cls.patterns)} albums={len(cls.album_patterns)}")


@app.command("debug-fetch")
def debug_fetch(
    url: str = typer.Argument(..., help="URL to fetch with the same client fmo uses"),
    cookies: Optional[Path] = typer.Option(None, help="Optional Netscape cookies.txt"),
    no_curl_cffi: bool = typer.Option(
        False, help="Disable curl-cffi (TLS-impersonating) requests"
    ),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Write body to file"),
) -> None:
    """Fetch a URL with the same HTTP stack the resolvers use and dump the body.

    Useful for capturing real HTML from Cloudflare-fronted hosts (bunkr,
    turbo, gofile) so a resolver can be fixed against the current markup.
    """
    from http.cookiejar import MozillaCookieJar
    from .config import Config
    from .http_client import HttpClient

    cj = None
    if cookies:
        cj = MozillaCookieJar(str(cookies))
        cj.load(ignore_discard=True, ignore_expires=True)

    cfg = Config(out_dir=Path("."), use_curl_cffi=not no_curl_cffi)

    async def go() -> None:
        client = HttpClient(
            user_agent=cfg.user_agent,
            cookies=cj,
            timeout_s=cfg.timeout_s,
            retries=cfg.retries,
            use_curl_cffi=cfg.use_curl_cffi,
        )
        try:
            body = await client.get_text(url, referer=url)
        finally:
            await client.aclose()
        if out:
            out.write_text(body, encoding="utf-8")
            console.print(f"wrote {len(body):,} bytes to {out}")
        else:
            sys.stdout.write(body)

    asyncio.run(go())


@app.command("check-resolvers")
def check_resolvers(
    live: bool = typer.Option(False, "--live", help="Actually hit the network"),
) -> None:
    """Health-check that each resolver's URL patterns still match its host."""
    import re
    sample = {
        "direct":     "https://example.com/foo.jpg",
        "bunkr":      "https://bunkr.cr/a/abcd1234",
        "cyberdrop":  "https://cyberdrop.me/a/abcd1234",
        "jpgsu":      "https://jpg5.su/img/foo.bar",
        "pixhost":    "https://pixhost.to/show/123/foo.jpg",
        "pixeldrain": "https://pixeldrain.com/u/abc12345",
        "imgbox":     "https://imgbox.com/g/abc12345",
        "redgifs":    "https://redgifs.com/watch/abcdef",
    }
    failed = 0
    for cls in all_resolvers():
        url = sample.get(cls.name)
        if not url:
            continue
        regs = cls.patterns + cls.album_patterns
        if not any(re.search(p, url, re.I) for p in regs):
            console.print(f"  [red]MISS[/] {cls.name}: no pattern matched {url}")
            failed += 1
        else:
            console.print(f"  [green]OK  [/] {cls.name}")
    if live:
        console.print("[yellow]--live live-resolver pings are run by `pytest --live`[/]")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    app()
