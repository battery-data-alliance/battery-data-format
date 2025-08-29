# Lightweight, side-effect-free package init
from .validate import validate_df, BDFValidationError
from pathlib import Path

# Version: prefer a generated _version.py; otherwise ask importlib; else fallback.
try:
    from ._version import __version__  # optional file you may add later
except Exception:
    try:
        from importlib.metadata import version
        __version__ = version("bdf")
    except Exception:
        __version__ = "0+unknown"

# Public facades used by tests
def detect_cycler(path):
    from .detect import detect
    return detect(path)

def read_raw_to_bdf(path, as_=None, *, validate: bool = True):
    """
    Auto-detect (or force via as_) → parse vendor file → normalize to BDF DataFrame.
    If validate=True, run BDF validation and raise BDFValidationError on failure.
    """
    from .detect import load_plugin
    from .normalize import to_bdf
    plugin = load_plugin(path, as_=as_)
    df_vendor = plugin.parse(path)
    df_bdf = to_bdf(df_vendor, plugin_id=plugin.id)

    if validate:
        report = validate_df(df_bdf, strict=True)  # raises on error
    return df_bdf

def validate_bdf(path_or_df, *, strict: bool = False):
    """Validate a BDF DataFrame or file path. Returns ValidationReport (or raises if strict)."""
    from .validate import validate_df, validate_path
    import os
    try:
        import pandas as pd  # only for isinstance check below
    except Exception:
        pd = None

    # accept str/bytes/Path/PathLike
    if isinstance(path_or_df, (str, bytes, os.PathLike, Path)):
        return validate_path(path_or_df, strict=strict)

    if pd is not None and isinstance(path_or_df, pd.DataFrame):
        return validate_df(path_or_df, strict=strict)

    raise TypeError(
        "validate_bdf expects a path (str/Path) or a pandas DataFrame."
    )

__all__ = ["detect_cycler", "read_raw_to_bdf", "validate_bdf", "__version__"]

