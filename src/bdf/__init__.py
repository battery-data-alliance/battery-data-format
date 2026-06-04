from __future__ import annotations

import os
import warnings

# mypy: ignore-errors
from pathlib import Path
from typing import Any

import pandas as pd

# Extracted submodules
from ._source import (
    _candidate_plugins,
    _looks_like_bdf_artifact,
    _resolve_source,
)

# light imports that never cause cycles
from .detect import detect as _detect, list_plugins as _list_plugins, load_plugin
from .normalize import guess_plugin_by_columns, normalize_columns
from .repair import CleanReport, clean  # public cleaning helpers
from .validate import BDFValidationError, validate_df  # prints report if asks; warns on non-monotonic time

__all__ = [
    # core I/O
    "read", "parse", "normalize", "validate", "detect", "plugins",
    # datasets helpers
    "datasets", "load_registry", "get_entry",
    # registry LD helpers
    "build_registry", "search", "sparql",
    # cleaning
    "clean", "CleanReport",
    # viz
    "plot", "explore", "ingest", "templates",
    # version
    "__version__",
    # errors
    "BDFValidationError",
    # plugin loading
    "load_plugin",
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
# public API  -- core read / parse / normalize
# -------------------------------
def read(
    source: str | Path,
    plugin: str | None = None,
    normalize: bool = True,
    validate: bool = True,
    include_optional: bool = True,
    registry_path: str | Path | None = None,
) -> pd.DataFrame:
    """
    Universal reader -> DataFrame.
      - source: local path, http(s) URL, or dataset id (from datasets.json)
      - plugin: force a specific cycler plugin id (optional)
      - normalize: if True, normalize to BDF columns; if False, parse only
      - validate: validate BDF artifacts (or normalized output)
    """
    local_path, plugin_hint = _resolve_source(source, registry_path=registry_path)
    if _looks_like_bdf_artifact(local_path):
        from .io import load as _load_bdf  # lazy import
        df = _load_bdf(local_path)
        from .normalize import canonicalize_legacy_labels  # lazy import
        df, legacy = canonicalize_legacy_labels(df)
        if legacy:
            warnings.warn(
                "Legacy BDF column labels detected (skos:altLabel/notation). "
                "They were normalized to preferred labels.",
                stacklevel=2,
            )
        if validate:
            validate_df(df)
        return df
    if not normalize:
        if validate:
            raise ValueError("validate=True requires a BDF artifact or normalize=True.")
        parse_errors: list[tuple[str, str]] = []
        for plg in _candidate_plugins(local_path, plugin=plugin, plugin_hint=plugin_hint):
            try:
                return plg.parse(local_path)
            except Exception as exc:
                if plugin is not None:
                    raise
                parse_errors.append((getattr(plg, "id", "?"), f"{type(exc).__name__}: {exc}"))
        details = "; ".join(f"{pid} -> {msg}" for pid, msg in parse_errors[:4])
        raise RuntimeError(f"Could not parse source '{local_path}'. {details}")

    normalize_errors: list[tuple[str, str]] = []
    for plg in _candidate_plugins(local_path, plugin=plugin, plugin_hint=plugin_hint):
        try:
            df_raw = plg.parse(local_path)
            df_raw = plg.augment(df_raw)
        except Exception as exc:
            if plugin is not None:
                raise
            normalize_errors.append((getattr(plg, "id", "?"), f"parse failed: {type(exc).__name__}: {exc}"))
            continue

        try:
            df = normalize_columns(df_raw, plugin=plg, strict=True, include_optional=include_optional)
        except ValueError as exc:
            if plugin is not None:
                raise
            alt = guess_plugin_by_columns(df_raw, current_id=getattr(plg, "id", None))
            if not alt or getattr(alt, "id", None) == getattr(plg, "id", None):
                normalize_errors.append(
                    (getattr(plg, "id", "?"), f"normalize failed: {type(exc).__name__}: {exc}")
                )
                continue
            try:
                warnings.warn(
                    f"Normalization failed with plugin '{getattr(plg, 'id', '?')}', retrying with column-based guess '{getattr(alt, 'id', '?')}'."
                    , stacklevel=2
                )
                plg = alt
                df_raw = plg.parse(local_path)
                df_raw = plg.augment(df_raw)
                df = normalize_columns(df_raw, plugin=plg, strict=True, include_optional=include_optional)
            except Exception as alt_exc:
                normalize_errors.append(
                    (
                        getattr(alt, "id", "?"),
                        f"retry failed: {type(alt_exc).__name__}: {alt_exc}",
                    )
                )
                continue
        except Exception as exc:
            if plugin is not None:
                raise
            normalize_errors.append((getattr(plg, "id", "?"), f"normalize failed: {type(exc).__name__}: {exc}"))
            continue

        if hasattr(plg, "fixup"):
            df = plg.fixup(df)
        if validate:
            validate_df(df)
        return df

    details = "; ".join(f"{pid} -> {msg}" for pid, msg in normalize_errors[:6])
    raise RuntimeError(f"Could not parse+normalize source '{local_path}'. {details}")



def parse(source: str | Path, plugin: str | None = None, registry_path: str | Path | None = None) -> pd.DataFrame:
    """Parse vendor file only (no normalization/validation)."""
    return read(source, plugin=plugin, normalize=False, validate=False, registry_path=registry_path)


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


# src/bdf/__init__.py (validate)
# src/bdf/__init__.py  (replace the existing validate with this)

def validate(
    obj,
    *,
    report: bool = False,
    raise_on_error: bool = False,   # <- default False so notebooks don't crash
    registry_path: str | Path | None = None,
):
    """
    Validate a BDF DataFrame, a local file path, an HTTP/HTTPS URL, or a dataset id.

    Behavior:
      - DataFrame: validate as-is (no transformations).
      - Path/URL/id: only treated as a *BDF artifact* (strict). We do NOT vendor-parse
        or normalize here. If it doesn't look like BDF, you'll get an 'ok=False' report.

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
                    header_line = head.splitlines()[0] if head else ""
                    cols_l = {c.strip().lower() for c in header_line.split(",")}
                    from .normalize import spec
                    for q, s in spec.COLUMNS.items():
                        if not s.get("required") or bool(s.get("deprecated")):
                            continue
                        pref = spec._label_for(q).lower()
                        notation = spec.notation_for(q).lower()
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
    return _bad_report(kind="type_error", detail="validate() expects a pandas DataFrame, a file path (str/Path), a URL, or a dataset id.")


def detect(path: str | Path):
    """Return SniffResult with the best-matching plugin and confidence."""
    return _detect(Path(path))


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
            "bdf.plot() requires the visualization module (matplotlib). "
            "Ensure matplotlib is installed."
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


def ingest(*args, **kwargs):
    """
    Convert raw vendor files to BDF and validate existing BDF artifacts.

    Delegates to bdf._ingest.ingest(); see that module for full parameter docs.
    """
    from ._ingest import ingest as _ingest
    return _ingest(*args, **kwargs)
