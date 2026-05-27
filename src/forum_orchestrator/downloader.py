"""Async streaming downloader.

Reads from :class:`HttpClient.stream`, writes to disk with a ``.part`` suffix,
hashes as we go (sha256), then atomically renames into place. Collision
suffixes ``(2)``, ``(3)``... are applied by :func:`naming.unique_path`.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .errors import DeadLink, TransientError
from .http_client import HttpClient
from .models import Resource
from .naming import unique_path

log = logging.getLogger(__name__)


@dataclass(slots=True)
class DownloadResult:
    path: Path
    size: int
    sha256: str


async def download_resource(
    http: HttpClient,
    resource: Resource,
    target_dir: Path,
    filename: str,
) -> DownloadResult:
    target = unique_path(target_dir, filename)
    part = target.with_suffix(target.suffix + ".part")
    h = hashlib.sha256()
    size = 0
    try:
        async with http.stream("GET", resource.url, referer=resource.referer,
                               headers=resource.headers) as r:
            if r.status_code in (404, 410):
                raise DeadLink(f"HTTP {r.status_code}")
            if r.status_code >= 400:
                raise TransientError(f"HTTP {r.status_code}")
            with open(part, "wb") as f:
                async for chunk in r.aiter_bytes(chunk_size=1 << 15):
                    if not chunk:
                        continue
                    f.write(chunk)
                    h.update(chunk)
                    size += len(chunk)
    except (DeadLink, TransientError):
        _silent_unlink(part)
        raise
    except Exception as e:
        _silent_unlink(part)
        raise TransientError(str(e)) from e

    os.replace(part, target)
    return DownloadResult(path=target, size=size, sha256=h.hexdigest())


def _silent_unlink(p: Path) -> None:
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        log.debug("could not unlink %s", p, exc_info=True)
