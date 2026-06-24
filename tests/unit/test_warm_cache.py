"""Unit tests for ``scripts/warm_cache.py``.

The warm-cache contract has two halves the CI matrix depends on:

* ``url_sources`` must enumerate every remote file the network jobs fetch — the
  union of the integration ``ALL_CASES`` URLs and the notebooks'
  ``REMOTE_DATA_SOURCES`` — sorted and de-duplicated.
* ``emit_key`` must hash that set (plus the pinned ontology versions)
  deterministically and identically to the ``sha256`` of the newline-joined
  URLs and version tags, because the ``actions/cache`` key (and
  ``fetch._safe_cache_name``) are content-addressed on those exact byte strings.

These tests pin both for a couple of known Zenodo files so a silent drift in the
URL set or the key algorithm is caught in unit tests before any network
fetch runs. They perform no network access.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from docs.examples.remote_sources import PINNED_ONTOLOGY_VERSIONS, REMOTE_DATA_SOURCES  # noqa: E402
from scripts import warm_cache  # noqa: E402
from scripts.warm_cache import emit_key, url_sources, warm  # noqa: E402
from tests.integration.test_cases import ALL_CASES  # noqa: E402

# A couple of known files in the Zenodo reference record (18986774). Pinned here
# so a rename in either ``ALL_CASES`` or ``REMOTE_DATA_SOURCES`` — which would
# silently rotate the cache key and re-download — fails this test loudly.
_KNOWN_ZENODO_URLS = (
    "https://zenodo.org/api/records/18986774/files/FZJ__INR21700__20250606__HPPC__25degC__Digatron.csv/content",
    "https://zenodo.org/api/records/18986774/files/DLR__LiLNMOHydra0b__20221130__GITT__25degC__Basytec.txt/content",
)


class TestUrlSources:
    """Tests for ``url_sources`` set membership and shape."""

    def test_sorted_and_deduplicated(self) -> None:
        urls = url_sources()
        assert urls == sorted(set(urls))

    def test_includes_all_case_urls(self) -> None:
        urls = set(url_sources())
        case_urls = {case.source for _id, case in ALL_CASES if case.is_url}
        assert case_urls <= urls

    def test_includes_all_remote_data_sources(self) -> None:
        urls = set(url_sources())
        assert set(REMOTE_DATA_SOURCES.values()) <= urls

    def test_includes_known_zenodo_files(self) -> None:
        urls = set(url_sources())
        for known in _KNOWN_ZENODO_URLS:
            assert known in urls

    def test_union_dedupes_overlap(self) -> None:
        # ALL_CASES and REMOTE_DATA_SOURCES intentionally overlap; the union must
        # not duplicate the shared URLs.
        urls = url_sources()
        assert len(urls) == len(set(urls))
        overlap = {case.source for _id, case in ALL_CASES if case.is_url} & set(REMOTE_DATA_SOURCES.values())
        assert overlap, "expected at least one shared URL to exercise de-duplication"


class TestEmitKey:
    """Tests for the content-addressed cache key."""

    def test_matches_manual_sha256_of_joined_urls(self) -> None:
        urls = url_sources()
        joined = "\n".join(urls + [f"ontology-version:{v}" for v in PINNED_ONTOLOGY_VERSIONS])
        expected = hashlib.sha256(joined.encode("utf-8")).hexdigest()
        assert emit_key(urls) == expected

    def test_deterministic_across_calls(self) -> None:
        assert emit_key(url_sources()) == emit_key(url_sources())

    def test_order_independent_given_sorted_input(self) -> None:
        # url_sources sorts, so re-sorting a shuffled copy reproduces the key —
        # the property the CI cache relies on for a stable restore.
        urls = url_sources()
        shuffled = sorted(reversed(urls))
        assert emit_key(shuffled) == emit_key(urls)

    def test_changes_when_url_added(self) -> None:
        urls = url_sources()
        changed = sorted([*urls, "https://example.com/new-file.csv"])
        assert emit_key(changed) != emit_key(urls)


class TestWarmSizeGuard:
    """Tests for the cumulative cache-size ceiling enforced by ``warm``.

    ``warm`` must abort (non-zero exit) once the total bytes of the fetched
    files exceed its ``max_bytes`` ceiling, so a runaway data set fails the CI
    warm step before the oversized cache is ever saved to ``actions/cache``.
    """

    @staticmethod
    def _fake_fetch(monkeypatch, tmp_path: Path, size_per_file: int):
        """Patch ``fetch.fetch_url`` to write a file of ``size_per_file`` bytes.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            tmp_path: Directory to write the fake cached files into.
            size_per_file: Byte length of each written file.
        """

        def _fetch(url: str, *args, **kwargs) -> Path:
            dest = tmp_path / warm_cache.fetch._safe_cache_name(url, None)
            dest.write_bytes(b"\0" * size_per_file)
            return dest

        monkeypatch.setattr(warm_cache.fetch, "fetch_url", _fetch)

    def test_passes_when_total_under_limit(self, monkeypatch, tmp_path) -> None:
        self._fake_fetch(monkeypatch, tmp_path, size_per_file=10)
        urls = ["https://example.com/a.csv", "https://example.com/b.csv"]
        # 20 bytes total, limit 100 -> no exit.
        warm(urls, max_bytes=100)

    def test_exits_when_total_exceeds_limit(self, monkeypatch, tmp_path) -> None:
        self._fake_fetch(monkeypatch, tmp_path, size_per_file=60)
        urls = ["https://example.com/a.csv", "https://example.com/b.csv"]
        # First file 60 <= 100, second pushes total to 120 > 100 -> exit.
        with pytest.raises(SystemExit) as exc:
            warm(urls, max_bytes=100)
        assert "exceeding" in str(exc.value)

    def test_exits_on_first_oversized_file(self, monkeypatch, tmp_path) -> None:
        self._fake_fetch(monkeypatch, tmp_path, size_per_file=200)
        with pytest.raises(SystemExit):
            warm(["https://example.com/big.csv"], max_bytes=100)
