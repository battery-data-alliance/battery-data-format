# src/bdf/cyclers/landt_txt.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple
import re
import pandas as pd

from .base import CyclerPlugin, SniffResult

TXT_FIRST_TOKEN = "rec#"

# Regex helpers
RE_TABS = re.compile(r"\t+")
RE_2PLUS_SPACES = re.compile(r" {2,}")
RE_ANY_WS = re.compile(r"\s+")

def _read_lines(path: Path, max_lines: int = 5000,
                encodings=("utf-8-sig", "utf-8", "cp1252", "utf-16", "utf-16-le", "utf-16-be")) -> List[str]:
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, errors="ignore") as f:
                out: List[str] = []
                for i, line in enumerate(f):
                    if i >= max_lines:
                        break
                    out.append(line.rstrip("\r\n"))
                return out
        except Exception:
            continue
    return path.read_bytes().decode("utf-8", errors="ignore").splitlines()[:max_lines]


def _clean_fields(fields: List[str]) -> List[str]:
    f = [str(c).strip() for c in fields]
    while f and f[0] == "":
        f.pop(0)
    while f and f[-1] == "":
        f.pop()
    return f

def _find_header_row_and_fields(lines: List[str]) -> Tuple[int, List[str]]:
    """
    Find the header line (starts with 'Rec#', case-insensitive) and split it into fields.
    Prefer tabs; otherwise split on runs of 2+ spaces; otherwise any whitespace.
    """
    for i, line in enumerate(lines[:1000]):
        if line.strip().lower().startswith(TXT_FIRST_TOKEN):
            # Try tab first
            if "\t" in line:
                fields = RE_TABS.split(line.strip())
            elif "  " in line:
                fields = RE_2PLUS_SPACES.split(line.strip())
            else:
                fields = RE_ANY_WS.split(line.strip())
            fields = _clean_fields(fields)
            if fields and fields[0].lower().startswith(TXT_FIRST_TOKEN):
                return i, fields
    raise ValueError("Landt TXT: could not locate header row (no 'Rec#' line).")

def _split_row_safely(line: str, ncols: int) -> List[str]:
    """
    Split a data row using the best delimiter for this row:
      - if tabs present, split on tabs
      - else if runs of >=2 spaces present, split on those
      - else split on any whitespace
    Always split with maxsplit = ncols-1 so the remainder collapses into the last column.
    Remove dangling empty fields and pad/trim to exactly ncols.
    """
    line = line.rstrip("\r\n")
    if not line.strip():
        return [""] * ncols

    if "\t" in line:
        parts = RE_TABS.split(line, maxsplit=ncols - 1)
    elif "  " in line:
        parts = RE_2PLUS_SPACES.split(line, maxsplit=ncols - 1)
    else:
        parts = RE_ANY_WS.split(line, maxsplit=ncols - 1)

    # Drop trailing empties from dangling delimiters
    while parts and parts[-1] == "":
        parts.pop()

    # Enforce exact width
    if len(parts) > ncols:
        head = parts[: ncols - 1]
        tail = " ".join(parts[ncols - 1 :])  # fold extras into last col (timestamp etc.)
        parts = head + [tail]
    elif len(parts) < ncols:
        parts = parts + [""] * (ncols - len(parts))

    return [p.strip() for p in parts]

def _read_landt_txt_robust(path: Path, header_idx: int, fields: List[str]) -> pd.DataFrame:
    ncols = len(fields)
    rows: List[List[str]] = []

    # Try a few encodings explicitly
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
            parts = _split_row_safely(raw, ncols)
            # Skip completely empty rows
            if all(p == "" for p in parts):
                continue
            rows.append(parts)

    df = pd.DataFrame(rows, columns=fields)
    df.columns = [str(c).strip() for c in df.columns]
    return df

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

        try:
            lines = _read_lines(Path(path))
            hdr_idx, fields = _find_header_row_and_fields(lines)
            hits = [c for c in ("rec#", "test(sec)", "volts", "amps", "dpt-time")
                    if any(c in f.lower() for f in fields)]
            msg = f"Found header at line {hdr_idx}: {', '.join(hits)}"
            return SniffResult(self.id, 0.98, msg)
        except Exception:
            # fallback: scan first 100 lines for Landt-ish tokens anywhere
            try:
                lines = _read_lines(Path(path), max_lines=100)
                blob = "\n".join(lines).lower()
                tokens = sum(t in blob for t in ("rec#", "test(sec)", "volts", "amps", "dpt-time"))
                if tokens >= 2:
                    return SniffResult(self.id, 0.7, "TXT with Landt-like tokens present")
            except Exception:
                pass
            return SniffResult(self.id, 0.4, "TXT extension; Landt header not confirmed")


    def parse(self, path: Path) -> pd.DataFrame:
        p = Path(path)
        # Locate header and fields using resilient split
        lines = _read_lines(p)
        header_idx, fields = _find_header_row_and_fields(lines)

        # Always use robust per-row reader to avoid silent misalignment
        df = _read_landt_txt_robust(p, header_idx, fields)
        return df
