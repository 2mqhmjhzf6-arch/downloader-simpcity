from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


class Kind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    OTHER = "other"


@dataclass(slots=True)
class Post:
    post_id: str
    post_number: int
    posted_at: Optional[datetime]
    raw_html: str
    page_url: str

    @property
    def post_date(self) -> Optional[date]:
        return self.posted_at.date() if self.posted_at else None


@dataclass(slots=True)
class Thread:
    url: str
    title: str
    thread_id: str
    pages: int = 1


@dataclass(slots=True)
class Resource:
    """A direct, downloadable URL produced by a Resolver."""

    url: str
    filename: Optional[str] = None
    kind: Kind = Kind.OTHER
    referer: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)
    width: Optional[int] = None
    height: Optional[int] = None
    # Resolver-supplied stable id used for dedup when URLs differ but file is same.
    dedup_key: Optional[str] = None


@dataclass(slots=True)
class PostLink:
    """An unresolved URL extracted from a post body."""

    url: str
    post: Post
    resolver_name: Optional[str] = None  # filled in by registry match
