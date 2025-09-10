# src/bdf/cyclers/__init__.py
from __future__ import annotations
from typing import List, Type, Dict

from .base import CyclerPlugin, SniffResult

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

# NEW: Basytec TXT
try:
    from .basytec_txt import BasytecTxt
    _PLUGINS.append(BasytecTxt)
except Exception:
    pass

PLUGIN_CLASSES: List[Type[CyclerPlugin]] = _PLUGINS

ALIASES: Dict[str, str] = {
    "biologic": "biologic-mpt",
    "bio-logic": "biologic-mpt",
    "biologic-mpt": "biologic-mpt",

    "neware": "neware-csv",
    "neware-csv": "neware-csv",

    "landt": "landt-txt",
    "landt-txt": "landt-txt",
    "landt-csv": "landt-csv",

    "basytec": "basytec-txt",
    "basytec-txt": "basytec-txt",
}

def get_builtin_plugins() -> List[Type[CyclerPlugin]]:
    return PLUGIN_CLASSES

def canonicalize_id(pid: str) -> str:
    return ALIASES.get((pid or "").lower(), pid)

__all__ = ["CyclerPlugin", "SniffResult", "get_builtin_plugins", "canonicalize_id"]
