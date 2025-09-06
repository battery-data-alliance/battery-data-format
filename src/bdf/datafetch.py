# src/bdf/datafetch.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List, Iterable, Union
import os, json, hashlib, tempfile

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

def fetch_url(
    url: str,
    *,
    sha256: Optional[str] = None,
    filename: Optional[str] = None,
    cache_subdir: str = "bdf",
    timeout: int = 120,
) -> Path:
    """
    Download a file with caching and optional SHA256 verification.
    Returns a cached Path.
    """
    cache_dir = Path(user_cache_dir(cache_subdir))
    cache_dir.mkdir(parents=True, exist_ok=True)
    name = filename or Path(url.split("?")[0]).name
    dest = cache_dir / name

    # Use cache if present and verified
    if dest.exists() and (not sha256 or sha256_file(dest).lower() == sha256.lower()):
        return dest

    # Download to temp and atomically move
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    tmp.write(chunk)
            tmp_path = Path(tmp.name)

    if sha256:
        got = sha256_file(tmp_path)
        if got.lower() != sha256.lower():
            tmp_path.unlink(missing_ok=True)
            raise ValueError(f"SHA256 mismatch: got {got}, want {sha256}")

    tmp_path.replace(dest)
    return dest

# -------------------------------
# Registry loading (FLAT structure)
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
    Load datasets registry shaped as:
      { "schema_version": "0.2", "datasets": [ { id, name, vendor, format, plugin, url, tags, ... } ] }
    Default: <repo-root>/data/datasets.json
    Or set env BDF_DATASETS=/path/to/datasets.json
    """
    # explicit path wins
    if path:
        p = Path(path)
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    # env var
    env = os.getenv("BDF_DATASETS")
    if env:
        p = Path(env)
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)

    # repo default
    root = _find_repo_root()
    default_path = root / "data" / "datasets.json"
    if default_path.exists():
        with open(default_path, "r", encoding="utf-8") as f:
            return json.load(f)

    raise FileNotFoundError("datasets.json not found. Provide path or set BDF_DATASETS.")

# -------------------------------
# Model & helpers
# -------------------------------

@dataclass
class DatasetEntry:
    # Essentials (flat registry)
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
    if isinstance(reg, dict) and "datasets" in reg and isinstance(reg["datasets"], list):
        for d in reg["datasets"]:
            if isinstance(d, dict):
                yield d
    else:
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
        if tags_lc:
            if not tags_lc.issubset(_set_ci(d.get("tags") or [])):
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
    - Back-compat (maps remaining args to tags):
        get_entry(reg, "landt", "li-graphite", "cycling", "none")

    Returns the first matching entry; raises ValueError if none or ambiguous (2+ matches).
    """
    # Back-compat path: (vendor, tag1, tag2, tag3)
    if len(args) == 4:
        vendor = args[0]
        tags = [args[1], args[2], args[3]]
    elif len(args) == 1 and not (id or name or vendor or format or plugin or tags):
        # Single positional: treat as ID first, then fallback to name
        key = args[0]
        matches = find_datasets(reg, id=key)
        if not matches:
            matches = find_datasets(reg, name=key)
        if not matches:
            raise ValueError(f"No dataset matched id or name: {key!r}")
        if len(matches) > 1:
            raise ValueError(f"Ambiguous match for {key!r}; refine your filters.")
        return matches[0]
    elif len(args) != 0:
        raise TypeError("get_entry expects either 0, 1, or 4 positional arguments.")

    matches = find_datasets(reg, id=id, name=name, vendor=vendor, format=format, plugin=plugin, tags=tags)
    if not matches:
        raise ValueError(f"No dataset matched filters: "
                         f"id={id!r}, name={name!r}, vendor={vendor!r}, format={format!r}, "
                         f"plugin={plugin!r}, tags={list(tags or [])!r}")
    if len(matches) > 1:
        # deterministic but nudge the user to refine
        raise ValueError(f"Multiple datasets matched; please refine filters. "
                         f"First few ids: {[m.id for m in matches[:5]]}")
    return matches[0]

# -------------------------------
# High-level loader
# -------------------------------

def load_bdf_from_entry(entry: DatasetEntry):
    """
    Fetch the file (cached), then:
      - if entry.is_bdf: load via bdf.io.load
      - else: auto-detect → parse → normalize (honoring entry.plugin if provided)
    Returns (local_path, df_bdf).
    """
    path = fetch_url(entry.url, sha256=entry.sha256, filename=entry.filename)

    if entry.is_bdf:
        from .io import load as load_bdf  # lazy import avoids cycles
        df = load_bdf(path)
    else:
        from .detect import load_plugin
        from .normalize import to_bdf
        plugin = load_plugin(path, as_=entry.plugin)
        df_vendor = plugin.parse(path)
        df = to_bdf(df_vendor, plugin_id=plugin.id)

    return path, df
