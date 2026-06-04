"""Utilities for reading file heads (first N bytes) from local paths and URLs.

Shared by metadata_parsers and table_parsers to avoid duplication.
"""

from __future__ import annotations

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


def read_url_head(url: str, n_bytes: int = HEAD_BYTES) -> bytes:
    """Return the first ``n_bytes`` bytes of an http(s) ``url`` via streaming GET.

    Exposed as a named helper so it can be tested independently.
    """
    import requests

    resp = requests.get(url, stream=True, timeout=30)
    if not resp.ok:
        raise ValueError(f"HTTP {resp.status_code} reading head from {url}")
    data = b""
    for chunk in resp.iter_content(chunk_size=8192):
        data += chunk
        if len(data) >= n_bytes:
            break
    return data[:n_bytes].removeprefix(b"\xef\xbb\xbf")


def read_head(source: str | Path, n_bytes: int = HEAD_BYTES) -> bytes:
    """Return the first ``n_bytes`` bytes of ``source`` (local path or http(s) URL)."""
    if is_url(str(source)):
        return read_url_head(str(source), n_bytes)
    with open(source, "rb") as fh:
        return fh.read(n_bytes).removeprefix(b"\xef\xbb\xbf")


__all__ = [
    "HEAD_BYTES",
    "is_url",
    "read_url_head",
    "read_head",
]
