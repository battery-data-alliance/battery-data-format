# src/bdf/cyclers/base.py
from dataclasses import dataclass
from pathlib import Path
import pandas as pd

@dataclass
class SniffResult:
    id: str
    confidence: float
    reason: str
    meta: dict

class CyclerPlugin:
    id = "abstract"
    exts: tuple[str, ...] = ()
    def sniff(self, path: Path, head: bytes) -> SniffResult: ...
    def parse(self, path: Path) -> pd.DataFrame: ...
