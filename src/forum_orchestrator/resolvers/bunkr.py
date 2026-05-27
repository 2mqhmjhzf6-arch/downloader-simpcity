"""Bunkr (.cr / .ax / .black / ...).

Bunkr serves albums at ``/a/<slug>`` and single files at ``/v/<slug>`` (videos)
or ``/i/<slug>``/CDN subdomains (images). The site is permanently behind a
Cloudflare interstitial and the CDN base for any given file rotates between a
handful of subdomains — see the userscript's ``xfpdBunkrFilterBases`` for the
full list. Our approach:

1. For an album URL, fetch the page and pull every internal link that looks
   like a single-file URL (``/v/`` or ``/f/``).
2. For a single-file URL, fetch the page, grab the human-readable filename
   from the ``<title>`` and the canonical CDN ``download`` link from the
   ``link[rel=preload]`` / ``<source>`` / ``<a class=ic-download>`` element,
   whichever is present.
3. POST to ``/api/vs`` with the slug if the file URL is masked (some bunkr
   pages no longer expose the CDN link directly).

The actual download happens later in the orchestrator — we only return the
resolved direct URL + filename here.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from ..models import Kind, Resource
from .base import Resolver, ResolveContext, basename_from_url, guess_kind, register

_BUNKR_TLDS = r"(ac|ax|black|cat|ci|cr|fi|is|media|nu|pk|ph|ps|red|ru|se|si|site|sk|ws|su|org)"

# Album grid item: <a href="https://bunkr.cr/v/foo.mp4">  (or /f/ for files)
_GRID_LINK = re.compile(
    rf"href=[\"'](https?://(?:[\w-]+\.)?bunkr+\.{_BUNKR_TLDS}/(?:v|f|i)/[^\"'>]+)",
    re.IGNORECASE,
)


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
        # CDN-direct URL: just hand it back.
        if re.search(r"(cdn|i-pizza|big-taco|b-cdn|gigachad-cdn|bunkr-cache)\.", url, re.I):
            return [Resource(
                url=url,
                filename=basename_from_url(url),
                kind=guess_kind(url),
                referer=_origin(url),
                dedup_key=url,
            )]
        return await self._resolve_single(url, ctx)

    async def _resolve_album(self, url: str, ctx: ResolveContext) -> list[Resource]:
        html = await ctx.http.get_text(url, referer=url)
        # Album title -> not actually used for filenames (each file has its own).
        seen: set[str] = set()
        items: list[Resource] = []
        for m in _GRID_LINK.finditer(html):
            link = m.group(1)
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

        # 1. Human filename from page title (`foo.mp4 | Bunkr`)
        title = ""
        if (t := dom.css_first("title")) is not None:
            title = (t.text() or "").strip()
        title = re.sub(r"\s*\|\s*Bunkr\s*$", "", title, flags=re.I).strip()

        # 2. Canonical CDN download URL — try a handful of selectors.
        direct: str | None = None
        for sel in (
            'a.ic-download-01-svg, a[class*="ic-download"]',
            'a.btn-main[href*="get.bunkr"]',
            'source[src]',
            'video[src]',
            'img.max-h-full[src]',
            'link[rel="preload"][as="image"][href]',
        ):
            node = dom.css_first(sel)
            if node is None:
                continue
            cand = node.attributes.get("href") or node.attributes.get("src")
            if cand:
                direct = urljoin(url, cand)
                break

        # 3. Fallback: scrape JSON blob shipped in <script id="__NEXT_DATA__">
        if direct is None:
            blob = dom.css_first('script#__NEXT_DATA__')
            if blob and blob.text():
                m = re.search(r'"(?:cdn|url|src)":"(https?://[^"]+)"', blob.text())
                if m:
                    direct = m.group(1).encode().decode("unicode_escape")

        if direct is None:
            return []

        return [Resource(
            url=direct,
            filename=title or basename_from_url(direct),
            kind=guess_kind(direct) if guess_kind(direct) != Kind.OTHER else guess_kind(title),
            referer=url,
            dedup_key=re.sub(r"^https?://[^/]+", "", direct),  # path-only dedup across CDN bases
        )]


def _origin(url: str) -> str:
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}"
