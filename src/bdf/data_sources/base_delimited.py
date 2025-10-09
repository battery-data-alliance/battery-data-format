from __future__ import annotations
import re, csv
from pathlib import Path
from typing import Iterable, Optional, Sequence, Dict, Tuple
import pandas as pd
from .base import CyclerPlugin, SniffResult

class DelimitedTextPlugin(CyclerPlugin):
    default_encoding: str = "utf-8"
    max_header_scan_lines: int = 600
    header_lines_field_regex: Optional[str] = None
    header_token_patterns: Sequence[str] = ()
    magic: Sequence[str] = ()
    unit_column_patterns: Dict[str, Sequence[Tuple[str, str]]] = {}
    header_prefix_to_strip: Optional[str] = None  # already added earlier
    ragged_row_policy: Optional[str] = None       # NEW: None | "fold_last" | "skip"

    def sniff(self, path: Path, head: bytes) -> SniffResult:
        txt = self._safe_decode(head)
        score, reasons = 0.0, []
        if path.suffix.lower() in getattr(self, "exts", ()):
            score += 0.3; reasons.append("ext")
        for m in self.magic:
            if m.lower() in txt.lower():
                score += 0.6; reasons.append("magic"); break
        for pat in self.header_token_patterns:
            if re.search(pat, txt, re.I):
                score += 0.1; reasons.append("tokens"); break
        return SniffResult(self.id, min(score, 1.0), "+".join(reasons), {})

    def parse(self, path: Path) -> pd.DataFrame:
        enc = self.default_encoding
        header_idx, sep = self._find_header_and_sep(path, enc)
        # Use robust reader when requested (e.g., Landt CSV)
        if self.ragged_row_policy == "fold_last":
            df = self._read_csv_ragged(path, sep, header_idx, enc)
        else:
            df = self._read_csv(path, sep, header_idx, enc)
            if not self._looks_ok(df) and header_idx > 0:
                df = self._read_csv(path, sep, header_idx - 1, enc)

        if self.header_prefix_to_strip:
            pref = str(self.header_prefix_to_strip)
            df.columns = [str(c).lstrip(pref).strip() for c in df.columns]

        self._unit_hints = self._detect_units_from_headers(df.columns)
        return df

    # ---------------- internals ----------------

    def _safe_decode(self, b: bytes) -> str:
        for enc in ("utf-8", "latin-1", "cp1252"):
            try: return b.decode(enc)
            except: pass
        return b.decode("latin-1", "ignore")

    def _scan_prefix(self, path: Path, enc: str) -> list[str]:
        out: list[str] = []
        with open(path, "r", encoding=enc, errors="replace") as f:
            for _ in range(self.max_header_scan_lines):
                line = f.readline()
                if not line: break
                out.append(line.rstrip("\n"))
        return out

    def _find_header_and_sep(self, path: Path, enc: str) -> tuple[int, str]:
        prefix = self._scan_prefix(path, enc)
        header_idx: Optional[int] = None
        if self.header_lines_field_regex:
            rx = re.compile(self.header_lines_field_regex, re.I)
            for s in prefix:
                m = rx.search(s)
                if m:
                    n = int(m.group(1)); header_idx = max(n - 1, 0); break
        if header_idx is None and self.header_token_patterns:
            for i, s in enumerate(prefix):
                if any(re.search(p, s, re.I) for p in self.header_token_patterns):
                    header_idx = i; break
        if header_idx is None: header_idx = 0

        header_line = prefix[header_idx] if header_idx < len(prefix) else ""
        if "\t" in header_line: sep = "\t"
        elif ";" in header_line: sep = ";"
        elif "," in header_line: sep = ","
        else: sep = r"\s+"
        return header_idx, sep

    def _read_csv(self, path: Path, sep: str, header_idx: int, enc: str) -> pd.DataFrame:
        df = pd.read_csv(
            path, sep=sep, skiprows=header_idx, header=0,
            encoding=enc, engine="python", dtype_backend="pyarrow",
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
        while f and f[0] == "": f.pop(0)
        while f and f[-1] == "": f.pop()
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
        while fields and fields[0] == "": fields.pop(0)
        while fields and fields[-1] == "": fields.pop()
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
        with open(path, "r", encoding=enc, errors="ignore") as f:
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
            while fields and fields[0] == "": fields.pop(0)
            while fields and fields[-1] == "": fields.pop()

        ncols = len(fields)
        rows: list[list[str]] = []

        with open(path, "r", encoding=enc, errors="ignore") as f:
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
        return df


    def _looks_ok(self, df: pd.DataFrame) -> bool:
        l = [str(c).lower().strip() for c in df.columns]
        if any(k in l for k in ("time/s", "ewe/v", "ecell/v", "i/ma", "current / a")):
            return True
        for c in l:
            if re.search(r"\btime\[[^\]]+\]", c): return True
            if re.search(r"\bu\[[^\]]+\]", c): return True
            if re.search(r"\bi\[[^\]]+\]", c): return True
        return False

    def _detect_units_from_headers(self, cols: Iterable[str]) -> dict[str, str]:
        hints: dict[str, str] = {}
        for canon_col, patterns in self.unit_column_patterns.items():
            for c in cols:
                for pat, unit in patterns:
                    if re.search(pat, str(c), re.I):
                        hints[canon_col] = unit; break
        return hints

    def fixup(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df
        hints = getattr(self, "_unit_hints", {})

        if "Current / A" in out.columns:
            u = (hints.get("Current / A") or "").lower()
            if u == "ma":
                out["Current / A"] = out["Current / A"].astype("float64") / 1000.0
            elif u == "ua":
                out["Current / A"] = out["Current / A"].astype("float64") / 1_000_000.0

        if "Test Time / s" in out.columns:
            u = (hints.get("Test Time / s") or "").lower()
            if u == "h":
                out["Test Time / s"] = out["Test Time / s"].astype("float64") * 3600.0
            elif u == "min":
                out["Test Time / s"] = out["Test Time / s"].astype("float64") * 60.0

        if "Voltage / V" in out.columns:
            u = (hints.get("Voltage / V") or "").lower()
            if u == "mv":
                out["Voltage / V"] = out["Voltage / V"].astype("float64") / 1000.0

        return out
