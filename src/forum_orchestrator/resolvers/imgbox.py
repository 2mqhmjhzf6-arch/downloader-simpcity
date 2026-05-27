"""Imgbox: single images + ``/g/`` galleries.

Single image page: ``https://imgbox.com/<id>`` (or ``/i/<id>``)
The "original" lives on ``https://imagesNN.imgbox.com/<a>/<b>/<id>_o.<ext>``;
the page exposes it as ``img#img``.
"""

from __future__ import annotations

from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from ..models import Resource
from .base import Resolver, ResolveContext, basename_from_url, guess_kind, register


@register
class Imgbox(Resolver):
    name = "imgbox"
    patterns = [
        r"(?:thumbs|images)\d*\.imgbox\.com/",
        r"imgbox\.com/(?!g/)[A-Za-z0-9]{6,}",
    ]
    album_patterns = [r"imgbox\.com/g/"]

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        if "/g/" in url:
            return await self._gallery(url, ctx)
        if "thumbs" in url:
            # thumbs<NN>.imgbox.com/.../<id>_b.<ext>  ->  imagesNN.imgbox.com/.../<id>_o.<ext>
            direct = url.replace("thumbs", "images", 1).replace("_b.", "_o.")
            return [Resource(
                url=direct,
                filename=basename_from_url(direct),
                kind=guess_kind(direct),
                referer="https://imgbox.com/",
                dedup_key=direct,
            )]
        return await self._page(url, ctx)

    async def _page(self, url: str, ctx: ResolveContext) -> list[Resource]:
        html = await ctx.http.get_text(url, referer=url)
        dom = HTMLParser(html)
        n = dom.css_first('img#img')
        if n is None:
            return []
        direct = n.attributes.get("src")
        if not direct:
            return []
        # the page's <img src> is the _o.ext original
        return [Resource(
            url=urljoin(url, direct),
            filename=n.attributes.get("title") or basename_from_url(direct),
            kind=guess_kind(direct),
            referer=url,
            dedup_key=direct,
        )]

    async def _gallery(self, url: str, ctx: ResolveContext) -> list[Resource]:
        html = await ctx.http.get_text(url, referer=url)
        dom = HTMLParser(html)
        out: list[Resource] = []
        for a in dom.css('a[href^="/"][href*="/"]'):
            href = a.attributes.get("href") or ""
            if href.startswith("/") and len(href) >= 7 and "/g/" not in href:
                out.extend(await self._page(urljoin(url, href), ctx))
        return out
