"""saint2.su / saint2.cr — video host.

Each video page exposes its source via ``<video id="main-video"><source src=…>``
or, for newer pages, in a small JSON blob inside an inline script. Album URLs
(``/a/<slug>``) list every clip with ``a[href*='/embed/']``.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from ..models import Kind, Resource
from .base import Resolver, ResolveContext, basename_from_url, register

_HOST = r"saint2\.(?:su|cr)"


@register
class Saint(Resolver):
    name = "saint"
    patterns = [rf"{_HOST}/(?:embed|d|watch)/"]
    album_patterns = [rf"{_HOST}/a/"]

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        if "/a/" in url:
            return await self._album(url, ctx)
        return await self._single(url, ctx)

    async def _single(self, url: str, ctx: ResolveContext) -> list[Resource]:
        html = await ctx.http.get_text(url, referer=url)
        dom = HTMLParser(html)
        direct = None
        for sel in (
            'video#main-video source[src]',
            'video source[src]',
            'video[src]',
        ):
            n = dom.css_first(sel)
            if n is not None:
                direct = n.attributes.get("src")
                if direct:
                    break
        if direct is None:
            m = re.search(r'"file"\s*:\s*"(https?://[^"]+\.mp4[^"]*)"', html)
            if m:
                direct = m.group(1).encode().decode("unicode_escape")
        if not direct:
            return []
        direct = urljoin(url, direct)
        name = basename_from_url(direct)
        # The CDN filename is usually opaque; recover the human title from <title>.
        title_node = dom.css_first("title")
        if title_node is not None:
            title = re.sub(r"\s*\|\s*Saint.*$", "", title_node.text() or "", flags=re.I).strip()
            if title:
                # Preserve original extension.
                ext = name.rsplit(".", 1)[-1] if "." in name else "mp4"
                name = f"{title}.{ext}"
        return [Resource(
            url=direct,
            filename=name,
            kind=Kind.VIDEO,
            referer=url,
            dedup_key=re.sub(r"^https?://[^/]+", "", direct),
        )]

    async def _album(self, url: str, ctx: ResolveContext) -> list[Resource]:
        html = await ctx.http.get_text(url, referer=url)
        dom = HTMLParser(html)
        out: list[Resource] = []
        seen: set[str] = set()
        for a in dom.css('a[href*="/embed/"], a[href*="/watch/"]'):
            href = a.attributes.get("href")
            if not href:
                continue
            full = urljoin(url, href)
            if full in seen:
                continue
            seen.add(full)
            try:
                out.extend(await self._single(full, ctx))
            except Exception:
                continue
        return out
