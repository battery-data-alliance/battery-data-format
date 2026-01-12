from __future__ import annotations

import warnings

# mypy: ignore-errors
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

# light imports that never cause cycles
from .detect import detect as _detect, list_plugins as _list_plugins, load_plugin
from .normalize import guess_plugin_by_columns, normalize_columns
from .repair import CleanReport, clean  # public cleaning helpers
from .validate import BDFValidationError, validate_df  # prints report if asked; warns on non-monotonic time

__all__ = [
    # core I/O
    "read", "parse", "normalize", "validate", "detect", "detect_cycler", "plugins",
    # datasets helpers
    "datasets", "load_registry", "get_entry",
    # cleaning
    "clean", "CleanReport",
    # viz
    "plot", "explore", "ingest",
    # version
    "__version__",
    # errors
    "BDFValidationError",
]

# Optional version
try:
    from importlib.metadata import version as _pkg_version  # type: ignore
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

# Install the formatter
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

    # 2) URL → cache it
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


# -------------------------------
# public API
# -------------------------------
def read(
    source: str | Path,
    plugin: str | None = None,
    validate: bool = True,
    include_optional: bool = True,
    registry_path: str | Path | None = None,
) -> pd.DataFrame:
    """
    Universal reader → BDF DataFrame (strict columns).
      - source: local path, http(s) URL, or dataset id (from datasets.json)
      - plugin: force a specific cycler plugin id (optional)
      - validate: run BDF validator (prints warnings/report if configured in validate_df)
    """
    local_path, plugin_hint = _resolve_source(source, registry_path=registry_path)
    plg = load_plugin(local_path, plugin_id=(plugin or plugin_hint))
    df_raw = plg.parse(local_path)
    df_raw = plg.augment(df_raw)
    try:
        df = normalize_columns(df_raw, plugin=plg, strict=True, include_optional=include_optional)
    except ValueError:
        if plugin is not None:
            raise
        alt = guess_plugin_by_columns(df_raw, current_id=getattr(plg, "id", None))
        if not alt:
            raise
        if getattr(alt, "id", None) != getattr(plg, "id", None):
            warnings.warn(
                f"Normalization failed with plugin '{getattr(plg, 'id', '?')}', retrying with column-based guess '{getattr(alt, 'id', '?')}'."
            )
        plg = alt
        df_raw = plg.parse(local_path)
        df_raw = plg.augment(df_raw)
        df = normalize_columns(df_raw, plugin=plg, strict=True, include_optional=include_optional)
    if hasattr(plg, "fixup"):
        df = plg.fixup(df)
    if validate:
        validate_df(df)
    return df



def parse(source: str | Path, plugin: str | None = None, registry_path: str | Path | None = None) -> pd.DataFrame:
    """Parse vendor file only (no normalization/validation)."""
    local_path, plugin_hint = _resolve_source(source, registry_path=registry_path)
    plg = load_plugin(local_path, plugin_id=(plugin or plugin_hint))
    return plg.parse(local_path)


def normalize(df: pd.DataFrame, plugin: str | None = None) -> pd.DataFrame:
    """
    Normalize a DataFrame to canonical BDF columns.
    If plugin id is provided, the plugin's local synonyms are applied too.
    """
    plg = None
    if plugin:
        from .data_sources import get_plugin_by_id  # lazy
        cls = get_plugin_by_id(plugin)
        if cls:
            plg = cls()
    return normalize_columns(df, plugin=plg, strict=True)


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
    cols = [c.strip() for c in header.split(",")]
    # import lazily to avoid cycles
    from .normalize import REQUIRED
    have = 0
    for req in REQUIRED:
        if any(req.lower() == c.lower() for c in cols):
            have += 1
    return have == len(REQUIRED)

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
    raise_on_error: bool = False,   # <- default False so notebooks don’t crash
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
            if any(name_lc.endswith(suf) for suf in (
                ".bdf.csv", ".bdf.csv.gz",
                ".bdf.parquet",
                ".bdf.feather",
                ".bdf.json", ".bdf.json.gz",
            )):
                return True
            # header sniff for CSV only (cheap and safe)
            if name_lc.endswith(".csv") or name_lc.endswith(".csv.gz"):
                try:
                    with (gzip.open(path, "rt") if name_lc.endswith(".gz") else open(path, encoding="utf-8", errors="ignore")) as f:
                        head = "".join([f.readline() for _ in range(2)]).lower()
                    # must contain all required labels (case-insensitive)
                    req = {"test time / s", "voltage / v", "current / a"}
                    header_line = head.splitlines()[0] if head else ""
                    return all(r in header_line for r in req)
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
    return _bad_report(kind="type_error", detail="validate() expects a pandas DataFrame, a file path (str/Path), a URL, or a dataset id.")


