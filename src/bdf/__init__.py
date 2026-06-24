from __future__ import annotations

import os
import shutil
import warnings

# mypy: ignore-errors
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import pandas as pd

# light imports that never cause cycles
from .io import load, read, save  # spec-driven reader/serializer (the public read())
from .plugins import detect  # spec-driven detection -> (plugin_id, Plugin)
from .repair import CleanReport, clean  # public cleaning helpers
from .table_normalizers import normalize  # spec-driven column normalizer
from .validate import BDFValidationError, validate_df  # error type + df validator, re-exported at top level

__all__ = [
    # core I/O
    "read",
    "load",
    "save",
    "normalize",
    "validate",
    "validate_df",
    "detect",
    # datasets helpers
    "datasets",
    "load_registry",
    "get_entry",
    # registry LD helpers
    "build_registry",
    "search",
    "sparql",
    # cleaning
    "clean",
    "CleanReport",
    # viz
    "plot",
    "explore",
    "ingest",
    "templates",
    # version
    "__version__",
    # errors
    "BDFValidationError",
]

# Optional version
try:
    from importlib.metadata import version as _pkg_version  # type: ignore

    try:
        __version__ = _pkg_version("batterydf")
    except Exception:
        __version__ = _pkg_version("bdf")
except Exception:
    __version__ = "0.0.0-dev"


# Keep a handle to the original in case you want to restore it later
_default_formatwarning = warnings.formatwarning


def _bdf_short_formatwarning(message, category, filename, lineno, line=None):
    """
    Render warnings without absolute paths. If the warning originates inside
    the bdf package, just show 'bdf.<module>:<lineno>'; otherwise show a short
    filename. Message text remains unchanged.
    """
    try:
        p = Path(filename).resolve()
        # Heuristic: if file path contains '/bdf/' (or '\bdf\') treat it as our package
        fp = str(p).replace("\\", "/")
        if "/bdf/" in fp or fp.endswith("/bdf/__init__.py"):
            # Build a dotted module-ish label
            try:
                # relative to the package root
                pkg_root = Path(__file__).resolve().parent
                rel = p.relative_to(pkg_root)
                mod = "bdf." + ".".join(rel.with_suffix("").parts)
            except Exception:
                mod = "bdf"
            where = f"{mod}:{lineno}"
        else:
            # External warnings: keep only the basename to avoid leaking user paths
            where = f"{p.name}:{lineno}"
    except Exception:
        where = "<unknown>"

    return f"{category.__name__} [{where}]: {message}\n"


def _enable_short_warnings() -> bool:
    val = os.getenv("BDF_FORMAT_WARNINGS", "").strip().lower()
    return val in {"1", "true", "yes", "on"}


# Install the formatter (opt-in via env var).
if _enable_short_warnings():
    warnings.formatwarning = _bdf_short_formatwarning


# -------------------------------
# small helpers
# -------------------------------
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


def _default_ingest_cache_dir() -> Path:
    import os

    env = os.getenv("BDF_CRAWL_CACHE")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".bdf" / "crawl"


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _parse_github_tree(url: str) -> tuple[str, str, str, str] | None:
    import re

    match = re.match(
        r"^https?://github\.com/(?P<org>[^/]+)/(?P<repo>[^/]+)"
        r"(?:/tree/(?P<branch>[^/]+)(?:/(?P<path>.*))?)?$",
        url,
    )
    if not match:
        return None
    org = match.group("org")
    repo = match.group("repo")
    branch = match.group("branch") or "main"
    subpath = match.group("path") or ""
    return org, repo, branch, subpath


def _download_github_repo(url: str, cache_dir: Path, refresh: bool) -> Path:
    parsed = _parse_github_tree(url)
    if not parsed:
        raise ValueError(f"Unsupported GitHub URL: {url}")
    org, repo, branch, subpath = parsed
    slug = f"{org}-{repo}-{branch}"
    zip_name = f"{slug}.zip"
    zip_path = cache_dir / zip_name
    extract_root = cache_dir / slug

    if refresh:
        if zip_path.exists():
            zip_path.unlink()
        if extract_root.exists():
            shutil.rmtree(extract_root)

    if not zip_path.exists():
        import requests

        zip_url = f"https://github.com/{org}/{repo}/archive/refs/heads/{branch}.zip"
        resp = requests.get(zip_url, timeout=60)
        resp.raise_for_status()
        zip_path.write_bytes(resp.content)

    if not extract_root.exists():
        import zipfile

        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_root)

    extracted_dirs = [p for p in extract_root.iterdir() if p.is_dir()]
    if not extracted_dirs:
        raise FileNotFoundError(f"No extracted repo found in {extract_root}")
    repo_root = extracted_dirs[0]
    return repo_root / subpath if subpath else repo_root


def _resolve_ingest_source(source: str | Path, cache_dir: Path, refresh: bool) -> Path:
    s = str(source)
    path = Path(s)
    if path.exists():
        return path.resolve()
    if _is_url(s):
        if "github.com" in s:
            return _download_github_repo(s, cache_dir, refresh)
        from .fetch import fetch_url  # lazy

        return fetch_url(s, refresh=refresh)
    raise FileNotFoundError(s)


def _find_contribution_file(root: Path) -> Path | None:
    preferred = root / "contribution.json"
    legacy = root / "collection.json"
    if preferred.exists() and legacy.exists():
        warnings.warn(
            "Both contribution.json and collection.json found; using contribution.json.",
            stacklevel=2,
        )
    if preferred.exists():
        return preferred
    if legacy.exists():
        return legacy
    return None


def _find_collection_roots(root: Path) -> list[Path]:
    if _find_contribution_file(root):
        return [root]
    roots: set[Path] = set()
    for name in ("contribution.json", "collection.json"):
        roots.update({p.parent for p in root.rglob(name)})
    return sorted(roots)


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
    from . import spec

    for _, quantity_spec in spec.COLUMN_ONTOLOGY:
        if not quantity_spec.required or quantity_spec.deprecated:
            continue
        pref = quantity_spec.formatted_label.lower()
        notation = quantity_spec.effective_notation.lower()
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


# src/bdf/__init__.py (validate)
# src/bdf/__init__.py  (replace the existing validate with this)


