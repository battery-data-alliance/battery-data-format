# src/bdf/data_sources/neware_xlsx.py
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .base import CyclerPlugin, SniffResult
from .excel_xlsx import _default_engine_for
from .neware_csv import NewareCSV

# Build a single sniff regex from the CSV plugin's header_token_patterns
_NEWARE_HEADER_RE = re.compile(
    "|".join(NewareCSV.header_token_patterns), re.IGNORECASE
)

# Sheet names to try, in priority order
_RECORD_SHEET_NAMES = ("record", "Record", "RECORD")


class NewareXlsx(CyclerPlugin):
    """Neware/BTS Excel (.xlsx) reader. Reads the 'record' sheet and applies Neware CSV synonyms."""

    id = "neware-xlsx"
    exts = (".xlsx", ".xlsm", ".xls")

    # Reuse the full Neware CSV synonym and unit maps
    column_synonyms = NewareCSV.column_synonyms
    unit_column_patterns = NewareCSV.unit_column_patterns
    timestamp_candidate_patterns = NewareCSV.timestamp_candidate_patterns

    def sniff(self, path: Path, head: bytes) -> SniffResult:
        score, reasons = 0.0, []
        suffix = path.suffix.lower()

        # Must be an Excel file
        if suffix not in self.exts:
            return SniffResult(self.id, 0.0, "no-ext", {})
        score += 0.25
        reasons.append("ext")

        # ZIP (xlsx) or OLE (xls) magic
        if head.startswith(b"PK") or head.startswith(b"\xD0\xCF\x11\xE0"):
            score += 0.15
            reasons.append("magic")

        # Try to detect Neware-specific content
        try:
            sheet = self._find_record_sheet(path)
            if sheet is not None:
                # Read just the header row to check for Neware columns
                df_head = pd.read_excel(
                    path,
                    sheet_name=sheet,
                    nrows=0,
                    engine=_default_engine_for(path),
                )
                cols_joined = " ".join(str(c) for c in df_head.columns)
                if _NEWARE_HEADER_RE.search(cols_joined):
                    score += 0.55
                    reasons.append("neware-headers")
        except Exception:
            pass

        return SniffResult(self.id, min(score, 1.0), "+".join(reasons), {})

    def parse(self, path: Path) -> pd.DataFrame:
        engine = _default_engine_for(path)
        sheet = self._find_record_sheet(path)
        if sheet is None:
            sheet = 0  # fall back to first sheet

        try:
            df = pd.read_excel(path, sheet_name=sheet, engine=engine)
        except ImportError as exc:
            raise RuntimeError(
                "Reading Excel files requires openpyxl (for .xlsx/.xlsm) or xlrd (for .xls). "
                "Install with `pip install openpyxl`."
            ) from exc

        # Clean up headers
        df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]

        # Drop fully empty rows
        df = df.dropna(how="all")

        # Coerce non-numeric, non-string columns (e.g. datetime.time, Timestamp)
        # to strings so downstream parsers (augment/fixup) can handle them uniformly
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                continue
            if pd.api.types.is_string_dtype(df[col]):
                continue
            df[col] = df[col].astype(str)

        return df

    def fixup(self, df: pd.DataFrame) -> pd.DataFrame:
        """Convert HH:MM:SS duration strings (and Excel epoch-leaked datetimes) to float seconds.

        Excel stores durations as fractional days. For durations < 24h openpyxl
        returns ``datetime.time`` objects (stringified as ``HH:MM:SS``).  For
        durations >= 24h it returns ``datetime`` objects relative to the Excel
        epoch (1899-12-30), stringified as e.g. ``1900-01-06 12:19:42``.

        Strategy: try ``pd.to_datetime`` first (handles both formats with
        ``format='mixed'``), subtract the Excel epoch.  For any remaining NaT
        (pure ``HH:MM:SS`` strings without a date part), fall back to
        ``pd.to_timedelta``.
        """
        epoch = pd.Timestamp("1899-12-31")

        for time_col in ("Test Time / s", "Step Time / s"):
            if time_col not in df.columns or pd.api.types.is_numeric_dtype(df[time_col]):
                continue

            # 1) Try pd.to_timedelta first (handles bare HH:MM:SS strings)
            td = pd.to_timedelta(df[time_col], errors="coerce")
            seconds = td.dt.total_seconds()

            # 2) Fill remaining NaT with epoch-leaked datetime parsing
            still_nat = seconds.isna()
            if still_nat.any():
                dt = pd.to_datetime(
                    df[time_col][still_nat], errors="coerce", format="mixed"
                )
                seconds = seconds.copy()
                seconds[still_nat] = (dt - epoch).dt.total_seconds()

            if seconds.notna().any():
                df[time_col] = seconds
        return df

    @staticmethod
    def _find_record_sheet(path: Path) -> str | int | None:
        """Return the 'record' sheet name if it exists, else None."""
        try:
            xl = pd.ExcelFile(path, engine=_default_engine_for(path))
            for name in _RECORD_SHEET_NAMES:
                if name in xl.sheet_names:
                    return name
            return None
        except Exception:
            return None
