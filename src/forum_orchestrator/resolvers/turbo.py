"""turbo.cr — video host.

Modern turbo.cr embed page (``/embed/<vvid>``) is a Plyr player that fetches
the actual signed CDN URL from ``/api/sign?v=<vvid>``, which returns JSON
``{"success": true, "url": "https://cdn.turbo.cr/.../signed?exp=..."}``.
We do the same call. The filename lives in ``<title>`` and the vvid is in
inline JS (``const vvid = "...";``).

Album page (``/a/<slug>``) lists each video as ``a[href*='/v/']`` /
``a[href*='/embed/']``; recurse.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from ..models import Kind, Resource
from .base import Resolver, ResolveContext, basename_from_url, register

_HOST = r"(?:[\w-]+\.)?turbo\.cr"
_VVID_RE = re.compile(r"""(?:const|var|let)\s+vvid\s*=\s*['"]([A-Za-z0-9_\-]+)['"]""")


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
        # Normalize /v/<id> and /d/<id> to /embed/<id> — the embed page is
        # the only one that ships the vvid + sign endpoint reliably.
        embed_url = re.sub(r"/(?:v|d)/", "/embed/", url, count=1)
        html = await ctx.http.get_text(embed_url, referer=embed_url)
        dom = HTMLParser(html)

        # Filename from <title> (also exposed as og:title).
        name = ""
        title_node = dom.css_first("title")
        if title_node is not None:
            name = (title_node.text() or "").strip()
        if not name:
            og = dom.css_first('meta[property="og:title"]')
            if og is not None:
                name = (og.attributes.get("content") or "").strip()

        # vvid lives in inline JS.
        vvid = None
        m = _VVID_RE.search(html)
        if m:
            vvid = m.group(1)
        if vvid is None:
            # Fall back to URL path segment.
            tail = urlparse(embed_url).path.rstrip("/").rsplit("/", 1)[-1]
            if tail:
                vvid = tail

        if not vvid:
            return []

        origin = f"{urlparse(embed_url).scheme}://{urlparse(embed_url).netloc}"
        sign_url = f"{origin}/api/sign?v={vvid}"
        try:
            data = await ctx.http.get_json(sign_url, referer=embed_url)
        except Exception:
            return []
        direct = (data or {}).get("url") if isinstance(data, dict) else None
        if not direct:
            return []
        direct = urljoin(embed_url, direct)

        if not name:
            name = basename_from_url(direct).split("?")[0] or f"{vvid}.mp4"
        elif "." not in name:
            cdn_base = basename_from_url(direct).split("?")[0]
            ext = cdn_base.rsplit(".", 1)[-1] if "." in cdn_base else "mp4"
            name = f"{name}.{ext}"

        # Strip the signed-URL query off the dedup key so the same file
        # resolves to the same dedup id on every run.
        unsigned = re.sub(r"\?.*$", "", direct)
        return [Resource(
            url=direct,
            filename=name,
            kind=Kind.VIDEO,
            referer=embed_url,
            dedup_key=re.sub(r"^https?://[^/]+", "", unsigned) + f"#{vvid}",
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
