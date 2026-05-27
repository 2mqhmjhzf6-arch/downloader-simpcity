"""anonfiles.com — tombstone resolver.

The site was permanently shut down in August 2023; no files hosted there can
be recovered. We register a resolver so URLs of this shape get a clear
explanation in ``_meta/failed_downloads.txt`` instead of being mis-routed or
silently dropped.
"""

from __future__ import annotations

from ..errors import DeadLink
from ..models import Resource
from .base import Resolver, ResolveContext, register


@register
class AnonFiles(Resolver):
    name = "anonfiles"
    patterns = [r"anonfiles\.com/"]
    album_patterns: list[str] = []

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        raise DeadLink("anonfiles.com was shut down in August 2023; the file is unrecoverable")
