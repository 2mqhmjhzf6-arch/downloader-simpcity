"""GoFile.io folder/file resolver.

GoFile content lives behind a JSON API:

1. ``GET https://api.gofile.io/accounts``           -> anonymous token
2. ``GET https://api.gofile.io/contents/<id>?wt=<wt>&cache=true``
   with ``Authorization: Bearer <token>``           -> folder/file listing

``wt`` (the "website token") is a static string embedded in their main
``alljs.js``. It changes occasionally so we scrape it on demand and cache it
on the resolver instance.

Output: every leaf-file ``link`` is returned as a :class:`Resource`. Sub-
folders are recursed.
"""

from __future__ import annotations

import re
from typing import Optional

from ..errors import DeadLink, ResolverError
from ..models import Resource
from .base import Resolver, ResolveContext, guess_kind, register

_API = "https://api.gofile.io"
_JS = "https://gofile.io/dist/js/global.js"
_FALLBACK_WT = "4fd6sg89d7s6"  # observed default; replaced live if we can scrape one


@register
class GoFile(Resolver):
    name = "gofile"
    patterns: list[str] = []
    album_patterns = [r"gofile\.io/d/"]

    _wt: Optional[str] = None
    _token: Optional[str] = None

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        m = re.search(r"gofile\.io/d/([A-Za-z0-9]+)", url)
        if not m:
            return []
        content_id = m.group(1)

        wt = await self._website_token(ctx)
        token = await self._account_token(ctx)

        return await self._collect(content_id, wt, token, ctx, root_url=url)

    async def _website_token(self, ctx: ResolveContext) -> str:
        if self._wt:
            return self._wt
        try:
            js = await ctx.http.get_text(_JS, referer="https://gofile.io/")
            m = re.search(r'wt\s*[:=]\s*["\']([A-Za-z0-9]+)["\']', js)
            if m:
                self._wt = m.group(1)
                return self._wt
        except Exception:
            pass
        self._wt = _FALLBACK_WT
        return self._wt

    async def _account_token(self, ctx: ResolveContext) -> str:
        if self._token:
            return self._token
        try:
            data = await ctx.http.post_json(
                f"{_API}/accounts", json={},
                referer="https://gofile.io/",
            )
        except Exception as e:
            raise ResolverError(f"gofile: account token failed: {e}") from e
        token = (data.get("data") or {}).get("token")
        if not token:
            raise ResolverError("gofile: no account token returned")
        self._token = token
        return token

    async def _collect(
        self, content_id: str, wt: str, token: str,
        ctx: ResolveContext, *, root_url: str,
    ) -> list[Resource]:
        api = f"{_API}/contents/{content_id}?wt={wt}&cache=true"
        try:
            data = await ctx.http.get_json(
                api,
                referer="https://gofile.io/",
                headers={"Authorization": f"Bearer {token}"},
            )
        except Exception as e:
            raise ResolverError(f"gofile: list failed for {content_id}: {e}") from e

        if data.get("status") != "ok":
            raise DeadLink(f"gofile: {data.get('status', 'unknown')}")
        node = data.get("data") or {}
        if node.get("passwordStatus") == "passwordRequired":
            # Password-protected folders aren't supported here yet.
            raise DeadLink("gofile: password-protected folder")
        children = node.get("children") or {}
        out: list[Resource] = []
        for child in children.values():
            ctype = child.get("type")
            if ctype == "folder":
                out.extend(await self._collect(
                    child["id"], wt, token, ctx, root_url=root_url,
                ))
                continue
            link = child.get("link") or child.get("directLink")
            if not link:
                continue
            name = child.get("name") or link.rsplit("/", 1)[-1]
            out.append(Resource(
                url=link,
                filename=name,
                kind=guess_kind(name),
                referer="https://gofile.io/",
                headers={"Cookie": f"accountToken={token}"},
                dedup_key=f"gofile:{child.get('id') or link}",
            ))
        return out
