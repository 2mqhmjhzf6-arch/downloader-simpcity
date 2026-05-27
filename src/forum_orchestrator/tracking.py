"""Append-only logs for unresolved hosts.

Two outputs:

* per-album files inside ``<thread>/_meta/``:

    - ``unsupported_hosts.txt``   — URLs whose resolver raised
      :class:`UnsupportedHost` (i.e. the host is a known stub).
    - ``failed_downloads.txt``    — every other failure (dead links, transient
      errors, resolver errors, download exceptions).

* a single user-level log of new, completely-unknown hosts encountered across
  every run, so progress against the "still to implement" backlog is visible:

    - ``user_data_dir("fmo") / new_unsupported_hosts.log``
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse


def split_failures(rows: Iterable[tuple[str, str]]) -> tuple[
    list[tuple[str, str, str]],   # unsupported: (host, url, reason)
    list[tuple[str, str]],        # other: (url, reason)
]:
    """Partition ``(url, reason)`` rows into (unsupported, other).

    A reason starting with ``UnsupportedHost`` or matching the
    "<name> resolver not yet implemented" pattern is treated as unsupported.
    """
    unsupported: list[tuple[str, str, str]] = []
    other: list[tuple[str, str]] = []
    for url, reason in rows:
        if _is_unsupported(reason):
            host = urlparse(url).hostname or ""
            unsupported.append((host, url, reason))
        else:
            other.append((url, reason))
    return unsupported, other


def _is_unsupported(reason: str) -> bool:
    return (
        "UnsupportedHost" in reason
        or "resolver not yet implemented" in reason
        or reason == "no resolver matched"
    )


def write_unsupported_hosts(meta_dir: Path,
                            rows: list[tuple[str, str, str]]) -> None:
    if not rows:
        return
    lines = ["# host\turl\treason"]
    for host, url, reason in rows:
        lines.append(f"{host}\t{url}\t{reason}")
    (meta_dir / "unsupported_hosts.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8",
    )


def write_failed_downloads(meta_dir: Path,
                           rows: list[tuple[str, str]]) -> None:
    if not rows:
        return
    lines = ["# url\treason"]
    for url, reason in rows:
        lines.append(f"{url}\t{reason}")
    (meta_dir / "failed_downloads.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8",
    )


def global_log_path() -> Path:
    """Return the user-data location for the global new-host log.

    Uses :mod:`platformdirs` if available; otherwise falls back to
    ``~/.local/share/fmo`` on Unix / ``%APPDATA%\\fmo`` on Windows.
    """
    try:
        from platformdirs import user_data_dir  # type: ignore
        return Path(user_data_dir("fmo")) / "new_unsupported_hosts.log"
    except Exception:
        import os, sys
        if sys.platform.startswith("win"):
            base = Path(os.environ.get("APPDATA", str(Path.home())))
        else:
            base = Path.home() / ".local" / "share"
        return base / "fmo" / "new_unsupported_hosts.log"


_global_seen: set[str] | None = None


def log_new_host(url: str) -> None:
    """Append ``url`` to the global new-host log unless it (or the same host)
    has already been recorded.

    Dedup key is the hostname — we only need one example URL per host.
    """
    global _global_seen
    host = urlparse(url).hostname or url
    path = global_log_path()
    if _global_seen is None:
        _global_seen = set()
        if path.exists():
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        h = urlparse(parts[1]).hostname
                        if h:
                            _global_seen.add(h)
            except Exception:
                pass
    if host in _global_seen:
        return
    _global_seen.add(host)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts}\t{url}\n")
    except Exception:
        # Best-effort logging; don't break a run if the user data dir is unwritable.
        pass
