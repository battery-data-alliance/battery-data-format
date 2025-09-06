# src/bdf/cyclers/base.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import pandas as pd
from typing import Any, Dict

@dataclass
class SniffResult:
    id: str
    confidence: float
    reason: str
    meta: Dict[str, Any] = field(default_factory=dict)  # <-- add a default

class CyclerPlugin:
    id = "abstract"
    exts: tuple[str, ...] = ()
    def sniff(self, path: Path, head: bytes) -> SniffResult: ...
    def parse(self, path: Path) -> pd.DataFrame: ...
