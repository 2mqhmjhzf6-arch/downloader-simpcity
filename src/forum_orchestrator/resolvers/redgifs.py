"""RedGifs: short-form video.

Public API requires an anonymous temp token:
    GET  https://api.redgifs.com/v2/auth/temporary    -> {token: ...}
    GET  https://api.redgifs.com/v2/gifs/<id>         -> {gif: {urls: {hd, sd, ...}}}
"""

from __future__ import annotations

import re

from ..models import Kind, Resource
from .base import Resolver, ResolveContext, register

_ID = re.compile(r"redgifs\.com/(?:ifr|watch|i)/([A-Za-z0-9]+)", re.IGNORECASE)


@register
class RedGifs(Resolver):
    name = "redgifs"
    patterns = [r"redgifs\.com/(?:ifr|watch|i)/"]
    album_patterns = [r"redgifs\.com/users/"]

    _token: str | None = None

    async def _ensure_token(self, ctx: ResolveContext) -> str:
        if self._token:
            return self._token
        data = await ctx.http.get_json("https://api.redgifs.com/v2/auth/temporary")
        self._token = data["token"]
        return self._token

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        if "/users/" in url:
            return await self._user(url, ctx)
        m = _ID.search(url)
        if not m:
            return []
        gif_id = m.group(1).lower()
        return [await self._one(gif_id, url, ctx)]

    async def _one(self, gif_id: str, url: str, ctx: ResolveContext) -> Resource:
        tok = await self._ensure_token(ctx)
        data = await ctx.http.get_json(
            f"https://api.redgifs.com/v2/gifs/{gif_id}",
            referer=url,
            headers={"Authorization": f"Bearer {tok}"},
        )
        urls = (data.get("gif") or {}).get("urls") or {}
        # Userscript honors quality cap; we pick the highest <= max_height.
        best = urls.get("hd") or urls.get("sd") or urls.get("file")
        if ctx.max_height and ctx.max_height < 720 and urls.get("sd"):
            best = urls["sd"]
        return Resource(
            url=best,
            filename=f"{gif_id}.mp4",
            kind=Kind.VIDEO,
            referer="https://www.redgifs.com/",
            dedup_key=f"redgifs:{gif_id}",
        )

    async def _user(self, url: str, ctx: ResolveContext) -> list[Resource]:
        tok = await self._ensure_token(ctx)
        m = re.search(r"/users/([^/?#]+)", url)
        if not m:
            return []
        user = m.group(1)
        out: list[Resource] = []
        page = 1
        while page <= 50:
            data = await ctx.http.get_json(
                f"https://api.redgifs.com/v2/users/{user}/search?page={page}&order=new",
                referer=url, headers={"Authorization": f"Bearer {tok}"},
            )
            gifs = data.get("gifs") or []
            if not gifs:
                break
            for g in gifs:
                gid = g["id"].lower()
                urls = g.get("urls") or {}
                best = urls.get("hd") or urls.get("sd")
                if not best:
                    continue
                out.append(Resource(
                    url=best, filename=f"{gid}.mp4", kind=Kind.VIDEO,
                    referer="https://www.redgifs.com/",
                    dedup_key=f"redgifs:{gid}",
                ))
            if page >= int(data.get("pages") or 1):
                break
            page += 1
        return out
