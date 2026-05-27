"""JPG.su / jpg.fish / jpg.church / cuckcapital.cr image host family.

These are Chevereto installations. The resolver logic lives in
:mod:`forum_orchestrator.resolvers._chevereto`; this module only registers the
host patterns.

Pattern:
    https://jpg5.su/img/<slug>.<id>   -> single image page
    https://jpg5.su/a/<slug>          -> album
"""

from __future__ import annotations

from ..models import Resource
from . import _chevereto
from .base import Resolver, ResolveContext, register

_HOST = r"(?:simp\d+\.)?(?:cuckcapital\.cr|jpe?g\d?\.(?:church|fish|fishing|pet|su|cr))"


@register
class JpgSu(Resolver):
    name = "jpgsu"
    # Match any URL on a chevereto host (image, CDN-direct, album).
    # Album dispatch is handled inside _chevereto.resolve_chevereto.
    patterns = [rf"{_HOST}/"]
    album_patterns = [rf"{_HOST}/(?:a|album)/"]

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        return await _chevereto.resolve_chevereto(url, ctx)