def validate(
    obj,
    *,
    report: bool = False,
    raise_on_error: bool = False,  # <- default False so notebooks don’t crash
    registry_path: str | Path | None = None,
):
    """
    Validate a BDF DataFrame, a local file path, an HTTP/HTTPS URL, or a dataset id.

    Behavior:
      - DataFrame: validate as-is (no transformations).
      - Path/URL/id: only treated as a *BDF artifact* (strict). We do NOT vendor-parse
        or normalize here. If it doesn’t look like BDF, you’ll get an 'ok=False' report.

    Returns:
      dict report with at least:
        {"ok": True, "issues": [...]}   or   {"ok": False, "kind": "...", "detail": "..."}
    """

    # small local helpers (kept inside to avoid extra imports at module load time)
    def _bad_report(kind: str, detail: str, **extra):
        r = {"ok": False, "kind": kind, "detail": detail}
        if extra:
            r.update(extra)
        if report:
            print(f"Validation failed: {detail}")
        if raise_on_error:
            from .validate import BDFValidationError

            raise BDFValidationError(detail)
        return r

    # Direct DataFrame path
    if isinstance(obj, pd.DataFrame):
        from .validate import validate_df

        return validate_df(obj, report=report, raise_on_error=raise_on_error)

    # Resolve path/URL/registry id to a local path
    if isinstance(obj, (str, Path)):
        from .__init__ import _resolve_source  # local helper already in your package

        local_path, _ = _resolve_source(obj, registry_path=registry_path)
        p = Path(local_path)
        fname = p.name

        # Only attempt to load files that look like BDF artifacts
        def _looks_like_bdf_artifact(path: Path) -> bool:
            # quick filename hint: *.bdf.csv, *.bdf.parquet, *.bdf.feather, *.bdf.json(.gz)
            name_lc = path.name.lower()
            if any(
                name_lc.endswith(suf)
                for suf in (
                    ".bdf.csv",
                    ".bdf.csv.gz",
                    ".bdf.parquet",
                    ".bdf.feather",
                    ".bdf.json",
                    ".bdf.json.gz",
                )
            ):
                return True
            # header sniff for CSV only (cheap and safe)
            if name_lc.endswith(".csv") or name_lc.endswith(".csv.gz"):
                try:
                    with (
                        gzip.open(path, "rt")
                        if name_lc.endswith(".gz")
                        else open(path, encoding="utf-8", errors="ignore")
                    ) as f:
                        head = "".join([f.readline() for _ in range(2)]).lower()
                    header_line = head.splitlines()[0] if head else ""
                    cols_l = {c.strip().lower() for c in header_line.split(",")}
                    from . import spec

                    for _, quantity_spec in spec.COLUMN_ONTOLOGY:
                        if not quantity_spec.required or quantity_spec.deprecated:
                            continue
                        pref = quantity_spec.formatted_label.lower()
                        notation = quantity_spec.effective_notation.lower()
                        if pref not in cols_l and notation not in cols_l:
                            return False
                    return True
                except Exception:
                    return False
            return False

        # Optional gzip import for header sniff
        import gzip as _maybe_gzip  # safe alias

        gzip = _maybe_gzip

        if not _looks_like_bdf_artifact(p):
            return _bad_report(
                kind="not_bdf_artifact",
                detail=f"{fname} does not look like a BDF artifact (expected .bdf.<ext> or a BDF-style header).",
                file=fname,
            )

        # Try to load with strict BDF IO (no transformations)
        try:
            from .io import load as _load_bdf  # strict loader for BDF CSV/Parquet/Feather/JSON

            df = _load_bdf(p)
        except Exception as e:
            return _bad_report(
                kind="io_error",
                detail=f"Failed to load BDF artifact {fname}: {e}",
                file=fname,
            )

        # Validate columns/units only; do NOT normalize or modify
        from .validate import validate_df

        return validate_df(df, report=report, raise_on_error=raise_on_error)

    # Anything else: wrong type
    return _bad_report(
        kind="type_error",
        detail="validate() expects a pandas DataFrame, a file path (str/Path), a URL, or a dataset id.",
    )


# ----- dataset helpers (lazy to avoid cycles) -----
def datasets(registry_path: str | Path | None = None) -> list[str]:
    """Return dataset IDs from the registry."""
    from ._registry import list_datasets as _list_datasets, load_registry as _load_registry  # lazy

    reg = _load_registry(registry_path)
    return _list_datasets(reg)


def load_registry(path: str | Path | None = None):
    from ._registry import load_registry as _load_registry  # lazy

    return _load_registry(path)


def get_entry(reg, entry_id: str):
    from ._registry import get_entry as _get_entry  # lazy

    return _get_entry(reg, entry_id)


