from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._errors import BDFValidationError
    from ._explore import explore
    from ._ingest import ingest
    from ._registry import get_entry, load_registry
    from ._templates import templates
    from ._validate import validate, validate_df
    from .io import read, save, scan
    from .plugins import detect
    from .registry_ld import build_registry, search, sparql
    from .repair import CleanReport, clean
    from .table_normalizers import normalize
    from .visualize import plot

__all__ = [
    # core I/O
    "read",
    "scan",
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


# name: module
_LAZY_ATTRS: dict[str, str] = {
    "read": ".io",
    "scan": ".io",
    "save": ".io",
    "normalize": ".table_normalizers",
    "validate": "._validate",
    "validate_df": "._validate",
    "BDFValidationError": "._errors",
    "detect": ".plugins",
    "load_registry": "._registry",
    "get_entry": "._registry",
    "build_registry": ".registry_ld",
    "search": ".registry_ld",
    "sparql": ".registry_ld",
    "clean": ".repair",
    "CleanReport": ".repair",
    "plot": ".visualize",
    "explore": "._explore",
    "ingest": "._ingest",
    "templates": "._templates",
}


def __getattr__(name: str) -> Any:  # noqa: ANN401
    """Lazy imports so `import bdf` doesn't pull in heavy imports."""
    module = _LAZY_ATTRS.get(name)
    if module is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    from importlib import import_module

    value = getattr(import_module(module, __name__), name)
    # Cache so later lookups skip __getattr__
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)


# Optional version
try:
    from importlib.metadata import version as _pkg_version  # type: ignore

    try:
        __version__ = _pkg_version("batterydf")
    except Exception:
        __version__ = _pkg_version("bdf")
except Exception:
    __version__ = "0.0.0-dev"


# Warning format settings

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


# ----- dataset helpers (lazy to avoid cycles) -----
def datasets(registry_path: str | Path | None = None) -> list[str]:
    """Return dataset IDs from the registry."""
    from ._registry import list_datasets as _list_datasets  # lazy

    return _list_datasets(registry_path)
