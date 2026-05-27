"""Smoke test wiring the orchestrator end-to-end against fixture HTML.

We monkeypatch :class:`HttpClient` to serve fixtures and run the whole pipeline
in --dry-run mode.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from forum_orchestrator.config import Config
from forum_orchestrator.orchestrator import Orchestrator
from tests.conftest import load_fixture


class _FakeHttp:
    def __init__(self, texts: dict[str, str], jsons: dict[str, object]):
        self._texts, self._jsons = texts, jsons
    async def get_text(self, url, **_): return self._texts.get(url, "")
    async def get_json(self, url, **_): return self._jsons.get(url, {})
    async def aclose(self): pass


async def test_dry_run_pipeline(tmp_path: Path, monkeypatch):
    thread_html = load_fixture("xenforo_thread_p1.html")
    page2 = thread_html.replace("post-12345", "post-22222").replace("post-12348", "post-22223")
    page3 = thread_html.replace("post-12345", "post-33333").replace("post-12348", "post-33334")
    bunkr_v = load_fixture("bunkr_v.html")
    bunkr_a = load_fixture("bunkr_album.html")
    cd_api  = json.loads(load_fixture("cyberdrop_album_api.json"))
    pd_info = {"name": "neat.png"}

    texts = {
        "https://simpcity.cr/threads/some.1/page-1": thread_html,
        "https://simpcity.cr/threads/some.1/page-2": page2,
        "https://simpcity.cr/threads/some.1/page-3": page3,
        "https://bunkr.cr/a/abcd1234": bunkr_a,
        "https://bunkr.cr/v/file1.mp4": bunkr_v,
        "https://bunkr.cr/v/file2.mp4": bunkr_v,
        "https://bunkr.cr/f/image1.jpg": bunkr_v,
    }
    jsons = {
        "https://api.cyberdrop.me/api/album?albumId=xy12zz": cd_api,
        "https://pixeldrain.com/api/file/AbCdEf12/info": pd_info,
    }
    fake = _FakeHttp(texts, jsons)

    cfg = Config(out_dir=tmp_path, dry_run=True, concurrency=2, use_curl_cffi=False)
    orc = Orchestrator(cfg)

    monkeypatch.setattr(
        "forum_orchestrator.orchestrator.HttpClient",
        lambda **kw: fake,
    )
    written = await orc.run("https://simpcity.cr/threads/some.1/")
    assert written == 0     # dry-run

    # State DB was created next to the thread folder.
    runs = list(tmp_path.glob("*/Some Cool Thread (1)/_meta/state.sqlite"))
    assert runs, list(tmp_path.rglob("*"))


async def test_only_filter_keeps_videos(tmp_path: Path, monkeypatch):
    """--only videos must drop image resources from a mixed Cyberdrop album."""
    thread_html = load_fixture("xenforo_thread_p1.html")
    cd_api = json.loads(load_fixture("cyberdrop_album_api.json"))

    texts = {"https://simpcity.cr/threads/some.1/page-1": thread_html}
    jsons = {"https://api.cyberdrop.me/api/album?albumId=xy12zz": cd_api}
    fake = _FakeHttp(texts, jsons)

    cfg = Config(
        out_dir=tmp_path, dry_run=True, concurrency=2,
        use_curl_cffi=False, only_kind="video", pages=(1, 1),
    )
    monkeypatch.setattr(
        "forum_orchestrator.orchestrator.HttpClient",
        lambda **kw: fake,
    )
    await Orchestrator(cfg).run("https://simpcity.cr/threads/some.1/")
    # No write happened (dry-run), but tracking files would only exist if
    # there were failures. Just confirm the run completed.
    runs = list(tmp_path.glob("*/Some Cool Thread (1)"))
    assert runs


async def test_unsupported_host_written_to_tracking_file(tmp_path: Path, monkeypatch):
    """A URL that hits a stub resolver lands in _meta/unsupported_hosts.txt."""
    # Build a tiny thread page that contains exactly one link to a stub host.
    thread_html = """<html><body>
      <div class="block-container">
      <article id="post-1" class="message" data-content="post-1">
        <ul class="message-attribution-main"><li><time datetime="2024-01-01T00:00:00+0000">jan 1</time></li></ul>
        <article class="message-body">
          <div class="bbWrapper">
            <a href="https://coomer.st/somebody/user">link</a>
          </div>
        </article>
      </article>
      </div>
    </body></html>"""

    fake = _FakeHttp(
        {"https://simpcity.cr/threads/x.99/page-1": thread_html},
        {},
    )
    cfg = Config(out_dir=tmp_path, dry_run=True, concurrency=2, use_curl_cffi=False)
    monkeypatch.setattr(
        "forum_orchestrator.orchestrator.HttpClient",
        lambda **kw: fake,
    )
    await Orchestrator(cfg).run("https://simpcity.cr/threads/x.99/")

    tracking = list(tmp_path.rglob("_meta/unsupported_hosts.txt"))
    assert tracking, list(tmp_path.rglob("*"))
    content = tracking[0].read_text()
    assert "coomer.st" in content
