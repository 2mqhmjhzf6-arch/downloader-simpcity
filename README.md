# Forum Media Orchestrator (`fmo`)

A Python CLI that scrapes XenForo-based forum threads (SimpCity et al.), resolves the
embedded third-party host links into direct media URLs, and downloads them into a
tidy, dated folder structure. Inspired by [`fansly-scraper`][fansly], [`CyberDropDownloader`][cdd],
[`gallery-dl`][gdl] and the `XenForoPostDownloader` userscript that ships with `simpcity`.

> **Working resolvers (15):** direct, bunkr, cyberdrop, jpgsu (jpg.church / jpg.fish / jpg5.su /
> cuckcapital.cr), pixl (pixl.is / .li / .cr), pixhost, pixeldrain, imgbox, ibb (imgbb), redgifs,
> gofile, saint2, turbo.cr, cyberfile, filester, plus an anonfiles "tombstone" resolver that
> records a clear "site shut down" reason instead of timing out. ~17 more hosts are stubbed and
> registered.

## What this does

You give `fmo` a XenForo thread URL. It:

1. Logs in with your browser cookies (no password ever pasted).
2. Walks every page of the thread, reading every post.
3. Extracts every image / video / file-host URL it can see.
4. Hands each URL to the resolver registered for that host, which returns one or more direct
   download URLs.
5. Skips anything it already has (per-thread SQLite state + SHA-256 dedup).
6. Streams everything to disk, with retries, progress bars, and human filenames.

## Requirements

* Python 3.10 or newer
* Optional but recommended: `curl-cffi` (`pip install .[cf]`) so Cloudflare-fronted hosts (Bunkr,
  Cyberdrop, Cyberfile, Saint, Turbo) work without a browser

## Installation — Windows (step by step)

