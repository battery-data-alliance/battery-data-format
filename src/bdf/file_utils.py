"""Utilities for reading file heads (first N bytes) from local paths and URLs.

Shared by metadata_parsers and table_parsers to avoid duplication.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

HEAD_BYTES = 65536  # large enough for long text preambles


def is_url(source: str) -> bool:
    """Return True if source is an http(s) URL."""
    try:
        u = urlparse(source)
        return u.scheme in ("http", "https") and bool(u.netloc)
    except Exception:
        return False


_BOM = b"\xef\xbb\xbf"


@lru_cache(maxsize=128)
def _read_head(source: str, n_bytes: int) -> bytes:
    """Cached core of read_head; both args must be hashable (str + int)."""
    if is_url(source):
        from .fetch import fetch_url

        local_path = fetch_url(source)
    else:
        local_path = Path(source)
    with open(local_path, "rb") as fh:
        chunk = fh.read(n_bytes + len(_BOM))
    return chunk.removeprefix(_BOM)[:n_bytes]


def read_head(source: str | Path, n_bytes: int = HEAD_BYTES) -> bytes:
    """Return the first ``n_bytes`` bytes of ``source`` (local path or http(s) URL).

    Results are cached in-process via ``lru_cache``; URL sources are resolved
    to a local disk-cached file via ``fetch_url`` before reading.

    Args:
        source: Local file path or http(s) URL.
        n_bytes: Maximum number of bytes to read.

    Returns:
        First ``n_bytes`` bytes of the file, BOM-stripped.
    """
    return _read_head(str(source), n_bytes)


def resolve_source(path: str | Path) -> Path:
    """Resolve ``path`` to a local ``Path``, fetching and caching http(s) URLs.

    Args:
        path: Local file path or http(s) URL.

    Returns:
        Local ``Path`` to the file (downloaded via ``fetch_url`` if needed).
    """
    s = str(path)
    if is_url(s):
        from .fetch import fetch_url

        return fetch_url(s)
    return Path(path)
