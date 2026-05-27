"""Async streaming downloader.

Reads from :class:`HttpClient.stream`, writes to disk with a unique
``.<uuid>.part`` suffix (so two concurrent downloads that resolve to the
same target filename can't trample each other's bytes), hashes as we go
(sha256), then under a per-directory lock picks the final unique name via
:func:`naming.unique_path` and atomically renames into place. Collision
suffixes ``(2)``, ``(3)``... come from :func:`naming.unique_path`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from .errors import DeadLink, TransientError
from .http_client import HttpClient
from .models import Resource
from .naming import unique_path

log = logging.getLogger(__name__)

# Per-target-directory lock so unique_path() + os.replace() are atomic with
# respect to other concurrent downloads landing in the same folder.
_dir_locks: dict[str, asyncio.Lock] = {}


def _lock_for(directory: Path) -> asyncio.Lock:
    key = str(directory.resolve())
    lk = _dir_locks.get(key)
    if lk is None:
        lk = asyncio.Lock()
        _dir_locks[key] = lk
    return lk


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
    # Unique .part filename: two concurrent downloads aimed at the same
    # final name cannot share a part file. The .part name encodes the
    # intended final name only for debug visibility — the rename uses a
    # locked unique_path() lookup, not this stem.
    tmp = target_dir / f".{uuid.uuid4().hex}.{filename}.part"
    h = hashlib.sha256()
    size = 0
    try:
        async with http.stream("GET", resource.url, referer=resource.referer,
                               headers=resource.headers) as r:
            if r.status_code in (404, 410):
                raise DeadLink(f"HTTP {r.status_code}")
            if r.status_code >= 400:
                raise TransientError(f"HTTP {r.status_code}")
            with open(tmp, "wb") as f:
                async for chunk in r.aiter_bytes(chunk_size=1 << 15):
                    if not chunk:
                        continue
                    f.write(chunk)
                    h.update(chunk)
                    size += len(chunk)
    except (DeadLink, TransientError):
        _silent_unlink(tmp)
        raise
    except Exception as e:
        _silent_unlink(tmp)
        raise TransientError(str(e)) from e

    # Pick the final target name under a per-directory lock so two parallel
    # downloads that both resolved to "foo.mov" end up as "foo.mov" and
    # "foo (2).mov" rather than racing on the same path.
    async with _lock_for(target_dir):
        target = unique_path(target_dir, filename)
        os.replace(tmp, target)
    return DownloadResult(path=target, size=size, sha256=h.hexdigest())


def _silent_unlink(p: Path) -> None:
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        log.debug("could not unlink %s", p, exc_info=True)
