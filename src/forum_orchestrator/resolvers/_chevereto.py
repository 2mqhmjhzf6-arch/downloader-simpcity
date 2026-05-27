"""Chevereto image-host family helper.

jpg.su / jpg.fish / jpg.church / cuckcapital.cr / pixl.is / pixl.li / etc. all
run the open-source Chevereto stack. The page structure is identical aside
from the domain, so we centralise the resolver logic here and let
``jpgsu.py`` and ``pixl.py`` plug their host regexes in.

Album pages support an optional password unlock that posts the password to the
same URL (``submit=submit&password=<value>``) and stores a session cookie. We
try each password from :class:`ResolveContext.passwords` in turn.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from ..models import Resource
from .base import ResolveContext, basename_from_url, guess_kind


def _strip_thumb(u: str) -> str:
    """``foo.md.jpg`` / ``foo.th.jpg`` -> ``foo.jpg``. Preserves query string."""
    base, sep, query = u.partition("?")
    stripped = re.sub(r"\.(md|th)\.(jpe?g|png|gif|webp)$", r".\2", base, flags=re.I)
    return stripped + (sep + query if sep else "")


async def resolve_image_page(url: str, ctx: ResolveContext) -> list[Resource]:
    html = await ctx.http.get_text(url, referer=url)
    dom = HTMLParser(html)
    direct = None
    for sel in ('a[data-action="download-image"][href]',
                'meta[property="og:image"]',
                'link[rel="image_src"]'):
        n = dom.css_first(sel)
        if not n:
            continue
        direct = n.attributes.get("href") or n.attributes.get("content")
        if direct:
            break
    if not direct:
        return []
    direct = urljoin(url, _strip_thumb(direct))
    return [Resource(
        url=direct,
        filename=basename_from_url(direct),
        kind=guess_kind(direct),
        referer=url,
        dedup_key=direct,
    )]


async def _unlock_if_needed(url: str, html: str, ctx: ResolveContext) -> str:
    """If the album page is password-protected, try each configured password."""
    if 'name="password"' not in html and "this album is private" not in html.lower():
        return html
    for pw in ctx.passwords:
        try:
            await ctx.http.request(
                "POST", url,
                data={"submit": "submit", "password": pw},
                referer=url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            html = await ctx.http.get_text(url, referer=url)
            if 'name="password"' not in html:
                return html
        except Exception:
            continue
    return html


async def resolve_album(url: str, ctx: ResolveContext) -> list[Resource]:
    out: list[Resource] = []
    seen: set[str] = set()
    page = 1
    while True:
        if page == 1:
            page_url = url
        else:
            page_url = url + (("&" if "?" in url else "?") + f"page={page}")
        try:
            html = await ctx.http.get_text(page_url, referer=url)
        except Exception:
            break
        if page == 1:
            html = await _unlock_if_needed(url, html, ctx)
        dom = HTMLParser(html)
        found = 0
        for a in dom.css('a.image-container[href], a.--media[href]'):
            href = a.attributes.get("href")
            if href and href not in seen:
                seen.add(href)
                found += 1
                out.extend(await resolve_image_page(urljoin(url, href), ctx))
        if found == 0:
            break
        page += 1
        if page > 50:                       # safety
            break
    return out


async def resolve_chevereto(url: str, ctx: ResolveContext) -> list[Resource]:
    """Top-level dispatch for any Chevereto URL."""
    if re.search(r"/(a|album)/", url):
        return await resolve_album(url, ctx)
    if "/img/" in url or "/image/" in url:
        return await resolve_image_page(url, ctx)
    direct = _strip_thumb(url)
    return [Resource(
        url=direct,
        filename=basename_from_url(direct),
        kind=guess_kind(direct),
        referer=url,
        dedup_key=direct,
    )]
