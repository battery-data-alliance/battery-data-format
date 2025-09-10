# src/bdf/cyclers/basytec_txt.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Optional

import pandas as pd

from .base import CyclerPlugin, SniffResult

# ---------- helpers ----------

def _try_encodings_head(path: Path, nbytes: int = 4096,
                        encs=("utf-8-sig","utf-8","cp1252","latin-1")) -> Tuple[str, str]:
    raw = path.read_bytes()[:nbytes]
    last = None
    for enc in encs:
        try:
            txt = raw.decode(enc, errors="strict")
            return txt.lower(), enc
        except Exception as e:
            last = e
            continue
    # last resort: permissive
    return raw.decode("latin-1", errors="replace").lower(), "latin-1"

def _read_lines(path: Path, enc: str, max_lines: int = 1000) -> List[str]:
    with open(path, "r", encoding=enc, errors="replace") as f:
        out = []
        for i, ln in enumerate(f):
            if i >= max_lines:
                break
            out.append(ln.rstrip("\r\n"))
        return out

def _looks_like_basytec_header(line: str) -> bool:
    # Accept either with or without leading '~'
    s = line.lstrip("~").strip().lower()
    return ("time[" in s and "]" in s) and ("u[" in s and "]" in s) and ("i[" in s and "]" in s)

def _find_header_row_and_fields(lines: List[str]) -> Tuple[int, List[str]]:
    for i, ln in enumerate(lines[:500]):
        if _looks_like_basytec_header(ln):
            # Strip leading '~' and split on whitespace
            fields = ln.lstrip("~").strip().split()
            return i, [f.strip() for f in fields]
    # Fallback: first non-empty, non-tilde line as header
    for i, ln in enumerate(lines[:200]):
        s = ln.strip()
        if s and not s.startswith("~"):
            return i, s.split()
    raise ValueError("Basytec TXT: could not locate a header row.")

# ---------- plugin ----------

@dataclass
class BasytecTxt(CyclerPlugin):
    id: str = "basytec-txt"
    label: str = "Basytec TXT"
    exts = (".txt", ".dat")

    def detect(self, path: Path) -> SniffResult:
        p = Path(path)
        if p.suffix.lower() not in self.exts:
            return SniffResult(self.id, 0.0, f"Extension {p.suffix} not Basytec TXT")
        head, enc = _try_encodings_head(p)
        if ("resultfile from basytec battery test system" in head
            or ("u[v]" in head and "i[a]" in head and ("time[h]" in head or "time[s]" in head))):
            return SniffResult(self.id, 0.95, f"Basytec-like header (enc={enc})")
        return SniffResult(self.id, 0.55, "TXT candidate; Basytec not confirmed")

    def parse(self, path: Path) -> pd.DataFrame:
        p = Path(path)

        # Pick an encoding that works
        _, enc = _try_encodings_head(p)
        lines = _read_lines(p, enc=enc, max_lines=2000)

        # Find the REAL header row and its fields (even if it starts with '~')
        header_idx, fields = _find_header_row_and_fields(lines)

        # Read the table:
        #  - skip everything up to and including the header line
        #  - do NOT use comment="~" (it would skip the header if it starts with '~')
        #  - whitespace-separated columns (Basytec TXT)
        df = pd.read_csv(
            p,
            sep=r"\s+",
            engine="python",
            header=None,
            names=fields,
            skiprows=header_idx + 1,
            encoding=enc,
            skip_blank_lines=True,
        )

        # Clean column names
        df.columns = [str(c).strip() for c in df.columns]
        return df
