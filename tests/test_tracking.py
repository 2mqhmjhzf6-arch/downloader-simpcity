from __future__ import annotations

from pathlib import Path

from forum_orchestrator.tracking import (
    split_failures,
    write_failed_downloads,
    write_unsupported_hosts,
)


def test_split_failures_classifies_unsupported():
    rows = [
        ("https://gofile.io/d/x",  "UnsupportedHost: gofile resolver not yet implemented"),
        ("https://coomer.st/a/user", "coomer resolver not yet implemented"),
        ("https://bunkr.cr/v/y",   "dead: HTTP 404"),
        ("https://bunkr.cr/v/z",   "TransientError: HTTP 429 after 4 retries"),
        ("https://unknown.host/x", "no resolver matched"),
    ]
    unsupported, other = split_failures(rows)
    assert {u[0] for u in unsupported} == {"gofile.io", "coomer.st", "unknown.host"}
    assert {u[0] for u in other} == {"https://bunkr.cr/v/y", "https://bunkr.cr/v/z"}


def test_write_tracking_files(tmp_path: Path):
    meta = tmp_path / "_meta"
    meta.mkdir()
    write_unsupported_hosts(meta, [("gofile.io", "https://gofile.io/d/x", "stub")])
    write_failed_downloads(meta, [("https://bunkr.cr/v/y", "dead: 404")])
    assert (meta / "unsupported_hosts.txt").read_text().startswith("# host")
    assert "gofile.io" in (meta / "unsupported_hosts.txt").read_text()
    assert "bunkr.cr/v/y" in (meta / "failed_downloads.txt").read_text()


def test_write_tracking_files_no_op_when_empty(tmp_path: Path):
    meta = tmp_path / "_meta"
    meta.mkdir()
    write_unsupported_hosts(meta, [])
    write_failed_downloads(meta, [])
    assert not (meta / "unsupported_hosts.txt").exists()
    assert not (meta / "failed_downloads.txt").exists()


def test_global_log_path_resolvable():
    from forum_orchestrator.tracking import global_log_path
    p = global_log_path()
    assert p.name == "new_unsupported_hosts.log"
