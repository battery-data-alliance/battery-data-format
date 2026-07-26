# src/bdf/fetch.py
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import warnings

# mypy: ignore-errors
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import requests
from platformdirs import user_cache_dir

# -------------------------------
# Utilities
# -------------------------------


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):  # 1 MiB
            h.update(chunk)
    return h.hexdigest()


def cache_dir(subdir: str = "bdf") -> Path:
    """Return the cache directory for ``subdir``, honouring ``BDF_CACHE_DIR``.

    Args:
        subdir: Cache subdirectory name, used as the platformdirs fallback
            app name when ``BDF_CACHE_DIR`` is unset.

    Returns:
        Path to the cache directory, created if missing.
    """
    base = os.getenv("BDF_CACHE_DIR")
    p = Path(base) if base else Path(user_cache_dir(subdir))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_cache_name(url: str, filename: Optional[str]) -> str:
    """Ensure uniqueness across different URLs that share the same basename.

    Walks URL path segments right-to-left to find a meaningful filename with
    extension (handles URLs like `.../file.csv/content`). Issues a warning
    if no extension is found and ``filename`` is not provided.
    """
    if filename:
        base = filename
    else:
        from urllib.parse import urlparse

        url_path = urlparse(url).path
        segments = [s for s in url_path.split("/") if s]
        base = None
        for segment in reversed(segments):
            if Path(segment).suffix:
                base = segment
                break
        if base is None:
            warnings.warn(
                f"Cannot determine file type from URL {url!r}. "
                "Provide the filename= parameter to fetch_url for better caching, "
                "or specify a plugin explicitly.",
                UserWarning,
                stacklevel=3,
            )
            base = "file"
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return f"{h}__{base}"


def _download(url: str, dest: Path, timeout: int = 120) -> None:
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    tmp.write(chunk)
            tmp_path = Path(tmp.name)
    tmp_path.replace(dest)


def fetch_url(
    url: str,
    *,
    sha256: Optional[str] = None,
    filename: Optional[str] = None,
    refresh: bool = False,
    cache_subdir: str = "bdf",
    timeout: int = 120,
    alt_urls: Optional[List[str]] = None,
    retries: int = 1,
) -> Path:
    """
    Download a file with caching and optional SHA256 verification.
    Returns a cached Path.

    - Uses a content-addressed cache name: "<sha12(url)>__<basename>"
    - Tries alt_urls[] if the primary download fails.
    - 'retries' = extra attempts on transient network errors (per URL).
    - 'refresh' forces a re-download even if cached.
    """
    cdir = cache_dir(cache_subdir)
    name = _safe_cache_name(url, filename)
    dest = cdir / name

    # Use cache if present and verified
    if dest.exists():
        if refresh:
            dest.unlink(missing_ok=True)
        elif not sha256 or sha256_file(dest).lower() == sha256.lower():
            return dest
        else:
            # bad hash -> drop cache and redownload
            dest.unlink(missing_ok=True)

    candidates = [url] + list(alt_urls or [])
    last_err: Optional[Exception] = None

    for u in candidates:
        # try with simple linear retries
        for attempt in range(retries + 1):
            try:
                _download(u, dest, timeout=timeout)
                if sha256:
                    got = sha256_file(dest)
                    if got.lower() != sha256.lower():
                        dest.unlink(missing_ok=True)
                        raise ValueError(f"SHA256 mismatch for {u}: got {got}, want {sha256}")
                return dest
            except Exception as e:
                last_err = e
                # A blocked socket (CI with --block-cached-sockets on a cache
                # miss) is not transient: fail fast instead of burning the
                # retry backoff. Match by name to avoid a pytest_socket import.
                if type(e).__name__ == "SocketBlockedError":
                    raise
                # small backoff on transient failures
                time.sleep(0.8 * (attempt + 1))
        # try next alt URL

    # Exhausted all attempts/URLs
    if last_err:
        raise last_err
    raise RuntimeError("Download failed for unknown reasons.")


# -------------------------------
# Registry loading (accepts either {"datasets":[...]} or {"entries":[...]})
# -------------------------------


def _find_repo_root(markers=("pyproject.toml", ".git"), max_up: int = 8) -> Path:
    p = Path.cwd().resolve()
    for _ in range(max_up):
        if any((p / m).exists() for m in markers):
            return p
        p = p.parent
    return Path.cwd().resolve()


