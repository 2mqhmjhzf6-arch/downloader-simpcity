from pathlib import Path

from forum_orchestrator.state import State


def test_delta_sync_roundtrip(tmp_path: Path):
    db = State(tmp_path / "s.sqlite")
    db.upsert_thread("12345", "https://x/threads/y.12345", "Thread Y")

    assert not db.is_downloaded("12345", "key-a")
    db.record_download("12345", "key-a", "https://h/x.jpg", "x.jpg", "deadbeef", 100)
    assert db.is_downloaded("12345", "key-a")

    new = db.filter_new("12345", ["key-a", "key-b", "key-c"])
    assert new == {"key-b", "key-c"}

    assert db.has_sha("12345", "deadbeef")
    assert not db.has_sha("12345", "00")

    db.record_failure("12345", "https://dead/", "HTTP 404")
    fails = db.failures_for("12345")
    assert ("https://dead/", "HTTP 404") in fails
    db.close()
