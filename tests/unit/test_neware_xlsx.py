# tests/unit/test_neware_xlsx.py
from __future__ import annotations

import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from bdf.data_sources.neware_xlsx import NewareXlsx

# NewareXlsx class attributes

class TestNewareXlsxAttributes:
    def test_plugin_id(self):
        assert NewareXlsx.id == "neware-xlsx"

    def test_exts(self):
        assert ".xlsx" in NewareXlsx.exts
        assert ".xlsm" in NewareXlsx.exts
        assert ".xls" in NewareXlsx.exts

    def test_inherits_csv_synonyms(self):
        from bdf.data_sources.neware_csv import NewareCSV
        assert NewareXlsx.column_synonyms is NewareCSV.column_synonyms

    def test_inherits_csv_unit_patterns(self):
        from bdf.data_sources.neware_csv import NewareCSV
        assert NewareXlsx.unit_column_patterns is NewareCSV.unit_column_patterns

    def test_inherits_csv_timestamp_patterns(self):
        from bdf.data_sources.neware_csv import NewareCSV
        assert NewareXlsx.timestamp_candidate_patterns is NewareCSV.timestamp_candidate_patterns

    def test_timestamp_patterns_include_date(self):
        """Neware exports a bare 'Date' column for timestamps."""
        import re
        pat = re.compile(
            "|".join(NewareXlsx.timestamp_candidate_patterns), re.IGNORECASE
        )
        assert pat.search("date")
        assert pat.search("DateTime")


# sniff()

class TestSniff:
    def setup_method(self):
        self.plugin = NewareXlsx()

    def test_rejects_non_excel_ext(self, tmp_path):
        p = tmp_path / "data.csv"
        p.write_bytes(b"PK\x03\x04")
        result = self.plugin.sniff(p, p.read_bytes()[:4096])
        assert result.confidence == 0.0

    def test_scores_excel_ext(self, tmp_path):
        p = tmp_path / "data.xlsx"
        p.write_bytes(b"\x00" * 100)
        result = self.plugin.sniff(p, p.read_bytes()[:4096])
        assert result.confidence >= 0.25

    def test_scores_zip_magic(self, tmp_path):
        p = tmp_path / "data.xlsx"
        p.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
        result = self.plugin.sniff(p, p.read_bytes()[:4096])
        assert result.confidence >= 0.4

    def test_scores_ole_magic(self, tmp_path):
        p = tmp_path / "data.xls"
        p.write_bytes(b"\xD0\xCF\x11\xE0" + b"\x00" * 100)
        result = self.plugin.sniff(p, p.read_bytes()[:4096])
        assert result.confidence >= 0.4


# parse() with synthetic Excel files

def _write_neware_xlsx(path: Path, df: pd.DataFrame, sheet_name: str = "record"):
    """Write a DataFrame to an Excel file mimicking Neware output."""
    df.to_excel(path, sheet_name=sheet_name, index=False, engine="openpyxl")


class TestParse:
    def setup_method(self):
        self.plugin = NewareXlsx()

    def test_reads_record_sheet(self, tmp_path):
        df = pd.DataFrame({
            "Total Time": [0.0, 1.0, 2.0],
            "Voltage(V)": [3.7, 3.8, 3.9],
            "Current(mA)": [100.0, 100.0, 100.0],
        })
        p = tmp_path / "neware.xlsx"
        _write_neware_xlsx(p, df, sheet_name="record")

        result = self.plugin.parse(p)
        assert "Total Time" in result.columns
        assert "Voltage(V)" in result.columns
        assert len(result) == 3

    def test_falls_back_to_first_sheet(self, tmp_path):
        df = pd.DataFrame({
            "Total Time": [0.0, 1.0],
            "Voltage(V)": [3.7, 3.8],
            "Current(mA)": [100.0, 100.0],
        })
        p = tmp_path / "neware.xlsx"
        _write_neware_xlsx(p, df, sheet_name="Sheet1")

        result = self.plugin.parse(p)
        assert "Total Time" in result.columns

    def test_coerces_datetime_time_to_string(self, tmp_path):
        """datetime.time objects from Excel should be coerced to strings for downstream parsers."""
        df = pd.DataFrame({
            "Total Time": [datetime.time(0, 0, 0), datetime.time(0, 0, 5)],
            "Voltage(V)": [3.7, 3.8],
            "Current(mA)": [100.0, 100.0],
        })
        p = tmp_path / "neware.xlsx"
        _write_neware_xlsx(p, df, sheet_name="record")

        result = self.plugin.parse(p)
        # Should be string, not datetime.time objects
        assert pd.api.types.is_string_dtype(result["Total Time"])

    def test_coerces_timestamp_to_string(self, tmp_path):
        """Timestamp objects (e.g. Date column) should be coerced to strings."""
        df = pd.DataFrame({
            "Total Time": [0.0, 1.0],
            "Voltage(V)": [3.7, 3.8],
            "Date": pd.to_datetime(["2026-03-09 11:35:38", "2026-03-09 11:35:39"]),
        })
        p = tmp_path / "neware.xlsx"
        _write_neware_xlsx(p, df, sheet_name="record")

        result = self.plugin.parse(p)
        assert pd.api.types.is_string_dtype(result["Date"])

    def test_strips_bom_from_headers(self, tmp_path):
        df = pd.DataFrame({"\ufeffVoltage(V)": [3.7], "Current(mA)": [100.0]})
        p = tmp_path / "neware.xlsx"
        _write_neware_xlsx(p, df, sheet_name="record")

        result = self.plugin.parse(p)
        assert "Voltage(V)" in result.columns

    def test_drops_fully_empty_rows(self, tmp_path):
        df = pd.DataFrame({
            "Total Time": [0.0, None, 2.0],
            "Voltage(V)": [3.7, None, 3.9],
            "Current(mA)": [100.0, None, 100.0],
        })
        p = tmp_path / "neware.xlsx"
        _write_neware_xlsx(p, df, sheet_name="record")

        result = self.plugin.parse(p)
        assert len(result) == 2


