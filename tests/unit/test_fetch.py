"""Unit tests for bdf.fetch utilities."""

from __future__ import annotations

import pytest

from bdf.fetch import _safe_cache_name


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
