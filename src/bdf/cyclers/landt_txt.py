# src/bdf/cyclers/landt_txt.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import pandas as pd

from .base import CyclerPlugin, SniffResult

TXT_FIRST_TOKEN = "rec#"

@dataclass
class LandtTXT(CyclerPlugin):
    id: str = "landt-txt"
    label: str = "Landt TXT"
    exts = (".txt",)

    def _find_header_row(self, path: Path) -> int:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if line.strip().lower().startswith(TXT_FIRST_TOKEN):
                    return i
        return -1

    def detect(self, path: Path) -> SniffResult:
        if path.suffix.lower() not in self.exts:
            return SniffResult(self.id, 0.0, f"Extension {path.suffix} not TXT")
        hdr = self._find_header_row(path)
        if hdr >= 0:
            return SniffResult(self.id, 0.98, "Found 'Rec#' header line")
        return SniffResult(self.id, 0.4, "TXT extension; header not found")

    def parse(self, path: Path) -> pd.DataFrame:
        path = Path(path)
        header_row = self._find_header_row(path)
        if header_row < 0:
            raise ValueError("Landt TXT: could not locate header row (no 'Rec#' line).")
        # Read using that header row; Landt TXT is tab-delimited
        df = pd.read_csv(
            path,
            engine="python",
            sep="\t",
            header=0,
            skiprows=header_row,
            comment=None,
            on_bad_lines="error",
        )
        df.columns = [str(c).strip() for c in df.columns]
        return df
