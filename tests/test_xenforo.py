from __future__ import annotations

import pytest

from forum_orchestrator.forum.xenforo import (
    ThreadScraper,
    _extract_posts,
    _extract_title,
    _extract_total_pages,
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
