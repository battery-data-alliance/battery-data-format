#!/usr/bin/env python
"""Prime the bdf fetch disk cache for the integration suite's URL cases.

Run once (no matrix) in CI before the network-marked integration jobs fan out,
so every matrix entry restores the same primed ``actions/cache`` and downloads
nothing.

Usage::

    python scripts/warm_cache.py            # sequential fetch into the cache
    python scripts/warm_cache.py --emit-key # print URL-set hash, no network

Honours ``BDF_CACHE_DIR`` (via ``bdf.fetch.cache_dir``) so the cache path is
deterministic and shareable with ``actions/cache``.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

# Fail the warm if the primed cache grows beyond this, to stay clear of
# GitHub's 10 GB per-repo ``actions/cache`` limit.
_MAX_CACHE_BYTES = 4 * 1024**3  # 4 GiB

# Make the repo root importable so ``tests.integration`` resolves when this
# script is run as ``python scripts/warm_cache.py``.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bdf import fetch  # noqa: E402
from bdf.spec import ColumnOntology, _ontology_cache_dir  # noqa: E402
from docs.examples.remote_sources import PINNED_ONTOLOGY_VERSIONS, REMOTE_DATA_SOURCES  # noqa: E402
from tests.integration.test_cases import ALL_CASES  # noqa: E402


def url_sources() -> list[str]:
    """Return the sorted, de-duplicated remote URLs the integration suite fetches.

    Combines the integration ``ALL_CASES`` URL sources with the example
    notebooks' declared remote data sources (``docs/examples/remote_sources.py``), so
    warming covers both the parser/detection cases and the network-marked
    notebook tests.

    Returns:
        Sorted list of unique URL strings.
    """
    urls = {case.source for _id, case in ALL_CASES if case.is_url}
    urls |= set(REMOTE_DATA_SOURCES.values())
    return sorted(urls)


def emit_key(urls: list[str]) -> str:
    """Compute the content-addressed cache key for the URL set and pinned ontology versions.

    Hashes the sorted, newline-joined URL list plus the pinned ontology version
    tags with SHA-256 — the same algorithm ``fetch._safe_cache_name`` uses on
    individual URLs. Performs no network.

    Args:
        urls: Sorted list of URL strings.

    Returns:
        The hex SHA-256 digest of the joined URL list and ontology versions.
    """
    joined = "\n".join(urls + [f"ontology-version:{v}" for v in PINNED_ONTOLOGY_VERSIONS])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def warm(urls: list[str], max_bytes: int = _MAX_CACHE_BYTES) -> None:
    """Fetch each URL sequentially into the cache, printing resolved paths.

    Exits non-zero if any fetch raises, or if the cumulative size of the
    fetched files exceeds ``max_bytes`` — a guard against approaching GitHub's
    10 GB ``actions/cache`` limit.

    Args:
        urls: Sorted list of URL strings to fetch.
        max_bytes: Cumulative byte ceiling for the warmed cache.
    """
    total = 0
    for url in urls:
        try:
            path = fetch.fetch_url(url)
        except Exception as e:  # noqa: BLE001
            sys.exit(f"ERROR: fetch failed for {url}: {e}")
        total += Path(path).stat().st_size
        print(f"  cached: {url} -> {path}")
        if total > max_bytes:
            sys.exit(
                f"ERROR: warmed cache reached {total / 1024**3:.2f} GiB, "
                f"exceeding the {max_bytes / 1024**3:.2f} GiB limit "
                f"(after fetching {url})."
            )


def warm_ontology_versions(versions: tuple[str, ...], max_bytes: int = _MAX_CACHE_BYTES) -> int:
    """Fetch each pinned ontology release into the versioned ontology cache.

    Mirrors ``ColumnOntology.load_version``'s cache layout (``bdf-ontology-v{version}.ttl``
    under ``_ontology_cache_dir()``) so the notebook's ``load_version`` call finds the
    file already cached and makes no network request.

    Args:
        versions: Ontology release tags to fetch (e.g. ``("1.1.0",)``).
        max_bytes: Cumulative byte ceiling, shared with ``warm``'s URL total.

    Returns:
        Total bytes written across all fetched versions.
    """
    cache_dir = _ontology_cache_dir()
    total = 0
    for version in versions:
        dest = cache_dir / f"bdf-ontology-v{version}.ttl"
        try:
            ColumnOntology.get_snapshot(dest=dest, version=version)
        except Exception as e:  # noqa: BLE001
            sys.exit(f"ERROR: ontology fetch failed for version {version!r}: {e}")
        total += dest.stat().st_size
        print(f"  cached: ontology release {version} -> {dest}")
        if total > max_bytes:
            sys.exit(
                f"ERROR: warmed ontology cache reached {total / 1024**3:.2f} GiB, "
                f"exceeding the {max_bytes / 1024**3:.2f} GiB limit "
                f"(after fetching version {version})."
            )
    return total


def main(argv: list[str] | None = None) -> None:
    """Entry point.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).
    """
    args = sys.argv[1:] if argv is None else argv
    urls = url_sources()

    if "--emit-key" in args:
        print(emit_key(urls))
        return

    print(f"Warming cache for {len(urls)} URL(s) into {fetch.cache_dir()}")
    warmed_bytes = 0
    warm(urls, max_bytes=_MAX_CACHE_BYTES)
    warmed_bytes += warm_ontology_versions(PINNED_ONTOLOGY_VERSIONS, max_bytes=_MAX_CACHE_BYTES - warmed_bytes)
    print("Cache warm complete.")


if __name__ == "__main__":
    main()
