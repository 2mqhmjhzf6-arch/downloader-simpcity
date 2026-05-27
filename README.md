# Forum Media Orchestrator (`fmo`)

A Python CLI that scrapes XenForo-based forum threads (SimpCity et al.), resolves the
embedded third-party host links into direct media URLs, and downloads them into a
tidy, dated folder structure. Inspired by [`fansly-scraper`][fansly], [`CyberDropDownloader`][cdd],
[`gallery-dl`][gdl] and the `XenForoPostDownloader` userscript that ships with `simpcity`.

> Status: **MVP / framework**. The plumbing (thread pagination, auth, state DB,
> dedup, dry-run, retries, naming, logging, plugin resolvers, tests) is complete.
> A first batch of resolvers ships working: **direct images/videos, Bunkr,
> Cyberdrop, JPG.su family, Pixhost, Pixeldrain, Imgbox, RedGifs**. The rest of
> the ~30 hosts from the reference userscript are stubbed and registered, so adding
> one is ~30 lines + a fixture test.

## Install

```bash
git clone <repo>
cd downloader-simpcity
python -m pip install -e ".[dev,cf]"      # cf = curl-cffi for Cloudflare-fronted hosts
fmo --help
```

Windows users without a build toolchain can skip `curl-cffi` (just install `.[dev]`);
Cloudflare-fronted hosts will still work most of the time via the plain `httpx` path.

## Quickstart

```bash
# 1. Dump cookies from your logged-in browser to a Netscape-format file
#    (any "Get cookies.txt" extension works), or just point fmo at the browser.
fmo download https://simpcity.cr/threads/some-thread.12345/ \
    --cookies-from-browser firefox \
    --out "D:/Downloads/SimpCity"

# 2. Dry run (parse + dedup + show plan, no downloads)
fmo download <url> --dry-run

# 3. Only pages 3-5
fmo download <url> --pages 3-5

# 4. Only specific posts
fmo download <url> --posts 12345,12348

# 5. Cap video resolution (4K always excluded, regardless of this flag)
fmo download <url> --max-height 1080

# 6. Re-run later -- delta sync skips files already saved
fmo download <url>

# 7. Health-check resolvers against real hosts (network required)
fmo check-resolvers
```

## Folder layout

```
<out>/
  2026-05-27_Run/                              # date of the run
    Thread Title (12345)/                      # one master folder per thread
      [2025-11-14] some_file.jpg               # YYYY-MM-DD = post date
      [2025-11-14] another.mp4
      _meta/
        state.sqlite                           # delta-sync DB
        failed_links.txt                       # written at end of run
```

## Auth

`fmo` reuses your browser session, so you never paste a password:

| Flag                              | What it does                                                |
| --------------------------------- | ----------------------------------------------------------- |
| `--cookies <file.txt>`            | Netscape cookies.txt (as exported by yt-dlp / extensions).  |
| `--cookies-from-browser <name>`   | `firefox`, `chrome`, `edge`, `brave`, `chromium`, `opera`.  |
| `--xf-user`, `--xf-session`       | Paste the two XenForo cookies directly.                     |

## Architecture

```
src/forum_orchestrator/
  cli.py                # typer entrypoint
  orchestrator.py       # high-level run loop
  http_client.py        # httpx async client with retries + optional curl-cffi
  auth.py               # cookies.txt + browser-cookie3 loaders
  forum/xenforo.py      # thread/page/post scraper
  resolvers/            # one module per host (plugin)
    base.py             # Resolver protocol + URL-pattern registry
    direct.py, bunkr.py, cyberdrop.py, jpgsu.py, pixhost.py,
    pixeldrain.py, imgbox.py, redgifs.py, ...
  downloader.py         # async streaming downloader, hash-dedup
  state.py              # SQLite delta-sync DB
  naming.py             # sanitize, date-prefix, collision-suffix
  ui.py                 # Rich progress TUI
```

### Adding a resolver

```python
# src/forum_orchestrator/resolvers/coolhost.py
from .base import Resolver, register, Resource, ResolveContext

@register
class CoolHost(Resolver):
    name = "coolhost"
    patterns = [r"coolhost\.example/(f|d)/[a-z0-9]+"]
    album_patterns = [r"coolhost\.example/a/[a-z0-9]+"]

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        ...
```

Then add `tests/fixtures/coolhost_album.html` and a test in
`tests/test_resolvers.py`.

## Tests

```bash
pytest                  # offline only, uses fixtures/
pytest --live           # also pings real hosts (resolver health checks)
```

## Windows packaging

```bash
python -m pip install pyinstaller
pyinstaller packaging/fmo.spec        # produces dist/fmo.exe
```

## Caveats

* The reference userscript runs inside a logged-in browser and inherits the
  browser's TLS fingerprint, CF cookies, and JS challenge solver. A pure-Python
  port cannot fully replicate that. `curl-cffi` gets us most of the way for
  Bunkr / Cyberdrop / etc.; truly hostile CAPTCHA gates will still 403 and the
  link will land in `failed_links.txt`.
* Use at your own risk; respect the terms of service of every site you scrape.

[fansly]: https://github.com/agnosto/fansly-scraper
[cdd]: https://github.com/Jules-WinnfieldX/CyberDropDownloader
[gdl]: https://codeberg.org/mikf/gallery-dl
