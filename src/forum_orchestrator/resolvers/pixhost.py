"""Pixhost: single images + galleries.

Single image: https://pixhost.to/show/<id>/<name>  -> thumb URL exposed; the
"real" image lives on https://img<NN>.pixhost.to/images/<id>/<name>.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from ..models import Resource
from .base import Resolver, ResolveContext, basename_from_url, guess_kind, register


@register
class Pixhost(Resolver):
    name = "pixhost"
    patterns = [r"(?:t|img)\d*\.pixhost\.to/", r"pixhost\.to/show/"]
    album_patterns = [r"pixhost\.to/gallery/"]

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        if "/gallery/" in url:
            return await self._gallery(url, ctx)
        if "/show/" in url:
            return await self._show(url, ctx)
        # Direct: t<NN>.pixhost.to/thumbs/.../<name> -> swap to img<NN>/images/
        direct = re.sub(r"^https?://t(\d+)\.pixhost\.to/thumbs/",
                        r"https://img\1.pixhost.to/images/", url)
        return [Resource(
            url=direct,
            filename=basename_from_url(direct),
            kind=guess_kind(direct),
            referer="https://pixhost.to/",
            dedup_key=direct,
        )]

    async def _show(self, url: str, ctx: ResolveContext) -> list[Resource]:
        html = await ctx.http.get_text(url, referer=url)
        dom = HTMLParser(html)
        n = dom.css_first('img#image')
        if n is None:
            return []
        direct = n.attributes.get("src")
        if not direct:
            return []
        return [Resource(
            url=urljoin(url, direct),
            filename=basename_from_url(direct),
            kind=guess_kind(direct),
            referer=url,
            dedup_key=direct,
        )]

    async def _gallery(self, url: str, ctx: ResolveContext) -> list[Resource]:
        html = await ctx.http.get_text(url, referer=url)
        dom = HTMLParser(html)
        out: list[Resource] = []
        for a in dom.css('a.image-link[href], a[href*="/show/"]'):
            href = a.attributes.get("href")
            if href:
                out.extend(await self._show(urljoin(url, href), ctx))
        return out
