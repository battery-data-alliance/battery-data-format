#!/usr/bin/env python
"""Prime the bdf fetch disk cache for the integration suite's URL cases.

Run once (no matrix) in CI before the network-marked integration jobs fan out,
so every matrix entry restores the same primed ``actions/cache`` and downloads
nothing.

Usage::

    python scripts/warm_cache.py            # sequential fetch into the cache
    python scripts/warm_cache.py --emit-key # print URL-set hash, no network

Honours ``BDF_CACHE_DIR`` (via ``bdf.fetch._cache_dir``) so the cache path is
deterministic and shareable with ``actions/cache``.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

# Make the repo root importable so ``tests.integration`` resolves when this
# script is run as ``python scripts/warm_cache.py``.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bdf import fetch  # noqa: E402
from examples.remote_sources import REMOTE_DATA_SOURCES  # noqa: E402
from tests.integration.test_cases import ALL_CASES  # noqa: E402


def url_sources() -> list[str]:
    """Return the sorted, de-duplicated remote URLs the integration suite fetches.

    Combines the integration ``ALL_CASES`` URL sources with the example
    notebooks' declared remote data sources (``examples/remote_sources.py``), so
    warming covers both the parser/detection cases and the network-marked
    notebook tests.

    Returns:
        Sorted list of unique URL strings.
    """
    urls = {case.source for _id, case in ALL_CASES if case.is_url}
    urls |= set(REMOTE_DATA_SOURCES.values())
    return sorted(urls)


def emit_key(urls: list[str]) -> str:
    """Compute the content-addressed cache key for the URL set.

    Hashes the sorted, newline-joined URL list with SHA-256 — the same
    algorithm ``fetch._safe_cache_name`` uses on individual URLs. Performs no
    network.

    Args:
        urls: Sorted list of URL strings.

    Returns:
        The hex SHA-256 digest of the joined URL list.
    """
    joined = "\n".join(urls)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def warm(urls: list[str]) -> None:
    """Fetch each URL sequentially into the cache, printing resolved paths.

    Exits non-zero if any fetch raises.

    Args:
        urls: Sorted list of URL strings to fetch.
    """
    for url in urls:
        try:
            path = fetch.fetch_url(url)
        except Exception as e:  # noqa: BLE001
            sys.exit(f"ERROR: fetch failed for {url}: {e}")
        print(f"  cached: {url} -> {path}")


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

    print(f"Warming cache for {len(urls)} URL(s) into {fetch._cache_dir()}")
    warm(urls)
    print("Cache warm complete.")


if __name__ == "__main__":
    main()
