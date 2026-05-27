"""GoFile.io folder/file resolver.

GoFile content lives behind a JSON API:

1. ``GET https://api.gofile.io/accounts``           -> anonymous token
2. ``GET https://api.gofile.io/contents/<id>?wt=<wt>&cache=true``
   with ``Authorization: Bearer <token>``           -> folder/file listing

``wt`` (the "website token") is a static string embedded in their JS. The
bundle URL changes periodically (``alljs.js`` -> ``global.js`` -> hashed
``_next`` chunks), so we discover it dynamically:

* fetch the user-facing ``/d/<id>`` page,
* scan inline scripts for ``wt: "..."`` or ``appdata.wt = "..."``,
* otherwise fetch every ``<script src>`` referenced on the page and look
  for the same pattern.

Output: every leaf-file ``link`` is returned as a :class:`Resource`. Sub-
folders are recursed.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional
from urllib.parse import urljoin

from ..errors import DeadLink, ResolverError
from ..models import Resource
from .base import Resolver, ResolveContext, guess_kind, register

_API = "https://api.gofile.io"
_HOME = "https://gofile.io/"
_WT_RE = re.compile(r'(?:appdata\s*\.\s*)?wt\s*[:=]\s*["\']([A-Za-z0-9]{8,})["\']')
# Fallback bundle paths to try if the page HTML doesn't reveal one.
_JS_FALLBACKS = (
    "https://gofile.io/dist/js/global.js",
    "https://gofile.io/dist/js/alljs.js",
)
_FALLBACK_WT = "4fd6sg89d7s6"  # last-known-good; works until they rotate


@register
class GoFile(Resolver):
    name = "gofile"
    patterns: list[str] = []
    album_patterns = [r"gofile\.io/d/"]

    def __init__(self) -> None:
        # Per-instance to avoid leaking a stale token across runs that share
        # the registered class.
        self._wt: Optional[str] = None
        self._token: Optional[str] = None

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        m = re.search(r"gofile\.io/d/([A-Za-z0-9]+)", url)
        if not m:
            return []
        content_id = m.group(1)

        wt = await self._website_token(ctx, content_id)
        token = await self._account_token(ctx)

        return await self._collect(content_id, wt, token, ctx, root_url=url)

    async def _website_token(self, ctx: ResolveContext, content_id: str) -> str:
        if self._wt:
            return self._wt
        page_url = f"https://gofile.io/d/{content_id}"
        # 1. Page HTML often inlines `wt` directly.
        page = ""
        try:
            page = await ctx.http.get_text(page_url, referer=_HOME)
            m = _WT_RE.search(page)
            if m:
                self._wt = m.group(1)
                return self._wt
        except Exception:
            pass
        # 2. Each <script src> referenced from the page is a candidate bundle.
        seen: set[str] = set()
        for src in re.findall(r'<script[^>]+src=["\']([^"\']+\.js[^"\']*)["\']', page):
            full = urljoin(page_url, src)
            if full in seen:
                continue
            seen.add(full)
            try:
                js = await ctx.http.get_text(full, referer=page_url)
            except Exception:
                continue
            m = _WT_RE.search(js)
            if m:
                self._wt = m.group(1)
                return self._wt
        # 3. Hard-coded fallback bundle paths.
        for js_url in _JS_FALLBACKS:
            try:
                js = await ctx.http.get_text(js_url, referer=_HOME)
            except Exception:
                continue
            m = _WT_RE.search(js)
            if m:
                self._wt = m.group(1)
                return self._wt
        # 4. Last-known-good static string; will 401 if rotated.
        self._wt = _FALLBACK_WT
        return self._wt

    async def _account_token(self, ctx: ResolveContext) -> str:
        if ctx.gofile_token:
            # Caller supplied a real account token; use it directly.
            self._token = ctx.gofile_token
            return ctx.gofile_token
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
        api_base = f"{_API}/contents/{content_id}?wt={wt}&cache=true"
        try:
            data = await ctx.http.get_json(
                api_base,
                referer="https://gofile.io/",
                headers={"Authorization": f"Bearer {token}"},
            )
        except Exception as e:
            raise ResolverError(f"gofile: list failed for {content_id}: {e}") from e

        status = data.get("status", "unknown")
        if status == "error-notPremium":
            raise DeadLink(
                "gofile: premium account required (set --gofile-token)"
            )
        if status != "ok":
            raise DeadLink(f"gofile: {status}")
        node = data.get("data") or {}
        if node.get("passwordStatus") == "passwordRequired":
            # Try each known password (post-spoiler + CLI) in turn. Gofile
            # expects sha256(pw) hex.
            unlocked = None
            for pw in ctx.passwords:
                pw_hash = hashlib.sha256(pw.encode("utf-8")).hexdigest()
                try:
                    retry = await ctx.http.get_json(
                        f"{api_base}&password={pw_hash}",
                        referer="https://gofile.io/",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                except Exception:
                    continue
                rnode = retry.get("data") or {}
                if (
                    retry.get("status") == "ok"
                    and rnode.get("passwordStatus") != "passwordRequired"
                ):
                    unlocked = rnode
                    break
            if unlocked is None:
                raise DeadLink("gofile: password-protected folder")
            node = unlocked
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
