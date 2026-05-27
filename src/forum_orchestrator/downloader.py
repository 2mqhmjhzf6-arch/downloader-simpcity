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
from typing import Optional

from .errors import DeadLink, TransientError
from .http_client import HttpClient
from .models import Kind, Resource
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
    decryptor = _make_decryptor(resource)
    try:
        async with http.stream("GET", resource.url, referer=resource.referer,
                               headers=resource.headers) as r:
            if r.status_code in (404, 410):
                raise DeadLink(f"HTTP {r.status_code}")
            if r.status_code >= 400:
                raise TransientError(f"HTTP {r.status_code}")
            # HTML-as-media guard. When a CDN returns an interstitial / login
            # page in place of the expected file, the response is
            # text/html(+meta), not the binary type we asked for. Writing
            # those bytes corrupts the .mp4 / .jpg on disk. Bail before we
            # touch the file.
            if _looks_like_html(r) and resource.kind in (Kind.VIDEO, Kind.IMAGE):
                raise TransientError(
                    f"server returned HTML for {resource.kind.value} URL"
                )
            with open(tmp, "wb") as f:
                async for chunk in r.aiter_bytes(chunk_size=1 << 15):
                    if not chunk:
                        continue
                    if decryptor is not None:
                        chunk = decryptor.decrypt(chunk)
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
        await _replace_with_retry(tmp, target)
    return DownloadResult(path=target, size=size, sha256=h.hexdigest())


def _looks_like_html(response) -> bool:
    headers = getattr(response, "headers", None) or {}
    try:
        ct = headers.get("content-type") or headers.get("Content-Type") or ""
    except AttributeError:
        # Some adapters expose headers as a list of tuples or a Mapping with
        # only __getitem__; fall through.
        try:
            ct = headers["content-type"]
        except Exception:
            ct = ""
    return str(ct).split(";", 1)[0].strip().lower() in {"text/html", "application/xhtml+xml"}


async def _replace_with_retry(src: Path, dst: Path) -> None:
    """``os.replace`` with a tiny backoff for Windows AV/indexer races.

    On Windows a freshly-closed file can briefly be held by an antivirus
    scanner or the search indexer, causing ``PermissionError [WinError 32]``.
    Retry a few times rather than losing the download.
    """
    delays = (0.0, 0.2, 0.5, 1.0)
    last_exc: Optional[Exception] = None
    for d in delays:
        if d:
            await asyncio.sleep(d)
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            last_exc = e
            continue
    assert last_exc is not None
    raise last_exc


def _make_decryptor(resource: Resource):
    """Build an AES-CTR decryptor when the resource carries a stream key.

    Only Mega.nz uses this today. The key/IV format follows Mega's
    convention: 16-byte key, 8-byte nonce in the high half of the IV with the
    low half acting as the block counter.
    """
    key = resource.stream_decrypt_key
    iv = resource.stream_decrypt_iv
    if not key or not iv:
        return None
    try:
        from Crypto.Cipher import AES
        from Crypto.Util import Counter
    except ImportError as e:  # pragma: no cover - optional dep
        raise TransientError(
            "pycryptodome required for encrypted streams (pip install pycryptodome)"
        ) from e
    nonce = iv[:8]
    counter = Counter.new(64, prefix=nonce, initial_value=0)
    return AES.new(key, AES.MODE_CTR, counter=counter)


def _silent_unlink(p: Path) -> None:
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        log.debug("could not unlink %s", p, exc_info=True)
