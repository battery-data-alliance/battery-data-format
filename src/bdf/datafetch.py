from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List
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
# Registry loading (YOUR structure)
# -------------------------------

def _find_repo_root(markers=("pyproject.toml", ".git"), max_up: int = 8) -> Path:
    p = Path.cwd().resolve()
    for _ in range(max_up):
        if any((p / m).exists() for m in markers):
            return p
        p = p.parent
    return Path.cwd().resolve()

def load_registry(path: Optional[str | Path] = None) -> Dict[str, Any]:
    """
    Load datasets registry shaped as:
      vendor -> chemistry -> test_type -> variant -> { entry }
    Default location: <repo-root>/data/datasets.json
    Alternate: set env BDF_DATASETS=... or pass a path explicitly.
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

@dataclass
class DatasetEntry:
    name: str
    url: str
    plugin: Optional[str]
    format: Optional[str]
    is_bdf: bool = False
    license: Optional[str] = None
    sha256: Optional[str] = None
    filename: Optional[str] = None
    notes: Optional[str] = None

def _get_ci(d: Dict[str, Any], key: str) -> Any:
    """Case-insensitive dict access."""
    key_l = key.lower()
    for k, v in d.items():
        if k.lower() == key_l:
            return v
    raise KeyError(f"Key not found (case-insensitive): {key}")

def list_registry_entries(reg: Dict[str, Any]) -> List[Tuple[str, str, str, str, str]]:
    """
    Flatten into rows:
      (vendor, chemistry, test_type, variant, name)
    """
    rows: List[Tuple[str, str, str, str, str]] = []
    for vendor, d1 in reg.items():
        for chemistry, d2 in d1.items():
            for test_type, d3 in d2.items():
                for variant, entry in d3.items():
                    nm = entry.get("name") or ""
                    rows.append((vendor, chemistry, test_type, variant, nm))
    return rows

def get_entry(
    reg: Dict[str, Any],
    vendor: str,
    chemistry: str,
    test_type: str,
    variant: str,
) -> DatasetEntry:
    """
    Access a single entry at vendor/chemistry/test_type/variant (all case-insensitive).
    Your JSON stores a single object (not a list) at the leaf.
    """
    e = _get_ci(_get_ci(_get_ci(_get_ci(reg, vendor), chemistry), test_type), variant)
    return DatasetEntry(
        name=e.get("name") or "",
        url=e["url"],
        plugin=e.get("plugin"),
        format=e.get("format"),
        is_bdf=bool(e.get("is_bdf", False)),
        license=e.get("license"),
        sha256=e.get("sha256"),
        filename=e.get("filename"),
        notes=e.get("notes"),
    )

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
