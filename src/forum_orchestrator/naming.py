"""Filename / directory hygiene.

Implements the PRD's `[YYYY-MM-DD] Original_Filename.ext` rule, plus Windows-safe
sanitization (reserved chars, reserved names, trailing dots, length caps).
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Optional

_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_INVALID_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')


def sanitize_segment(name: str, sub: str = "-") -> str:
    """Make a single path segment safe on every OS (Windows is strictest)."""
    s = _INVALID_CHARS.sub(sub, name).strip().rstrip(". ")
    if not s:
        return "_"
    stem, dot, ext = s.rpartition(".")
    base_for_check = (stem or s).upper()
    if base_for_check in _WINDOWS_RESERVED_NAMES:
        s = f"_{s}"
    # Cap segment length (NTFS allows 255; leave room for `[YYYY-MM-DD] ` prefix
    # and collision suffix). Truncate keeping the extension.
    MAX = 200
    if len(s) > MAX:
        stem, dot, ext = s.rpartition(".")
        keep = MAX - (len(ext) + 1 if dot else 0)
        s = (stem[:keep] + ("." + ext if dot else "")) if stem else s[:MAX]
    return s


def date_prefix(d: Optional[date]) -> str:
    if d is None:
        return ""
    return f"[{d.isoformat()}] "


def build_filename(original: str, posted: Optional[date]) -> str:
    return f"{date_prefix(posted)}{sanitize_segment(original)}"


def unique_path(directory: Path, filename: str) -> Path:
    """Suffix `(2)`, `(3)`, ... until the path is free."""
    p = directory / filename
    if not p.exists():
        return p
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        stem, ext = filename, ""
    i = 2
    while True:
        cand = directory / (f"{stem} ({i}).{ext}" if ext else f"{stem} ({i})")
        if not cand.exists():
            return cand
        i += 1


def run_dir(out_root: Path, today: date) -> Path:
    return out_root / f"{today.isoformat()}_Run"


def thread_dir(run_root: Path, title: str, thread_id: str) -> Path:
    seg = sanitize_segment(f"{title} ({thread_id})")
    return run_root / seg


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p
