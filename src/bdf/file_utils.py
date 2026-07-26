"""Utilities for reading file heads (first N bytes) from local paths and URLs.

Shared by metadata_parsers and table_parsers to avoid duplication.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

HEAD_BYTES = 65536  # large enough for long text preambles

_COMPRESS = {
    ".gz": "gzip",
    ".bz2": "bz2",
    ".xz": "xz",
    ".zst": "zstd",
}


def is_url(source: str) -> bool:
    """Return True if source is an http(s) URL."""
    try:
        u = urlparse(source)
        return u.scheme in ("http", "https") and bool(u.netloc)
    except Exception:
        return False


def _detect_compression(path: Path) -> str | None:
    """Return the compression codec implied by ``path``'s trailing extension.

    Args:
        path: File path whose string form is checked against :data:`_COMPRESS` suffixes.

    Returns:
        Compression codec name (e.g. "gzip"), or None if no known compression suffix matches.
    """
    s = str(path).lower()
    for ext, comp in _COMPRESS.items():
        if s.endswith(ext):
            return comp
    return None


def strip_compression_suffix(name: str) -> str:
    """Strip a single trailing compression extension from ``name``.

    (``.gz``/``.bz2``/``.xz``/``.zst``)

    Args:
        name: File name or path string, e.g. ``"data.bdf.json.gz"``.

    Returns:
        ``name`` with its trailing compression suffix removed, unchanged if it has none.
    """
    lower = name.lower()
    for ext in _COMPRESS:
        if lower.endswith(ext):
            return name[: -len(ext)]
    return name


def _decompress_to(src: Path, dest: Path, comp: str) -> None:
    """Write the decompressed contents of ``src`` (compressed via ``comp``) to ``dest``."""
    import shutil

    if comp == "gzip":
        import gzip

        with gzip.open(src, "rb") as fin, open(dest, "wb") as fout:
            shutil.copyfileobj(fin, fout)
    elif comp == "bz2":
        import bz2

        with bz2.open(src, "rb") as fin, open(dest, "wb") as fout:
            shutil.copyfileobj(fin, fout)
    elif comp == "xz":
        import lzma

        with lzma.open(src, "rb") as fin, open(dest, "wb") as fout:
            shutil.copyfileobj(fin, fout)
    elif comp == "zstd":  # zstd only added to stdlib in 3.14, pyarrow is already a dependency
        import pyarrow as pa

        with pa.input_stream(str(src), compression="zstd") as fin, open(dest, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        return
    else:
        raise ValueError(f"Unsupported compression: {comp}")


def _decompress(path: Path) -> Path:
    """Return a local ``Path`` to the decompressed contents of ``path``.

    Returns ``path`` unchanged if it has no recognized compression suffix.
    Decompressed output is cached under the bdf cache dir, keyed by the source
    path plus its mtime/size, to avoid repeated decompression.

    Args:
        path: Local file path, possibly compressed.

    Returns:
        Local ``Path`` to the decompressed file (same as ``path`` if not compressed).
    """
    comp = _detect_compression(path)
    if comp is None:
        return path

    from .fetch import cache_dir

    stat = path.stat()
    key = f"{path.resolve()}|{stat.st_mtime_ns}|{stat.st_size}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

    inner_name = path.name
    for ext, c in _COMPRESS.items():
        if c == comp and inner_name.lower().endswith(ext):
            inner_name = inner_name[: -len(ext)]
            break

    dest_dir = cache_dir("bdf") / "decompressed"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{digest}__{inner_name}"
    if dest.exists():
        return dest

    tmp_fd, tmp_name = tempfile.mkstemp(dir=dest_dir)
    os.close(tmp_fd)
    tmp = Path(tmp_name)
    _decompress_to(path, tmp, comp)
    tmp.replace(dest)
    return dest


def open_compressed(path: Path) -> Any:
    """Open ``path`` for binary writing through the ``comp`` compression codec.

    Args:
        path: Output file path.
        comp: Compression codec name, one of :data:`_COMPRESS`'s values.

    Returns:
        A writable binary file object; the caller is responsible for closing it.

    Raises:
        ValueError: If ``comp`` is not a supported codec.
    """
    comp = _detect_compression(path)
    if comp is None:
        return path
    if comp == "gzip":
        import gzip

        return gzip.open(path, "wb")
    if comp == "bz2":
        import bz2

        return bz2.open(path, "wb")
    if comp == "xz":
        import lzma

        return lzma.open(path, "wb")
    if comp == "zstd":  # zstd only added to stdlib in 3.14, pyarrow is already a dependency, can use that
        import pyarrow as pa

        return pa.output_stream(str(path), compression="zstd")
    raise ValueError(f"Unsupported compression: {comp}")


_BOM = b"\xef\xbb\xbf"


@lru_cache(maxsize=128)
def _read_head(source: str, n_bytes: int) -> bytes:
    """Cached core of read_head; both args must be hashable (str + int)."""
    local_path = resolve_source(source)
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
    """Resolve ``path`` to a local, decompressed ``Path``.

    Fetches http(s) URLs to a cached local copy
    Decompresses local files to a cached local copy (``.gz``/``.bz2``/``.xz``/``.zst``)

    Args:
        path: Local file path or http(s) URL.

    Returns:
        Local, decompressed ``Path`` to the file.
    """
    s = str(path)
    if is_url(s):
        from .fetch import fetch_url

        local = fetch_url(s)
    else:
        local = Path(path)
    return _decompress(local)
