"""Bunkr (.cr / .ax / .black / ...).

Bunkr serves albums at ``/a/<slug>`` and single files at ``/v/<slug>`` (videos)
or ``/i/<slug>``/CDN subdomains (images). The site is permanently behind a
Cloudflare interstitial and the CDN base for any given file rotates between a
handful of subdomains — see the userscript's ``xfpdBunkrFilterBases`` for the
full list. Our approach:

1. For an album URL, fetch the page and pull every file from the Next.js
   ``__NEXT_DATA__`` JSON blob (modern bunkr) or from ``<a href>`` grid links
   (older markup) as a fallback. Recurse into ``_resolve_single`` per file.
2. For a single-file URL, fetch the page and read the file metadata from
   ``__NEXT_DATA__`` (``name`` + ``cdn``/``url``). Fall back to assorted CSS
   selectors if the JSON shape ever changes again.
3. For CDN-direct URLs that bypass the file page entirely, route them back
   through ``bunkr.cr/v/<basename>`` (or ``/f/``) so we get a real file URL +
   correct ``Referer``. The naked CDN response is usually a Cloudflare
   interstitial that we'd otherwise save to disk as ``.mp4``.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from ..models import Kind, Resource
from .base import Resolver, ResolveContext, basename_from_url, guess_kind, register

_BUNKR_TLDS = r"(ac|ax|black|cat|ci|cr|fi|is|media|nu|pk|ph|ps|red|ru|se|si|site|sk|ws|su|org)"

_BUNKR_HOME = "https://bunkr.cr"

_GRID_LINK = re.compile(
    rf"href=[\"'](https?://(?:[\w-]+\.)?bunkr+\.{_BUNKR_TLDS}/(?:v|f|i)/[^\"'>]+)",
    re.IGNORECASE,
)

_VIDEO_EXTS = re.compile(r"\.(mp4|m4v|mov|webm|mkv|avi|ts)(?:\?|$)", re.IGNORECASE)


@register
class Bunkr(Resolver):
    name = "bunkr"
    patterns = [
        rf"(?:[\w-]+\.)?bunkr+\.{_BUNKR_TLDS}/(?:v|f|i)/",
        rf"(?:i|cdn|i-pizza|big-taco-1img)(?:\d+)?\.bunkr+\.{_BUNKR_TLDS}/",
        r"(?:bunkr-cache|b-cdn|gigachad-cdn)\.[a-z]+/",
    ]
    album_patterns = [rf"bunkr+\.{_BUNKR_TLDS}/a/"]

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        if "/a/" in url:
            return await self._resolve_album(url, ctx)
        # CDN-direct URL: route back through bunkr.cr's file page so we get
        # a real CDN link + correct Referer (the naked CDN URL returns an
        # interstitial unless we hit it via the file page).
        if re.search(r"(?:cdn\d*|i-pizza\d*|big-taco[\w-]*|b-cdn|gigachad-cdn|bunkr-cache)\.", url, re.I):
            base = basename_from_url(url)
            route = "v" if _VIDEO_EXTS.search(base) else "f"
            for page_route in (route, "v" if route == "f" else "f"):
                page_url = f"{_BUNKR_HOME}/{page_route}/{base}"
                try:
                    items = await self._resolve_single(page_url, ctx)
                except Exception:
                    items = []
                if items:
                    return items
            # Naked CDN URL with no working file page: hitting it directly
            # returns the Cloudflare interstitial as text/html, which would
            # land on disk as a corrupt .mp4. Fail loudly instead so the URL
            # ends up in failed_downloads.txt for manual retry.
            return []
        return await self._resolve_single(url, ctx)

    async def _resolve_album(self, url: str, ctx: ResolveContext) -> list[Resource]:
        html = await ctx.http.get_text(url, referer=url)
        slugs = _album_slugs_from_next_data(html)
        if not slugs:
            # Fallback: scrape anchors directly (older / non-Next.js bunkr).
            for m in _GRID_LINK.finditer(html):
                slugs.append(m.group(1))
        seen: set[str] = set()
        items: list[Resource] = []
        for link in slugs:
            if not link.startswith("http"):
                # Slug-only -> assume video first, fall back to file page.
                link = f"{_BUNKR_HOME}/v/{link}"
            if link in seen:
                continue
            seen.add(link)
            try:
                items.extend(await self._resolve_single(link, ctx))
            except Exception:
                continue
        return items

    async def _resolve_single(self, url: str, ctx: ResolveContext) -> list[Resource]:
        html = await ctx.http.get_text(url, referer=url)
        dom = HTMLParser(html)

        title = ""
        if (t := dom.css_first("title")) is not None:
            title = (t.text() or "").strip()
        title = re.sub(r"\s*\|\s*Bunkr\s*$", "", title, flags=re.I).strip()

        direct: str | None = None
        filename: str | None = None

        data = _next_data(dom)
        if data is not None:
            file_obj = _find_file_object(data)
            if file_obj is not None:
                filename = (file_obj.get("name") or file_obj.get("filename") or "").strip() or None
                cdn = (file_obj.get("cdn") or "").strip()
                raw_url = (file_obj.get("url") or file_obj.get("src") or "").strip()
                if raw_url.startswith("http"):
                    direct = raw_url
                elif cdn and filename:
                    direct = cdn.rstrip("/") + "/" + filename.lstrip("/")
                elif cdn:
                    direct = cdn

        if direct is None:
            for sel in (
                'a.ic-download-01-svg, a[class*="ic-download"]',
                'a.btn-main[href*="get.bunkr"]',
                'source[src]',
                'video[src]',
                'img.max-h-full[src]',
                'link[rel="preload"][as="image"][href]',
            ):
                node = dom.css_first(sel)
                if node is None:
                    continue
                cand = node.attributes.get("href") or node.attributes.get("src")
                if cand:
                    direct = urljoin(url, cand)
                    break

        if direct is None:
            blob = dom.css_first('script#__NEXT_DATA__')
            if blob and blob.text():
                m = re.search(r'"(?:cdn|url|src)":"(https?://[^"]+)"', blob.text())
                if m:
                    direct = m.group(1).encode().decode("unicode_escape")

        if direct is None:
            return []

        name = filename or title or basename_from_url(direct)
        kind = guess_kind(direct)
        if kind == Kind.OTHER:
            kind = guess_kind(name)

        return [Resource(
            url=direct,
            filename=name,
            kind=kind,
            referer=f"{_BUNKR_HOME}/",
            dedup_key=re.sub(r"^https?://[^/]+", "", direct),
        )]


def _next_data(dom: HTMLParser) -> dict[str, Any] | None:
    node = dom.css_first('script#__NEXT_DATA__')
    if node is None or not node.text():
        return None
    try:
        return json.loads(node.text())
    except json.JSONDecodeError:
        return None


def _find_file_object(data: Any) -> dict[str, Any] | None:
    """Walk the JSON looking for an object with a ``name`` and a CDN-ish key."""
    stack = [data]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            keys = cur.keys()
            if "name" in keys and any(k in keys for k in ("cdn", "url", "src", "mediafiles")):
                return cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def _album_slugs_from_next_data(html: str) -> list[str]:
    dom = HTMLParser(html)
    data = _next_data(dom)
    if data is None:
        return []
    slugs: list[str] = []
    stack = [data]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            slug = cur.get("slug") or cur.get("id")
            name = cur.get("name") or ""
            # Only treat as a file if there's a file-shaped attribute alongside.
            looks_like_file = any(k in cur for k in ("size", "type", "cdn", "url", "src"))
            if looks_like_file and isinstance(slug, str) and slug and isinstance(name, str) and name:
                route = "v" if _VIDEO_EXTS.search(name) else "f"
                slugs.append(f"{_BUNKR_HOME}/{route}/{slug}")
            else:
                stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return slugs


def _origin(url: str) -> str:
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}"
