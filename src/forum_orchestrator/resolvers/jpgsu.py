"""JPG.su / jpg.fish / jpg.church / cuckcapital.cr image host family.

These are all Chevereto installations. Pattern:
    https://jpg5.su/img/<slug>.<id>   -> single image page
    https://jpg5.su/a/<slug>          -> album

Each image page exposes the original by appending ``?width=original`` (Chevereto)
or by reading the ``link[rel=image_src]`` href.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from ..models import Resource
from .base import Resolver, ResolveContext, register, guess_kind, basename_from_url

_HOST = r"(?:simp\d+\.)?(?:cuckcapital\.cr|jpe?g\d?\.(?:church|fish|fishing|pet|su|cr))"


@register
class JpgSu(Resolver):
    name = "jpgsu"
    patterns = [rf"{_HOST}/img/", rf"{_HOST}/(?:[^/]+\.)?(?:jpe?g|png|gif|webp)"]
    album_patterns = [rf"{_HOST}/(?:a|album)/"]

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        if re.search(r"/(a|album)/", url):
            return await self._album(url, ctx)
        if "/img/" in url:
            return await self._image_page(url, ctx)
        # Already-direct CDN URL.
        return [Resource(
            url=_strip_thumb(url),
            filename=basename_from_url(_strip_thumb(url)),
            kind=guess_kind(url),
            referer=url,
            dedup_key=_strip_thumb(url),
        )]

    async def _image_page(self, url: str, ctx: ResolveContext) -> list[Resource]:
        html = await ctx.http.get_text(url, referer=url)
        dom = HTMLParser(html)
        direct = None
        for sel in ('a[data-action="download-image"][href]',
                    'meta[property="og:image"]',
                    'link[rel="image_src"]'):
            n = dom.css_first(sel)
            if not n:
                continue
            direct = n.attributes.get("href") or n.attributes.get("content")
            if direct:
                break
        if not direct:
            return []
        direct = urljoin(url, _strip_thumb(direct))
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
        # Chevereto paginates with ?sort=date_desc&page=N. We follow until empty.
        page = 1
        while True:
            page_url = url + (("&" if "?" in url else "?") + f"page={page}") if page > 1 else url
            try:
                html = await ctx.http.get_text(page_url, referer=url)
            except Exception:
                break
            dom = HTMLParser(html)
            found = 0
            for a in dom.css('a.image-container[href], a.--media[href]'):
                href = a.attributes.get("href")
                if href and href not in seen:
                    seen.add(href)
                    found += 1
                    out.extend(await self._image_page(urljoin(url, href), ctx))
            if found == 0:
                break
            page += 1
            if page > 50:                       # safety
                break
        return out


def _strip_thumb(u: str) -> str:
    # Chevereto thumbs: .../foo.md.jpg -> .../foo.jpg ; .../foo.th.jpg -> .../foo.jpg
    return re.sub(r"\.(md|th)\.(jpe?g|png|gif|webp)$", r".\2", u, flags=re.I)
