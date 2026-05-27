from __future__ import annotations

import json
import re
from typing import Any
from unittest.mock import AsyncMock

import pytest

from forum_orchestrator.models import Kind
from forum_orchestrator.resolvers.base import (
    ResolveContext,
    all_resolvers,
    find_resolver,
)
from forum_orchestrator.resolvers.bunkr import Bunkr
from forum_orchestrator.resolvers.cyberdrop import Cyberdrop
from forum_orchestrator.resolvers.direct import Direct
from forum_orchestrator.resolvers.imgbox import Imgbox
from forum_orchestrator.resolvers.jpgsu import JpgSu
from forum_orchestrator.resolvers.pixeldrain import Pixeldrain
from forum_orchestrator.resolvers.pixhost import Pixhost
from forum_orchestrator.resolvers.redgifs import RedGifs

from tests.conftest import load_fixture


def _ctx(text_map: dict[str, str] | None = None, json_map: dict[str, Any] | None = None):
    http = AsyncMock()
    http.get_text = AsyncMock(side_effect=lambda u, **kw: (text_map or {}).get(u, ""))
    http.get_json = AsyncMock(side_effect=lambda u, **kw: (json_map or {}).get(u, {}))
    return ResolveContext(http=http)


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_registry_routes_urls():
    cases = {
        "https://example.com/foo.jpg":                       "direct",
        "https://bunkr.cr/a/abcd":                           "bunkr",
        "https://bunkr.cr/v/abcd":                           "bunkr",
        "https://cyberdrop.me/a/xy12zz":                     "cyberdrop",
        "https://cyberdrop.me/f/xxxxx":                      "cyberdrop",
        "https://pixeldrain.com/u/abc":                      "pixeldrain",
        "https://pixeldrain.com/l/abc":                      "pixeldrain",
        "https://imgbox.com/g/zzyyxx":                       "imgbox",
        "https://redgifs.com/watch/foo":                     "redgifs",
        "https://jpg5.su/img/foo.bar":                       "jpgsu",
        "https://pixhost.to/show/123/foo.jpg":               "pixhost",
        "https://gofile.io/d/abcd":                          "gofile",
        "https://saint2.su/embed/x":                         "saint",
    }
    for url, expected in cases.items():
        cls = find_resolver(url)
        assert cls is not None and cls.name == expected, f"{url} -> {cls}"


def test_album_pattern_wins_over_single():
    # cyberdrop /a/ is an album; /f/ is a single-file. Make sure album wins
    # only on album URLs.
    assert find_resolver("https://cyberdrop.me/a/x").name == "cyberdrop"
    assert find_resolver("https://cyberdrop.me/f/x").name == "cyberdrop"


# ---------------------------------------------------------------------------
# direct
# ---------------------------------------------------------------------------


async def test_direct_passthrough():
    r = await Direct().resolve("https://x/file.png", _ctx())
    assert r[0].kind == Kind.IMAGE
    assert r[0].filename == "file.png"


async def test_direct_twitter_rewrite():
    out = await Direct().resolve(
        "https://pbs.twimg.com/media/Fabc?format=jpg&name=medium", _ctx()
    )
    assert out[0].url.endswith(".jpg?name=orig")


# ---------------------------------------------------------------------------
# bunkr
# ---------------------------------------------------------------------------


async def test_bunkr_single():
    html = load_fixture("bunkr_v.html")
    ctx = _ctx({"https://bunkr.cr/v/cool_video_001.mp4": html})
    out = await Bunkr().resolve("https://bunkr.cr/v/cool_video_001.mp4", ctx)
    assert len(out) == 1
    r = out[0]
    assert r.filename == "cool_video_001.mp4"
    assert r.kind == Kind.VIDEO
    assert r.url.startswith("https://i-pizza1.bunkr.ru/")


async def test_bunkr_album_fans_out():
    album = load_fixture("bunkr_album.html")
    single = load_fixture("bunkr_v.html")
    text_map = {
        "https://bunkr.cr/a/abcd": album,
        "https://bunkr.cr/v/file1.mp4": single,
        "https://bunkr.cr/v/file2.mp4": single,
        "https://bunkr.cr/f/image1.jpg": single,
    }
    ctx = _ctx(text_map)
    out = await Bunkr().resolve("https://bunkr.cr/a/abcd", ctx)
    assert len(out) == 3            # 2 v/ + 1 f/, non-bunkr ignored


# ---------------------------------------------------------------------------
# cyberdrop
# ---------------------------------------------------------------------------


async def test_cyberdrop_album_api():
    data = json.loads(load_fixture("cyberdrop_album_api.json"))
    ctx = _ctx(json_map={
        "https://api.cyberdrop.me/api/album?albumId=xy12zz": data,
    })
    out = await Cyberdrop().resolve("https://cyberdrop.me/a/xy12zz", ctx)
    assert {r.filename for r in out} == {"IMG_001.jpg", "clip.mp4"}
    assert {r.kind for r in out} == {Kind.IMAGE, Kind.VIDEO}


# ---------------------------------------------------------------------------
# pixeldrain
# ---------------------------------------------------------------------------


async def test_pixeldrain_list_api():
    data = json.loads(load_fixture("pixeldrain_list.json"))
    ctx = _ctx(json_map={
        "https://pixeldrain.com/api/list/LIST123": data,
    })
    out = await Pixeldrain().resolve("https://pixeldrain.com/l/LIST123", ctx)
    assert [r.filename for r in out] == ["first.jpg", "second.mp4"]
    assert out[0].url.endswith("/file/FILE1?download")
    assert out[0].dedup_key == "pixeldrain:FILE1"


async def test_pixeldrain_single_uses_info():
    ctx = _ctx(json_map={
        "https://pixeldrain.com/api/file/AbCdEf12/info": {"name": "neat.png"},
    })
    out = await Pixeldrain().resolve("https://pixeldrain.com/u/AbCdEf12", ctx)
    assert out[0].filename == "neat.png"
    assert out[0].dedup_key == "pixeldrain:AbCdEf12"


# ---------------------------------------------------------------------------
# pixhost / imgbox  -- thumb-rewriting branches
# ---------------------------------------------------------------------------


async def test_pixhost_thumb_rewrite():
    out = await Pixhost().resolve("https://t99.pixhost.to/thumbs/12/3_foo.jpg", _ctx())
    assert out[0].url == "https://img99.pixhost.to/images/12/3_foo.jpg"


async def test_imgbox_thumb_rewrite():
    out = await Imgbox().resolve(
        "https://thumbs2.imgbox.com/aa/bb/abc1234_b.jpg", _ctx()
    )
    assert out[0].url == "https://images2.imgbox.com/aa/bb/abc1234_o.jpg"


# ---------------------------------------------------------------------------
# stubs raise UnsupportedHost
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "https://gofile.io/d/abc",
    "https://saint2.su/embed/x",
    "https://cyberfile.su/abc",
    "https://turbo.cr/v/x",
])
async def test_stubs_raise(url):
    from forum_orchestrator.errors import UnsupportedHost
    cls = find_resolver(url)
    assert cls is not None
    with pytest.raises(UnsupportedHost):
        await cls().resolve(url, _ctx())


# ---------------------------------------------------------------------------
# every shipped resolver has at least one pattern
# ---------------------------------------------------------------------------


def test_every_resolver_has_a_pattern():
    for cls in all_resolvers():
        assert cls.patterns or cls.album_patterns, cls.name
