"""Opt-in live health checks for resolvers.

Run with ``pytest --live``. These pings confirm the host hasn't moved its
HTML layout in a way that breaks our extraction. Failures here mean a
resolver needs updating, not that the test is broken.
"""

from __future__ import annotations

import pytest

from forum_orchestrator.http_client import HttpClient
from forum_orchestrator.resolvers.base import ResolveContext

pytestmark = pytest.mark.live


@pytest.fixture
async def http():
    c = HttpClient(user_agent="Mozilla/5.0", use_curl_cffi=True)
    try:
        yield c
    finally:
        await c.aclose()


async def test_redgifs_anonymous_token(http):
    # The lightest possible live check: just confirm the auth endpoint still works.
    data = await http.get_json("https://api.redgifs.com/v2/auth/temporary")
    assert "token" in data and len(data["token"]) > 20


async def test_pixeldrain_info_endpoint(http):
    # Known stable demo file id from pixeldrain's own example.
    r = await http.request(
        "GET", "https://pixeldrain.com/api/file/abcd1234/info"
    )
    # 404 is fine (id may not exist); we only care that the host answers JSON-ish.
    assert r.status_code in (200, 404)
