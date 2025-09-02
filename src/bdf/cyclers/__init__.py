# src/bdf/cyclers/__init__.py
from __future__ import annotations
from typing import List, Type, Dict

from .base import CyclerPlugin, SniffResult
from .biologic_mpt import BioLogicMPT
from .neware_csv import NewareCSV
from .landt_csv import LandtCSV
from .landt_txt import LandtTXT

PLUGIN_CLASSES: List[Type[CyclerPlugin]] = [
    BioLogicMPT,
    NewareCSV,
    LandtCSV,
    LandtTXT,
]

ALIASES: Dict[str, str] = {
    "biologic": "biologic-mpt",
    "bio-logic": "biologic-mpt",
    "biologic-mpt": "biologic-mpt",
    "neware": "neware-csv",
    "neware-csv": "neware-csv",
    "landt": "landt-csv",   # default alias → CSV
    "landt-csv": "landt-csv",
    "landt-txt": "landt-txt",
}

def get_builtin_plugins() -> List[Type[CyclerPlugin]]:
    return PLUGIN_CLASSES

def canonicalize_id(pid: str) -> str:
    return ALIASES.get(pid, pid)

__all__ = ["CyclerPlugin", "SniffResult", "get_builtin_plugins", "canonicalize_id"]
