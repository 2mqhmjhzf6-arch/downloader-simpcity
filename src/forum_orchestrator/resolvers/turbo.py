"""turbo.cr — video host (Cyberdrop family).

Single video page (``/v/<slug>`` or ``/embed/<slug>``):
    The page embeds the CDN URL in a ``<source>`` tag and exposes the original
    filename in ``<title>`` / a ``data-filename`` attribute.

Album page (``/a/<slug>``):
    Lists each video as ``a[href*='/v/']``. We recurse.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from ..models import Kind, Resource
from .base import Resolver, ResolveContext, basename_from_url, register

_HOST = r"(?:[\w-]+\.)?turbo\.cr"


@register
class Turbo(Resolver):
    name = "turbo"
    patterns = [rf"{_HOST}/(?:embed|v|d)/"]
    album_patterns = [rf"{_HOST}/a/"]

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        if "/a/" in url:
            return await self._album(url, ctx)
        return await self._single(url, ctx)

    async def _single(self, url: str, ctx: ResolveContext) -> list[Resource]:
        html = await ctx.http.get_text(url, referer=url)
        dom = HTMLParser(html)

        # Filename — prefer <title>, fall back to data attribute.
        name = ""
        title_node = dom.css_first("title")
        if title_node is not None:
            name = re.sub(r"\s*\|\s*Turbo.*$", "", title_node.text() or "", flags=re.I).strip()
        if not name:
            f = dom.css_first("[data-filename]")
            if f is not None:
                name = f.attributes.get("data-filename") or ""

        direct = None
        for sel in ('source[src]', 'video[src]',
                    'a.btn-download[href]', 'a[download][href]'):
            n = dom.css_first(sel)
            if n is not None:
                direct = n.attributes.get("src") or n.attributes.get("href")
                if direct:
                    break
        if direct is None:
            m = re.search(r'"(?:url|src|file)"\s*:\s*"(https?://[^"]+\.mp4[^"]*)"', html)
            if m:
                direct = m.group(1).encode().decode("unicode_escape")
        if not direct:
            return []
        direct = urljoin(url, direct)
        if not name:
            name = basename_from_url(direct)
        elif "." not in name:
            ext = basename_from_url(direct).rsplit(".", 1)[-1] if "." in basename_from_url(direct) else "mp4"
            name = f"{name}.{ext}"
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
        seen: set[str] = set()
        out: list[Resource] = []
        for a in dom.css('a[href*="/v/"], a[href*="/embed/"]'):
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
