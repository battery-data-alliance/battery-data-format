from pathlib import Path

# ---- version ----
try:
    from ._version import __version__
except Exception:
    try:
        from importlib.metadata import version as _pkg_version
        __version__ = _pkg_version("bdf")
    except Exception:
        __version__ = "0+unknown"

# ---- public facades ----
def detect_cycler(path):
    from .detect import detect
    return detect(path)

def read_raw_to_bdf(path, as_=None, *, validate: bool = True):
    """Auto-detect (or force via as_) → parse vendor file → normalize to BDF; validate if requested."""
    from .detect import load_plugin
    from .normalize import to_bdf
    from .validate import validate_df

    plugin = load_plugin(path, as_=as_)
    df_vendor = plugin.parse(path)
    df_bdf = to_bdf(df_vendor, plugin_id=plugin.id)
    if validate:
        validate_df(df_bdf, strict=True)
    return df_bdf

def validate_bdf(path_or_df, *, strict: bool = False):
    """Validate a BDF DataFrame or file path."""
    from .validate import validate_df, validate_path
    import os
    try:
        import pandas as pd
    except Exception:
        pd = None

    if isinstance(path_or_df, (str, bytes, os.PathLike, Path)):
        return validate_path(path_or_df, strict=strict)
    if pd is not None and isinstance(path_or_df, pd.DataFrame):
        return validate_df(path_or_df, strict=strict)
    raise TypeError("validate_bdf expects a path (str/Path) or a pandas DataFrame.")

# define __all__ before extending
__all__ = ["detect_cycler", "read_raw_to_bdf", "validate_bdf", "__version__"]

# optional re-exports (only if requests/platformdirs are present)
try:
    from .datafetch import load_registry, list_registry_entries, get_entry, fetch_url, load_bdf_from_entry
except Exception:
    pass
else:
    __all__.extend(["load_registry", "list_registry_entries", "get_entry", "fetch_url", "load_bdf_from_entry"])