1. **Install Python** from [python.org](https://www.python.org/downloads/windows/) —
   tick **"Add python.exe to PATH"** at the bottom of the first installer screen.
2. **Open PowerShell** (Start ▸ type `powershell`).
3. Clone the repo and install:
   ```powershell
   git clone <repo-url>
   cd downloader-simpcity
   python -m pip install -e ".[dev,cf]"
   ```
   If the `cf` extra fails to build, drop it: `python -m pip install -e ".[dev]"` — almost everything
   still works.
4. Verify the install:
   ```powershell
   fmo --help
   ```

## Installation — macOS / Linux

```bash
git clone <repo-url>
cd downloader-simpcity
python -m pip install -e ".[dev,cf]"
fmo --help
```

## Authentication

`fmo` reuses your browser session, so you never paste a password. Three ways:

### A. `cookies.txt` (most portable)

1. Install the Chrome extension [**"Get cookies.txt LOCALLY"**](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
   (Firefox: extension named simply **"cookies.txt"**).
2. Log into the forum in your browser.
3. Click the extension icon → **Export** → save the file as `cookies.txt`.
4. Pass it to `fmo`:
   ```bash
   fmo download <thread-url> --cookies cookies.txt
   ```

### B. Live browser read (no file)

```bash
fmo download <thread-url> --cookies-from-browser firefox
```
Works with `firefox`, `chrome`, `edge`, `brave`, `chromium`, `opera`. The browser doesn't need to
be running.

### C. Raw cookie paste

If you only have the two session cookies handy:

```bash
fmo download <thread-url> --xf-user <value> --xf-session <value>
```

## First run

```bash
fmo download https://simpcity.cr/threads/example.12345/ \
    --cookies cookies.txt \
    --out "D:/Downloads/SimpCity"
```

You'll see something like:

```
Thread: Some Cool Thread  (12345, 47 posts, 5 pages)
  243 candidate URLs extracted
  18 URLs with no matching resolver — skipping
⠋ all       resolving         ━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%   225/225
  192 new files to download  (28 dedup/skipped)
⠋ all       downloading       ━━━━━━━━━━━━━━━━━━━━━━━━━━  67%   148/220   12.4 MB/s  eta 0:03:21
Done. 192 new files in D:/Downloads/SimpCity/2026-05-27_Run/Some Cool Thread (12345)
```

### What each line means

| Line | Meaning |
|---|---|
| `Thread: ...` | Thread metadata: title, ID, post count, page count |
| `candidate URLs extracted` | Every `href` / `src` / `data-url` that looked like media |
| `no matching resolver — skipping` | Host has no registered pattern; appended to `_meta/unsupported_hosts.txt` and to the global new-host log (see below) |
| `resolving` bar | One tick per resolver call (one URL or one album) |
| `new files to download` | Live count after subtracting whatever the per-thread SQLite already has |
| `dedup/skipped` | Already downloaded earlier (this run or a previous run) — counts intra-thread dupes too |
| `downloading` bar | One tick per file completed, with speed + ETA |
| `Done` | Final count + absolute output path |

Add `--quiet` to suppress both bars (useful in cron jobs); the summary lines still print.

## CLI flags

| Flag | Default | Meaning |
|---|---|---|
| `--out PATH` / `-o` | `./downloads` | Output root |
| `--cookies FILE` | — | Netscape `cookies.txt` |
| `--cookies-from-browser NAME` | — | Live read from a browser profile |
| `--xf-user`, `--xf-session` | — | Raw XenForo cookie values |
| `--pages 1-3` or `--pages 4` | all | Page range |
| `--posts 12345,12348` | all | Post-ID allowlist |
| `--max-height 1080` | 1080 | Video cap (4K is *always* excluded) |
| `--only videos\|photos\|both` | `both` | Type filter; works inside mixed albums |
| `--concurrency 6` | 6 | Parallel resolve / download workers |
| `--dry-run` | off | Plan only, no files written |
| `--quiet` | off | Suppress progress bars |
| `--no-curl-cffi` | off | Force plain `httpx` (no TLS impersonation) |
| `-v / --verbose` | off | DEBUG-level logging |

## Folder layout

```
<out>/
  2026-05-27_Run/                              # date the run started
    Thread Title (12345)/
      [2025-11-14] some_file.jpg               # YYYY-MM-DD = original post date
      [2025-11-14] another.mp4
      _meta/
        state.sqlite                           # delta-sync DB
        unsupported_hosts.txt                  # URLs whose host has no resolver yet
        failed_downloads.txt                   # everything else that failed
        failed_links.txt                       # legacy combined file (back-compat)
```

The **global** new-host log (one example URL per unrecognised hostname, across all runs) lives at:

| OS | Path |
|---|---|
| Windows | `%APPDATA%\fmo\new_unsupported_hosts.log` |
| macOS | `~/Library/Application Support/fmo/new_unsupported_hosts.log` |
| Linux | `~/.local/share/fmo/new_unsupported_hosts.log` |

This is your live backlog of "hosts to implement next."

## Tests

```bash
pytest                  # offline, uses tests/fixtures/    (~46 tests)
pytest --live           # also pings real hosts (resolver health checks)
```

`--dry-run` runs the whole pipeline without writing files — useful for sanity-checking a thread
before a large download.

## Debugging guide

| Symptom | Likely cause | Fix |
|---|---|---|
| `AuthError: forum returned login page` | Cookies stale / expired | Re-export `cookies.txt` |
| `TransientError: HTTP 403` on bunkr/cyberdrop | Cloudflare gate | Install with `[cf]` extra (curl-cffi) |
| Lots of `UnsupportedHost` rows in `_meta/unsupported_hosts.txt` | Host is registered but not implemented | Check the global new-host log to see the backlog |
| `dead: HTTP 404` | File was deleted on the host | Nothing to do |
| `no resolver matched` | Host is brand new to `fmo` | URL is in the global new-host log; consider adding a resolver |
| Slow run | Default `--concurrency 6`; raise it | `--concurrency 12` (don't go crazy — most hosts rate-limit) |

Run with `-v` for DEBUG-level logging including HTTP retries.

## Adding a resolver

```python
# src/forum_orchestrator/resolvers/coolhost.py
from .base import Resolver, register, ResolveContext
from ..models import Resource

@register
class CoolHost(Resolver):
    name = "coolhost"
    patterns = [r"coolhost\.example/(f|d)/[a-z0-9]+"]
    album_patterns = [r"coolhost\.example/a/[a-z0-9]+"]

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        ...
```

Then drop `tests/fixtures/coolhost_album.html` plus a test in `tests/test_resolvers.py` (the
existing tests are good templates) and import the module in
`src/forum_orchestrator/resolvers/__init__.py`.

For Chevereto-family hosts (jpg.church, pixl.is, etc.) just call
`_chevereto.resolve_chevereto(url, ctx)` — see `pixl.py` for a 12-line example.

## Future: MEGA (mega.nz) downloader

> **TL;DR — it is possible but considerably harder than every other host. Recommendation: ship a
> stub that points users to MEGAcmd until we see how often MEGA links actually show up in scraped
> threads.**

Why it's hard:

* **End-to-end encryption.** Every file is AES-CTR-encrypted client-side; the per-file key is
  encoded in the URL fragment (`#<key>`) and never sent to the server. The server stores nothing
  but ciphertext. Decryption has to happen in our process before the bytes hit disk.
* **Custom JSON-RPC protocol.** All API calls go to `https://g.api.mega.co.nz/cs` with session
  tokens, sequence numbers, and batched request envelopes. None of it is documented.
* **Folder vs file URLs.** `/file/<id>#<key>` returns one ciphertext blob; `/folder/<id>#<key>`
  requires a separate list-children RPC and a per-child key tree.
* **Maintenance burden.** MEGA changes the protocol every 6–18 months. Existing Python ports
  (`mega.py`, `megapy`) are routinely broken for months at a time.

Three realistic options:

1. **Out-of-process** — shell out to MEGA's official C++ CLI (`MEGAcmd`). Pros: maintained by MEGA,
   handles every protocol change. Cons: separate native install, ~80 MB binary, license is
   non-trivial.
2. **In-process** — vendor a slimmed-down fork of `mega.py`, swap its crypto for the `cryptography`
   library, hand-port to async. Estimate: ~1500 LOC, plus periodic fixes when MEGA changes the
   wire format.
3. **Stub it out** — register a resolver that recognises the URL shape and writes a clear
   "install MEGAcmd and run `mega-get <url>` manually" message to `unsupported_hosts.txt`.

Recommendation: ship option 3 first. If `_meta/unsupported_hosts.txt` across users frequently shows
MEGA URLs, escalate to option 1 (it gives full coverage in two days of work) and only consider
option 2 if shelling out is unacceptable.

## Architecture

```
src/forum_orchestrator/
  cli.py                # typer entrypoint
  orchestrator.py       # high-level run loop
  http_client.py        # httpx async client with retries + optional curl-cffi
  auth.py               # cookies.txt + browser-cookie3 loaders
  tracking.py           # per-album + global host log files
  forum/xenforo.py      # thread/page/post scraper
  resolvers/
    base.py             # Resolver protocol + URL-pattern registry
    _chevereto.py       # shared helper for jpg.church / pixl / etc.
    direct.py, bunkr.py, cyberdrop.py, jpgsu.py, pixl.py, pixhost.py,
    pixeldrain.py, imgbox.py, ibb.py, redgifs.py, gofile.py,
    saint2.py, turbo.py, cyberfile.py, filester.py, anonfiles.py,
    stubs.py            # not-yet-implemented hosts
  downloader.py         # async streaming downloader, hash-dedup
  state.py              # SQLite delta-sync DB
  naming.py             # sanitize, date-prefix, collision-suffix
  ui.py                 # Rich progress TUI
```

## Windows packaging

```powershell
python -m pip install pyinstaller
pyinstaller packaging/fmo.spec        # produces dist/fmo.exe
```

## Caveats

* The reference userscript runs inside a logged-in browser and inherits the browser's TLS
  fingerprint, CF cookies, and JS challenge solver. A pure-Python port cannot fully replicate
  that. `curl-cffi` gets us most of the way for Bunkr / Cyberdrop / etc.; truly hostile CAPTCHA
  gates will still 403 and the link will land in `_meta/failed_downloads.txt`.
* Use at your own risk; respect the terms of service of every site you scrape.

[fansly]: https://github.com/agnosto/fansly-scraper
[cdd]: https://github.com/Jules-WinnfieldX/CyberDropDownloader
[gdl]: https://codeberg.org/mikf/gallery-dl
