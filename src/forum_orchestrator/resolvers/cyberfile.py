"""Cyberfile.su / .me — file host with AJAX-driven file details.

Single-file URL ``https://cyberfile.su/<id>``:
  GET page -> extract ``data-id`` (or ``u`` query param embedded in JS).
  POST ``/account/ajax/file_details`` with ``u=<id>``  ->  HTML snippet that
    contains a ``var openUrl = "<direct>"`` line.

Folder URL ``https://cyberfile.su/folder/<id>``:
  POST ``/account/ajax/load_files`` with ``pageType=folder&nodeId=<id>&pageStart=1``
  -> JSON with ``data.html`` (anchor list). Iterate every ``<a href>`` and
     recurse into ``_single``.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from ..errors import DeadLink
from ..models import Resource
from .base import Resolver, ResolveContext, basename_from_url, guess_kind, register

_HOST_RE = r"cyberfile\.(?:su|me)"


@register
class Cyberfile(Resolver):
    name = "cyberfile"
    patterns = [rf"{_HOST_RE}/(?!folder/)[A-Za-z0-9]+"]
    album_patterns = [rf"{_HOST_RE}/folder/"]

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        if "/folder/" in url:
            return await self._folder(url, ctx)
        return await self._single(url, ctx)

    async def _single(self, url: str, ctx: ResolveContext) -> list[Resource]:
        html = await ctx.http.get_text(url, referer=url)

        file_id = _extract_file_id(html)
        if not file_id:
            m = re.search(rf"{_HOST_RE}/([A-Za-z0-9]+)/?$", url)
            if m:
                file_id = m.group(1)
        if not file_id:
            return []

        try:
            snippet = await ctx.http.post_json(
                f"https://cyberfile.su/account/ajax/file_details",
                data={"u": file_id},
                referer=url,
                headers={"X-Requested-With": "XMLHttpRequest",
                         "Content-Type": "application/x-www-form-urlencoded"},
            )
        except Exception:
            return []

        # Response is either JSON ({"html": "..."}) or a raw HTML string.
        body = ""
        if isinstance(snippet, dict):
            body = snippet.get("html") or snippet.get("data") or ""
        elif isinstance(snippet, str):
            body = snippet
        if not body:
            return []

        m = re.search(r"openUrl\s*=\s*['\"](https?://[^'\"]+)['\"]", body)
        if not m:
            m = re.search(r"window\.open\(['\"](https?://[^'\"]+)['\"]", body)
        if not m:
            return []
        direct = m.group(1)

        # Filename: prefer page <title>, fall back to URL basename.
        name = basename_from_url(direct)
        title_node = HTMLParser(html).css_first("title")
        if title_node is not None:
            t = re.sub(r"\s*\|\s*Cyberfile.*$", "", title_node.text() or "", flags=re.I).strip()
            if t and "." in t:
                name = t

        return [Resource(
            url=direct,
            filename=name,
            kind=guess_kind(name),
            referer=url,
            dedup_key=f"cyberfile:{file_id}",
        )]

    async def _folder(self, url: str, ctx: ResolveContext) -> list[Resource]:
        m = re.search(rf"{_HOST_RE}/folder/([A-Za-z0-9]+)", url)
        if not m:
            return []
        node_id = m.group(1)

        out: list[Resource] = []
        seen: set[str] = set()
        page_start = 1
        for _ in range(50):
            try:
                resp = await ctx.http.post_json(
                    "https://cyberfile.su/account/ajax/load_files",
                    data={
                        "pageType": "folder",
                        "nodeId": node_id,
                        "pageStart": page_start,
                        "perPage": 0,
                        "filterOrderBy": "",
                    },
                    referer=url,
                    headers={"X-Requested-With": "XMLHttpRequest",
                             "Content-Type": "application/x-www-form-urlencoded"},
                )
            except Exception:
                break
            data = resp.get("data") if isinstance(resp, dict) else None
            inner_html = (data or {}).get("html") if isinstance(data, dict) else None
            if not inner_html and isinstance(resp, dict):
                inner_html = resp.get("html")
            if not inner_html:
                break

            dom = HTMLParser(inner_html)
            found = 0
            for a in dom.css('a[href*="cyberfile."]'):
                href = a.attributes.get("href")
                if not href:
                    continue
                full = urljoin(url, href)
                if "/folder/" in full or full in seen:
                    continue
                seen.add(full)
                found += 1
                try:
                    out.extend(await self._single(full, ctx))
                except DeadLink:
                    continue
                except Exception:
                    continue
            if not found:
                break
            page_start += 1
        return out


def _extract_file_id(html: str) -> str | None:
    for pat in (
        r'data-file-id=["\']([A-Za-z0-9]+)',
        r'data-id=["\']([A-Za-z0-9]+)',
        r'fileId\s*[:=]\s*["\']([A-Za-z0-9]+)',
    ):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return None
