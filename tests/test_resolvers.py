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


def _ctx(text_map: dict[str, str] | None = None,
         json_map: dict[str, Any] | None = None,
         post_map: dict[str, Any] | None = None):
    http = AsyncMock()
    http.get_text = AsyncMock(side_effect=lambda u, **kw: (text_map or {}).get(u, ""))
    http.get_json = AsyncMock(side_effect=lambda u, **kw: (json_map or {}).get(u, {}))
    http.post_json = AsyncMock(side_effect=lambda u, **kw: (post_map or {}).get(u, {}))
    http.request = AsyncMock(return_value=None)
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
    "https://coomer.st/somebody/user",
    "https://i.kemono.cr/data/abc",
    "https://postimg.cc/abc",
    "https://pornhub.com/view_video?v=xyz",
    "https://rule34.xxx/index?id=1",
])
async def test_stubs_raise(url):
    from forum_orchestrator.errors import UnsupportedHost
    cls = find_resolver(url)
    assert cls is not None, url
    with pytest.raises(UnsupportedHost):
        await cls().resolve(url, _ctx())


# ---------------------------------------------------------------------------
# every shipped resolver has at least one pattern
# ---------------------------------------------------------------------------


def test_every_resolver_has_a_pattern():
    for cls in all_resolvers():
        assert cls.patterns or cls.album_patterns, cls.name


# ---------------------------------------------------------------------------
# new resolvers
# ---------------------------------------------------------------------------


def test_new_resolvers_routed():
    cases = {
        "https://ibb.co/AbCdEf":             "ibb",
        "https://ibb.co/album/aBcDeF":       "ibb",
        "https://pixl.is/img/foo":           "pixl",
        "https://pixl.li/album/bar":         "pixl",
        "https://saint2.su/embed/abc":       "saint",
        "https://saint2.cr/a/xyz":           "saint",
        "https://turbo.cr/v/foo":            "turbo",
        "https://turbo.cr/a/foo":            "turbo",
        "https://cyberfile.su/AbCd123":      "cyberfile",
        "https://cyberfile.me/folder/zzz":   "cyberfile",
        "https://filester.me/d/abc":         "filester",
        "https://filester.gg/f/zzz":         "filester",
        "https://gofile.io/d/AbCd":          "gofile",
        "https://anonfiles.com/abc":         "anonfiles",
    }
    for url, expected in cases.items():
        cls = find_resolver(url)
        assert cls is not None, url
        assert cls.name == expected, f"{url} -> {cls.name}"


async def test_ibb_single():
    from forum_orchestrator.resolvers.ibb import Ibb
    html = load_fixture("ibb_image.html")
    ctx = _ctx({"https://ibb.co/AbCdEf": html})
    out = await Ibb().resolve("https://ibb.co/AbCdEf", ctx)
    assert len(out) == 1
    assert out[0].url == "https://i.ibb.co/abcdef/my-photo.jpg"
    assert out[0].kind == Kind.IMAGE


async def test_ibb_direct_cdn_passthrough():
    from forum_orchestrator.resolvers.ibb import Ibb
    out = await Ibb().resolve("https://i.ibb.co/abc/foo.png", _ctx())
    assert out[0].url == "https://i.ibb.co/abc/foo.png"
    assert out[0].kind == Kind.IMAGE


async def test_ibb_album():
    from forum_orchestrator.resolvers.ibb import Ibb
    album = load_fixture("ibb_album.html")
    image = load_fixture("ibb_image.html")
    ctx = _ctx({
        "https://ibb.co/album/zzz": album,
        "https://ibb.co/img1": image,
        "https://ibb.co/img2": image,
    })
    out = await Ibb().resolve("https://ibb.co/album/zzz", ctx)
    assert len(out) == 2


async def test_pixl_image_via_chevereto():
    from forum_orchestrator.resolvers.pixl import Pixl
    html = load_fixture("pixl_image.html")
    ctx = _ctx({"https://pixl.is/img/file123": html})
    out = await Pixl().resolve("https://pixl.is/img/file123", ctx)
    assert len(out) == 1
    assert out[0].url == "https://pixl.is/i/foo/file123.jpg"


async def test_saint_single():
    from forum_orchestrator.resolvers.saint2 import Saint
    html = load_fixture("saint_single.html")
    ctx = _ctx({"https://saint2.su/embed/abc": html})
    out = await Saint().resolve("https://saint2.su/embed/abc", ctx)
    assert len(out) == 1
    assert out[0].kind == Kind.VIDEO
    assert out[0].url == "https://cdn.saint2.su/videos/abc/My_Cool_Clip.mp4"
    assert "My Cool Clip" in (out[0].filename or "")


