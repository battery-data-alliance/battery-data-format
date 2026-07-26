"""Unit tests for bdf.file_utils utilities (URL detection, head reading)."""

from __future__ import annotations

import gzip
from pathlib import Path

from bdf.file_utils import _decompress, is_url, read_head

# ---------------------------------------------------------------------------
# is_url
# ---------------------------------------------------------------------------


def test_is_url_returns_true_for_https() -> None:
    """is_url returns True for valid https URLs."""
    assert is_url("https://example.com/file.txt") is True


def test_is_url_returns_true_for_http() -> None:
    """is_url returns True for valid http URLs."""
    assert is_url("http://example.com/file.txt") is True


def test_is_url_returns_false_for_file_path() -> None:
    """is_url returns False for local file paths."""
    assert is_url("/path/to/file.txt") is False
    assert is_url("file.txt") is False


def test_is_url_returns_false_for_ftp() -> None:
    """is_url returns False for non-http(s) schemes."""
    assert is_url("ftp://example.com/file.txt") is False


def test_is_url_returns_false_for_malformed() -> None:
    """is_url returns False for malformed URLs."""
    assert is_url("http://") is False
    assert is_url("https://") is False


# ---------------------------------------------------------------------------
# read_head
# ---------------------------------------------------------------------------


def test_read_head_local_file(tmp_path: Path) -> None:
    """read_head reads bytes from a local file."""
    p = tmp_path / "test.txt"
    p.write_text("hello world")
    head = read_head(p, n_bytes=5)
    assert head == b"hello"


def test_read_head_removes_bom(tmp_path: Path) -> None:
    """read_head strips UTF-8 BOM from local files."""
    p = tmp_path / "test.txt"
    p.write_bytes(b"\xef\xbb\xbfhello")
    head = read_head(p, n_bytes=10)
    assert head == b"hello"


# ---------------------------------------------------------------------------
# _decompress
# ---------------------------------------------------------------------------


def test_decompress_caching(tmp_path: Path, monkeypatch) -> None:
    """_decompress reuses the cached output on repeated calls for the same source."""
    monkeypatch.setenv("BDF_CACHE_DIR", str(tmp_path / "cache"))

    p = tmp_path / "test.csv.gz"
    with gzip.open(p, "wb") as f:
        f.write(b"hello world")

    first = _decompress(p)
    assert first.read_bytes() == b"hello world"
    mtime1 = first.stat().st_mtime

    second = _decompress(p)
    third = _decompress(p)

    assert second == first
    assert third == first
    assert second.stat().st_mtime == mtime1
    assert third.stat().st_mtime == mtime1
