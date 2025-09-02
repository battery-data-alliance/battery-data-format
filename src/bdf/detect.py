# src/bdf/detect.py
from __future__ import annotations
from pathlib import Path
from typing import Iterable, Optional
from .cyclers import get_builtin_plugins, canonicalize_id
from .cyclers.base import CyclerPlugin, SniffResult  # adjust if your base path differs

def _iter_plugins() -> Iterable[CyclerPlugin]:
    for cls in get_builtin_plugins():
        try:
            yield cls()  # class -> instance
        except TypeError:
            # if get_builtin_plugins() returns instances already
            yield cls  # type: ignore[misc]

def load_plugin(path: Path | str, as_: Optional[str] = None) -> CyclerPlugin:
    """Return a plugin instance either by explicit id/alias or by sniffing."""
    path = Path(path)
    if as_:
        target = canonicalize_id(as_)
        for pl in _iter_plugins():
            if getattr(pl, "id", None) == target:
                return pl
        raise ValueError(f"Unknown plugin id: {as_}")

    # sniff: choose highest-confidence plugin
    best_pl: Optional[CyclerPlugin] = None
    best_score = float("-inf")
    reason = ""
    for pl in _iter_plugins():
        try:
            sr: SniffResult = pl.detect(path)
        except Exception:
            continue
        if sr.confidence > best_score:
            best_score = sr.confidence
            best_pl = pl
            reason = sr.reason
    if best_pl is None:
        raise ValueError(f"No suitable cycler plugin found for: {path}")
    return best_pl

# (optional) a convenience facade if you expose detect() elsewhere
def detect(path: Path | str) -> SniffResult:
    path = Path(path)
    # run all and return best SniffResult
    best_sr: Optional[SniffResult] = None
    for pl in _iter_plugins():
        try:
            sr = pl.detect(path)
        except Exception:
            continue
        if best_sr is None or sr.confidence > best_sr.confidence:
            best_sr = sr
    if best_sr is None:
        raise ValueError(f"Could not detect cycler for: {path}")
    return best_sr
