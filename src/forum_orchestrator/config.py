from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional


@dataclass(slots=True)
class Config:
    out_dir: Path
    cookies_file: Optional[Path] = None
    cookies_browser: Optional[str] = None
    xf_user: Optional[str] = None
    xf_session: Optional[str] = None
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    max_height: Optional[int] = 1080            # video cap; 4K is always excluded
    exclude_4k: bool = True
    concurrency: int = 6
    dry_run: bool = False
    pages: Optional[tuple[int, int]] = None     # inclusive range
    posts: Optional[set[str]] = None
    use_curl_cffi: bool = True
    retries: int = 4
    timeout_s: float = 30.0
    passwords: list[str] = field(default_factory=list)
    only_kind: Literal["video", "photo", "both"] = "both"
    quiet: bool = False
