from __future__ import annotations
from pathlib import Path
from urllib.parse import urlparse
import pandas as pd

# light imports that never cause cycles
from .detect import detect as _detect, load_plugin, list_plugins as _list_plugins
from .normalize import normalize_columns
from .validate import validate_df  # prints report if asked; warns on non-monotonic time
from .repair import fix_time, clean_bdf, CleanReport  # public cleaning helpers

__all__ = [
    # core I/O
    "read", "parse", "normalize", "validate", "detect", "plugins",
    # datasets helpers
    "datasets", "read_dataset", "load_registry", "get_entry",
    # cleaning
    "fix_time", "clean_bdf", "CleanReport",
    # viz
    "plot",
    # version
    "__version__",
]

# Optional version
try:
    from importlib.metadata import version as _pkg_version  # type: ignore
    __version__ = _pkg_version("bdf")
except Exception:
    __version__ = "0.0.0-dev"


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
    from ._registry import load_registry as _load_registry, get_entry as _get_entry  # lazy
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


def validate(
    obj,
    *,
    report: bool = False,
    raise_on_error: bool = True,
    registry_path: str | Path | None = None,
):
    """
    Validate a BDF DataFrame, a local file path, or an HTTP/HTTPS URL.
    - DataFrame: validate as-is.
    - Path/URL/dataset id: try loading as an already-normalized BDF artifact, else vendor parse→normalize.
    Returns a dict report.
    """
    if isinstance(obj, pd.DataFrame):
        df = obj
    elif isinstance(obj, (str, Path)):
        # try: path/URL/id → local path
        local_path, _ = _resolve_source(obj, registry_path=registry_path)

        # 1) try as BDF artifact
        df = None
        try:
            from ._io import load as _load_bdf  # lazy, supports CSV/Parquet/Feather/JSON
            df_bdf = _load_bdf(local_path)
            # sanity: if it already contains required columns, accept
            from .normalize import REQUIRED  # lazy
            if all(c in df_bdf.columns for c in REQUIRED):
                df = df_bdf
        except Exception:
            df = None

        # 2) fallback to vendor pipeline
        if df is None:
            df = read(local_path, validate=False)
    else:
        raise TypeError("validate() expects a pandas DataFrame, a file path (str/Path), a URL, or a dataset id.")

    return validate_df(df, report=report, raise_on_error=raise_on_error)


def detect(path: str | Path):
    """Return SniffResult with the best-matching plugin and confidence."""
    return _detect(Path(path))


def plugins() -> list[str]:
    """List available plugin ids."""
    return _list_plugins()


# ----- dataset helpers (lazy to avoid cycles) -----
def datasets(registry_path: str | Path | None = None) -> list[str]:
    """Return dataset IDs from the registry."""
    from ._registry import load_registry as _load_registry, list_datasets as _list_datasets  # lazy
    reg = _load_registry(registry_path)
    return _list_datasets(reg)


def load_registry(path: str | Path | None = None):
    from ._registry import load_registry as _load_registry  # lazy
    return _load_registry(path)


def get_entry(reg, entry_id: str):
    from ._registry import get_entry as _get_entry  # lazy
    return _get_entry(reg, entry_id)


def read_dataset(entry_id: str, registry_path: str | Path | None = None, validate: bool = True):
    """
    Fetch by dataset id and return (local_path, BDF DataFrame).
    """
    from ._registry import load_registry as _load_registry, get_entry as _get_entry
    from .fetch import fetch_url
    reg = _load_registry(registry_path)
    entry = _get_entry(reg, entry_id)
    url = entry["url"]
    path = fetch_url(url, sha256=entry.get("sha256"), filename=entry.get("filename"))
    df = read(path, plugin=entry.get("plugin"), validate=validate)
    return path, df

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