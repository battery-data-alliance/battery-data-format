"""Unit tests for bdf.fetch utilities."""

from __future__ import annotations

from pathlib import Path

import pytest

import bdf.fetch as fetch
from bdf.fetch import _safe_cache_name, cache_dir, fetch_url


class TestSafeCacheName:
    """Tests for _safe_cache_name."""

    def test_explicit_filename_used_as_is(self) -> None:
        name = _safe_cache_name("https://example.com/data", "myfile.csv")
        assert name.endswith("__myfile.csv")

    def test_explicit_filename_overrides_url_ext(self) -> None:
        name = _safe_cache_name("https://example.com/data.nda/content", "override.csv")
        assert name.endswith("__override.csv")

    def test_extension_from_last_url_segment(self) -> None:
        name = _safe_cache_name("https://example.com/path/data.csv", None)
        assert name.endswith("__data.csv")

    def test_extension_found_by_walking_right_to_left(self) -> None:
        # Zenodo-style: real filename buried, then /content appended
        name = _safe_cache_name(
            "https://zenodo.org/api/records/123/files/SINTEF__Neware.nda/content",
            None,
        )
        assert name.endswith("__SINTEF__Neware.nda")

    def test_query_string_stripped_before_walking(self) -> None:
        name = _safe_cache_name("https://example.com/files/data.csv/download?token=abc", None)
        assert name.endswith("__data.csv")

    def test_hash_prefix_ensures_uniqueness(self) -> None:
        name_a = _safe_cache_name("https://host-a.com/data.csv", None)
        name_b = _safe_cache_name("https://host-b.com/data.csv", None)
        assert name_a != name_b
        assert name_a.endswith("__data.csv")
        assert name_b.endswith("__data.csv")

    def test_no_extension_anywhere_warns(self) -> None:
        with pytest.warns(UserWarning, match="Cannot determine file type"):
            name = _safe_cache_name("https://example.com/api/data", None)
        assert name.endswith("__file")

    def test_no_extension_warning_message_mentions_filename_param(self) -> None:
        with pytest.warns(UserWarning, match="filename="):
            _safe_cache_name("https://example.com/api/v1/resource", None)


class TestCacheDir:
    """Tests for cache_dir's BDF_CACHE_DIR override."""

    def test_override_honoured_when_set(self, tmp_path: Path, monkeypatch) -> None:
        override = tmp_path / "cache"
        monkeypatch.setenv("BDF_CACHE_DIR", str(override))
        assert cache_dir() == override
        assert override.is_dir()

    def test_fallback_when_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("BDF_CACHE_DIR", raising=False)
        monkeypatch.setattr(fetch, "user_cache_dir", lambda subdir: "/tmp/bdf-fallback-xyz")
        assert cache_dir("bdf") == Path("/tmp/bdf-fallback-xyz")

    def test_fallback_when_empty(self, monkeypatch) -> None:
        monkeypatch.setenv("BDF_CACHE_DIR", "")
        monkeypatch.setattr(fetch, "user_cache_dir", lambda subdir: "/tmp/bdf-fallback-xyz")
        assert cache_dir("bdf") == Path("/tmp/bdf-fallback-xyz")

    def test_cache_reuse_no_redownload(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("BDF_CACHE_DIR", str(tmp_path))
        url = "https://example.com/data.csv"

        calls: list[str] = []

        def fake_download(u: str, dest: Path, timeout: int = 120) -> None:
            calls.append(u)
            dest.write_bytes(b"col\n1\n")

        monkeypatch.setattr(fetch, "_download", fake_download)

        first = fetch_url(url)
        assert first.exists()
        assert calls == [url]

        second = fetch_url(url)
        assert second == first
        assert calls == [url]  # cache hit -> _download not called again
