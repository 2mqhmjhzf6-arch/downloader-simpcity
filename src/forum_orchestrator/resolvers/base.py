"""Resolver protocol + registry.

A *Resolver* takes a single URL (extracted from a forum post) and returns one
or more :class:`Resource` objects representing the direct, downloadable URLs.

URL ↔ resolver matching is done with two ordered regex lists:

* ``patterns``       — single-item URLs (an image, a video page).
* ``album_patterns`` — folder/album URLs that should fan out to many resources.

Patterns are matched against the raw URL substring. We mirror the userscript's
convention so anyone familiar with that file can map ours back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Optional, Protocol, Type

from ..models import Kind, Resource

if TYPE_CHECKING:
    from ..http_client import HttpClient


@dataclass
class ResolveContext:
    http: "HttpClient"
    max_height: Optional[int] = 1080
    exclude_4k: bool = True
    passwords: list[str] = field(default_factory=list)
    # Optional hint about the post that contained this URL — some resolvers use
    # the surrounding HTML to recover a human filename.
    post_html: Optional[str] = None


class Resolver(Protocol):
    name: ClassVar[str]
    patterns: ClassVar[list[str]]
    album_patterns: ClassVar[list[str]]

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]: ...


_REGISTRY: list[Type[Resolver]] = []
_COMPILED: list[tuple[Type[Resolver], list[re.Pattern], list[re.Pattern]]] = []


def register(cls: Type[Resolver]) -> Type[Resolver]:
    _REGISTRY.append(cls)
    _COMPILED.append((
        cls,
        [re.compile(p, re.IGNORECASE) for p in getattr(cls, "patterns", [])],
        [re.compile(p, re.IGNORECASE) for p in getattr(cls, "album_patterns", [])],
    ))
    return cls


def all_resolvers() -> list[Type[Resolver]]:
    return list(_REGISTRY)


def find_resolver(url: str) -> Optional[Type[Resolver]]:
    """Return the first registered resolver whose pattern matches ``url``.

    Resolution order:
      1. Album patterns on host-specific resolvers (so ``/a/<slug>`` URLs aren't
         caught by the single-item rule of the same host).
      2. Single-item patterns on host-specific resolvers.
      3. The ``direct`` catch-all is tried last, because its file-extension
         patterns would otherwise swallow any specific-host URL that happens
         to end in ``.jpg`` etc.
    """
    specific = [(c, s, a) for (c, s, a) in _COMPILED if getattr(c, "name", "") != "direct"]
    for cls, singles, albums in specific:
        if any(p.search(url) for p in albums):
            return cls
    for cls, singles, albums in specific:
        if any(p.search(url) for p in singles):
            return cls
    for cls, singles, albums in _COMPILED:
        if getattr(cls, "name", "") != "direct":
            continue
        if any(p.search(url) for p in singles) or any(p.search(url) for p in albums):
            return cls
    return None


# ---------------------------------------------------------------------------
# helpers shared by multiple resolvers
# ---------------------------------------------------------------------------

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg", ".avif"}
VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi", ".flv", ".wmv", ".mpg", ".mpeg", ".ts"}


def guess_kind(url_or_name: str) -> Kind:
    lower = url_or_name.lower().split("?", 1)[0].split("#", 1)[0]
    for ext in IMAGE_EXTS:
        if lower.endswith(ext):
            return Kind.IMAGE
    for ext in VIDEO_EXTS:
        if lower.endswith(ext):
            return Kind.VIDEO
    return Kind.OTHER


def basename_from_url(url: str) -> str:
    from urllib.parse import urlparse, unquote
    p = urlparse(url)
    name = unquote(p.path.rsplit("/", 1)[-1])
    return name or "file"
