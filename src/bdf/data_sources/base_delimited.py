from __future__ import annotations

import csv
import os
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Dict, Optional, Tuple

import pandas as pd

from .base import CyclerPlugin, SniffResult


class DelimitedTextPlugin(CyclerPlugin):
    """
    Generic delimited-text reader with robust header detection, optional INI-like
    preambles, and light unit hints. Plugins should remain declarative by
    overriding class attributes (no per-plugin methods unless absolutely needed).
    """

    # --- basic CSV/TXT settings ---
    decimal: str = "."                             # decimal separator for numeric fields
    default_encoding: str = "utf-8"
    max_header_scan_lines: int = 600
    header_lines_field_regex: Optional[str] = None   # e.g., r"^Header\s*Lines:\s*(\d+)"
    header_token_patterns: Sequence[str] = ()        # regexes that indicate a header row
    magic: Sequence[str] = ()                        # quick sniff magic strings in head

    # --- normalization helpers ---
    unit_column_patterns: Dict[str, Sequence[Tuple[str, str]]] = {}
    header_prefix_to_strip: Optional[str] = None     # strip prefix from each header, if present

    # --- robustness knobs ---
    ragged_row_policy: Optional[str] = None          # None | "fold_last" | "skip"
    strip_headers: bool = True                       # strip whitespace/BOM in headers
    drop_units_row: bool = False                     # drop a unit annotation row after the header
    unit_row_min_ratio: float = 0.6                  # fraction of bracketed/empty cells to treat as unit row

    # --- INI-style preamble / sectioned files (e.g., Novonix with [Data]) ---
    data_section_marker: Optional[str] = None        # regex; if present, start from the line after this marker
    data_header_offset: int = 1                      # number of lines after marker where the header row appears

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------
    def sniff(self, path: Path, head: bytes) -> SniffResult:
        txt = self._safe_decode(head)
        score, reasons = 0.0, []
        if path.suffix.lower() in getattr(self, "exts", ()):
            score += 0.25
            reasons.append("ext")
        for m in self.magic:
            if m.lower() in txt.lower():
                score += 0.6
                reasons.append("magic")
                break
        for pat in self.header_token_patterns:
            if re.search(pat, txt, re.I):
                score += 0.35
                reasons.append("tokens")
                break
        return SniffResult(self.id, min(score, 1.0), "+".join(reasons), {})

    def parse(self, path: Path) -> pd.DataFrame:
        enc = self.default_encoding

        used_marker = False
        if getattr(self, "data_section_marker", None):
            header_idx, sep = self._find_header_from_marker(path, enc, self.data_section_marker, getattr(self, "data_header_offset", 1))
            used_marker = True
        else:
            header_idx, sep = self._find_header_and_sep(path, enc)

        if self.ragged_row_policy == "fold_last":
            df = self._read_csv_ragged(path, sep, header_idx, enc)
        else:
            df = self._read_csv(path, sep, header_idx, enc)
            # Only attempt the "retry one line up" when we did NOT rely on a marker;
            # with a marker, the off-by-one is our bug, not the file's.
            if not used_marker and not self._looks_ok(df) and header_idx > 0:
                if self._debug_on():
                    print(f"[bdf][DelimitedTextPlugin] retry one line up: header_idx={header_idx-1}")
                df = self._read_csv(path, sep, header_idx - 1, enc)

        if self.header_prefix_to_strip:
            pref = str(self.header_prefix_to_strip)
            df.columns = [str(c).lstrip(pref).strip() for c in df.columns]

        if getattr(self, "strip_headers", False):
            df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]

        if getattr(self, "drop_units_row", False):
            df = self._drop_units_row(df)

        self._unit_hints = self._detect_units_from_headers(df.columns)

        if self._debug_on():
            print(f"[bdf][DelimitedTextPlugin] columns={list(df.columns)}")

        return df

    # -------------------------------------------------------------------------
    # Internals
    # -------------------------------------------------------------------------
    def _debug_on(self) -> bool:
        return os.environ.get("BDF_DEBUG", "").strip() not in ("", "0", "false", "False")


    def _safe_decode(self, b: bytes) -> str:
        for enc in ("utf-8", "latin-1", "cp1252"):
            try:
                return b.decode(enc)
            except Exception:
                pass
        return b.decode("latin-1", "ignore")

    def _scan_prefix(self, path: Path, enc: str) -> list[str]:
        out: list[str] = []
        with open(path, encoding=enc, errors="replace") as f:
            for _ in range(self.max_header_scan_lines):
                line = f.readline()
                if not line:
                    break
                out.append(line.rstrip("\n"))
        return out

    def _detect_sep_from_line(self, header_line: str) -> str:
        if "\t" in header_line:
            return "\t"
        if ";" in header_line:
            return ";"
        if "," in header_line:
            return ","
        return r"\s+"

    def _find_header_from_marker(self, path: Path, enc: str, marker_regex: str, offset: int) -> tuple[int, str]:
        """
        Find a section marker (e.g., '^[Data]$') and return (header_idx, sep).
        Robustness:
        - Locate the marker line.
        - Start scanning at marker_idx + max(offset,1) (default to 'next line').
        - Skip blank lines and any bracketed section headers like [Something].
        - First non-empty, non-bracketed line is treated as the header row.
        - Infer delimiter from that header row (comma/semicolon/tab > whitespace).
        """
        pat = re.compile(marker_regex, re.IGNORECASE)

        # Read a small prefix; the Novonix files aren't huge but we still stream
        lines: list[str] = []
        with open(path, encoding=enc, errors="replace") as f:
            lines = f.readlines()

        marker_idx = None
        for i, raw in enumerate(lines):
            if pat.match(raw.strip()):
                marker_idx = i
                break

        if marker_idx is None:
            # Fallback to generic finder
            return self._find_header_and_sep(path, enc)

        # Start at next line (or custom offset), then skip blank or bracketed metadata
        start = marker_idx + (offset if offset is not None else 1)
        start = max(start, marker_idx + 1)  # Always at least line after the marker

        header_idx = None
        header_line = ""
        for j in range(start, len(lines)):
            s = lines[j].strip()
            if not s:
                continue
            if s.startswith("[") and s.endswith("]"):
                # e.g., stray [Header] lines; keep skipping
                continue
            header_idx = j
            header_line = lines[j].rstrip("\r\n")
            break

        if header_idx is None:
            # Fallback to generic finder if something odd happens
            return self._find_header_and_sep(path, enc)

        # Detect delimiter prioritizing real CSV separators
        sep = self._detect_sep_from_line(header_line)

        if self._debug_on():
            print(f"[bdf][DelimitedTextPlugin] marker={marker_regex!r} header_idx={header_idx} sep={sep!r} header_line={header_line!r}")

        return header_idx, sep

    def _detect_sep_from_line(self, header_line: str) -> str:
        """
        Prefer comma/semicolon/tab if found; otherwise use whitespace.
        """
        if "," in header_line:
            return ","
        if ";" in header_line:
            return ";"
        if "\t" in header_line:
            return "\t"
        return r"\s+"


    def _find_header_and_sep(self, path: Path, enc: str) -> tuple[int, str]:
        prefix = self._scan_prefix(path, enc)
        header_idx: Optional[int] = None

        # 1) If vendor provided "Header Lines: N" style field
        if self.header_lines_field_regex:
            rx = re.compile(self.header_lines_field_regex, re.I)
            for s in prefix:
                m = rx.search(s)
                if m:
                    n = int(m.group(1))
                    header_idx = max(n - 1, 0)
                    break

        # 2) Heuristic match for header token patterns
        if header_idx is None and self.header_token_patterns:
            for i, s in enumerate(prefix):
                if any(re.search(p, s, re.I) for p in self.header_token_patterns):
                    header_idx = i
                    break

        # 3) Default to first line
        if header_idx is None:
            header_idx = 0

        header_line = prefix[header_idx] if header_idx < len(prefix) else ""
        sep = self._detect_sep_from_line(header_line)
        return header_idx, sep

    def _read_csv(self, path: Path, sep: str, header_idx: int, enc: str) -> pd.DataFrame:
        df = pd.read_csv(
            path,
            sep=sep,
            skiprows=header_idx,
            header=0,
            encoding=enc,
            engine="python",
            dtype_backend="pyarrow",
            decimal=getattr(self, "decimal", ".") or ".",
        )
        df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]
        return df

    def _split_header_fields(self, header_line: str, sep: str) -> list[str]:
        try:
            fields = next(csv.reader([header_line], delimiter=sep, quotechar='"'))
        except Exception:
            fields = header_line.split(sep)
        # clean
        f = [str(c).strip() for c in fields]
        while f and f[0] == "":
            f.pop(0)
        while f and f[-1] == "":
            f.pop()
        return f

    def _split_ws_row(self, line: str, ncols: int) -> list[str]:
        line = line.rstrip("\r\n")
        if not line.strip():
            return [""] * ncols
        # Prefer tabs, then 2+ spaces, else any whitespace; collapse rest into last field
        if "\t" in line:
            parts = re.split(r"\t+", line, maxsplit=ncols - 1)
        elif re.search(r" {2,}", line):
            parts = re.split(r" {2,}", line, maxsplit=ncols - 1)
        else:
            parts = re.split(r"\s+", line.strip(), maxsplit=ncols - 1)
        while parts and parts[-1] == "":
            parts.pop()
        if len(parts) > ncols:
            head = parts[: ncols - 1]
            tail = " ".join(parts[ncols - 1:]).strip()
            parts = head + [tail]
        elif len(parts) < ncols:
            parts += [""] * (ncols - len(parts))
        return [p.strip() for p in parts]

    def _split_ws_header_fields(self, header_line: str) -> list[str]:
        s = header_line.strip()
        if "\t" in s:
            fields = re.split(r"\t+", s)
        elif re.search(r" {2,}", s):
            fields = re.split(r" {2,}", s)
        else:
            fields = re.split(r"\s+", s)
        # trim empties
        while fields and fields[0] == "":
            fields.pop(0)
        while fields and fields[-1] == "":
            fields.pop()
        return [f.strip() for f in fields]

    def _read_csv_ragged(self, path: Path, sep: str, header_idx: int, enc: str) -> pd.DataFrame:
        """
        Robust reader:
          - If sep == '\\s+' (or contains '\\s'): split with whitespace rules (tabs → 2+ spaces → any ws),
            folding extras into the last column.
          - Else (single-char delimiters): use csv.reader; fold extras into the last column.
        """
        # Read header line
        header_line = None
        with open(path, encoding=enc, errors="ignore") as f:
            for i, line in enumerate(f):
                if i == header_idx:
                    header_line = line.rstrip("\r\n")
                    break
        if header_line is None:
            raise ValueError("Failed to re-read header line for ragged CSV/TXT reader.")

        is_ws = "\\s" in sep
        if is_ws:
            fields = self._split_ws_header_fields(header_line)
        else:
            import csv as _csv
            try:
                fields = next(_csv.reader([header_line], delimiter=sep, quotechar='"'))
            except Exception:
                fields = header_line.split(sep)
            fields = [str(c).strip() for c in fields]
            while fields and fields[0] == "":
                fields.pop(0)
            while fields and fields[-1] == "":
                fields.pop()

        ncols = len(fields)
        rows: list[list[str]] = []

        with open(path, encoding=enc, errors="ignore") as f:
            for i, raw in enumerate(f):
                if i <= header_idx:
                    continue
                if is_ws:
                    parts = self._split_ws_row(raw, ncols)
                else:
                    import csv as _csv
                    try:
                        parts = next(_csv.reader([raw.rstrip("\r\n")], delimiter=sep, quotechar='"'))
                    except Exception:
                        parts = raw.rstrip("\r\n").split(sep)
                    if len(parts) > ncols:
                        head = parts[: ncols - 1]
                        tail = sep.join(parts[ncols - 1:]).rstrip(sep).rstrip()
                        parts = head + [tail]
                    elif len(parts) == ncols + 1 and parts[-1] == "":
                        parts = parts[:-1]
                    if len(parts) < ncols:
                        parts += [""] * (ncols - len(parts))
                    parts = [p.strip() for p in parts]

                # skip completely empty rows
                if all(p == "" for p in parts):
                    continue
                rows.append(parts)

        df = pd.DataFrame(rows, columns=fields)
        df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]
        return self._coerce_decimal(df)

    def _coerce_decimal(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Handle locales where ',' is used as the decimal separator for ragged readers.
        Keep non-numeric text intact by using errors='ignore'.
        """
        dec = getattr(self, "decimal", ".") or "."
        if dec == ".":
            return df

        out = df
        for c in out.columns:
            s = out[c]
            if not pd.api.types.is_object_dtype(s):
                continue
            try:
                s_fixed = s.astype("string").str.replace(dec, ".", regex=False)
                out[c] = pd.to_numeric(s_fixed, errors="ignore")
            except Exception:
                continue
        return out

    def _looks_ok(self, df: pd.DataFrame) -> bool:
        cols_lower = [str(c).lower().strip() for c in df.columns]
        if any(k in cols_lower for k in ("time/s", "ewe/v", "ecell/v", "i/ma", "current / a")):
            return True
        for c in cols_lower:
            if re.search(r"\btime\[[^\]]+\]", c):
                return True
            if re.search(r"\bu\[[^\]]+\]", c):
                return True
            if re.search(r"\bi\[[^\]]+\]", c):
                return True
        return False

    def _detect_units_from_headers(self, cols: Iterable[str]) -> dict[str, str]:
        """
        Lightweight hint system: if a plugin provides unit_column_patterns like
          {
            "Test Time / s": [(r"run time\s*\(h\)", "h")],
            "Current / A":   [(r"current\s*\(mA\)", "mA")],
          }
        we record those to apply tiny fixups below (only for simple linear cases).
        Prefer the full Pint-based conversions in the main normalizer path.
        """
        hints: dict[str, str] = {}
        for canon_col, patterns in self.unit_column_patterns.items():
            for c in cols:
                for pat, unit in patterns:
                    if re.search(pat, str(c), re.I):
                        hints[canon_col] = unit
                        break
        return hints

    def _drop_units_row(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        row = df.iloc[0]
        total = len(row)
        if total == 0:
            return df

        unit_like = 0
        for val in row:
            if pd.isna(val):
                unit_like += 1
                continue
            s = str(val).strip()
            if not s:
                unit_like += 1
                continue
            if re.match(r"^\[.*\]$", s):
                unit_like += 1

        ratio = unit_like / total
        if ratio >= getattr(self, "unit_row_min_ratio", 0.6):
            return df.iloc[1:].reset_index(drop=True)
        return df

    def fixup(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Tiny post-read unit fixups for a few simple linear cases where plugins
        provided clear unit hints via unit_column_patterns. The main normalization
        path should handle robust conversions; this is a light safety net.
        """
        out = df
        hints = getattr(self, "_unit_hints", {})

        if "Current / A" in out.columns:
            u = (hints.get("Current / A") or "").lower()
            if u == "ma":
                out["Current / A"] = pd.to_numeric(out["Current / A"], errors="coerce") / 1000.0
            elif u == "ua":
                out["Current / A"] = pd.to_numeric(out["Current / A"], errors="coerce") / 1_000_000.0

        if "Test Time / s" in out.columns:
            u = (hints.get("Test Time / s") or "").lower()
            if u == "h":
                out["Test Time / s"] = pd.to_numeric(out["Test Time / s"], errors="coerce") * 3600.0
            elif u == "min":
                out["Test Time / s"] = pd.to_numeric(out["Test Time / s"], errors="coerce") * 60.0

        if "Voltage / V" in out.columns:
            u = (hints.get("Voltage / V") or "").lower()
            if u == "mv":
                out["Voltage / V"] = pd.to_numeric(out["Voltage / V"], errors="coerce") / 1000.0

        return out