def detect(path: str | Path):
    """Return SniffResult with the best-matching plugin and confidence."""
    return _detect(Path(path))


# Backwards-friendly alias used by CLI
detect_cycler = detect


def plugins() -> list[str]:
    """List available plugin ids."""
    return _list_plugins()


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
            "bdf.plot() requires the visualization module (matplotlib). "
            "Ensure matplotlib is installed."
        ) from e
    return _plot(*args, **kwargs)


def explore(*args, **kwargs):
    """
    Forward to bdf._explore.explore(...).

    Example:
        bdf.explore(df, xdata="Test Time / s", ydata=["Voltage / V"], backend="bokeh")
    """
    try:
        from ._explore import explore as _explore
    except Exception as e:
        raise RuntimeError("bdf.explore() is unavailable.") from e
    return _explore(*args, **kwargs)


def ingest(
    source: str | Path,
    *,
    out_dir: str | Path | None = None,
    format: str = "parquet",
    recursive: bool = True,
    validate_existing: bool = True,
    validate_converted: bool = True,
    include_optional: bool = True,
    plugin: str | None = None,
    raise_on_error: bool = False,
):
    """
    Convert raw vendor files to BDF and validate existing BDF artifacts.

    - source: file or directory
    - format: "parquet" (default) or "csv"
    - out_dir: optional output root for converted files
    - validate_existing: validate files that already look like BDF
    - validate_converted: validate after conversion
    - plugin: force a specific plugin id for raw files

    Returns a summary dict with converted/validated/failed entries.
    """
    p = Path(source)
    if not p.exists():
        raise FileNotFoundError(p)

    fmt = format.lower().strip()
    if fmt not in {"parquet", "csv"}:
        raise ValueError("format must be 'parquet' or 'csv'")

    root = p if p.is_dir() else p.parent
    out_root = Path(out_dir) if out_dir else root

    def _strip_all_suffixes(path: Path) -> Path:
        name = path.name
        while True:
            suffix = Path(name).suffix
            if not suffix:
                break
            name = Path(name).stem
        return path.with_name(name)

    def _output_path(src: Path) -> Path:
        rel = src.relative_to(root) if src.is_relative_to(root) else Path(src.name)
        base = _strip_all_suffixes(rel)
        suffix = ".bdf.parquet" if fmt == "parquet" else ".bdf.csv"
        return out_root / base.parent / f"{base.name}{suffix}"

    # Snapshot file list before writing outputs
    if p.is_dir():
        pattern = "**/*" if recursive else "*"
        files = [f for f in p.glob(pattern) if f.is_file()]
    else:
        files = [p]

    from .io import save as _save  # lazy import

    summary = {
        "converted": [],
        "validated": [],
        "failed": [],
        "skipped": [],
    }

    for f in files:
        try:
            if _looks_like_bdf_artifact(f):
                if validate_existing:
                    rep = validate(f, report=False, raise_on_error=False)
                    summary["validated"].append({"path": str(f), "ok": rep.get("ok"), "report": rep})
                else:
                    summary["skipped"].append({"path": str(f), "reason": "already_bdf"})
                continue

            df = read(
                f,
                plugin=plugin,
                validate=validate_converted,
                include_optional=include_optional,
            )
            out_path = _output_path(f)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            _save(df, out_path, index=False)
            summary["converted"].append({"path": str(f), "output": str(out_path)})
        except Exception as e:
            summary["failed"].append({"path": str(f), "error": str(e)})
            if raise_on_error:
                raise

    return summary
