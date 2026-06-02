# tests/unit/test_neware_xlsx.py
from __future__ import annotations
import pytest

import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from bdf.data_sources.neware_xlsx import NewareXlsx

# NewareXlsx class attributes

class TestNewareXlsxAttributes:
    def test_plugin_id(self):
        """Uses the expected plugin identifier."""
        assert NewareXlsx.id == "neware-xlsx"

    def test_exts(self):
        """Declares supported Excel file extensions."""
        assert ".xlsx" in NewareXlsx.exts
        assert ".xlsm" in NewareXlsx.exts
        assert ".xls" in NewareXlsx.exts

    def test_inherits_csv_synonyms(self):
        """Reuses column synonym mappings from the CSV plugin."""
        from bdf.data_sources.neware_csv import NewareCSV
        assert NewareXlsx.column_synonyms is NewareCSV.column_synonyms

    def test_inherits_csv_unit_patterns(self):
        """Reuses unit-column patterns from the CSV plugin."""
        from bdf.data_sources.neware_csv import NewareCSV
        assert NewareXlsx.unit_column_patterns is NewareCSV.unit_column_patterns

    def test_inherits_csv_timestamp_patterns(self):
        """Reuses timestamp candidate patterns from the CSV plugin."""
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
        """Returns zero confidence for non-Excel extensions."""
        p = tmp_path / "data.csv"
        p.write_bytes(b"PK\x03\x04")
        result = self.plugin.sniff(p, p.read_bytes()[:4096])
        assert result.confidence == 0.0

    def test_scores_excel_ext(self, tmp_path):
        """Awards extension-based confidence for Excel files."""
        p = tmp_path / "data.xlsx"
        p.write_bytes(b"\x00" * 100)
        result = self.plugin.sniff(p, p.read_bytes()[:4096])
        assert result.confidence >= 0.25

    def test_scores_zip_magic(self, tmp_path):
        """Awards magic-byte confidence for ZIP-based Excel files."""
        p = tmp_path / "data.xlsx"
        p.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
        result = self.plugin.sniff(p, p.read_bytes()[:4096])
        assert result.confidence >= 0.4

    def test_scores_ole_magic(self, tmp_path):
        """Awards magic-byte confidence for OLE-based Excel files."""
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
        """Reads data from the preferred record sheet."""
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
        """Falls back to the first sheet when record is absent."""
        df = pd.DataFrame({
            "Total Time": [0.0, 1.0],
            "Voltage(V)": [3.7, 3.8],
            "Current(mA)": [100.0, 100.0],
        })
        p = tmp_path / "neware.xlsx"
        _write_neware_xlsx(p, df, sheet_name="Sheet1")

        result = self.plugin.parse(p)
        assert "Total Time" in result.columns

    def test_coerces_datetime_time_to_float(self, tmp_path):
        """datetime.time values from Excel should be normalized to numeric seconds."""
        df = pd.DataFrame({
            "Total Time": [datetime.time(0, 0, 0), datetime.time(0, 0, 5)],
            "Voltage(V)": [3.7, 3.8],
            "Current(mA)": [100.0, 100.0],
        })
        p = tmp_path / "neware.xlsx"
        _write_neware_xlsx(p, df, sheet_name="record")

        result = self.plugin.parse(p)
        assert result["Total Time"].dtype == np.float64
        assert result["Total Time"].iloc[0] == 0.0
        assert result["Total Time"].iloc[1] == 5.0

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

    def test_drops_fully_empty_rows(self, tmp_path):
        """Drops rows that are fully empty after parsing."""
        df = pd.DataFrame({
            "Total Time": [0.0, None, 2.0],
            "Voltage(V)": [3.7, None, 3.9],
            "Current(mA)": [100.0, None, 100.0],
        })
        p = tmp_path / "neware.xlsx"
        _write_neware_xlsx(p, df, sheet_name="record")

        result = self.plugin.parse(p)
        assert len(result) == 2

    def test_parse_converts_epoch_leaked_datetimes(self, tmp_path):
        """parse() converts epoch-leaked datetime strings into float seconds."""
        df = pd.DataFrame(
            {
                "Total Time": [
                    "1900-01-01 00:00:00.900000",  # 1 day + 0.9s = 86400.9
                    "1900-01-06 12:19:42.500000",  # 6 days 12:19:42.5 = 562782.5
                ],
                "Voltage(V)": [3.7, 3.8],
                "Current(mA)": [100.0, 100.0],
            }
        )
        p = tmp_path / "neware.xlsx"
        _write_neware_xlsx(p, df, sheet_name="record")

        result = self.plugin.parse(p)
        assert np.isclose(result["Total Time"].iloc[0], 86400.9)
        assert np.isclose(result["Total Time"].iloc[1], 562782.5)

    def test_parse_converts_mixed_hms_and_epoch_leaked(self, tmp_path):
        """parse() handles mixed HH:MM:SS and epoch-leaked datetime strings."""
        df = pd.DataFrame(
            {
                "Total Time": [
                    "00:00:00",  # 0s
                    "12:30:00",  # 45000s
                    "1900-01-01 00:00:05.000000",  # 86405s
                ],
                "Voltage(V)": [3.7, 3.8, 3.9],
                "Current(mA)": [100.0, 100.0, 100.0],
            }
        )
        p = tmp_path / "neware.xlsx"
        _write_neware_xlsx(p, df, sheet_name="record")

        result = self.plugin.parse(p)
        assert np.isclose(result["Total Time"].iloc[0], 0.0)
        assert np.isclose(result["Total Time"].iloc[1], 45000.0)
        assert np.isclose(result["Total Time"].iloc[2], 86405.0)

    def test_parse_epoch_leaked_continuity_at_24h_boundary(self, tmp_path):
        """parse() keeps Test Time continuous across the HH:MM:SS/epoch-leaked boundary."""
        df = pd.DataFrame(
            {
                "Total Time": [
                    "23:59:58",  # 86398s
                    "23:59:59",  # 86399s
                    "1900-01-01 00:00:00.000000",  # 86400s
                    "1900-01-01 00:00:01.000000",  # 86401s
                ],
                "Voltage(V)": [3.7, 3.8, 3.9, 4.0],
                "Current(mA)": [100.0, 100.0, 100.0, 100.0],
            }
        )
        p = tmp_path / "neware.xlsx"
        _write_neware_xlsx(p, df, sheet_name="record")

        result = self.plugin.parse(p)
        seconds = result["Total Time"]
        assert np.isclose(seconds.iloc[0], 86398.0)
        assert np.isclose(seconds.iloc[1], 86399.0)
        assert np.isclose(seconds.iloc[2], 86400.0)
        assert np.isclose(seconds.iloc[3], 86401.0)
        diffs = seconds.diff().dropna()
        assert (diffs <= 2.0).all(), f"Discontinuity at 24h boundary: {diffs.tolist()}"

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
        assert pytest.approx(out["Unix Time / s"].iloc[0], abs=1e-6) == 1773056138
        assert pytest.approx(out["Unix Time / s"].iloc[1], abs=1e-6) == 1773056139


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
        """Finds the record sheet when present."""
        p = tmp_path / "test.xlsx"
        pd.DataFrame({"A": [1]}).to_excel(p, sheet_name="record", index=False)
        assert NewareXlsx()._find_record_sheet(p) == "record"

    def test_returns_none_without_record_sheet(self, tmp_path):
        """Returns None when no record sheet exists."""
        p = tmp_path / "test.xlsx"
        pd.DataFrame({"A": [1]}).to_excel(p, sheet_name="data", index=False)
        assert NewareXlsx()._find_record_sheet(p) is None

    def test_returns_none_for_invalid_file(self, tmp_path):
        """Returns None for unreadable Excel files."""
        p = tmp_path / "bad.xlsx"
        p.write_bytes(b"not an excel file")
        assert NewareXlsx()._find_record_sheet(p) is None