def load_registry(path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """
    Load datasets registry.

    Accepted shapes:
      { "schema_version": "0.2", "datasets": [ { ... } ] }
      { "schema_version": "0.3", "entries":  [ { ... } ] }
    Default lookup order:
      1) explicit 'path'
      2) env BDF_DATASETS
      3) <repo-root>/data/datasets.json
    """
    # explicit path wins
    if path:
        p = Path(path)
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    # env var
    env = os.getenv("BDF_DATASETS")
    if env:
        p = Path(env)
        if p.exists():
            with open(p, encoding="utf-8") as f:
                return json.load(f)

    # repo default
    root = _find_repo_root()
    default_path = root / "data" / "datasets.json"
    if default_path.exists():
        with open(default_path, encoding="utf-8") as f:
            return json.load(f)

    raise FileNotFoundError("datasets.json not found. Provide path or set BDF_DATASETS.")


# -------------------------------
# Model & helpers
# -------------------------------


@dataclass
class DatasetEntry:
    # Essentials
    id: Optional[str] = None
    name: str = ""
    vendor: Optional[str] = None
    format: Optional[str] = None
    plugin: Optional[str] = None
    url: str = ""
    tags: List[str] = field(default_factory=list)

    # Nice-to-haves
    is_bdf: bool = False
    license: Optional[str] = None
    sha256: Optional[str] = None
    filename: Optional[str] = None
    encoding: Optional[str] = None
    alt_urls: List[str] = field(default_factory=list)
    notes: Optional[str] = None


def _ci_eq(a: Optional[str], b: Optional[str]) -> bool:
    return (a or "").lower() == (b or "").lower()


def _set_ci(elems: Iterable[str]) -> set:
    return {str(x).lower() for x in elems}


def _iter_dataset_dicts(reg: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """Yield each dataset dict from the registry."""
    if isinstance(reg, dict):
        if isinstance(reg.get("datasets"), list):
            yield from (d for d in reg["datasets"] if isinstance(d, dict))
            return
        if isinstance(reg.get("entries"), list):
            yield from (d for d in reg["entries"] if isinstance(d, dict))
            return
    # Graceful fail: if someone passes just a list
    if isinstance(reg, list):
        for d in reg:
            if isinstance(d, dict):
                yield d


def _coerce_entry(d: Dict[str, Any]) -> DatasetEntry:
    return DatasetEntry(
        id=d.get("id"),
        name=d.get("name") or "",
        vendor=d.get("vendor"),
        format=d.get("format"),
        plugin=d.get("plugin"),
        url=d.get("url") or "",
        tags=list(d.get("tags") or []),
        is_bdf=bool(d.get("is_bdf", False)),
        license=d.get("license"),
        sha256=d.get("sha256"),
        filename=d.get("filename"),
        encoding=d.get("encoding"),
        alt_urls=list(d.get("alt_urls") or []),
        notes=d.get("notes"),
    )


def list_registry_entries(reg: Dict[str, Any]) -> List[Tuple[str, str, str, str, str]]:
    """
    Flatten into rows:
      (id, vendor, format, 'tag1 tag2 ...', name)
    """
    rows: List[Tuple[str, str, str, str, str]] = []
    for d in _iter_dataset_dicts(reg):
        eid = str(d.get("id") or "")
        vendor = str(d.get("vendor") or "")
        fmt = str(d.get("format") or "")
        tags = " ".join(d.get("tags") or [])
        nm = str(d.get("name") or "")
        rows.append((eid, vendor, fmt, tags, nm))
    return rows


def find_datasets(
    reg: Dict[str, Any],
    *,
    id: Optional[str] = None,
    name: Optional[str] = None,
    vendor: Optional[str] = None,
    format: Optional[str] = None,
    plugin: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
) -> List[DatasetEntry]:
    """
    Filter datasets by simple fields. Tags must all be present if provided.
    Case-insensitive for strings.
    """
    tags_lc = _set_ci(tags or [])
    out: List[DatasetEntry] = []
    for d in _iter_dataset_dicts(reg):
        if id and not _ci_eq(d.get("id"), id):
            continue
        if name and not _ci_eq(d.get("name"), name):
            continue
        if vendor and not _ci_eq(d.get("vendor"), vendor):
            continue
        if format and not _ci_eq(d.get("format"), format):
            continue
        if plugin and not _ci_eq(d.get("plugin"), plugin):
            continue
        if tags_lc and not tags_lc.issubset(_set_ci(d.get("tags") or [])):
            continue
        out.append(_coerce_entry(d))
    return out


def get_entry(
    reg: Dict[str, Any],
    *args: str,
    id: Optional[str] = None,
    name: Optional[str] = None,
    vendor: Optional[str] = None,
    format: Optional[str] = None,
    plugin: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
) -> DatasetEntry:
    """
    Flexible access:
    - get_entry(reg, "some-id")                    -> by id or name (case-insensitive)
    - get_entry(reg, vendor="landt", format="csv", tags=["li-graphite","cycling"])
    """
    if len(args) == 1 and not (id or name or vendor or format or plugin or tags):
        key = args[0]
        matches = find_datasets(reg, id=key) or find_datasets(reg, name=key)
        if not matches:
            raise ValueError(f"No dataset matched id or name: {key!r}")
        if len(matches) > 1:
            raise ValueError(f"Ambiguous match for {key!r}; refine your filters.")
        return matches[0]
    elif len(args) != 0:
        raise TypeError("get_entry expects either 0 or 1 positional arguments.")

    matches = find_datasets(reg, id=id, name=name, vendor=vendor, format=format, plugin=plugin, tags=tags)
    if not matches:
        raise ValueError("No dataset matched filters.")
    if len(matches) > 1:
        raise ValueError(
            f"Multiple datasets matched; please refine filters. First few ids: {[m.id for m in matches[:5]]}"
        )
    return matches[0]


# -------------------------------
# High-level loader (NEW API)
# -------------------------------


def load_bdf_from_entry(entry: DatasetEntry):
    """
    Fetch the file (cached), then:
      - if entry.is_bdf: load via bdf.io.load
      - else: call bdf.read(path, plugin=entry.plugin)
    Returns (local_path, df_bdf).
    """
    from . import read as read_bdf  # new API

    path = fetch_url(
        entry.url,
        sha256=entry.sha256,
        filename=entry.filename,
        alt_urls=entry.alt_urls or None,
    )

    df_pl, _meta = read_bdf(path, plugin=entry.plugin, validate=True)
    df = df_pl.to_pandas()

    return path, df
