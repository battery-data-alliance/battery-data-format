"""Unit tests for bdf.head_utils utilities (URL detection, head reading)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bdf.head_utils import is_url, read_head

# ---------------------------------------------------------------------------
# Zenodo URLs (record 18214281)
# ---------------------------------------------------------------------------

_ZENODO_BASE = "https://zenodo.org/api/records/18214281/files"
_ZENODO_BASYTEC_URL = f"{_ZENODO_BASE}/DLR__LiLNMOHydra0b__20221130__GITT__25degC__Basytec.txt/content"
_ZENODO_BIOLOGIC_URL = f"{_ZENODO_BASE}/SINTEF__NaCR32140-MP10-04__2025-08-25__GITT_0p05C_25degC__BioLogic.mpt/content"


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


@pytest.mark.network
@pytest.mark.parametrize(
    "url,expected_content",
    [
        (_ZENODO_BASYTEC_URL, b"Basytec"),
        (_ZENODO_BIOLOGIC_URL, b"BT-Lab"),
    ],
    ids=["basytec", "biologic"],
)
def test_read_head_url(url: str, expected_content: bytes) -> None:
    """read_head reads bytes from a remote URL."""
    pytest.importorskip("requests")
    head = read_head(url, n_bytes=4096)
    assert len(head) == 4096
    assert expected_content in head
