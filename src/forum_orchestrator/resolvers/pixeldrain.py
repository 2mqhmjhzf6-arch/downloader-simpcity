"""Pixeldrain: single files (/u/) and lists (/l/).

Public API:
    GET https://pixeldrain.com/api/file/<id>/info
    GET https://pixeldrain.com/api/list/<id>           -> {files: [...]}
Direct download URL: https://pixeldrain.com/api/file/<id>?download
"""

from __future__ import annotations

import re
from typing import Optional

from ..models import Resource
from .base import Resolver, ResolveContext, guess_kind, register

_ID = re.compile(r"pixel(?:drain|dra)\.(?:com|net|in)/(?:l|u)/([A-Za-z0-9]+)", re.IGNORECASE)


@register
class Pixeldrain(Resolver):
    name = "pixeldrain"
    patterns = [r"pixel(?:drain|dra)\.(?:com|net|in)/u/"]
    album_patterns = [r"pixel(?:drain|dra)\.(?:com|net|in)/l/"]

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        m = _ID.search(url)
        if not m:
            return []
        id_ = m.group(1)
        if "/l/" in url:
            return await self._list(id_, url, ctx)
        return [await self._file(id_, url, ctx)]

    async def _list(self, list_id: str, url: str, ctx: ResolveContext) -> list[Resource]:
        data = await ctx.http.get_json(
            f"https://pixeldrain.com/api/list/{list_id}", referer=url
        )
        out: list[Resource] = []
        for f in data.get("files", []):
            fid = f.get("id")
            name = f.get("name") or fid
            if not fid:
                continue
            out.append(_resource(fid, name, url))
        return out

    async def _file(self, fid: str, url: str, ctx: ResolveContext) -> Resource:
        name: Optional[str] = None
        try:
            info = await ctx.http.get_json(
                f"https://pixeldrain.com/api/file/{fid}/info", referer=url
            )
            name = info.get("name")
        except Exception:
            pass
        return _resource(fid, name or fid, url)


def _resource(fid: str, name: str, referer: str) -> Resource:
    direct = f"https://pixeldrain.com/api/file/{fid}?download"
    return Resource(
        url=direct,
        filename=name,
        kind=guess_kind(name),
        referer=referer,
        dedup_key=f"pixeldrain:{fid}",
    )
