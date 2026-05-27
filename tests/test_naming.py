from datetime import date
from pathlib import Path

from forum_orchestrator.naming import (
    build_filename,
    date_prefix,
    sanitize_segment,
    thread_dir,
    unique_path,
)


def test_sanitize_strips_invalid_chars():
    assert sanitize_segment('foo<bar>:baz?.txt') == 'foo-bar--baz-.txt'


def test_sanitize_strips_trailing_dot_and_space():
    assert sanitize_segment("hello . ") == "hello"


def test_sanitize_handles_reserved_names():
    assert sanitize_segment("CON").startswith("_")
    assert sanitize_segment("nul.txt").startswith("_")


def test_date_prefix_formats():
    assert date_prefix(date(2025, 11, 14)) == "[2025-11-14] "
    assert date_prefix(None) == ""


def test_build_filename():
    assert build_filename("photo.jpg", date(2025, 1, 2)) == "[2025-01-02] photo.jpg"


def test_unique_path_suffixes(tmp_path: Path):
    (tmp_path / "x.bin").write_bytes(b"")
    p = unique_path(tmp_path, "x.bin")
    assert p.name == "x (2).bin"
    p.write_bytes(b"")
    p = unique_path(tmp_path, "x.bin")
    assert p.name == "x (3).bin"


def test_thread_dir(tmp_path: Path):
    d = thread_dir(tmp_path, 'Hot/Thread?', '999')
    assert d.parent == tmp_path
    assert "Hot-Thread-" in d.name
    assert "(999)" in d.name
