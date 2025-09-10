# src/bdf/detect.py
from __future__ import annotations
from pathlib import Path
from typing import Iterable, Optional, Type

from .cyclers import get_builtin_plugins, canonicalize_id
from .cyclers.base import CyclerPlugin, SniffResult

# ---------- internals ----------

def _iter_plugins() -> Iterable[Type[CyclerPlugin] | CyclerPlugin]:
    # get_builtin_plugins returns classes; keep generic
    return get_builtin_plugins()

def _instantiate(cls_or_inst: Type[CyclerPlugin] | CyclerPlugin) -> CyclerPlugin:
    return cls_or_inst() if isinstance(cls_or_inst, type) else cls_or_inst

def _read_head_text(p: Path, n: int = 4096) -> str:
    encodings = ("utf-8-sig", "utf-8", "cp1252", "utf-16", "utf-16-le", "utf-16-be")
    for enc in encodings:
        try:
            with open(p, "r", encoding=enc, errors="ignore") as f:
                return f.read(n).lower()
        except Exception:
            continue
    return ""

def _is_basytec_head(head: str) -> bool:
    # Clear banner often present in Basytec text exports
    if "resultfile from basytec battery test system" in head:
        return True
    # Typical Basytec column tokens
    if "u[v]" in head and "i[a]" in head and ("time[h]" in head or "time[s]" in head):
        return True
    return False

def _is_landt_txt_head(head: str) -> bool:
    # Tokens commonly found in Landt TXT exports
    tokens_any = ("rec#", "test(sec)", "dpt-time", "volts", "amps")
    return any(tok in head for tok in tokens_any)

def _is_landt_csv_head(head: str) -> bool:
    # Landt modern snake_case CSV headers
    return all(t in head for t in ("channel_index", "test_time_s", "voltage_v"))

def _is_neware_csv_head(head: str) -> bool:
    # NEWARE-like columns
    tokens_any = ("total time(s)", "current(a)", "record time(")
    return any(t in head for t in tokens_any)

def _heuristic_by_extension(p: Path) -> Optional[SniffResult]:
    ext = p.suffix.lower()
    head = _read_head_text(p)

    if ext == ".mpt":
        return SniffResult("biologic-mpt", 0.7, "Fallback by extension: .mpt")

    if ext == ".txt":
        # Check Basytec FIRST to avoid misclassifying as Landt
        if _is_basytec_head(head):
            return SniffResult("basytec-txt", 0.95, "TXT with Basytec banner/columns")
        # Then Landt TXT
        if _is_landt_txt_head(head):
            return SniffResult("landt-txt", 0.75, "TXT with Landt-like tokens; fallback")
        # Generic TXT fallback (lowest confidence)
        return SniffResult("landt-txt", 0.6, "Fallback by extension: .txt")

    if ext == ".csv":
        if _is_landt_csv_head(head):
            return SniffResult("landt-csv", 0.9, "CSV with Landt snake_case header")
        if _is_neware_csv_head(head):
            return SniffResult("neware-csv", 0.8, "CSV with NEWARE-like columns")
        return SniffResult("neware-csv", 0.6, "Fallback by extension: .csv")

    return None

# ---------- public API ----------

def detect(path: Path | str) -> SniffResult:
    """
    Run all registered plugins' detect methods and return the best SniffResult.
    Falls back to a heuristic by extension/content if confidence is low.
    """
    p = Path(path)
    best_sr: Optional[SniffResult] = None

    for cls_or_inst in _iter_plugins():
        pl = _instantiate(cls_or_inst)
        try:
            sr = pl.detect(p)
        except Exception:
            continue
        if best_sr is None or sr.confidence > best_sr.confidence:
            best_sr = sr

    if best_sr is None or best_sr.confidence < 0.5:
        hs = _heuristic_by_extension(p)
        if hs is not None:
            return hs

    if best_sr is None:
        names = [getattr(ci, "__name__", str(ci)) for ci in get_builtin_plugins()]
        raise ValueError(f"Could not detect cycler for: {p}\n(Registered plugins: {names})")

    return best_sr

def load_plugin(path: Path | str, as_: Optional[str] = None) -> CyclerPlugin:
    """
    Return an instantiated plugin for 'path'.
    If 'as_' is provided, resolve aliases and return that plugin.
    Otherwise, run detect(path) and return the matching plugin instance.
    """
    # Build a map of id -> class
    id_to_class: dict[str, Type[CyclerPlugin]] = {}
    for cls_or_inst in _iter_plugins():
        pl = _instantiate(cls_or_inst)
        # ensure we store the class, not the instance
        id_to_class[pl.id] = type(pl)

    if as_:
        pid = canonicalize_id(as_)  # e.g., "landt" -> "landt-txt"
        cls = id_to_class.get(pid)
        if not cls:
            known = ", ".join(sorted(id_to_class))
            raise ValueError(f"Unknown plugin id: {pid} (known: {known})")
        return cls()

    sr = detect(path)
    cls = id_to_class.get(sr.id)
    if not cls:
        known = ", ".join(sorted(id_to_class))
        raise ValueError(f"Detected plugin {sr.id!r} not registered (known: {known})")
    return cls()

__all__ = ["detect", "load_plugin"]
