# src/bdf/cyclers/landt_csv.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Optional
import csv

import pandas as pd

from .base import CyclerPlugin, SniffResult


# Minimal signature set for Landt CSV
CSV_KEYS = {
    "channel_index",
    "cycle_index",
    "step_index",
    "date_time_iso_string",
    "test_time_s",
    "step_time_s",
    "current_a",
    "voltage_v",
}

# ---------------------------
# Helpers
# ---------------------------

def _read_lines(path: Path, max_lines: int = 4000, encodings=("utf-8-sig", "utf-8", "cp1252")) -> List[str]:
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, errors="ignore") as f:
                out = []
                for i, line in enumerate(f):
                    if i >= max_lines:
                        break
                    out.append(line.rstrip("\r\n"))
                return out
        except Exception:
            continue
    # last resort
    return path.read_bytes().decode("utf-8", errors="ignore").splitlines()[:max_lines]


def _guess_delim(header: str) -> str:
    counts = {d: header.count(d) for d in (",", ";", "\t", "|")}
    best = max(counts, key=counts.get)
    return best or ","


def _find_header_row_and_fields(lines: List[str]) -> Tuple[int, str, List[str]]:
    """
    Find the header line containing Landt CSV keys; return (idx, delim, fields).
    Raises ValueError if not found.
    """
    for i, line in enumerate(lines[:500]):
        low = line.strip().lower()
        hits = [k for k in CSV_KEYS if k in low]
        if len(hits) >= 4:
            delim = _guess_delim(line)
            # Parse header with csv.reader to preserve exact field names
            try:
                fields = next(csv.reader([line], delimiter=delim, quotechar='"'))
            except Exception:
                fields = [p.strip() for p in line.split(delim)]
            fields = [str(c).strip() for c in fields]
            return i, delim, fields
    raise ValueError("Landt CSV: could not locate header row with expected fields.")


def _read_landt_csv_robust(path: Path, header_idx: int, delim: str, fields: List[str]) -> pd.DataFrame:
    """
    Robust line-by-line reader:
      - skip lines up to (and including) header_idx
      - split each row with maxsplit=ncols-1 so any extra delimiters fold into the last column (e.g., step_name)
    """
    ncols = len(fields)
    rows: List[List[str]] = []

    # Try a few encodings
    fh = None
    for enc in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            fh = open(path, "r", encoding=enc, errors="ignore")
            break
        except Exception:
            continue
    if fh is None:
        fh = open(path, "r", encoding="utf-8", errors="ignore")

    with fh:
        for i, raw in enumerate(fh):
            if i <= header_idx:
                continue
            line = raw.rstrip("\r\n")
            if not line.strip():
                continue
            # First try csv.reader (in case quotes are correct)
            try:
                parts = next(csv.reader([line], delimiter=delim, quotechar='"'))
            except Exception:
                parts = line.split(delim)
            # If row too long due to stray delimiters, fold into last column
            if len(parts) > ncols:
                head = parts[: ncols - 1]
                tail = delim.join(parts[ncols - 1 :])
                parts = head + [tail]
            # If row too short, pad
            if len(parts) < ncols:
                parts = parts + [""] * (ncols - len(parts))
            rows.append([p.strip() for p in parts])

    df = pd.DataFrame(rows, columns=fields)
    df.columns = [str(c).strip() for c in df.columns]
    return df


# ---------------------------
# Plugin
# ---------------------------

@dataclass
class LandtCSV(CyclerPlugin):
    """Landt modern CSV export (snake_case headers, SI units)."""
    id: str = "landt-csv"
    label: str = "Landt CSV"
    exts = (".csv",)

    def detect(self, path: Path) -> SniffResult:
        if path.suffix.lower() not in self.exts:
            return SniffResult(self.id, 0.0, f"Extension {path.suffix} not CSV")

        try:
            # Reuse the robust header finder used by parse()
            lines = _read_lines(Path(path), max_lines=4000)
            header_idx, delim, fields = _find_header_row_and_fields(lines)
            # Check how many signature fields we see
            low_fields = [f.strip().lower() for f in fields]
            hits = [k for k in CSV_KEYS if any(k == lf for lf in low_fields)]
            msg = f"Landt CSV header at line {header_idx} (sep={delim!r}); fields: {', '.join(hits[:6])}..."
            return SniffResult(self.id, 0.98, msg)
        except Exception:
            # Fall back to a shallow check (first two lines), still return *something*
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    l1 = f.readline().strip().lower()
                    l2 = f.readline().strip().lower()
                header_line = l1 if any(k in l1 for k in CSV_KEYS) else l2
                hits = [k for k in CSV_KEYS if k in header_line]
                if len(hits) >= 2:
                    return SniffResult(self.id, 0.8, f"Found Landt-like CSV keys: {', '.join(hits)}")
            except Exception:
                pass
            return SniffResult(self.id, 0.4, "CSV extension; Landt fields not confirmed")

    def parse(self, path: Path) -> pd.DataFrame:
        p = Path(path)
        # Locate the true header (skip any banner lines like "Cell Model:")
        lines = _read_lines(p, max_lines=4000)
        header_idx, delim, fields = _find_header_row_and_fields(lines)

        # Fast path: use pandas with skiprows so the detected header becomes row 0
        try:
            df = pd.read_csv(
                p,
                engine="python",
                sep=delim,
                header=0,
                skiprows=header_idx,  # row at header_idx becomes the header
                on_bad_lines="error",
            )
            df.columns = [str(c).strip() for c in df.columns]
            return df
        except Exception:
            # Robust fallback (handles stray unquoted delimiters in text columns like step_name)
            return _read_landt_csv_robust(p, header_idx, delim, fields)
