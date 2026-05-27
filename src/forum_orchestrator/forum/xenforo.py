"""XenForo thread scraper.

Handles:
  * thread metadata (title, id)
  * pagination (``?page=N`` discovery + last-page marker)
  * post extraction (id, post number, posted timestamp, raw body HTML)
  * URL extraction from a post body (``href``, ``src``, ``data-url``)

Tested against the SimpCity / XenForo 2.2 templates the reference userscript
targets, but the selectors are generic enough that any default-themed XenForo
forum should parse.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional
from urllib.parse import parse_qs, urljoin, urlparse

from selectolax.parser import HTMLParser

from ..http_client import HttpClient
from ..models import Post, PostLink, Thread

_THREAD_ID_RX = re.compile(r"/threads/[^/]+\.(\d+)", re.IGNORECASE)

_REDIRECT_HOST = re.compile(r"^https?://(?:[\w-]+\.)?simpcity\.\w+/redirect/", re.IGNORECASE)

_UI_ASSET = re.compile(
    r"(?:/assets/|/static/|/favicon|"
    r"dash\.bunkr\.|/icon[\w.-]*\.(?:svg|png|ico)|"
    r"twemoji[@/]|/emoji[/.]|/sprite\.)",
    re.IGNORECASE,
)


def _unwrap_redirect(url: str) -> str:
    """Decode `simpcity.cr/redirect/?to=<base64>&...` envelopes."""
    if not _REDIRECT_HOST.match(url):
        return url
    q = parse_qs(urlparse(url).query)
    raw = (q.get("to") or [""])[0]
    if not raw:
        return url
    padded = raw + "=" * (-len(raw) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            decoded = decoder(padded).decode("utf-8", "replace")
        except Exception:
            continue
        if decoded.startswith(("http://", "https://")):
            return decoded
    return url


def _is_ui_asset(url: str) -> bool:
    return bool(_UI_ASSET.search(url))


def parse_thread_id(url: str) -> str:
    m = _THREAD_ID_RX.search(url)
    if not m:
        raise ValueError(f"could not extract thread id from {url!r}")
    return m.group(1)


def _thread_root(url: str) -> str:
    # Strip /page-N and anchors so we can build /page-K.
    base = re.sub(r"/page-\d+/?$", "/", url.split("#", 1)[0])
    if not base.endswith("/"):
        base += "/"
    return base


@dataclass
class ThreadScraper:
    http: HttpClient

    async def fetch_thread(
        self,
        url: str,
        pages: Optional[tuple[int, int]] = None,
        posts: Optional[set[str]] = None,
    ) -> tuple[Thread, list[Post]]:
        thread_id = parse_thread_id(url)
        first_url = _thread_root(url) + "page-1"
        html = await self.http.get_text(first_url, referer=url)
        dom = HTMLParser(html)
        title = _extract_title(dom)
        total_pages = _extract_total_pages(dom)
        thread = Thread(url=url, title=title, thread_id=thread_id, pages=total_pages)

        lo, hi = (pages or (1, total_pages))
        lo = max(1, lo)
        hi = min(total_pages, hi)

        all_posts: list[Post] = []
        for page in range(lo, hi + 1):
            page_url = _thread_root(url) + f"page-{page}"
            page_html = html if page == 1 else await self.http.get_text(page_url, referer=url)
            for post in _extract_posts(page_html, page_url):
                if posts and post.post_id not in posts:
                    continue
                all_posts.append(post)
        return thread, all_posts

    def extract_links(self, post: Post, forum_origin: str) -> list[PostLink]:
        dom = HTMLParser(post.raw_html)
        urls: list[str] = []
        # Reference userscript matches against href / src / data-url. Mirror that.
        for node in dom.css("a[href], img[src], img[data-url], iframe[src], video source[src]"):
            for attr in ("href", "src", "data-url"):
                v = node.attributes.get(attr)
                if v:
                    abs_url = urljoin(forum_origin, v)
                    abs_url = _unwrap_redirect(abs_url)
                    if _is_ui_asset(abs_url):
                        break
                    urls.append(abs_url)
                    break  # one URL per element
        return [PostLink(url=u, post=post) for u in _dedup_preserve(urls)]


# ---------- helpers ---------------------------------------------------------


def _extract_title(dom: HTMLParser) -> str:
    n = dom.css_first('h1.p-title-value, h1.MessageCard__title, h1.threadHead, title')
    if n is None:
        return "Untitled thread"
    raw = n.text(strip=True)
    return re.sub(r"\s*\|\s*SimpCity.*$", "", raw).strip() or "Untitled thread"


def _extract_total_pages(dom: HTMLParser) -> int:
    nav = dom.css_first('.pageNav-main, nav.pageNav, .block-outer .pageNav')
    if nav is None:
        return 1
    best = 1
    for a in nav.css("a, li"):
        t = a.text(strip=True)
        if t.isdigit():
            best = max(best, int(t))
    return best


def _extract_posts(html: str, page_url: str) -> Iterable[Post]:
    dom = HTMLParser(html)
    for art in dom.css('article.message, div.message'):
        pid = (
            art.attributes.get("data-content")
            or art.attributes.get("id")
            or ""
        )
        m = re.search(r"(\d+)$", pid)
        if not m:
            continue
        post_id = m.group(1)
        try:
            post_number = int(
                (art.css_first('.message-attribution-opposite a, .messageDetails .postNumber')
                 .text(strip=True)).lstrip("#")
            )
        except Exception:
            post_number = 0

        posted_at: Optional[datetime] = None
        t_node = art.css_first('time[datetime]')
        if t_node is not None:
            dt = t_node.attributes.get("datetime")
            if dt:
                try:
                    posted_at = datetime.fromisoformat(dt.replace("Z", "+00:00"))
                except ValueError:
                    posted_at = None

        body = art.css_first('.message-body, .bbWrapper')
        body_html = body.html if body is not None else art.html or ""
        yield Post(
            post_id=post_id,
            post_number=post_number,
            posted_at=posted_at.astimezone(timezone.utc) if posted_at else None,
            raw_html=body_html,
            page_url=page_url,
        )


def _dedup_preserve(seq: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