async def test_turbo_single():
    from forum_orchestrator.resolvers.turbo import Turbo
    html = load_fixture("turbo_single.html")
    ctx = _ctx({"https://turbo.cr/v/sample": html})
    out = await Turbo().resolve("https://turbo.cr/v/sample", ctx)
    assert len(out) == 1
    assert out[0].kind == Kind.VIDEO
    assert out[0].url == "https://cdn.turbo.cr/v/sample.mp4"


async def test_filester_single():
    from forum_orchestrator.resolvers.filester import Filester
    html = load_fixture("filester_single.html")
    ctx = _ctx({"https://filester.me/d/abc": html})
    out = await Filester().resolve("https://filester.me/d/abc", ctx)
    assert len(out) == 1
    assert out[0].url == "https://cdn.filester.me/dl/abc/archive.zip"
    assert out[0].filename == "archive.zip"


async def test_cyberfile_single():
    from forum_orchestrator.resolvers.cyberfile import Cyberfile
    page = load_fixture("cyberfile_single.html")
    text_map = {"https://cyberfile.su/AbCd123": page}
    post_map = {
        "https://cyberfile.su/account/ajax/file_details": {
            "html": 'var openUrl = "https://files.cyberfile.su/dl/great_video.mp4";',
        },
    }
    ctx = _ctx(text_map=text_map, post_map=post_map)
    out = await Cyberfile().resolve("https://cyberfile.su/AbCd123", ctx)
    assert len(out) == 1
    assert out[0].url == "https://files.cyberfile.su/dl/great_video.mp4"
    assert out[0].dedup_key == "cyberfile:WhAtEvEr123"


async def test_gofile_folder():
    from forum_orchestrator.resolvers.gofile import GoFile
    text_map = {
        "https://gofile.io/dist/js/global.js": 'wt: "abc123token"',
    }
    post_map = {
        "https://api.gofile.io/accounts": {"data": {"token": "mytok"}},
    }
    json_map = {
        "https://api.gofile.io/contents/FOLDER1?wt=abc123token&cache=true": {
            "status": "ok",
            "data": {
                "children": {
                    "f1": {"type": "file", "id": "f1", "name": "a.mp4",
                            "link": "https://store.gofile.io/dl/a.mp4"},
                    "f2": {"type": "file", "id": "f2", "name": "b.jpg",
                            "link": "https://store.gofile.io/dl/b.jpg"},
                },
            },
        },
    }
    ctx = _ctx(text_map=text_map, json_map=json_map, post_map=post_map)
    out = await GoFile().resolve("https://gofile.io/d/FOLDER1", ctx)
    assert {r.filename for r in out} == {"a.mp4", "b.jpg"}
    assert all(r.dedup_key.startswith("gofile:") for r in out)


async def test_anonfiles_tombstone():
    from forum_orchestrator.errors import DeadLink
    from forum_orchestrator.resolvers.anonfiles import AnonFiles
    with pytest.raises(DeadLink):
        await AnonFiles().resolve("https://anonfiles.com/abc", _ctx())


# ---------------------------------------------------------------------------
# chevereto thumb stripping (covers F4)
# ---------------------------------------------------------------------------


async def test_chevereto_cdn_strips_thumb_via_jpgsu():
    """A direct CDN URL with a .md.jpg suffix routes through JpgSu and is
    rewritten back to the full-size image."""
    url = "https://simp1.cuckcapital.cr/images/2024/05/01/foo.md.jpg"
    out = await JpgSu().resolve(url, _ctx())
    assert len(out) == 1
    assert out[0].url == "https://simp1.cuckcapital.cr/images/2024/05/01/foo.jpg"


async def test_chevereto_strip_preserves_query():
    from forum_orchestrator.resolvers._chevereto import _strip_thumb
    assert _strip_thumb("https://x/foo.md.jpg?cb=1") == "https://x/foo.jpg?cb=1"
    assert _strip_thumb("https://x/foo.th.png") == "https://x/foo.png"
    assert _strip_thumb("https://x/foo.png") == "https://x/foo.png"


# ---------------------------------------------------------------------------
# bunkr — CDN-direct routes via file-page, __NEXT_DATA__ parsing
# ---------------------------------------------------------------------------


