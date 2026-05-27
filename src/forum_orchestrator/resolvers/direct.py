"""Direct media URLs (already point at an image/video file).

Also catches forum-internal /attachments/ and /data/video/ paths used by
XenForo.
"""

from __future__ import annotations

from ..models import Resource
from .base import IMAGE_EXTS, Resolver, ResolveContext, basename_from_url, guess_kind, register


@register
class Direct(Resolver):
    name = "direct"
    patterns = [
        r"\.(jpe?g|png|gif|webp|bmp|tiff?|avif)(\?|$)",
        r"\.(mp4|webm|mkv|mov|m4v|avi|flv|wmv|mpe?g|ts)(\?|$)",
        r"/attachments/",
        r"/data/video/",
        r"redd\.it/",
        r"phncdn\.com/",
        r"pbs\.twimg\.com/media/",
        r"media\.tumblr\.com/",
    ]
    album_patterns: list[str] = []

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        # Twitter quirk from the userscript: drop ?format=&name=
        # https://pbs.twimg.com/media/<id>?format=jpg&name=large -> /media/<id>.jpg?name=orig
        import re
        m = re.match(r"(https?://pbs\.twimg\.com/media/[^?]+)\?(?:.*?format=(\w+))?(?:.*?name=(\w+))?",
                     url, re.I)
        if m:
            base, fmt, _name = m.groups()
            ext = f".{fmt}" if fmt else ".jpg"
            url = f"{base}{ext}?name=orig"

        return [Resource(
            url=url,
            filename=basename_from_url(url),
            kind=guess_kind(url),
            dedup_key=url.split("?", 1)[0],
        )]
