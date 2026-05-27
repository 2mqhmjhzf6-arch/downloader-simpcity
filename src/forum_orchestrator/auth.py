"""Cookie loading for forum + host auth.

Two sources are supported:
  1. A Netscape-format cookies.txt (`--cookies` / yt-dlp compatible).
  2. A live browser profile, via the `browser-cookie3` library
     (`--cookies-from-browser firefox`).
"""

from __future__ import annotations

import http.cookiejar
from pathlib import Path
from typing import Optional


def load_cookiejar(
    cookies_file: Optional[Path] = None,
    cookies_browser: Optional[str] = None,
    xf_user: Optional[str] = None,
    xf_session: Optional[str] = None,
    forum_domain: str = "simpcity.cr",
) -> http.cookiejar.CookieJar:
    jar = http.cookiejar.CookieJar()

    if cookies_file is not None:
        if not Path(cookies_file).exists():
            raise FileNotFoundError(
                f"--cookies path does not exist: {cookies_file}\n"
                "Export one with a browser extension (Firefox 'cookies.txt' or "
                "Chrome 'Get cookies.txt LOCALLY'), or use --cookies-from-browser "
                "<firefox|chrome|edge|brave> to read your live browser profile."
            )
        moz = http.cookiejar.MozillaCookieJar(str(cookies_file))
        moz.load(ignore_discard=True, ignore_expires=True)
        for c in moz:
            jar.set_cookie(c)

    if cookies_browser:
        try:
            import browser_cookie3 as bc3
        except ImportError as e:
            raise RuntimeError(
                "Install `browser-cookie3` to use --cookies-from-browser"
            ) from e
        fn = {
            "firefox": bc3.firefox,
            "chrome": bc3.chrome,
            "chromium": bc3.chromium,
            "edge": bc3.edge,
            "brave": bc3.brave,
            "opera": bc3.opera,
            "vivaldi": bc3.vivaldi,
            "safari": bc3.safari,
        }.get(cookies_browser.lower())
        if fn is None:
            raise ValueError(f"unknown browser: {cookies_browser!r}")
        for c in fn():
            jar.set_cookie(c)

    if xf_user or xf_session:
        for name, value in (("xf_user", xf_user), ("xf_session", xf_session)):
            if not value:
                continue
            jar.set_cookie(
                http.cookiejar.Cookie(
                    version=0,
                    name=name,
                    value=value,
                    port=None,
                    port_specified=False,
                    domain=forum_domain,
                    domain_specified=True,
                    domain_initial_dot=False,
                    path="/",
                    path_specified=True,
                    secure=True,
                    expires=None,
                    discard=False,
                    comment=None,
                    comment_url=None,
                    rest={},
                )
            )
    return jar


def jar_to_dict(jar: http.cookiejar.CookieJar, domain_suffix: str | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in jar:
        if domain_suffix and not (c.domain or "").endswith(domain_suffix):
            continue
        out[c.name] = c.value
    return out
