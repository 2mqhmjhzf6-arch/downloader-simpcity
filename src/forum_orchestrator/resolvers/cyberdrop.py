"""Cyberdrop albums + single files.

The userscript hits the public Cyberdrop API:
    GET https://api.cyberdrop.me/api/album?albumId=<slug>
which returns a JSON list of files with original filenames. We do the same;
for single-file URLs we fetch the page and pick the canonical download link.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from ..models import Resource
from .base import Resolver, ResolveContext, basename_from_url, guess_kind, register

_TLD = r"(?:me|cc|ch|cloud|nl|to|cr)"
_ALBUM_SLUG = re.compile(rf"cyberdrop\.{_TLD}/a/([a-z0-9]+)", re.IGNORECASE)


@register
class Cyberdrop(Resolver):
    name = "cyberdrop"
    patterns = [
        rf"fs-\d+\.cyberdrop\.{_TLD}/",
        rf"cyberdrop\.{_TLD}/(?:f|e)/",
    ]
    album_patterns = [rf"cyberdrop\.{_TLD}/a/"]

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        if "/a/" in url:
            return await self._album(url, ctx)
        if "/f/" in url or "/e/" in url:
            return await self._single(url, ctx)
        # fs-<n>.cyberdrop.* CDN-direct URL
        return [Resource(
            url=url,
            filename=basename_from_url(url),
            kind=guess_kind(url),
            referer=f"https://cyberdrop.{urlparse(url).hostname.split('.')[-1]}/",
            dedup_key=url,
        )]

    async def _album(self, url: str, ctx: ResolveContext) -> list[Resource]:
        m = _ALBUM_SLUG.search(url)
        if not m:
            return []
        slug = m.group(1)
        api = f"https://api.cyberdrop.me/api/album?albumId={slug}"
        try:
            data = await ctx.http.get_json(api, referer=url)
        except Exception:
            return await self._album_html(url, ctx)
        files = data.get("files") or data.get("media") or []
        out: list[Resource] = []
        for it in files:
            direct = it.get("url") or it.get("href")
            if not direct:
                continue
            name = it.get("name") or it.get("filename") or basename_from_url(direct)
            out.append(Resource(
                url=direct,
                filename=name,
                kind=guess_kind(name),
                referer=url,
                dedup_key=re.sub(r"^https?://[^/]+", "", direct),
            ))
        return out

    async def _album_html(self, url: str, ctx: ResolveContext) -> list[Resource]:
        html = await ctx.http.get_text(url, referer=url)
        dom = HTMLParser(html)
        out: list[Resource] = []
        for a in dom.css('a[href*="/f/"], a.image[href]'):
            href = a.attributes.get("href")
            if href:
                out.extend(await self._single(href, ctx))
        return out

    async def _single(self, url: str, ctx: ResolveContext) -> list[Resource]:
        html = await ctx.http.get_text(url, referer=url)
        dom = HTMLParser(html)
        direct = None
        for sel in ('a#downloadBtn[href]', 'a.btn-download[href]', 'source[src]',
                    'img#image[src]', 'video[src]'):
            node = dom.css_first(sel)
            if node is not None:
                direct = node.attributes.get("href") or node.attributes.get("src")
                if direct:
                    break
        if not direct:
            return []
        name = direct.rsplit("/", 1)[-1]
        return [Resource(
            url=direct,
            filename=name,
            kind=guess_kind(name),
            referer=url,
            dedup_key=re.sub(r"^https?://[^/]+", "", direct),
        )]
