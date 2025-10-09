from __future__ import annotations
from pathlib import Path
from typing import Optional
from .data_sources import all_plugins, get_plugin_by_id
from .data_sources.base import SniffResult, CyclerPlugin

def _head(path: Path, n: int = 8192) -> bytes:
    with open(path, "rb") as f: return f.read(n)

def detect(path: Path) -> SniffResult:
    h = _head(path)
    best: Optional[SniffResult] = None
    for cls in all_plugins():
        sr = cls().sniff(path, h)
        if best is None or sr.confidence > best.confidence:
            best = sr
    if best is None:
        raise ValueError("No plugin matched this file.")
    return best

def load_plugin(path: Path, plugin_id: Optional[str] = None) -> CyclerPlugin:
    if plugin_id:
        cls = get_plugin_by_id(plugin_id)
        if not cls:
            known = ", ".join(c.id for c in all_plugins())
            raise ValueError(f"Unknown plugin id: {plugin_id} (known: {known})")
        return cls()
    sr = detect(path)
    cls = get_plugin_by_id(sr.id)
    return cls()
    
def list_plugins() -> list[str]:
    return [c.id for c in all_plugins()]
