"""Registered-but-not-yet-implemented resolvers.

Every host from the reference userscript that we haven't fully ported yet still
gets a *pattern entry* here so that:

  * the URL is correctly attributed to a host (not mis-routed to ``direct``),
  * the orchestrator records it in ``failed_links.txt`` with a clear reason
    instead of silently dropping it,
  * adding a real implementation is mechanical (delete the stub, drop in a
    proper module under ``resolvers/``).

Each stub raises :class:`UnsupportedHost` when called.
"""

from __future__ import annotations

from ..errors import UnsupportedHost
from ..models import Resource
from .base import Resolver, ResolveContext, register


def _stub(name: str, patterns: list[str], album_patterns: list[str] | None = None):
    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        raise UnsupportedHost(f"{name} resolver not yet implemented")

    cls = type(
        f"{name.title()}Stub",
        (object,),
        {
            "name": name,
            "patterns": patterns,
            "album_patterns": album_patterns or [],
            "resolve": resolve,
        },
    )
    register(cls)
    return cls


Coomer     = _stub("coomer",     [r"coomer\.st/[\w.-]+/user"])
Kemono     = _stub("kemono",     [r"\.kemono\.cr/data/"])
Postimg    = _stub("postimg",    [r"i?postimg\.cc/"])
Pixxxels   = _stub("pixxxels",   [r"pixxxels\.cc/"])
Imagevenue = _stub("imagevenue", [r"imagevenue\.com/"])
Imagebam   = _stub("imagebam",   [r"images\d+\.imagebam\.com/"], [r"imagebam\.com/(view|gallery)/"])
Imgvb      = _stub("imgvb",      [r"imgvb\.com/images/"], [r"imgvb\.com/album"])
Pomf       = _stub("pomf",       [r"pomf2\.lain\.la/"])
Pornhub    = _stub("pornhub",    [r"pornhub\.com/view_video"])
Spankbang  = _stub("spankbang",  [r"spankbang\.com/.*/video"])
XVideos    = _stub("xvideos",    [r"xvideos\.com/video"])
NoodleMag  = _stub("noodlemag",  [r"(?:adult\.)?noodlemagazine\.com/watch/"])
Rule34     = _stub("rule34",     [r"rule34\.xxx/"])
OnlyFans   = _stub("onlyfans",   [r"public\.onlyfans\.com/files"])
GiveXxx    = _stub("givexxx",    [r"give\.xxx/"])
BoxCom     = _stub("box",        [r"m\.box\.com/"])
Yandex     = _stub("yandex",     [r"(?:disk\.)?yandex\.[a-z]+"])