# fixup()

class TestFixup:
    def setup_method(self):
        self.plugin = NewareXlsx()

    def test_converts_hms_strings_to_seconds(self):
        df = pd.DataFrame({
            "Test Time / s": ["00:00:00.000", "00:00:05.000", "01:00:00.000"],
            "Voltage / V": [3.7, 3.8, 3.9],
        })
        result = self.plugin.fixup(df)
        assert result["Test Time / s"].dtype == np.float64
        assert result["Test Time / s"].iloc[0] == 0.0
        assert result["Test Time / s"].iloc[1] == 5.0
        assert result["Test Time / s"].iloc[2] == 3600.0

    def test_leaves_numeric_time_untouched(self):
        df = pd.DataFrame({
            "Test Time / s": [0.0, 5.0, 3600.0],
            "Voltage / V": [3.7, 3.8, 3.9],
        })
        result = self.plugin.fixup(df)
        assert list(result["Test Time / s"]) == [0.0, 5.0, 3600.0]

    def test_handles_non_time_strings_gracefully(self):
        """Non-time strings should not crash fixup."""
        df = pd.DataFrame({
            "Test Time / s": ["not", "a", "time"],
            "Voltage / V": [3.7, 3.8, 3.9],
        })
        result = self.plugin.fixup(df)
        # to_timedelta returns all NaT, notna().any() is False → no conversion
        assert list(result["Test Time / s"]) == ["not", "a", "time"]

    def test_converts_epoch_leaked_datetimes(self):
        """Durations >= 24h appear as Excel-epoch datetimes (1899-12-31 based)."""
        df = pd.DataFrame({
            "Test Time / s": [
                "1900-01-01 00:00:00.900000",  # 1 day + 0.9s = 86400.9
                "1900-01-06 12:19:42.500000",  # 6 days 12:19:42.5 = 562782.5
            ],
            "Voltage / V": [3.7, 3.8],
        })
        result = self.plugin.fixup(df)
        assert result["Test Time / s"].dtype == np.float64
        assert np.isclose(result["Test Time / s"].iloc[0], 86400.9)
        assert np.isclose(result["Test Time / s"].iloc[1], 562782.5)

    def test_converts_mixed_hms_and_epoch_leaked(self):
        """Real Neware files mix HH:MM:SS (< 24h) and epoch-leaked datetimes (>= 24h)."""
        df = pd.DataFrame({
            "Test Time / s": [
                "00:00:00",                     # 0s (bare HH:MM:SS)
                "12:30:00",                     # 45000s
                "1900-01-01 00:00:05.000000",   # 1 day + 5s = 86405s
            ],
            "Voltage / V": [3.7, 3.8, 3.9],
        })
        result = self.plugin.fixup(df)
        assert result["Test Time / s"].dtype == np.float64
        assert result["Test Time / s"].iloc[0] == 0.0
        assert result["Test Time / s"].iloc[1] == 45000.0
        assert np.isclose(result["Test Time / s"].iloc[2], 86405.0)

    def test_epoch_leaked_continuity_at_24h_boundary(self):
        """Test Time must be continuous across the 24h boundary where Excel switches formats.

        Below 24h openpyxl returns datetime.time → HH:MM:SS strings.
        At/above 24h it returns datetime objects from epoch 1899-12-31.
        The transition must not introduce a ~86400s jump.
        """
        df = pd.DataFrame({
            "Test Time / s": [
                "23:59:58",                     # 86398s
                "23:59:59",                     # 86399s
                "1900-01-01 00:00:00.000000",   # 24h exactly = 86400s
                "1900-01-01 00:00:01.000000",   # 86401s
            ],
            "Voltage / V": [3.7, 3.8, 3.9, 4.0],
        })
        result = self.plugin.fixup(df)
        seconds = result["Test Time / s"]
        assert seconds.dtype == np.float64
        assert seconds.iloc[0] == 86398.0
        assert seconds.iloc[1] == 86399.0
        assert seconds.iloc[2] == 86400.0
        assert seconds.iloc[3] == 86401.0
        # Verify no jump > 2s between consecutive rows
        diffs = seconds.diff().dropna()
        assert (diffs <= 2.0).all(), f"Discontinuity at 24h boundary: {diffs.tolist()}"

    def test_skips_missing_time_columns(self):
        df = pd.DataFrame({"Voltage / V": [3.7, 3.8]})
        result = self.plugin.fixup(df)
        assert "Test Time / s" not in result.columns