async def test_bunkr_cdn_direct_routes_to_file_page():
    """A naked CDN URL should be re-routed through bunkr.cr/v/<basename> so we
    pick up the real (rotated) CDN target + correct Referer."""
    file_page = (
        '<html><head><title>1-341_Pandora-xqlYb9K0.mp4 | Bunkr</title></head>'
        '<body><a class="ic-download-01-svg" '
        'href="https://cdn7.bunkr.ru/1-341_Pandora-xqlYb9K0.mp4">dl</a>'
        '</body></html>'
    )
    ctx = _ctx({"https://bunkr.cr/v/1-341_Pandora-xqlYb9K0.mp4": file_page})
    out = await Bunkr().resolve(
        "https://cdn3.bunkr.ru/1-341_Pandora-xqlYb9K0.mp4", ctx,
    )
    assert len(out) == 1
    assert out[0].url == "https://cdn7.bunkr.ru/1-341_Pandora-xqlYb9K0.mp4"
    assert out[0].referer == "https://bunkr.cr/"


async def test_bunkr_single_via_next_data():
    next_data = {
        "props": {"pageProps": {"file": {
            "name": "Pandora-Best.mp4",
            "cdn": "https://cdn9.bunkr.ru",
            "size": 42,
            "type": "video",
        }}}
    }
    html = (
        '<html><head><title>Pandora-Best.mp4 | Bunkr</title></head>'
        '<body><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(next_data) + '</script></body></html>'
    )
    ctx = _ctx({"https://bunkr.cr/v/abc": html})
    out = await Bunkr().resolve("https://bunkr.cr/v/abc", ctx)
    assert len(out) == 1
    assert out[0].filename == "Pandora-Best.mp4"
    assert out[0].url == "https://cdn9.bunkr.ru/Pandora-Best.mp4"
    assert out[0].kind == Kind.VIDEO
    assert out[0].referer == "https://bunkr.cr/"


async def test_bunkr_album_via_next_data():
    file1_data = {"props": {"pageProps": {"file": {
        "name": "one.mp4", "cdn": "https://cdn1.bunkr.ru", "size": 1, "type": "video",
    }}}}
    file2_data = {"props": {"pageProps": {"file": {
        "name": "two.jpg", "cdn": "https://cdn2.bunkr.ru", "size": 2, "type": "image",
    }}}}
    album_data = {"props": {"pageProps": {"album": {"files": [
        {"slug": "ONE_slug", "name": "one.mp4", "size": 1, "type": "video"},
        {"slug": "TWO_slug", "name": "two.jpg", "size": 2, "type": "image"},
    ]}}}}
    album_html = (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(album_data) + '</script></body></html>'
    )
    file1_html = (
        '<html><head><title>one.mp4 | Bunkr</title></head><body>'
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(file1_data) + '</script></body></html>'
    )
    file2_html = (
        '<html><head><title>two.jpg | Bunkr</title></head><body>'
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(file2_data) + '</script></body></html>'
    )
    ctx = _ctx({
        "https://bunkr.cr/a/MyAlbum": album_html,
        "https://bunkr.cr/v/ONE_slug": file1_html,
        "https://bunkr.cr/f/TWO_slug": file2_html,
    })
    out = await Bunkr().resolve("https://bunkr.cr/a/MyAlbum", ctx)
    assert {r.filename for r in out} == {"one.mp4", "two.jpg"}
    assert {r.url for r in out} == {
        "https://cdn1.bunkr.ru/one.mp4",
        "https://cdn2.bunkr.ru/two.jpg",
    }


# ---------------------------------------------------------------------------
# turbo — JS-driven embed page
# ---------------------------------------------------------------------------


async def test_turbo_embed_inline_json():
    from forum_orchestrator.resolvers.turbo import Turbo
    html = (
        '<html><head><title>gg5WwANtZ9B | Turbo</title></head>'
        '<body><script>'
        'var sources = [{ "src": "https://cdn.turbo.cr/videos/gg5WwANtZ9B.mp4",'
        ' "type": "video/mp4" }];'
        '</script></body></html>'
    )
    ctx = _ctx({"https://turbo.cr/embed/gg5WwANtZ9B": html})
    out = await Turbo().resolve("https://turbo.cr/embed/gg5WwANtZ9B", ctx)
    assert len(out) == 1
    assert out[0].url == "https://cdn.turbo.cr/videos/gg5WwANtZ9B.mp4"
    assert out[0].kind == Kind.VIDEO
