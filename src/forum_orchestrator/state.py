"""SQLite-backed state for delta sync + intra-thread dedup.

Tables:
  thread     thread_id PK, url, title, first_seen, last_seen
  downloaded thread_id, key, url, filename, sha256, size, downloaded_at
  failed     thread_id, url, reason, failed_at

`key` is the resolver's dedup key (preferred) or the URL itself. A row in
`downloaded` means: this thread has already saved this resource; skip on rerun.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS thread (
    thread_id   TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    title       TEXT NOT NULL,
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS downloaded (
    thread_id   TEXT NOT NULL,
    key         TEXT NOT NULL,
    url         TEXT NOT NULL,
    filename    TEXT NOT NULL,
    sha256      TEXT,
    size        INTEGER,
    downloaded_at INTEGER NOT NULL,
    PRIMARY KEY (thread_id, key)
);
CREATE INDEX IF NOT EXISTS idx_downloaded_sha ON downloaded(sha256);
CREATE TABLE IF NOT EXISTS failed (
    thread_id   TEXT NOT NULL,
    url         TEXT NOT NULL,
    reason      TEXT NOT NULL,
    failed_at   INTEGER NOT NULL
);
"""


class State:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ---- thread ----
    def upsert_thread(self, thread_id: str, url: str, title: str) -> None:
        now = int(time.time())
        with self.tx() as c:
            c.execute(
                """INSERT INTO thread(thread_id, url, title, first_seen, last_seen)
                   VALUES(?, ?, ?, ?, ?)
                   ON CONFLICT(thread_id) DO UPDATE SET
                       url = excluded.url,
                       title = excluded.title,
                       last_seen = excluded.last_seen""",
                (thread_id, url, title, now, now),
            )

    # ---- downloaded ----
    def is_downloaded(self, thread_id: str, key: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM downloaded WHERE thread_id=? AND key=? LIMIT 1",
            (thread_id, key),
        )
        return cur.fetchone() is not None

    def filter_new(self, thread_id: str, keys: Iterable[str]) -> set[str]:
        keys = list(keys)
        if not keys:
            return set()
        # SQLite has a max variable count; chunk if needed.
        out: set[str] = set(keys)
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            qs = ",".join("?" * len(chunk))
            cur = self._conn.execute(
                f"SELECT key FROM downloaded WHERE thread_id=? AND key IN ({qs})",
                (thread_id, *chunk),
            )
            for (k,) in cur.fetchall():
                out.discard(k)
        return out

    def record_download(
        self, thread_id: str, key: str, url: str, filename: str,
        sha256: Optional[str], size: Optional[int],
    ) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT OR REPLACE INTO downloaded
                   (thread_id, key, url, filename, sha256, size, downloaded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (thread_id, key, url, filename, sha256, size, int(time.time())),
            )

    def has_sha(self, thread_id: str, sha256: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM downloaded WHERE thread_id=? AND sha256=? LIMIT 1",
            (thread_id, sha256),
        )
        return cur.fetchone() is not None

    # ---- failed ----
    def record_failure(self, thread_id: str, url: str, reason: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO failed(thread_id, url, reason, failed_at) VALUES (?, ?, ?, ?)",
                (thread_id, url, reason, int(time.time())),
            )

    def failures_for(self, thread_id: str) -> list[tuple[str, str]]:
        cur = self._conn.execute(
            "SELECT url, reason FROM failed WHERE thread_id=? ORDER BY failed_at",
            (thread_id,),
        )
        return list(cur.fetchall())
