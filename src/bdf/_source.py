# src/bdf/_source.py
"""Source resolution: local paths, URLs, dataset registry IDs."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


def _is_url(x: str) -> bool:
    try:
        u = urlparse(str(x))
        return u.scheme in ("http", "https") and bool(u.netloc)
    except Exception:
        return False


def _resolve_source(
    source: str | Path,
    *,
    registry_path: str | Path | None = None,
) -> tuple[Path, str | None]:
    """
    Return a local Path for the source and an optional plugin hint.
    Source may be: local path, http(s) URL, or dataset id from the registry.
    """
    s = str(source)

    # 1) existing file path
    p = Path(s)
    if p.exists():
        return p, None

    # 2) URL -> cache it
    if _is_url(s):
        from .fetch import fetch_url  # lazy
        path = fetch_url(s)
        return path, None

    # 3) dataset id from registry
    from ._registry import get_entry as _get_entry, load_registry as _load_registry  # lazy
    reg = _load_registry(registry_path)
    entry = _get_entry(reg, s)  # raises if not found/ambiguous
    url = entry["url"]
    plugin_hint = entry.get("plugin")
    sha256 = entry.get("sha256")
    filename = entry.get("filename")

    from .fetch import fetch_url  # lazy
    path = fetch_url(url, sha256=sha256, filename=filename)
    return path, plugin_hint


def _is_csv(path: Path) -> bool:
    s = "".join(path.suffixes).lower()
    return s.endswith(".csv") or s.endswith(".bdf.csv")


def _csv_header_has_bdf_required(path: Path) -> bool:
    """Quickly check if first row contains required BDF columns."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            header = f.readline().strip()
    except Exception:
        return False
    cols_l = {c.strip().lower() for c in header.split(",")}
    # import lazily to avoid cycles
    from .normalize import spec
    for q, s in spec.COLUMNS.items():
        if not s.get("required") or bool(s.get("deprecated")):
            continue
        pref = spec._label_for(q).lower()
        notation = spec.notation_for(q).lower()
        if pref not in cols_l and notation not in cols_l:
            return False
    return True


def _looks_like_bdf_artifact(path: Path) -> bool:
    """Return True if filename + header suggest this is a BDF file we should try to load."""
    sfx = "".join(path.suffixes).lower()
    # Parquet/Feather/JSON: accept outright if extension matches
    if sfx.endswith(".parquet") or sfx.endswith(".bdf.parquet"):
        return True
    if sfx.endswith(".feather") or sfx.endswith(".bdf.feather"):
        return True
    if sfx.endswith(".json") or sfx.endswith(".bdf.json"):
        return True
    # CSV: require either .bdf.csv OR BDF header row with required columns
    if _is_csv(path):
        if ".bdf.csv" in sfx:
            return True
        return _csv_header_has_bdf_required(path)
    return False


def _candidate_plugins(path: Path, *, plugin: str | None, plugin_hint: str | None):
    # Import via the package so monkeypatching bdf.load_plugin works in tests.
    import bdf as _bdf
    load_plugin = _bdf.load_plugin

    try:
        primary = load_plugin(path, plugin_id=(plugin or plugin_hint))
    except Exception:
        if plugin is not None:
            raise
        primary = load_plugin(path, plugin_id=None)
    if plugin is not None:
        return [primary]

    candidates = []
    seen: set[str] = set()

    def _push(plg) -> None:
        pid = str(getattr(plg, "id", "")).strip() or plg.__class__.__name__
        if pid in seen:
            return
        seen.add(pid)
        candidates.append(plg)

    _push(primary)

    try:
        from .data_sources import all_plugins  # lazy

        with open(path, "rb") as f:
            head = f.read(8192)

        ranked = []
        for cls in all_plugins():
            try:
                plg = cls()
                sr = plg.sniff(path, head)
                score = float(getattr(sr, "confidence", 0.0) or 0.0)
                ranked.append((score, plg))
            except Exception:
                continue
        for _score, plg in sorted(ranked, key=lambda x: x[0], reverse=True):
            _push(plg)
    except Exception:
        pass

    return candidates
