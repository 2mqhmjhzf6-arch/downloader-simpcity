from __future__ import annotations

import pytest

from forum_orchestrator.forum.xenforo import (
    ThreadScraper,
    _extract_posts,
    _extract_title,
    _extract_total_pages,
    _is_ui_asset,
    _unwrap_redirect,
    parse_thread_id,
)
from selectolax.parser import HTMLParser

from tests.conftest import load_fixture


def test_parse_thread_id():
    assert parse_thread_id("https://simpcity.cr/threads/some-thread.12345/") == "12345"
    assert parse_thread_id("https://simpcity.cr/threads/another.99/page-3") == "99"


def test_extract_title_pages_posts():
    html = load_fixture("xenforo_thread_p1.html")
    dom = HTMLParser(html)
    assert _extract_title(dom) == "Some Cool Thread"
    assert _extract_total_pages(dom) == 3
    posts = list(_extract_posts(html, "https://simpcity.cr/threads/x.1/page-1"))
    assert {p.post_id for p in posts} == {"12345", "12348"}
    assert posts[0].posted_at is not None
    assert posts[0].posted_at.date().isoformat() == "2025-11-14"
    assert posts[0].post_number == 1


def test_unwrap_redirect_decodes_base64_envelope():
    src = "https://simpcity.cr/redirect/?to=aHR0cHM6Ly9nb2ZpbGUuaW8vZC9WYmtGYmU&e=1&m=b64"
    assert _unwrap_redirect(src) == "https://gofile.io/d/VbkFbe"


def test_unwrap_redirect_passthrough_for_non_simpcity():
    src = "https://example.com/redirect/?to=aHR0cHM6Ly9nb2ZpbGUuaW8vZC9WYmtGYmU"
    assert _unwrap_redirect(src) == src


def test_unwrap_redirect_passthrough_when_no_to_param():
    src = "https://simpcity.cr/redirect/?foo=bar"
    assert _unwrap_redirect(src) == src


def test_unwrap_redirect_passthrough_for_invalid_base64():
    src = "https://simpcity.cr/redirect/?to=!!not-base64!!"
    assert _unwrap_redirect(src) == src


def test_ui_asset_blocks_bunkr_dash_and_twemoji():
    assert _is_ui_asset("https://dash.bunkr.pk/assets/img/icon.svg")
    assert _is_ui_asset("https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/svg/1f459.svg")
    assert _is_ui_asset("https://simpcity.cr/styles/foo/sprite.svg")
    assert not _is_ui_asset("https://bunkr.cr/v/some-video.mp4")
    assert not _is_ui_asset("https://jpg5.su/img/foo.jpg")


def test_extract_passwords_from_spoiler_and_inline():
    html = (
        '<article class="message" data-content="post-1" id="js-post-1">'
        '  <div class="message-attribution-opposite"><a>#1</a></div>'
        '  <div class="bbWrapper">'
        '    <a href="https://mega.nz/file/xyz#abc">mega</a>'
        '    <div class="bbCodeBlock--spoiler"><div class="bbCodeBlock-content">'
        '      hunter2'
        '    </div></div>'
        '    <span class="bbCodeInlineSpoiler">inline-secret</span>'
        '    plain text pw: foo-bar123 and also password = qux'
        '  </div>'
        '</article>'
    )
    posts = list(_extract_posts(html, "https://simpcity.cr/threads/x.1/page-1"))
    assert len(posts) == 1
    pws = posts[0].passwords
    # Order preserved, dedup applied. All four hints must be captured.
    assert "hunter2" in pws
    assert "inline-secret" in pws
    assert "foo-bar123" in pws
    assert "qux" in pws


def test_extract_passwords_empty_when_no_hints():
    html = (
        '<article class="message" data-content="post-7" id="js-post-7">'
        '  <div class="message-attribution-opposite"><a>#1</a></div>'
        '  <div class="bbWrapper">just a link <a href="https://x/y.jpg">img</a></div>'
        '</article>'
    )
    posts = list(_extract_posts(html, "https://simpcity.cr/threads/x.1/page-1"))
    assert posts[0].passwords == []


def test_extract_links_finds_all_known_hosts():
    html = load_fixture("xenforo_thread_p1.html")
    posts = list(_extract_posts(html, "https://simpcity.cr/threads/x.1/page-1"))
    scraper = ThreadScraper(http=None)  # type: ignore[arg-type]
    links = scraper.extract_links(posts[0], "https://simpcity.cr")
    urls = {l.url for l in links}
    assert "https://bunkr.cr/a/abcd1234" in urls
    assert "https://cyberdrop.me/a/xy12zz" in urls
    assert "https://pixeldrain.com/u/AbCdEf12" in urls
    assert "https://example.com/cover.jpg" in urls
