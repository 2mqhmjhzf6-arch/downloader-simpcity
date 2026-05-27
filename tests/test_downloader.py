"""Regression tests for the streaming downloader.

The key invariant we care about is: when two concurrent downloads land on
the same target filename, neither file is corrupted by the other's bytes,
and both end up on disk (with a (2) suffix on the second).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from forum_orchestrator.downloader import download_resource
from forum_orchestrator.models import Kind, Resource


class _FakeStreamResponse:
    """Mimics the small surface of httpx.Response that the downloader uses."""

    def __init__(self, body: bytes, status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code

    async def aiter_bytes(self, chunk_size: int = 1 << 15):
        # Yield in small chunks so two concurrent downloads have many
        # interleaving opportunities.
        for i in range(0, len(self._body), 1024):
            yield self._body[i : i + 1024]
            await asyncio.sleep(0)


class _FakeHttp:
    """Returns deterministic bytes keyed by URL; no real network."""

    def __init__(self, bodies: dict[str, bytes]) -> None:
        self._bodies = bodies

    @asynccontextmanager
    async def stream(self, method: str, url: str, **_kw):
        yield _FakeStreamResponse(self._bodies[url])


@pytest.mark.anyio
async def test_concurrent_downloads_same_filename_do_not_collide(tmp_path: Path):
    body_a = b"AAAA" * 8192        # 32 KB of 'A'
    body_b = b"BBBB" * 8192        # 32 KB of 'B'
    http = _FakeHttp({
        "https://host/a": body_a,
        "https://host/b": body_b,
    })
    res_a = Resource(url="https://host/a", filename="clip.mov", kind=Kind.VIDEO)
    res_b = Resource(url="https://host/b", filename="clip.mov", kind=Kind.VIDEO)

    results = await asyncio.gather(
        download_resource(http, res_a, tmp_path, "clip.mov"),
        download_resource(http, res_b, tmp_path, "clip.mov"),
    )

    # Both downloads land on disk under distinct names...
    paths = sorted(r.path.name for r in results)
    assert paths == ["clip (2).mov", "clip.mov"]

    # ...and each file holds exactly its own bytes (no interleaving).
    blobs = {p.read_bytes() for p in tmp_path.iterdir() if p.suffix != ".part"}
    assert blobs == {body_a, body_b}

    # No leftover .part files.
    assert not list(tmp_path.glob("*.part"))


@pytest.fixture
def anyio_backend():
    return "asyncio"
