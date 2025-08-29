# src/bdf/cyclers/neware_csv.py
from __future__ import annotations
from pathlib import Path
import re
import io
import pandas as pd
from .base import CyclerPlugin, SniffResult

HEADER_TOKENS = {
    "english": {"Voltage(V)", "Current(A)", "Cycle", "Step", "Record", "Time(s)", "DateTime"},
    "chinese": {"电压(V)", "电流(A)", "时间(s)"}
}

class NewareCSV(CyclerPlugin):
    id = "neware-csv"
    exts = (".csv",)

    def sniff(self, path: Path, head: bytes) -> SniffResult:
        score, reasons = 0.0, []
        # Extension is generic, small weight
        if path.suffix.lower() in self.exts:
            score += 0.1; reasons.append("ext")
        # Look for NEWARE/BTS markers or typical headers
        txt = head.decode("utf-8", "ignore")
        if re.search(r"NEWARE|BTS\s*DA|Neware", txt, re.I):
            score += 0.5; reasons.append("banner")
        if any(tok in txt for tok in HEADER_TOKENS["english"] | HEADER_TOKENS["chinese"]):
            score += 0.4; reasons.append("header-tokens")
        return SniffResult(self.id, score, "+".join(reasons), {})

    def _detect_encoding(self, path: Path) -> str:
        # NEWARE exports are often UTF-8-SIG or GB encodings
        for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
            try:
                with open(path, "r", encoding=enc, errors="strict") as f:
                    f.read(2048)
                return enc
            except Exception:
                continue
        return "utf-8"

    def _looks_like_header(self, line: str) -> bool:
        # Consider a line a header if it contains several known tokens separated by delimiters
        if not ("," in line or "\t" in line or ";" in line):
            return False
        bag = set(re.split(r"[,\t;]", line.strip()))
        bag_lc = {s.strip() for s in bag}
        # match if at least two typical labels appear
        hits = (
            len(bag & HEADER_TOKENS["english"]) +
            len(bag & HEADER_TOKENS["chinese"])
        )
        return hits >= 2

    def parse(self, path: Path) -> pd.DataFrame:
        enc = self._detect_encoding(path)

        # Read prefix to find header row
        with open(path, "r", encoding=enc, errors="replace") as f:
            prefix = []
            for _ in range(1000):
                line = f.readline()
                if not line:
                    break
                prefix.append(line.rstrip("\n"))

        header_idx = None
        for i, line in enumerate(prefix):
            if self._looks_like_header(line):
                header_idx = i
                break
        if header_idx is None:
            header_idx = 0

        # Choose delimiter from header line
        header_line = prefix[header_idx] if header_idx < len(prefix) else ""
        if "\t" in header_line:
            sep = "\t"
        elif ";" in header_line:
            sep = ";"
        else:
            sep = ","

        df = pd.read_csv(
            path,
            sep=sep,
            skiprows=header_idx,
            header=0,
            encoding=enc,
            engine="python",
        )
        df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]
        return df