# Full pipeline: parse → augment → normalize → fixup

class TestFullPipeline:
    def test_numeric_time_columns(self, tmp_path):
        """Standard case: Total Time as numeric seconds."""
        import bdf

        df = pd.DataFrame({
            "Total Time": [0.0, 1.5, 3.0],
            "Voltage(V)": [3.7, 3.8, 3.9],
            "Current(mA)": [100.0, 200.0, 300.0],
        })
        p = tmp_path / "neware.xlsx"
        _write_neware_xlsx(p, df, sheet_name="record")

        out = bdf.read(p)
        assert "Test Time / s" in out.columns
        assert "Voltage / V" in out.columns
        assert "Current / A" in out.columns
        assert out["Test Time / s"].dtype == np.float64
        assert list(out["Test Time / s"]) == [0.0, 1.5, 3.0]

    def test_hms_string_time_columns_converted(self, tmp_path):
        """HH:MM:SS strings should be converted to float seconds via fixup()."""
        import bdf

        df = pd.DataFrame({
            "Total Time": ["00:00:00.000", "00:00:05.000"],
            "Voltage(V)": [3.7, 3.8],
            "Current(mA)": [100.0, 200.0],
        })
        p = tmp_path / "neware.xlsx"
        _write_neware_xlsx(p, df, sheet_name="record")

        out = bdf.read(p)
        assert out["Test Time / s"].dtype == np.float64
        assert out["Test Time / s"].iloc[0] == 0.0
        assert out["Test Time / s"].iloc[1] == 5.0

    def test_date_column_becomes_unix_time(self, tmp_path):
        """The 'Date' column should be converted to Unix Time / s via augment()."""
        import bdf

        df = pd.DataFrame({
            "Total Time": [0.0, 1.0],
            "Voltage(V)": [3.7, 3.8],
            "Current(mA)": [100.0, 200.0],
            "Date": pd.to_datetime(["2026-03-09 11:35:38", "2026-03-09 11:35:39"]),
        })
        p = tmp_path / "neware.xlsx"
        _write_neware_xlsx(p, df, sheet_name="record")

        out = bdf.read(p, include_optional=True)
        assert "Unix Time / s" in out.columns
        assert out["Unix Time / s"].dtype == np.float64
        # Verify values are plausible epoch seconds (year 2026 ≈ 1.77e9)
        assert out["Unix Time / s"].iloc[0] > 1.7e9

    def test_current_ma_scaled_to_amps(self, tmp_path):
        """Current(mA) header should be converted to Amps."""
        import bdf

        df = pd.DataFrame({
            "Total Time": [0.0, 1.0],
            "Voltage(V)": [3.7, 3.8],
            "Current(mA)": [500.0, -500.0],
        })
        p = tmp_path / "neware.xlsx"
        _write_neware_xlsx(p, df, sheet_name="record")

        out = bdf.read(p)
        # Current(mA) → Current / A: 500 mA = 0.5 A
        assert np.isclose(out["Current / A"].iloc[0], 0.5)
        assert np.isclose(out["Current / A"].iloc[1], -0.5)

    def test_capacity_columns_preserved(self, tmp_path):
        """Charging/discharging capacity should come through."""
        import bdf

        df = pd.DataFrame({
            "Total Time": [0.0, 1.0],
            "Voltage(V)": [3.7, 3.8],
            "Current(mA)": [100.0, 200.0],
            "Chg. Cap.(mAh)": [10.0, 20.0],
            "DChg. Cap.(mAh)": [5.0, 15.0],
        })
        p = tmp_path / "neware.xlsx"
        _write_neware_xlsx(p, df, sheet_name="record")

        out = bdf.read(p, include_optional=True)
        assert "Charging Capacity / Ah" in out.columns
        assert "Discharging Capacity / Ah" in out.columns


# _find_record_sheet

class TestFindRecordSheet:
    def test_finds_record_sheet(self, tmp_path):
        p = tmp_path / "test.xlsx"
        pd.DataFrame({"A": [1]}).to_excel(p, sheet_name="record", index=False)
        assert NewareXlsx._find_record_sheet(p) == "record"

    def test_returns_none_without_record_sheet(self, tmp_path):
        p = tmp_path / "test.xlsx"
        pd.DataFrame({"A": [1]}).to_excel(p, sheet_name="data", index=False)
        assert NewareXlsx._find_record_sheet(p) is None

    def test_returns_none_for_invalid_file(self, tmp_path):
        p = tmp_path / "bad.xlsx"
        p.write_bytes(b"not an excel file")
        assert NewareXlsx._find_record_sheet(p) is None
