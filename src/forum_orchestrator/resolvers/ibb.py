"""ImgBB (ibb.co) — single images + albums.

Pages expose the original through ``<meta property="og:image">``. Albums list
each image in ``div.image-container > a[href="/<id>"]`` with pagination via
``a[data-pagination="next"]``. This is also a Chevereto fork but the markup
is older and the album selector differs — we keep a local implementation.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from ..models import Resource
from .base import Resolver, ResolveContext, basename_from_url, guess_kind, register


@register
class Ibb(Resolver):
    name = "ibb"
    patterns = [r"(?:[a-z]\d?\.)?ibb\.co/(?!album/)[A-Za-z0-9]+"]
    album_patterns = [r"ibb\.co/album/"]

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        if "/album/" in url:
            return await self._album(url, ctx)
        if re.match(r"https?://i\.ibb\.co/", url):
            # already direct CDN URL
            return [Resource(
                url=url,
                filename=basename_from_url(url),
                kind=guess_kind(url),
                referer="https://ibb.co/",
                dedup_key=url,
            )]
        return await self._image_page(url, ctx)

    async def _image_page(self, url: str, ctx: ResolveContext) -> list[Resource]:
        html = await ctx.http.get_text(url, referer=url)
        dom = HTMLParser(html)
        meta = dom.css_first('meta[property="og:image"]')
        if meta is None:
            return []
        direct = meta.attributes.get("content")
        if not direct:
            return []
        return [Resource(
            url=direct,
            filename=basename_from_url(direct),
            kind=guess_kind(direct),
            referer=url,
            dedup_key=direct,
        )]

    async def _album(self, url: str, ctx: ResolveContext) -> list[Resource]:
        out: list[Resource] = []
        seen: set[str] = set()
        next_url: str | None = url
        for _ in range(50):  # safety cap
            if next_url is None:
                break
            try:
                html = await ctx.http.get_text(next_url, referer=url)
            except Exception:
                break
            dom = HTMLParser(html)
            found = 0
            for a in dom.css('div.image-container a[href], a.image-container[href]'):
                href = a.attributes.get("href")
                if not href:
                    continue
                full = urljoin(next_url, href)
                if full in seen or "/album/" in full:
                    continue
                seen.add(full)
                found += 1
                out.extend(await self._image_page(full, ctx))
            # Look for next-page link.
            nxt = dom.css_first('a[data-pagination="next"][href]')
            if nxt is None or not found:
                break
            next_url = urljoin(next_url, nxt.attributes.get("href") or "")
        return out
