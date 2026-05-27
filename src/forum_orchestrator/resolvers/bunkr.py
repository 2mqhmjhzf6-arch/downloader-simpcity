"""Bunkr (.cr / .ax / .black / ...).

Bunkr serves albums at ``/a/<slug>`` and single files at ``/f/<slug>`` or
``/v/<slug>``. The site is permanently behind Cloudflare and the CDN base
for any given file rotates between many subdomains. Modern (server-rendered)
bunkr does NOT use Next.js anymore — album pages ship a grid of HTML cards
with ``<a href="/f/<slug>">``; single-file pages ship the CDN URL in an
``<img class="max-h-full">``, ``<source>``, ``<video>``, or a "Download"
button. Older Next.js markup is still handled as a fallback.

Strategy:

1. **Album** (``/a/<slug>``): scrape ``<a href="/f/.">`` from the grid; if
   none found, fall back to the Next.js JSON walker.
2. **Single file** (``/f/<slug>`` or ``/v/<slug>``): try CSS selectors for
   the download/source nodes; fall back to Next.js JSON; final fallback is
   a regex pass over the full HTML for any ``cdn*.bunkr.*`` /
   ``*.scdn.st/.../<file>`` URL.
3. **CDN-direct** (``cdn*.bunkr.*/<basename>``): the naked CDN URL returns
   a Cloudflare interstitial. Route back through the file page; the slug
   is the trailing ``-XXXXX`` segment before the extension. If neither
   ``/f/<slug>`` nor ``/v/<slug>`` works, return [] so the URL lands in
   ``failed_downloads.txt`` instead of being saved as a corrupt .mp4.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from ..models import Kind, Resource
from .base import Resolver, ResolveContext, basename_from_url, guess_kind, register

_BUNKR_TLDS = r"(ac|ax|black|cat|ci|cr|fi|is|media|nu|pk|ph|ps|red|ru|se|si|site|sk|ws|su|org)"

_BUNKR_HOME = "https://bunkr.cr"

_GRID_LINK = re.compile(
    rf"href=[\"'](https?://(?:[\w-]+\.)?bunkr+\.{_BUNKR_TLDS}/(?:v|f|i)/[^\"'>]+)",
    re.IGNORECASE,
)

_VIDEO_EXTS = re.compile(r"\.(mp4|m4v|mov|webm|mkv|avi|ts)(?:\?|$)", re.IGNORECASE)

# Trailing slug from a bunkr CDN filename, e.g. "84-373_Pandora-MQ0959lw.mp4"
# -> "MQ0959lw". 8-15 alphanumeric chars after a dash, before the extension.
_CDN_SLUG_TAIL = re.compile(r"-([A-Za-z0-9_-]{6,20})(?=\.[A-Za-z0-9]{2,5}(?:\?|$))")

# Bunkr's many CDN hostnames + a generic match for "static.scdn.st" media.
_CDN_HOSTS_RE = re.compile(
    r"(?:cdn\d*|i-pizza\d*|big-taco[\w-]*|b-cdn|gigachad-cdn|bunkr-cache)\.",
    re.IGNORECASE,
)

# The bunkr "get" interstitial — a fake direct link that actually serves a
# token-gated HTML page, not the file. We need to follow it and re-extract.
_GET_INTERSTITIAL_RE = re.compile(
    rf"^https?://(?:[\w-]+\.)?get\.bunkr+r?\.{_BUNKR_TLDS}/",
    re.IGNORECASE,
)


def _is_real_cdn(url: str) -> bool:
    """True unless ``url`` is the ``get.bunkrr`` interstitial or an HTML page.

    Used to reject candidate "direct" URLs that would write a Cloudflare /
    token-gated HTML body to disk under a media filename.
    """
    if _GET_INTERSTITIAL_RE.match(url):
        return False
    path = urlparse(url).path.lower()
    if path.endswith((".html", ".htm", ".php")):
        return False
    # Bunkr's own file-page paths look like ``/v/<slug>`` or ``/f/<slug>``;
    # those are HTML pages, not direct downloads.
    host = urlparse(url).netloc.lower()
    if "bunkr" in host and re.search(r"^/(v|f|i|a)/", path):
        return False
    return True


@register
class Bunkr(Resolver):
    name = "bunkr"
    patterns = [
        rf"(?:[\w-]+\.)?bunkr+\.{_BUNKR_TLDS}/(?:v|f|i)/",
        rf"(?:i|cdn|i-pizza|big-taco-1img)(?:\d+)?\.bunkr+\.{_BUNKR_TLDS}/",
        r"(?:bunkr-cache|b-cdn|gigachad-cdn)\.[a-z]+/",
    ]
    album_patterns = [rf"bunkr+\.{_BUNKR_TLDS}/a/"]

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        if "/a/" in url:
            return await self._resolve_album(url, ctx)
        # CDN-direct URL: route back through bunkr.cr's file page so we get
        # a real CDN link + correct Referer (the naked CDN URL returns an
        # interstitial unless we hit it via the file page).
        if _CDN_HOSTS_RE.search(url):
            base = basename_from_url(url)
            # The bunkr file-page slug is the trailing segment after the
            # last "-" (before the extension): "foo-bar-MQ0959lw.mp4" -> "MQ0959lw".
            slug_m = _CDN_SLUG_TAIL.search(base)
            candidates: list[str] = []
            if slug_m:
                candidates.append(slug_m.group(1))
            # Last-resort: the whole basename (old bunkr behavior).
            candidates.append(base)
            route_pref = "v" if _VIDEO_EXTS.search(base) else "f"
            for slug in candidates:
                for page_route in (route_pref, "v" if route_pref == "f" else "f"):
                    page_url = f"{_BUNKR_HOME}/{page_route}/{slug}"
                    try:
                        items = await self._resolve_single(page_url, ctx)
                    except Exception:
                        items = []
                    if items:
                        return items
            # Couldn't find a working file page: fail loudly so the URL ends
            # up in failed_downloads.txt instead of saving the CF interstitial
            # as a corrupt .mp4.
            return []
        return await self._resolve_single(url, ctx)

    async def _resolve_album(self, url: str, ctx: ResolveContext) -> list[Resource]:
        html = await ctx.http.get_text(url, referer=url)
        dom = HTMLParser(html)

        # New (server-rendered) bunkr: <a href="/f/<slug>"> inside each card.
        slugs: list[str] = []
        for a in dom.css('a[href*="/f/"], a[href*="/v/"], a[href*="/i/"]'):
            href = a.attributes.get("href")
            if not href or href.startswith(("#", "javascript:")):
                continue
            slugs.append(urljoin(url, href))

        # Fallback 1: regex over raw HTML for absolute bunkr file URLs.
        if not slugs:
            for m in _GRID_LINK.finditer(html):
                slugs.append(m.group(1))

        # Fallback 2: legacy Next.js JSON.
        if not slugs:
            slugs.extend(_album_slugs_from_next_data(html))

        seen: set[str] = set()
        items: list[Resource] = []
        for link in slugs:
            if not link.startswith("http"):
                link = f"{_BUNKR_HOME}/v/{link}"
            if link in seen:
                continue
            seen.add(link)
            try:
                items.extend(await self._resolve_single(link, ctx))
            except Exception:
                continue
        return items

    async def _resolve_single(self, url: str, ctx: ResolveContext) -> list[Resource]:
        html = await ctx.http.get_text(url, referer=url)
        dom = HTMLParser(html)

        title = ""
        if (t := dom.css_first("title")) is not None:
            title = (t.text() or "").strip()
        title = re.sub(r"\s*\|\s*Bunkr\s*$", "", title, flags=re.I).strip()

        direct: str | None = None
        filename: str | None = None

        # 1. Legacy Next.js JSON.
        data = _next_data(dom)
        if data is not None:
            file_obj = _find_file_object(data)
            if file_obj is not None:
                filename = (file_obj.get("name") or file_obj.get("filename") or "").strip() or None
                cdn = (file_obj.get("cdn") or "").strip()
                raw_url = (file_obj.get("url") or file_obj.get("src") or "").strip()
                if raw_url.startswith("http"):
                    direct = raw_url
                elif cdn and filename:
                    direct = cdn.rstrip("/") + "/" + filename.lstrip("/")
                elif cdn:
                    direct = cdn

        # 2. CSS selectors covering current + older markup.
        if direct is None:
            for sel in (
                'a[href*="get.bunkr"]',
                'a.btn-main[href*="cdn"]',
                'a.ic-download-01-svg[href]',
                'a[class*="ic-download"][href]',
                'a[download][href]',
                'source[src]',
                'video[src]',
                'img.max-h-full[src]',
                'img[class*="grid-images_box-img"][src]',
                'link[rel="preload"][as="image"][href]',
            ):
                node = dom.css_first(sel)
                if node is None:
                    continue
                cand = node.attributes.get("href") or node.attributes.get("src")
                if not cand:
                    continue
                cand = urljoin(url, cand)
                # Skip obvious thumbnails — they're tiny PNGs, not the original.
                if "/thumbs/" in cand or cand.endswith("-thumb.jpg"):
                    continue
                direct = cand
                break

        # 3. Regex pass over raw HTML for any plausible CDN URL.
        if direct is None:
            for rx in (
                r'"(?:cdn|url|src|file)":"(https?:\\?/\\?/[^"]+)"',
                r'(https?://(?:[\w-]+\.)?(?:scdn\.st|bunkr[\w.-]+|b-cdn[\w.-]*)/[^\s"\'<>]+\.[A-Za-z0-9]{2,5}(?:\?[^\s"\'<>]*)?)',
            ):
                m = re.search(rx, html, re.IGNORECASE)
                if m:
                    cand = m.group(1).encode().decode("unicode_escape")
                    if "/thumbs/" in cand:
                        continue
                    direct = cand
                    break

        if direct is None:
            return []

        # If the candidate URL points at the "get.bunkrr" interstitial, follow
        # it once and re-extract from THAT page. Returning the interstitial as
        # a direct download writes a token-gated HTML page to disk with the
        # file's extension.
        if _GET_INTERSTITIAL_RE.match(direct):
            try:
                followed = await self._follow_get_interstitial(direct, ctx)
            except Exception:
                followed = None
            if not followed or not _is_real_cdn(followed):
                return []
            direct = followed

        # Reject any final URL that obviously isn't a media file (.html,
        # extension-less, etc.). Better to fail loudly than to save corruption.
        if not _is_real_cdn(direct):
            return []
        if direct.split("?", 1)[0].lower().endswith((".html", ".htm", ".php")):
            return []

        name = filename or title or basename_from_url(direct)
        # Strip junk from the title-derived name (Bunkr appends " | Bunkr").
        name = re.sub(r"\s*\|\s*Bunkr\s*$", "", name, flags=re.I).strip()
        kind = guess_kind(direct)
        if kind == Kind.OTHER:
            kind = guess_kind(name)

        return [Resource(
            url=direct,
            filename=name,
            kind=kind,
            referer=f"{_BUNKR_HOME}/",
            dedup_key=re.sub(r"^https?://[^/]+", "", direct),
        )]

    async def _follow_get_interstitial(self, url: str, ctx: ResolveContext) -> str | None:
        """Resolve a ``get.bunkrr.<tld>/file/<id>`` URL into the real CDN link.

        The interstitial page either embeds a `<source>` / `<video>` pointing
        at the CDN, exposes a `download_url` field in inlined JSON, or just
        redirects via JS. We accept any of those.
        """
        html = await ctx.http.get_text(url, referer=f"{_BUNKR_HOME}/")
        dom = HTMLParser(html)
        for sel in (
            'source[src]', 'video[src]', 'a[download][href]',
            'a[href*="cdn"]', 'a[href*="scdn.st"]',
        ):
            n = dom.css_first(sel)
            if n is None:
                continue
            cand = n.attributes.get("href") or n.attributes.get("src")
            if cand:
                cand = urljoin(url, cand)
                if _is_real_cdn(cand):
                    return cand
        for rx in (
            r'"(?:download_url|cdn|url|src|file)":"(https?:\\?/\\?/[^"]+)"',
            r'(https?://(?:[\w-]+\.)?(?:scdn\.st|bunkr[\w.-]+|b-cdn[\w.-]*)/[^\s"\'<>]+\.[A-Za-z0-9]{2,5}(?:\?[^\s"\'<>]*)?)',
        ):
            m = re.search(rx, html, re.IGNORECASE)
            if m:
                cand = m.group(1).encode().decode("unicode_escape")
                if _is_real_cdn(cand):
                    return cand
        return None


def _next_data(dom: HTMLParser) -> dict[str, Any] | None:
    node = dom.css_first('script#__NEXT_DATA__')
    if node is None or not node.text():
        return None
    try:
        return json.loads(node.text())
    except json.JSONDecodeError:
        return None


def _find_file_object(data: Any) -> dict[str, Any] | None:
    """Walk the JSON looking for an object with a ``name`` and a CDN-ish key."""
    stack = [data]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            keys = cur.keys()
            if "name" in keys and any(k in keys for k in ("cdn", "url", "src", "mediafiles")):
                return cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def _album_slugs_from_next_data(html: str) -> list[str]:
    dom = HTMLParser(html)
    data = _next_data(dom)
    if data is None:
        return []
    slugs: list[str] = []
    stack = [data]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            slug = cur.get("slug") or cur.get("id")
            name = cur.get("name") or ""
            looks_like_file = any(k in cur for k in ("size", "type", "cdn", "url", "src"))
            if looks_like_file and isinstance(slug, str) and slug and isinstance(name, str) and name:
                route = "v" if _VIDEO_EXTS.search(name) else "f"
                slugs.append(f"{_BUNKR_HOME}/{route}/{slug}")
            else:
                stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return slugs


def _origin(url: str) -> str:
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}"
