"""filester.me / .sh / .si / .gg — file host.

Single file ``/d/<slug>``:
  GET page; the direct link is published in ``<a class="btn-download" href>``
  or ``<a id="downloadbutton" href>``. Filename is in the page ``<h1>``.

Folder ``/f/<slug>``:
  GET page; rows are ``a[href*='/d/']`` — recurse into each. Pagination via
  ``a[rel="next"]``.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from ..models import Resource
from .base import Resolver, ResolveContext, basename_from_url, guess_kind, register

_HOST = r"filester\.(?:me|sh|si|gg)"


@register
class Filester(Resolver):
    name = "filester"
    patterns = [rf"{_HOST}/d/"]
    album_patterns = [rf"{_HOST}/f/"]

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        if "/f/" in url:
            return await self._folder(url, ctx)
        return await self._single(url, ctx)

    async def _single(self, url: str, ctx: ResolveContext) -> list[Resource]:
        html = await ctx.http.get_text(url, referer=url)
        dom = HTMLParser(html)
        direct = None
        for sel in (
            'a#downloadbutton[href]',
            'a.btn-download[href]',
            'a.btn-primary[href*="/download/"]',
            'a[download][href]',
        ):
            n = dom.css_first(sel)
            if n is not None:
                direct = n.attributes.get("href")
                if direct:
                    break
        if not direct:
            return []
        direct = urljoin(url, direct)

        name = basename_from_url(direct)
        h1 = dom.css_first("h1, h2.filename")
        if h1 is not None:
            t = (h1.text() or "").strip()
            if t and "." in t:
                name = t

        return [Resource(
            url=direct,
            filename=name,
            kind=guess_kind(name),
            referer=url,
            dedup_key=re.sub(r"^https?://[^/]+", "", direct),
        )]

    async def _folder(self, url: str, ctx: ResolveContext) -> list[Resource]:
        out: list[Resource] = []
        seen: set[str] = set()
        next_url: str | None = url
        for _ in range(50):
            if next_url is None:
                break
            html = await ctx.http.get_text(next_url, referer=url)
            dom = HTMLParser(html)
            found = 0
            for a in dom.css('a[href*="/d/"]'):
                href = a.attributes.get("href")
                if not href:
                    continue
                full = urljoin(next_url, href)
                if full in seen:
                    continue
                seen.add(full)
                found += 1
                try:
                    out.extend(await self._single(full, ctx))
                except Exception:
                    continue
            nxt = dom.css_first('a[rel="next"][href]')
            if nxt is None or not found:
                break
            next_url = urljoin(next_url, nxt.attributes.get("href") or "")
        return out