def build_registry(
    sources: str | list[str],
    registry_dir: str | Path | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    from .registry_ld import build_registry as _build_registry  # lazy

    return _build_registry(sources, registry_dir=registry_dir, refresh=refresh)


def search(query: str, registry_dir: str | Path | None = None, limit: int = 50):
    from .registry_ld import search as _search  # lazy

    return _search(query, registry_dir=registry_dir, limit=limit)


def sparql(query: str, registry_dir: str | Path | None = None):
    from .registry_ld import sparql as _sparql  # lazy

    return _sparql(query, registry_dir=registry_dir)


def templates(*names, root: str | Path = ".", overwrite: bool = False):
    # Importing submodule "bdf.templates" can shadow this function on the package object.
    # Restore this symbol after the call so repeated bdf.templates(...) calls stay callable.
    _self = templates
    try:
        from importlib import import_module

        mod = import_module(".templates", __name__)
        return mod.templates(*names, root=root, overwrite=overwrite)
    finally:
        globals()["templates"] = _self


def plot(*args, **kwargs):
    """
    Forward to bdf.visualize.plot(...).

    Example:
        bdf.plot(df, xdata="Test Time / s", ydata="Voltage / V", yydata="Current / A",
                 xunit="h", yyunit="mA", title="Voltage vs Time", show=True)
    """
    try:
        from .visualize import plot as _plot
    except Exception as e:
        raise RuntimeError(
            "bdf.plot() requires the visualization module (matplotlib). Ensure matplotlib is installed."
        ) from e
    return _plot(*args, **kwargs)


def explore(*args, **kwargs):
    """
    Forward to bdf._explore.explore(...).

    Example:
        bdf.explore(df, xdata="Test Time / s", ydata=["Voltage / V"], backend="plotly")
    """
    try:
        from ._explore import explore as _explore
    except Exception as e:
        raise RuntimeError("bdf.explore() is unavailable.") from e
    return _explore(*args, **kwargs)


def ingest(
    source: str | Path | list[str | Path],
    *,
    out_dir: str | Path | None = None,
    format: str = "parquet",
    layout: str = "flat",
    battery_metadata: str = "embedded",
    recursive: bool = True,
    validate_existing: bool = True,
    validate_converted: bool = True,
    include_optional: bool = True,
    plugin: str | None = None,
    incremental: bool = True,
    force: bool = False,
    raise_on_error: bool = False,
    discover_collections: bool = False,
    refresh: bool = False,
    cache_dir: str | Path | None = None,
    data_dir: str | Path | None = "timeseries",
    raw_dir: str | Path | None = "timeseries/raw",
    cell_metadata_dir: str | Path | None = "batteries",
    doi_enrich: bool = True,
    doi_timeout: int = 15,
    human: bool = False,
):
    """
    Convert raw vendor files to BDF and validate existing BDF artifacts.

    - source: file, directory, URL, or list of sources
    - format: "parquet" (default) or "csv"
    - layout: "flat" (default) or "nested"
        * flat: convert into out_dir/source and emit one collection metadata file
        * nested: convert into data/ under out_dir/source, emit root dataset metadata,
          and emit per-cell metadata.jsonld folders that describe only the battery
    - battery_metadata: "embedded" (default) or "separate" for flat layout
    - out_dir: optional output root for converted files (defaults to source_dir)
    - data_dir: output subdir for converted files (relative to out_dir)
    - raw_dir: input subdir for raw files (relative to source_dir)
    - cell_metadata_dir: base dir for per-cell metadata folders (relative to out_dir)
    - validate_existing: validate files that already look like BDF
    - validate_converted: validate after conversion
    - plugin: force a specific plugin id for raw files
    - incremental: skip previously processed files when unchanged
    - force: reprocess even if a file looks unchanged
    - discover_collections: if True, ingest each folder containing contribution.json (or collection.json)
    - refresh/cache_dir: refresh cached remote sources
    - doi_enrich: if True, enrich missing dataset metadata from DOI (DataCite, then Crossref)
    - doi_timeout: per-request timeout (seconds) for DOI lookups
    - human: if True, serialize with human prefLabels; default writes skos:notation labels

    Returns a summary dict with converted/validated/failed entries.
    When source is a list, the summary includes "sources"; when discover_collections
    is True, the summary includes "roots".
    Metadata generation uses contribution.json/person.json, and nested layout requires battery.json.
    """
    if isinstance(source, (list, tuple, set)):
        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for src in source:
            try:
                summary = ingest(
                    src,
                    out_dir=out_dir,
                    format=format,
                    layout=layout,
                    battery_metadata=battery_metadata,
                    recursive=recursive,
                    validate_existing=validate_existing,
                    validate_converted=validate_converted,
                    include_optional=include_optional,
                    plugin=plugin,
                    incremental=incremental,
                    force=force,
                    raise_on_error=raise_on_error,
                    discover_collections=discover_collections,
                    refresh=refresh,
                    cache_dir=cache_dir,
                    data_dir=data_dir,
                    raw_dir=raw_dir,
                    cell_metadata_dir=cell_metadata_dir,
                    doi_enrich=doi_enrich,
                    doi_timeout=doi_timeout,
                    human=human,
                )
                results.append({"source": str(src), "summary": summary})
            except Exception as exc:
                errors.append({"source": str(src), "error": str(exc)})
                if raise_on_error:
                    raise
        return {"sources": results, "errors": errors}

    cache_root: Path | None = None
    path = Path(str(source))
    if path.exists():
        p = path.resolve()
    else:
        cache_root = _ensure_dir(Path(cache_dir) if cache_dir else _default_ingest_cache_dir())
        p = _resolve_ingest_source(str(source), cache_root, refresh)

    if discover_collections and p.is_dir():
        collection_roots = _find_collection_roots(p)
        if not collection_roots:
            raise FileNotFoundError("No contribution.json (or collection.json) found under root.")

        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for collection_root in collection_roots:
            per_out_dir = None
            if out_dir is not None:
                out_base = Path(out_dir)
                try:
                    rel = collection_root.relative_to(p)
                except Exception:
                    rel = Path(collection_root.name)
                per_out_dir = out_base / rel
            try:
                summary = ingest(
                    collection_root,
                    out_dir=per_out_dir,
                    format=format,
                    layout=layout,
                    battery_metadata=battery_metadata,
                    recursive=recursive,
                    validate_existing=validate_existing,
                    validate_converted=validate_converted,
                    include_optional=include_optional,
                    plugin=plugin,
                    incremental=incremental,
                    force=force,
                    raise_on_error=raise_on_error,
                    discover_collections=False,
                    refresh=refresh,
                    cache_dir=cache_dir,
                    data_dir=data_dir,
                    raw_dir=raw_dir,
                    cell_metadata_dir=cell_metadata_dir,
                    doi_enrich=doi_enrich,
                    doi_timeout=doi_timeout,
                    human=human,
                )
                results.append({"path": str(collection_root), "summary": summary})
            except Exception as exc:
                errors.append({"path": str(collection_root), "error": str(exc)})
                if raise_on_error:
                    raise
        return {"roots": results, "errors": errors}

    if not p.exists():
        raise FileNotFoundError(p)

    fmt = format.lower().strip()
    if fmt not in {"parquet", "csv"}:
        raise ValueError("format must be 'parquet' or 'csv'")

    layout_mode = layout.lower().strip()
    if layout_mode not in {"flat", "nested"}:
        raise ValueError("layout must be 'flat' or 'nested'")

    battery_mode = battery_metadata.lower().strip()
    if battery_mode not in {"embedded", "separate"}:
        raise ValueError("battery_metadata must be 'embedded' or 'separate'")

    root = p if p.is_dir() else p.parent
    out_root = Path(out_dir) if out_dir else root
    data_root = out_root / "data" if layout_mode == "nested" else out_root
    raw_root: Optional[Path] = None
    raw_path = Path(raw_dir) if raw_dir is not None else None

    if data_dir is not None:
        data_path = Path(data_dir)
        data_root = data_path if data_path.is_absolute() else out_root / data_path

    if raw_path is not None:
        configured_raw = raw_path if raw_path.is_absolute() else root / raw_path
        if configured_raw.exists():
            raw_root = configured_raw
            if data_dir is None and raw_path.name.lower() == "raw" and raw_path.parent.parts:
                parent = raw_path.parent
                data_root = parent if parent.is_absolute() else out_root / parent
        else:
            warnings.warn(
                f"Configured raw_dir not found: {configured_raw}. Falling back to auto-discovery.",
                stacklevel=2,
            )

    if raw_root is None and data_dir is not None:
        data_path = Path(data_dir)
        if not data_path.is_absolute():
            candidate = root / data_path / "raw"
            if candidate.exists():
                raw_root = candidate

    if raw_root is None:
        candidate = root / "timeseries" / "raw"
        if candidate.exists():
            if data_dir is None:
                data_root = out_root / "timeseries"
            raw_root = candidate

    def _strip_all_suffixes(path: Path) -> Path:
        name = path.name
        while True:
            suffix = Path(name).suffix
            if not suffix:
                break
            name = Path(name).stem
        return path.with_name(name)

    def _output_path(src: Path) -> Path:
        base_root = raw_root if raw_root and src.is_relative_to(raw_root) else root
        rel = src.relative_to(base_root) if src.is_relative_to(base_root) else Path(src.name)
        base = _strip_all_suffixes(rel)
        suffix = ".bdf.parquet" if fmt == "parquet" else ".bdf.csv"
        return data_root / base.parent / f"{base.name}{suffix}"

    def _metadata_output_path(out_path: Path) -> Path:
        base = _strip_all_suffixes(out_path)
        return base.with_suffix(".jsonld")

    def _cell_meta_root() -> Path:
        if cell_metadata_dir is None:
            return out_root
        cell_path = Path(cell_metadata_dir)
        return cell_path if cell_path.is_absolute() else out_root / cell_path

    def _parse_filename_parts(path: Path) -> dict[str, str]:
        base = _strip_all_suffixes(path).name
        parts = base.split("__")
        if len(parts) < 5:
            return {}
        institution = parts[0]
        cell_id = parts[1]
        date = parts[2]
        technique = parts[3]
        ambient = "__".join(parts[4:]) if len(parts) > 4 else ""
        return {
            "institution": institution,
            "cell_id": cell_id,
            "date": date,
            "measurement_technique": technique,
            "ambient": ambient,
        }

    def _parse_cell_id(path: Path) -> Optional[str]:
        parts = _parse_filename_parts(path)
        return parts.get("cell_id")

    def _short_cell_id(cell_id: str) -> str:
        return cell_id.rsplit("-", 1)[-1] if "-" in cell_id else cell_id

    def _match_cell_id_from_name(path: Path, keys: list[str]) -> Optional[str]:
        name = _strip_all_suffixes(path).name.lower()
        for key in keys:
            if key and key in name:
                return key
        return None

    # Snapshot file list before writing outputs
    file_root = raw_root if raw_root and raw_root.is_dir() else p
    if file_root.is_dir():
        pattern = "**/*" if recursive else "*"
        files = [f for f in file_root.glob(pattern) if f.is_file()]
    else:
        files = [p]

    from .io import save as _save  # lazy import

    summary = {
        "converted": [],
        "validated": [],
        "failed": [],
        "skipped": [],
        "metadata": [],
        "metadata_failed": [],
    }

    state_path = root / ".bdf.state.json"
    state: dict[str, Any] = {"version": 1, "items": {}}

    def _load_json(path: Path) -> dict:
        import json

        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _normalize_doi(value: Any) -> Optional[str]:
        import re

        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        sl = s.lower()
        if sl.startswith("doi:"):
            s = s[4:].strip()
        if sl.startswith("https://doi.org/"):
            s = s[len("https://doi.org/") :]
        elif sl.startswith("http://doi.org/"):
            s = s[len("http://doi.org/") :]
        elif sl.startswith("http://dx.doi.org/"):
            s = s[len("http://dx.doi.org/") :]
        match = re.search(r"(10\.\d{4,9}/\S+)", s)
        if not match:
            return None
        doi = match.group(1).rstrip(").,;\"'")
        return doi or None

    def _doi_from_identifiers(values: Any) -> Optional[str]:
        if isinstance(values, str):
            return _normalize_doi(values)
        if isinstance(values, list):
            for item in values:
                doi = _normalize_doi(item)
                if doi:
                    return doi
        return None

    def _normalize_citation_values(values: Any) -> list[str]:
        if values is None:
            return []
        raw_values = values if isinstance(values, list) else [values]
        out: list[str] = []
        for item in raw_values:
            doi = _normalize_doi(item)
            if not doi:
                continue
            value = f"https://doi.org/{doi}"
            if value not in out:
                out.append(value)
        return out

    def _canonicalize_metadata_keys(meta_raw: dict) -> dict:
        if not isinstance(meta_raw, dict):
            return meta_raw
        normalized = dict(meta_raw)
        dataset_doi = _normalize_doi(normalized.get("dataset_doi"))
        if dataset_doi:
            normalized["dataset_doi"] = f"https://doi.org/{dataset_doi}"
            if not normalized.get("doi"):
                normalized["doi"] = dataset_doi
        else:
            doi = _normalize_doi(normalized.get("doi"))
            if doi:
                normalized["doi"] = doi
                normalized.setdefault("dataset_doi", f"https://doi.org/{doi}")

        citation_doi_values = normalized.get("citation_doi")
        if citation_doi_values is not None:
            citation_dois = _normalize_citation_values(citation_doi_values)
            if citation_dois:
                normalized["citation_doi"] = citation_dois[0] if len(citation_dois) == 1 else citation_dois
                if not normalized.get("citation"):
                    normalized["citation"] = citation_dois
        if normalized.get("citation") is not None:
            citation_values = _normalize_citation_values(normalized.get("citation"))
            if citation_values:
                normalized["citation"] = citation_values

        creators = normalized.get("creators")
        if isinstance(creators, dict):
            normalized["creators"] = [creators]
        creator = normalized.get("creator")
        if isinstance(creator, dict):
            normalized["creator"] = [creator]
        return normalized

    def _strip_html(value: str) -> str:
        import re

        return re.sub(r"<[^>]+>", "", value).strip()

    def _doi_request_json(url: str) -> Optional[dict]:
        try:
            import requests
        except Exception:
            return None
        headers = {
            "User-Agent": f"bdf/{__version__}",
            "Accept": "application/json",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=doi_timeout)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def _datacite_to_meta(attrs: dict, doi: str) -> dict:
        out: dict[str, Any] = {}
        titles = attrs.get("titles")
        if isinstance(titles, list):
            for item in titles:
                if isinstance(item, dict) and item.get("title"):
                    out["title"] = item["title"]
                    break
        elif isinstance(titles, str):
            out["title"] = titles

        descriptions = attrs.get("descriptions")
        desc = None
        if isinstance(descriptions, list):
            for item in descriptions:
                if isinstance(item, dict) and item.get("descriptionType", "").lower() == "abstract":
                    desc = item.get("description")
                    if desc:
                        break
            if not desc:
                for item in descriptions:
                    if isinstance(item, dict) and item.get("description"):
                        desc = item["description"]
                        break
        if isinstance(desc, str) and desc.strip():
            out["description"] = _strip_html(desc)

        creators_out: list[dict[str, Any]] = []
        creators = attrs.get("creators") or []
        if isinstance(creators, list):
            for creator in creators:
                if not isinstance(creator, dict):
                    continue
                given = creator.get("givenName")
                family = creator.get("familyName")
                name = creator.get("name") or " ".join([p for p in (given, family) if p])
                if not name:
                    continue
                orcid = None
                for ident in creator.get("nameIdentifiers") or []:
                    if not isinstance(ident, dict):
                        continue
                    if str(ident.get("nameIdentifierScheme", "")).upper() == "ORCID":
                        orcid = ident.get("nameIdentifier")
                        break
                affiliation = None
                aff_list = creator.get("affiliation")
                if isinstance(aff_list, list) and aff_list:
                    if isinstance(aff_list[0], dict):
                        affiliation = aff_list[0].get("name")
                    elif isinstance(aff_list[0], str):
                        affiliation = aff_list[0]
                entry = {"name": name}
                if given:
                    entry["given_name"] = given
                if family:
                    entry["family_name"] = family
                if orcid:
                    entry["orcid"] = orcid
                if affiliation:
                    entry["affiliation"] = affiliation
                creators_out.append(entry)
        if creators_out:
            out["creators"] = creators_out

        pub_year = attrs.get("publicationYear")
        if pub_year:
            out["publication_date"] = str(pub_year)

        url = attrs.get("url") or f"https://doi.org/{doi}"
        if url:
            out["url"] = url

        subjects = attrs.get("subjects")
        if isinstance(subjects, list):
            keywords: list[str] = []
            for item in subjects:
                if isinstance(item, dict) and item.get("subject"):
                    keywords.append(item["subject"])
                elif isinstance(item, str):
                    keywords.append(item)
            if keywords:
                out["keywords"] = keywords

        return out

    def _crossref_to_meta(message: dict, doi: str) -> dict:
        out: dict[str, Any] = {}
        titles = message.get("title")
        if isinstance(titles, list) and titles:
            out["title"] = titles[0]
        elif isinstance(titles, str):
            out["title"] = titles

        abstract = message.get("abstract")
        if isinstance(abstract, str) and abstract.strip():
            out["description"] = _strip_html(abstract)

        creators_out: list[dict[str, Any]] = []
        authors = message.get("author") or []
        if isinstance(authors, list):
            for author in authors:
                if not isinstance(author, dict):
                    continue
                given = author.get("given")
                family = author.get("family")
                name = author.get("name") or " ".join([p for p in (given, family) if p])
                if not name:
                    continue
                orcid = author.get("ORCID")
                affiliation = None
                aff_list = author.get("affiliation")
                if isinstance(aff_list, list) and aff_list:
                    if isinstance(aff_list[0], dict):
                        affiliation = aff_list[0].get("name")
                    elif isinstance(aff_list[0], str):
                        affiliation = aff_list[0]
                entry = {"name": name}
                if given:
                    entry["given_name"] = given
                if family:
                    entry["family_name"] = family
                if orcid:
                    entry["orcid"] = orcid
                if affiliation:
                    entry["affiliation"] = affiliation
                creators_out.append(entry)
        if creators_out:
            out["creators"] = creators_out

        issued = message.get("issued", {})
        if isinstance(issued, dict):
            date_parts = issued.get("date-parts")
            if isinstance(date_parts, list) and date_parts:
                parts = date_parts[0]
                if isinstance(parts, list) and parts:
                    year = str(parts[0])
                    if len(parts) >= 3:
                        month = f"{int(parts[1]):02d}" if str(parts[1]).isdigit() else str(parts[1])
                        day = f"{int(parts[2]):02d}" if str(parts[2]).isdigit() else str(parts[2])
                        out["publication_date"] = f"{year}-{month}-{day}"
                    elif len(parts) == 2:
                        month = f"{int(parts[1]):02d}" if str(parts[1]).isdigit() else str(parts[1])
                        out["publication_date"] = f"{year}-{month}"
                    else:
                        out["publication_date"] = year

        url = message.get("URL") or f"https://doi.org/{doi}"
        if url:
            out["url"] = url

        subjects = message.get("subject")
        if isinstance(subjects, list) and subjects:
            out["keywords"] = [str(s) for s in subjects if s]

        return out

    def _lookup_doi_metadata(doi: str) -> dict:
        from urllib.parse import quote

        datacite = _doi_request_json(f"https://api.datacite.org/dois/{quote(doi)}")
        if datacite:
            attrs = datacite.get("data", {}).get("attributes", {})
            if isinstance(attrs, dict):
                meta = _datacite_to_meta(attrs, doi)
                if meta:
                    return meta

        crossref = _doi_request_json(f"https://api.crossref.org/works/{quote(doi)}")
        if crossref:
            message = crossref.get("message", {})
            if isinstance(message, dict):
                meta = _crossref_to_meta(message, doi)
                if meta:
                    return meta

        return {}

    def _apply_doi_enrichment(meta_raw: dict) -> dict:
        meta_raw = _canonicalize_metadata_keys(meta_raw)
        if not doi_enrich or not isinstance(meta_raw, dict):
            return meta_raw
        doi = _normalize_doi(meta_raw.get("doi")) or _doi_from_identifiers(meta_raw.get("identifiers"))
        if not doi:
            return meta_raw
        needs_creators = not (meta_raw.get("creators") or meta_raw.get("creator"))
        needs_title = not meta_raw.get("title")
        needs_description = not meta_raw.get("description")
        if not (needs_creators or needs_title or needs_description):
            return meta_raw
        meta = _lookup_doi_metadata(doi)
        if not meta:
            warnings.warn(f"DOI enrichment failed for {doi}", stacklevel=2)
            return meta_raw

        enriched = dict(meta_raw)
        if needs_title and meta.get("title"):
            enriched["title"] = meta["title"]
        if needs_description and meta.get("description"):
            enriched["description"] = meta["description"]
        if needs_creators and meta.get("creators"):
            enriched["creators"] = meta["creators"]
        if not enriched.get("publication_date") and meta.get("publication_date"):
            enriched["publication_date"] = meta["publication_date"]
        if not enriched.get("url") and meta.get("url"):
            enriched["url"] = meta["url"]
        if not enriched.get("keywords") and meta.get("keywords"):
            enriched["keywords"] = meta["keywords"]
        return enriched

    def _load_state() -> None:
        if not incremental or not state_path.exists():
            return
        try:
            raw = _load_json(state_path)
            if isinstance(raw, dict) and isinstance(raw.get("items"), dict):
                state["items"] = raw["items"]
        except Exception:
            state["items"] = {}

    def _save_state() -> None:
        if not incremental:
            return
        import json
        from datetime import datetime, timezone

        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def _file_signature(path: Path) -> dict[str, Any]:
        stat = path.stat()
        return {"mtime": stat.st_mtime, "size": stat.st_size}

    def _state_key(path: Path) -> str:
        try:
            rel = path.relative_to(raw_root or root)
        except Exception:
            rel = Path(path.name)
        return rel.as_posix()

    def _is_metadata_file(path: Path) -> bool:
        name = path.name.lower()
        if name in {
            "collection.json",
            "contribution.json",
            "dataset.json",
            "battery.json",
            "person.json",
            "people.json",
            "data_download.json",
            "bdf.mapping.json",
            "bdf.map.json",
            "metadata.jsonld",
            "metadata.html",
            ".bdf.state.json",
        }:
            return True
        if name.endswith(".map.json") or name.endswith(".mapping.json"):
            return True
        return name.startswith("metadata.")

    _load_state()

    def _filter_fields(cls, data: dict) -> dict:
        allowed = set(getattr(cls, "__dataclass_fields__", {}).keys())
        return {k: v for k, v in data.items() if k in allowed}

    def _guess_encoding_format(path: Path) -> Optional[str]:
        sfx = "".join(path.suffixes).lower()
        if sfx.endswith(".csv"):
            return "text/csv"
        if sfx.endswith(".tsv"):
            return "text/tab-separated-values"
        if sfx.endswith(".txt"):
            return "text/plain"
        if sfx.endswith(".json"):
            return "application/json"
        if sfx.endswith(".parquet"):
            return "application/x-parquet"
        if sfx.endswith(".zip"):
            return "application/zip"
        if sfx.endswith(".nda") or sfx.endswith(".ndax"):
            return "application/octet-stream"
        return None

    def _load_people_index(dir_path: Path) -> dict[str, dict]:
        for name in ("person.json", "people.json"):
            people_path = dir_path / name
            if not people_path.exists():
                continue
            people_raw = _load_json(people_path)
            people_index: dict[str, dict] = {}
            if isinstance(people_raw, dict):
                for pid, pdata in people_raw.items():
                    if isinstance(pdata, dict):
                        people_index[str(pid).lower()] = pdata
            elif isinstance(people_raw, list):
                for pdata in people_raw:
                    if isinstance(pdata, dict) and pdata.get("id") is not None:
                        people_index[str(pdata["id"]).lower()] = pdata
            return people_index
        return {}

    def _expand_battery_items(battery_raw: Any) -> list[dict]:
        if isinstance(battery_raw, list):
            return [item for item in battery_raw if isinstance(item, dict)]
        if isinstance(battery_raw, dict):
            if "cells" in battery_raw and isinstance(battery_raw.get("cells"), list):
                spec = battery_raw.get("spec")
                if not isinstance(spec, dict):
                    spec = {}

                manufacturer_value = spec.get("manufacturer")
                manufacturer_name = manufacturer_value
                if isinstance(manufacturer_value, dict):
                    manufacturer_name = manufacturer_value.get("name")

                product_id = spec.get("productID") or spec.get("model")
                base_item: dict[str, Any] = {**spec}
                if manufacturer_name:
                    base_item["manufacturer"] = manufacturer_name
                if product_id and not base_item.get("model"):
                    base_item["model"] = product_id

                items: list[dict] = []
                for entry in battery_raw.get("cells", []):
                    if entry is None:
                        continue
                    if isinstance(entry, dict):
                        name = entry.get("name")
                        cell_id = entry.get("cell_id") or entry.get("id") or name
                        if not cell_id:
                            continue
                        item = {**base_item, **entry}
                        item["id"] = str(cell_id)
                        if name:
                            item["name"] = str(name).lower()
                        items.append(item)
                        continue

                    name = str(entry).strip()
                    if not name:
                        continue
                    item = {**base_item, "id": name, "name": name.lower()}
                    items.append(item)
                return items

            if "ids" in battery_raw and isinstance(battery_raw.get("ids"), list):
                spec = battery_raw.get("spec")
                if not isinstance(spec, dict):
                    spec = {k: v for k, v in battery_raw.items() if k != "ids"}
                manufacturer = spec.get("manufacturer")
                if isinstance(manufacturer, dict):
                    manufacturer = manufacturer.get("name")
                model = spec.get("model") or spec.get("productID")
                if manufacturer:
                    spec["manufacturer"] = manufacturer
                if model and not spec.get("model"):
                    spec["model"] = model
                batch = spec.get("batch")
                namespace = spec.get("namespace")
                name_template = spec.get("name_template")
                iri_template = spec.get("iri_template")
                use_short_id = bool(name_template)

                def _format_template(template: str, *, short_id: str, full_id: str, name: Optional[str]) -> str:
                    return str(template).format(
                        manufacturer=manufacturer,
                        model=model,
                        batch=batch,
                        namespace=namespace,
                        id=short_id,
                        short_id=short_id,
                        full_id=full_id,
                        name=name or full_id,
                    )

                def _build_full_id(short_id: str) -> str:
                    if manufacturer and model and batch:
                        return f"{manufacturer}-{model}-{batch}-{short_id}"
                    return short_id

                def _build_name(short_id: str, full_id: str) -> Optional[str]:
                    if name_template:
                        return _format_template(
                            name_template,
                            short_id=short_id,
                            full_id=full_id,
                            name=None,
                        ).lower()
                    return None

                def _build_id(short_id: str, full_id: str) -> str:
                    return short_id if use_short_id else full_id

                def _build_iri(short_id: str, full_id: str, name: Optional[str]) -> Optional[str]:
                    if iri_template:
                        return _format_template(
                            iri_template,
                            short_id=short_id,
                            full_id=full_id,
                            name=name,
                        ).lower()
                    if namespace:
                        base = str(namespace).rstrip("/")
                        if manufacturer and model and batch:
                            return f"{base}/{manufacturer}/{model}/{batch}/{short_id}".lower()
                        return f"{base}/{short_id}".lower()
                    return None

                items: list[dict] = []
                for entry in battery_raw.get("ids", []):
                    if entry is None:
                        continue
                    if isinstance(entry, dict):
                        short_id = entry.get("short_id") or entry.get("id")
                        if short_id is None:
                            continue
                        short_id = str(short_id)
                        full_id = str(entry.get("full_id") or _build_full_id(short_id))
                        name = entry.get("name") or _build_name(short_id, full_id)
                        if name:
                            name = str(name).lower()
                        iri = entry.get("iri") or _build_iri(short_id, full_id, name)
                        if iri:
                            iri = str(iri).lower()
                        item = {**spec, **entry}
                        item["id"] = _build_id(short_id, full_id)
                        if name:
                            item["name"] = name
                        if iri:
                            item["iri"] = iri
                        items.append(item)
                        continue
                    short_id = str(entry)
                    full_id = _build_full_id(short_id)
                    name = _build_name(short_id, full_id)
                    if name:
                        name = str(name).lower()
                    iri = _build_iri(short_id, full_id, name)
                    if iri:
                        iri = str(iri).lower()
                    item = {**spec, "id": _build_id(short_id, full_id)}
                    if name:
                        item["name"] = name
                    if iri:
                        item["iri"] = iri
                    items.append(item)
                return items
            return [battery_raw]
        return []

    def _build_battery_index(dir_path: Path) -> dict[str, Any]:
        from .metadata import Battery  # lazy import

        battery_path = dir_path / "battery.json"
        if not battery_path.exists():
            return {}
        battery_raw = _load_json(battery_path)
        battery_items = _expand_battery_items(battery_raw)
        batteries = [Battery(**_filter_fields(Battery, item)) for item in battery_items if isinstance(item, dict)]
        index: dict[str, Battery] = {}
        for b in batteries:
            if b.id:
                index[str(b.id).lower()] = b
            if b.name:
                index.setdefault(str(b.name).lower(), b)
        return index

    def _resolve_creator(item: Any, people_index: dict[str, dict]):
        from .metadata import Creator  # lazy import

        if isinstance(item, str):
            pdata = people_index.get(item.lower())
            if not pdata:
                warnings.warn(f"Creator id not found in person.json: {item}", stacklevel=2)
                return None
            return Creator(**_filter_fields(Creator, pdata))
        if isinstance(item, dict):
            if "id" in item and (len(item) == 1 or all(k in {"id"} for k in item)):
                pid = str(item["id"]).lower()
                pdata = people_index.get(pid)
                if not pdata:
                    warnings.warn(f"Creator id not found in person.json: {item['id']}", stacklevel=2)
                    return None
                return Creator(**_filter_fields(Creator, pdata))
            return Creator(**_filter_fields(Creator, item))
        return None

    def _build_creators(meta_raw: dict, people_index: dict[str, dict], *, allow_fallback_unknown: bool = True):
        creators_raw = meta_raw.get("creators") or meta_raw.get("creator") or []
        creators = [c for c in (_resolve_creator(it, people_index) for it in creators_raw) if c is not None]
        if not creators and people_index:
            from .metadata import Creator  # lazy import

            creators = [Creator(**_filter_fields(Creator, pdata)) for pdata in people_index.values()]
        if not creators and allow_fallback_unknown:
            from .metadata import Creator  # lazy import

            creators = [Creator(name="Unknown contributor")]
        return creators

    def _finalize_dataset_metadata(meta_raw: dict, *, source_label: str) -> dict:
        if not isinstance(meta_raw, dict):
            meta_raw = {}
        out = dict(meta_raw)
        doi = _normalize_doi(out.get("doi"))
        if doi:
            out["doi"] = doi
            out.setdefault("dataset_doi", f"https://doi.org/{doi}")
        if not out.get("license"):
            out["license"] = "CC-BY-4.0"
        if not out.get("title"):
            out["title"] = f"Battery dataset ({doi})" if doi else f"Battery dataset ({source_label})"
            warnings.warn(
                f"Missing title in metadata for {source_label}; using auto-generated title.",
                stacklevel=2,
            )
        if not out.get("description"):
            out["description"] = (
                "Auto-generated BDF metadata. Add description/creators in sidecar metadata for richer records."
            )
            warnings.warn(
                f"Missing description in metadata for {source_label}; using auto-generated description.",
                stacklevel=2,
            )
        return out

    def _error_code(exc: Exception) -> str:
        if isinstance(exc, FileNotFoundError):
            return "file_not_found"
        if isinstance(exc, PermissionError):
            return "permission_denied"
        if isinstance(exc, BDFValidationError):
            return "validation_error"
        if isinstance(exc, ValueError):
            return "value_error"
        if isinstance(exc, KeyError):
            return "key_error"
        return "processing_error"

    def _write_metadata(src: Path, *, df: pd.DataFrame, out_path: Path) -> Optional[Path]:
        dataset_path = src.parent / "dataset.json"
        if not dataset_path.exists():
            return None

        from .metadata import Battery, DataDownload, Dataset  # lazy import

        meta_raw = _load_json(dataset_path)
        meta_raw = _apply_doi_enrichment(meta_raw)
        meta_raw = _finalize_dataset_metadata(meta_raw, source_label=src.name)
        url_base = meta_raw.get("url_base")
        people_index = _load_people_index(src.parent)
        creators = _build_creators(meta_raw, people_index)

        meta_kwargs = dict(meta_raw)
        meta_kwargs.pop("url_base", None)
        meta_kwargs.pop("creators", None)
        meta_kwargs.pop("creator", None)
        meta_kwargs["creators"] = creators
        meta = Dataset(**meta_kwargs)

        rel_path = src.relative_to(src.parent) if src.is_relative_to(src.parent) else Path(src.name)
        base_url = f"{url_base.rstrip('/')}/{rel_path.as_posix().lstrip('/')}" if url_base else src.name
        base_name = src.name
        base_encoding = _guess_encoding_format(src)

        download_path = src.parent / "data_download.json"
        dists: list[DataDownload] = []
        if download_path.exists():
            dd_raw = _load_json(download_path)
            dd_list = dd_raw if isinstance(dd_raw, list) else [dd_raw]
            for item in dd_list:
                if not isinstance(item, dict):
                    continue
                dd_item = {
                    "url": base_url,
                    "name": base_name,
                    "encoding_format": base_encoding,
                }
                if item.get("path"):
                    path = str(item["path"]).lstrip("/")
                    dd_item["url"] = f"{url_base.rstrip('/')}/{path}" if url_base else path
                    if not item.get("name"):
                        dd_item["name"] = Path(path).name
                if item.get("url"):
                    dd_item["url"] = item["url"]
                for key, value in item.items():
                    if key in {"url", "path"}:
                        continue
                    dd_item[key] = value
                dists.append(DataDownload(**_filter_fields(DataDownload, dd_item)))
        if not dists:
            dists = [DataDownload(url=base_url, name=base_name, encoding_format=base_encoding)]

        battery_path = src.parent / "battery.json"
        batteries: list[Battery] = []
        if battery_path.exists():
            battery_raw = _load_json(battery_path)
            battery_items = _expand_battery_items(battery_raw)
            batteries = [Battery(**_filter_fields(Battery, item)) for item in battery_items if isinstance(item, dict)]

        cell_id = _parse_cell_id(src)
        if not cell_id and batteries:
            key_list = []
            for b in batteries:
                if b.id:
                    key_list.append(str(b.id).lower())
                if b.name:
                    key_list.append(str(b.name).lower())
            cell_id = _match_cell_id_from_name(src, key_list)

        if cell_id:
            cell_id_lower = cell_id.lower()
            matched = [
                b
                for b in batteries
                if str(b.id).lower() == cell_id_lower or (b.name and str(b.name).lower() == cell_id_lower)
            ]
        else:
            matched = []
        if matched:
            batteries = matched

        extra_fields = None
        if batteries:
            about_value = [b.to_schemaorg() for b in batteries]
            if len(about_value) == 1:
                about_value = about_value[0]
            extra_fields = {"schema:about": about_value}
        meta_out = _metadata_output_path(out_path)
        meta.save_jsonld(meta_out, distributions=dists, extra_fields=extra_fields, df=df)
        return meta_out

    def _parse_measurement_technique(path: Path) -> Optional[str]:
        parts = _parse_filename_parts(path)
        return parts.get("measurement_technique")

    def _write_collection_metadata(*, include_batteries: bool = False) -> tuple[Optional[Path], dict[str, list[str]]]:
        dataset_path = _find_contribution_file(root)
        if not dataset_path:
            return None, {}

        from .metadata import DataDownload, Dataset  # lazy import

        meta_raw = _load_json(dataset_path)
        meta_raw = _apply_doi_enrichment(meta_raw)
        meta_raw = _finalize_dataset_metadata(meta_raw, source_label=root.name)
        url_base = meta_raw.get("url_base")
        collection_doi = meta_raw.get("doi")
        people_index = _load_people_index(root)
        creators = _build_creators(meta_raw, people_index)

        meta_kwargs = dict(meta_raw)
        meta_kwargs.pop("url_base", None)
        meta_kwargs.pop("creators", None)
        meta_kwargs.pop("creator", None)
        meta_kwargs["creators"] = creators
        meta = Dataset(**meta_kwargs)

        def _is_bdf_output(path: Path) -> bool:
            sfx = "".join(path.suffixes).lower()
            return ".bdf" in sfx

        bdf_files = [f for f in data_root.rglob("*") if f.is_file() and _is_bdf_output(f)]
        battery_index = _build_battery_index(root)
        child_nodes: list[dict[str, Any]] = []
        dataset_links: dict[str, list[str]] = {}
        for f in sorted(bdf_files):
            try:
                rel = f.relative_to(out_root)
            except Exception:
                try:
                    rel = f.relative_to(root)
                except Exception:
                    rel = Path(f.name)
            rel_posix = rel.as_posix().lstrip("/")
            url = f"{url_base.rstrip('/')}/{rel_posix}" if url_base else rel_posix
            encoding = _guess_encoding_format(f)
            dist = DataDownload(url=url, name=f.name, encoding_format=encoding)

            technique = _parse_measurement_technique(f)
            child_title = f"{meta.title} - {technique}" if technique else f"{meta.title} - {f.name}"
            child_desc = meta.description
            if technique and technique.lower() not in (meta.description or "").lower():
                child_desc = f"{meta.description} Measurement technique: {technique}."

            child_kwargs: dict[str, Any] = {
                "title": child_title,
                "creators": creators,
                "description": child_desc,
                "keywords": meta.keywords,
                "license": meta.license,
                "version": meta.version,
                "publication_date": meta.publication_date,
                "measurement_technique": technique,
                "citation": meta.citation,
            }

            override_path = root / rel.parent / "dataset.json"
            if not override_path.exists():
                override_path = root / "dataset.json"
            child_identifier = rel_posix
            if override_path.exists():
                override_raw = _load_json(override_path)
                if isinstance(override_raw, dict):
                    override_raw = _canonicalize_metadata_keys(override_raw)
                    override_creators = _build_creators(override_raw, people_index, allow_fallback_unknown=False)
                    if override_creators:
                        child_kwargs["creators"] = override_creators
                    override_raw = dict(override_raw)
                    override_raw.pop("creators", None)
                    override_raw.pop("creator", None)
                    override_raw.pop("url_base", None)
                    if "measurementTechnique" in override_raw and "measurement_technique" not in override_raw:
                        override_raw["measurement_technique"] = override_raw.pop("measurementTechnique")
                    if override_raw.get("doi"):
                        child_kwargs["doi"] = override_raw["doi"]
                    override_filtered = _filter_fields(Dataset, override_raw)
                    for key, value in override_filtered.items():
                        if value is not None:
                            child_kwargs[key] = value
                    if override_raw.get("identifier"):
                        child_identifier = override_raw["identifier"]

            if collection_doi and not child_kwargs.get("doi"):
                child_kwargs["doi"] = collection_doi

            dataset_uri = None
            if url:
                dataset_uri = f"{url}#dataset"
            elif child_identifier:
                dataset_uri = f"bdf:dataset/{child_identifier}"

            child_meta = Dataset(**child_kwargs)
            extra_fields: dict[str, Any] = {}
            cell_id = _parse_cell_id(f)
            if not cell_id and battery_index:
                cell_id = _match_cell_id_from_name(f, list(battery_index.keys()))
            if cell_id and battery_index:
                battery = battery_index.get(cell_id.lower())
                if battery:
                    extra_fields["schema:about"] = {"@id": battery.to_schemaorg().get("@id")}
                    if dataset_uri:
                        dataset_links.setdefault(cell_id.lower(), []).append(dataset_uri)
            child_obj = child_meta.to_schemaorg_dataset(
                dataset_uri=dataset_uri,
                identifier=child_identifier,
                distributions=[dist],
                context=[],
                extra_fields=extra_fields or None,
            )
            child_obj.pop("@context", None)
            child_nodes.append(child_obj)

        extra_fields = {"schema:hasPart": child_nodes} if child_nodes else {}
        meta_out = out_root / "metadata.jsonld"

        if include_batteries and battery_index:
            import json

            from .metadata import DEFAULT_JSONLD_CONTEXT  # lazy import

            dataset_obj = meta.to_schemaorg_dataset(
                extra_fields=extra_fields or None,
                context=[],
            )
            dataset_obj.pop("@context", None)

            batteries: list[Any] = []
            seen_ids: set[str] = set()
            for battery in battery_index.values():
                if not battery.id:
                    continue
                key = str(battery.id).lower()
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                batteries.append(battery)

            battery_nodes: list[dict[str, Any]] = []
            for battery in batteries:
                battery_doc = battery.to_schemaorg()
                key = None
                if battery.name and battery.name.lower() in dataset_links:
                    key = battery.name.lower()
                elif battery.id and battery.id.lower() in dataset_links:
                    key = battery.id.lower()
                if key:
                    dataset_refs = [{"@id": uri} for uri in dataset_links.get(key, [])]
                    if dataset_refs:
                        battery_doc["schema:subjectOf"] = dataset_refs
                battery_nodes.append(battery_doc)

            graph_obj = {"@context": list(DEFAULT_JSONLD_CONTEXT), "@graph": [dataset_obj, *battery_nodes]}
            with open(meta_out, "w", encoding="utf-8") as f:
                json.dump(graph_obj, f, ensure_ascii=False, indent=2)
        else:
            meta.save_jsonld(meta_out, extra_fields=extra_fields or None)
        return meta_out, dataset_links

    def _write_battery_metadata_files(battery_index: dict[str, Any], dataset_links: dict[str, list[str]]) -> list[Path]:
        import json

        from .metadata import DEFAULT_JSONLD_CONTEXT  # lazy import

        meta_paths: list[Path] = []
        batteries: list[Any] = []
        seen_ids: set[str] = set()
        for battery in battery_index.values():
            if not battery.id:
                continue
            key = str(battery.id).lower()
            if key in seen_ids:
                continue
            seen_ids.add(key)
            batteries.append(battery)

        for battery in batteries:
            meta_out = out_root / f"{battery.id}.metadata.jsonld"
            battery_doc = {"@context": list(DEFAULT_JSONLD_CONTEXT), **battery.to_schemaorg()}
            dataset_refs: list[dict[str, str]] = []
            key = None
            if battery.name and battery.name.lower() in dataset_links:
                key = battery.name.lower()
            elif battery.id and battery.id.lower() in dataset_links:
                key = battery.id.lower()
            if key:
                dataset_refs = [{"@id": uri} for uri in dataset_links.get(key, [])]
            if dataset_refs:
                battery_doc["schema:subjectOf"] = dataset_refs
            with open(meta_out, "w", encoding="utf-8") as f:
                json.dump(battery_doc, f, ensure_ascii=False, indent=2)
            meta_paths.append(meta_out)
        return meta_paths

    def _write_nested_metadata() -> list[Path]:
        dataset_path = _find_contribution_file(root)
        if not dataset_path:
            raise FileNotFoundError(
                "contribution.json (or collection.json) is required for nested metadata generation."
            )

        import json

        from .metadata import DEFAULT_JSONLD_CONTEXT  # lazy import

        battery_index = _build_battery_index(root)
        if not battery_index:
            warnings.warn(
                "battery.json not found or empty; generating only collection metadata for nested layout.",
                stacklevel=2,
            )
            root_meta, _ = _write_collection_metadata()
            return [root_meta] if root_meta else []

        meta_paths: list[Path] = []
        root_meta, dataset_links = _write_collection_metadata()
        if root_meta:
            meta_paths.append(root_meta)

        batteries: list[Any] = []
        seen_ids: set[str] = set()
        for battery in battery_index.values():
            if not battery.id:
                continue
            key = str(battery.id).lower()
            if key in seen_ids:
                continue
            seen_ids.add(key)
            batteries.append(battery)

        cell_root = _cell_meta_root()
        for battery in batteries:
            cell_id = str(battery.id).lower()
            cell_dir = cell_root / cell_id
            cell_dir.mkdir(parents=True, exist_ok=True)
            meta_out = cell_dir / "metadata.jsonld"
            battery_doc = {"@context": list(DEFAULT_JSONLD_CONTEXT), **battery.to_schemaorg()}
            dataset_refs: list[dict[str, str]] = []
            if dataset_links:
                key = None
                if battery.name and battery.name.lower() in dataset_links:
                    key = battery.name.lower()
                elif battery.id and battery.id.lower() in dataset_links:
                    key = battery.id.lower()
                if key:
                    dataset_refs = [{"@id": uri} for uri in dataset_links.get(key, [])]
            if dataset_refs:
                battery_doc["schema:subjectOf"] = dataset_refs
            with open(meta_out, "w", encoding="utf-8") as f:
                json.dump(battery_doc, f, ensure_ascii=False, indent=2)
            meta_paths.append(meta_out)

        return meta_paths

    collection_metadata = layout_mode == "flat" and p.is_dir() and _find_contribution_file(root)

    for f in files:
        try:
            if f.name.startswith("~$"):
                summary["skipped"].append({"path": str(f), "reason": "excel_temp_file"})
                continue
            if _is_metadata_file(f):
                summary["skipped"].append({"path": str(f), "reason": "metadata_file"})
                continue

            if _looks_like_bdf_artifact(f):
                output_used = f
                out_path = _output_path(f)

                def _place_existing(src: Path, dst: Path) -> Path:
                    if dst.resolve() == src.resolve():
                        return src
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if dst.exists():
                        if force:
                            dst.unlink()
                            shutil.move(src, dst)
                            return dst
                        summary["skipped"].append({"path": str(src), "reason": "output_exists"})
                        return dst
                    shutil.move(src, dst)
                    return dst

                if layout_mode == "nested":
                    if not f.is_relative_to(data_root):
                        output_used = _place_existing(f, out_path)
                else:
                    output_used = _place_existing(f, out_path)

                if validate_existing:
                    rep = validate(output_used, report=False, raise_on_error=False)
                    summary["validated"].append({"path": str(output_used), "ok": rep.get("ok"), "report": rep})

                existing_entry = {"path": str(f), "output": str(output_used), "existing_bdf": True}
                if layout_mode == "flat" and not collection_metadata:
                    df_for_meta = None
                    try:
                        from .io import load as _load_bdf  # lazy import

                        df_for_meta = _load_bdf(output_used)
                    except Exception:
                        df_for_meta = None
                    try:
                        meta_path = _write_metadata(output_used, df=df_for_meta, out_path=output_used)
                        if meta_path:
                            existing_entry["metadata"] = str(meta_path)
                            summary["metadata"].append({"path": str(output_used), "metadata": str(meta_path)})
                    except Exception as meta_err:
                        summary["metadata_failed"].append(
                            {"path": str(output_used), "error": str(meta_err), "code": _error_code(meta_err)}
                        )
                        if raise_on_error:
                            raise
                summary["converted"].append(existing_entry)
                continue
            if incremental and not force:
                key = _state_key(f)
                current = _file_signature(f)
                prev = state["items"].get(key)
                if prev and prev.get("mtime") == current["mtime"] and prev.get("size") == current["size"]:
                    summary["skipped"].append({"path": str(f), "reason": "unchanged"})
                    continue
                if prev and (prev.get("mtime") != current["mtime"] or prev.get("size") != current["size"]):
                    output_ref = prev.get("output")
                    output_path = None
                    if output_ref:
                        output_path = (root / output_ref).resolve()
                    if output_path and output_path.exists():
                        summary["skipped"].append({"path": str(f), "reason": "changed"})
                        continue

            df_pl, _read_meta = read(
                f,
                plugin=plugin,
                validate=validate_converted,
                include_optional=include_optional,
                lazy=False,
            )
            df = df_pl.to_pandas()
            out_path = _output_path(f)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            _save(df, out_path, index=False, human=human)
            converted_entry = {"path": str(f), "output": str(out_path)}
            if incremental:
                key = _state_key(f)
                sig = _file_signature(f)
                output_rel = None
                try:
                    output_rel = out_path.relative_to(root).as_posix()
                except Exception:
                    output_rel = str(out_path)
                state["items"][key] = {**sig, "output": output_rel}
            if layout_mode == "flat" and not collection_metadata:
                try:
                    meta_path = _write_metadata(f, df=df, out_path=out_path)
                    if meta_path:
                        converted_entry["metadata"] = str(meta_path)
                        summary["metadata"].append({"path": str(f), "metadata": str(meta_path)})
                except Exception as meta_err:
                    summary["metadata_failed"].append(
                        {"path": str(f), "error": str(meta_err), "code": _error_code(meta_err)}
                    )
                    if raise_on_error:
                        raise
            summary["converted"].append(converted_entry)
        except Exception as e:
            summary["failed"].append({"path": str(f), "error": str(e), "code": _error_code(e)})
            if raise_on_error:
                raise

    if collection_metadata:
        try:
            include_batteries = battery_mode == "embedded"
            meta_path, dataset_links = _write_collection_metadata(include_batteries=include_batteries)
            if meta_path:
                summary["metadata"].append({"path": str(root), "metadata": str(meta_path)})
            if battery_mode == "separate":
                battery_index = _build_battery_index(root)
                if battery_index:
                    for meta_path in _write_battery_metadata_files(battery_index, dataset_links):
                        summary["metadata"].append({"path": str(meta_path.parent), "metadata": str(meta_path)})
        except Exception as meta_err:
            summary["metadata_failed"].append(
                {"path": str(root), "error": str(meta_err), "code": _error_code(meta_err)}
            )
            if raise_on_error:
                raise
    elif layout_mode == "nested" and p.is_dir():
        try:
            meta_paths = _write_nested_metadata()
            for meta_path in meta_paths:
                summary["metadata"].append({"path": str(meta_path.parent), "metadata": str(meta_path)})
        except Exception as meta_err:
            summary["metadata_failed"].append(
                {"path": str(root), "error": str(meta_err), "code": _error_code(meta_err)}
            )
            if raise_on_error:
                raise

    _save_state()

    return summary
