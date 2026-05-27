"""Thin async HTTP wrapper.

Uses httpx by default; falls back to `curl_cffi` for hosts known to gate on
TLS fingerprint (Bunkr's Cloudflare interstitial, Cyberfile, etc.) when the
optional dependency is installed.
"""

from __future__ import annotations

import asyncio
import logging
import random
from contextlib import asynccontextmanager
from http.cookiejar import CookieJar
from typing import AsyncIterator, Optional

import httpx

log = logging.getLogger(__name__)

try:  # optional
    from curl_cffi.requests import AsyncSession as _CurlAsyncSession  # type: ignore
    HAVE_CURL_CFFI = True
except Exception:  # pragma: no cover - optional dep
    _CurlAsyncSession = None  # type: ignore
    HAVE_CURL_CFFI = False


DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

_CF_HOSTS = (
    "bunkr.", "bunkrr.", "bunkrrr.",
    "cyberdrop.", "cyberfile.",
    "saint2.", "turbo.cr",
)


def _is_cf_host(url: str) -> bool:
    u = url.lower()
    return any(h in u for h in _CF_HOSTS)


class HttpClient:
    def __init__(
        self,
        user_agent: str,
        cookies: Optional[CookieJar] = None,
        timeout_s: float = 30.0,
        retries: int = 4,
        use_curl_cffi: bool = True,
    ):
        headers = {"User-Agent": user_agent, **DEFAULT_HEADERS}
        self._timeout = timeout_s
        self._retries = retries
        self._cookies = cookies
        self._httpx = httpx.AsyncClient(
            headers=headers,
            cookies=cookies,
            follow_redirects=True,
            timeout=timeout_s,
            http2=True,
        )
        self._curl: Optional[_CurlAsyncSession] = None  # type: ignore[type-arg]
        if use_curl_cffi and HAVE_CURL_CFFI:
            self._curl = _CurlAsyncSession(
                impersonate="chrome124",
                timeout=timeout_s,
                headers=headers,
            )
            if cookies is not None:
                for c in cookies:
                    try:
                        self._curl.cookies.set(c.name, c.value, domain=c.domain or "")
                    except Exception:
                        pass

    async def aclose(self) -> None:
        await self._httpx.aclose()
        if self._curl is not None:
            try:
                await self._curl.close()
            except Exception:
                pass

    @asynccontextmanager
    async def __aenter__(self) -> "HttpClient":  # type: ignore[override]
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # ---- request helpers --------------------------------------------------

    async def get_text(self, url: str, *, referer: Optional[str] = None,
                       headers: Optional[dict] = None) -> str:
        r = await self.request("GET", url, referer=referer, headers=headers)
        return r.text

    async def get_json(self, url: str, *, referer: Optional[str] = None,
                       headers: Optional[dict] = None):
        r = await self.request("GET", url, referer=referer, headers=headers)
        return r.json()

    async def post_json(self, url: str, data=None, json=None, *,
                        referer: Optional[str] = None,
                        headers: Optional[dict] = None):
        r = await self.request("POST", url, data=data, json=json,
                               referer=referer, headers=headers)
        return r.json()

    async def request(
        self,
        method: str,
        url: str,
        *,
        referer: Optional[str] = None,
        headers: Optional[dict] = None,
        data=None,
        json=None,
    ) -> httpx.Response:
        h: dict[str, str] = {}
        if referer:
            h["Referer"] = referer
        if headers:
            h.update(headers)

        use_curl = self._curl is not None and _is_cf_host(url)
        last_exc: Optional[Exception] = None
        for attempt in range(self._retries + 1):
            try:
                if use_curl:
                    r = await self._curl.request(method, url, headers=h, data=data, json=json)  # type: ignore[union-attr]
                    # adapt curl_cffi response to httpx.Response-ish API
                    return _CurlResponseAdapter(r)
                r = await self._httpx.request(method, url, headers=h, data=data, json=json)
                if r.status_code in (429, 502, 503, 504):
                    raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
                return r
            except Exception as e:
                last_exc = e
                if attempt == self._retries:
                    break
                delay = (2**attempt) + random.random() * 0.3
                log.debug("retry %s %s in %.1fs: %s", method, url, delay, e)
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    @asynccontextmanager
    async def stream(self, method: str, url: str, *,
                     referer: Optional[str] = None,
                     headers: Optional[dict] = None) -> AsyncIterator[httpx.Response]:
        h = {"Referer": referer} if referer else {}
        if headers:
            h.update(headers)
        async with self._httpx.stream(method, url, headers=h) as r:
            yield r


class _CurlResponseAdapter:
    """Minimal duck-typed wrapper so curl_cffi responses look like httpx ones
    for the small surface we use (status_code, text, json(), headers)."""
    def __init__(self, r):
        self._r = r
    @property
    def status_code(self) -> int: return int(self._r.status_code)
    @property
    def text(self) -> str: return self._r.text
    @property
    def content(self) -> bytes: return self._r.content
    @property
    def headers(self): return self._r.headers
    @property
    def url(self): return self._r.url
    def json(self): return self._r.json()
    def raise_for_status(self):
        if 400 <= self.status_code:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=None, response=None)  # type: ignore[arg-type]
