# src/bdf/cyclers/biologic_mpt.py
from __future__ import annotations
import re
from pathlib import Path
import pandas as pd
from .base import CyclerPlugin, SniffResult

class BioLogicMPT(CyclerPlugin):
    id = "biologic-mpt"
    exts = (".mpt",)

    def sniff(self, path: Path, head: bytes) -> SniffResult:
        txt = head.decode("latin-1", "ignore")
        score, reasons = 0.0, []
        if path.suffix.lower() in self.exts:
            score += 0.3; reasons.append("ext")
        if "BT-Lab ASCII FILE" in txt or "EC-Lab ASCII FILE" in txt:
            score += 0.6; reasons.append("magic")
        return SniffResult(self.id, score, "+".join(reasons), {})

    def parse(self, path: Path) -> pd.DataFrame:
        enc = "latin-1"

        # Read a prefix to locate header position and delimiter
        with open(path, "r", encoding=enc, errors="replace") as f:
            prefix = []
            for _ in range(600):
                line = f.readline()
                if not line:
                    break
                prefix.append(line.rstrip("\n"))

        # Strategy A: use 'Nb header lines : N' (N INCLUDES the column header)
        header_idx = None
        for line in prefix:
            m = re.search(r"Nb\s+header\s+lines\s*:\s*(\d+)", line, re.I)
            if m:
                n = int(m.group(1))
                header_idx = max(n - 1, 0)   # <-- key fix: use N-1
                break

        # Strategy B: scan for a plausible header row
        def looks_like_header(s: str) -> bool:
            l = s.lower()
            return (
                ("\t" in s or "," in s or ";" in s) and
                (
                    "time" in l and "/s" in l or
                    "ewe/v" in l or "ecell/v" in l or
                    "i/m" in l or ("current" in l and "/a" in l)
                )
            )

        if header_idx is None:
            for i, line in enumerate(prefix):
                if looks_like_header(line):
                    header_idx = i
                    break

        if header_idx is None:
            header_idx = 0  # last resort

        # Delimiter from header line
        header_line = prefix[header_idx] if header_idx < len(prefix) else ""
        if "\t" in header_line:
            sep = "\t"
        elif ";" in header_line:
            sep = ";"
        elif "," in header_line:
            sep = ","
        else:
            sep = r"\s+"

        # Parse with correct header row
        df = pd.read_csv(
            path,
            sep=sep,
            skiprows=header_idx,  # skip lines BEFORE header
            header=0,             # use next line as header
            encoding=enc,
            engine="python",
        )

        # Clean column names
        df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]

        # If we still didn't get expected vendor headers, try a defensive retry:
        lower_cols = [c.lower() for c in df.columns]
        if not any(k in lower_cols for k in ("time/s", "ewe/v", "ecell/v", "i/ma", "current / a")):
            # try header_idx-1 in case the file reports N off by one
            if header_idx > 0:
                df = pd.read_csv(
                    path, sep=sep, skiprows=header_idx - 1, header=0, encoding=enc, engine="python"
                )
                df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]

        return df
