# src/bdf/cyclers/__init__.py
from __future__ import annotations
from typing import List, Type, Dict

from .base import CyclerPlugin, SniffResult

# Import plugins defensively so one failure doesn't break them all
_PLUGINS: List[Type[CyclerPlugin]] = []

try:
    from .biologic_mpt import BioLogicMPT
    _PLUGINS.append(BioLogicMPT)
except Exception:
    pass

try:
    from .neware_csv import NewareCSV
    _PLUGINS.append(NewareCSV)
except Exception:
    pass

try:
    from .landt_csv import LandtCSV
    _PLUGINS.append(LandtCSV)
except Exception:
    pass

try:
    from .landt_txt import LandtTXT
    _PLUGINS.append(LandtTXT)
except Exception:
    pass

PLUGIN_CLASSES: List[Type[CyclerPlugin]] = _PLUGINS

ALIASES: Dict[str, str] = {
    "biologic": "biologic-mpt",
    "bio-logic": "biologic-mpt",
    "biologic-mpt": "biologic-mpt",
    "neware": "neware-csv",
    "neware-csv": "neware-csv",
    "landt": "landt-txt",    # default TXT for bare "landt"
    "landt-txt": "landt-txt",
    "landt-csv": "landt-csv",
}

def get_builtin_plugins() -> List[Type[CyclerPlugin]]:
    return PLUGIN_CLASSES

def canonicalize_id(pid: str) -> str:
    return ALIASES.get(pid, pid)

__all__ = ["CyclerPlugin", "SniffResult", "get_builtin_plugins", "canonicalize_id"]
