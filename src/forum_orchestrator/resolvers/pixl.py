"""pixl.is / pixl.li / pixl.cr — Chevereto-family image host.

Same engine as jpg5.su; resolver logic is delegated to ``_chevereto``.
"""

from __future__ import annotations

from ..models import Resource
from . import _chevereto
from .base import Resolver, ResolveContext, register

_HOST = r"(?:[a-z]\d?\.)?pixl\.(?:is|li|cr|cx|to)"


@register
class Pixl(Resolver):
    name = "pixl"
    patterns = [rf"{_HOST}/"]
    album_patterns = [rf"{_HOST}/(?:a|album)/"]

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        return await _chevereto.resolve_chevereto(url, ctx)
