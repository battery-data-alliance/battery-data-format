# tests/unit/test_header_start_time.py
"""Tests for header start-time extraction in DelimitedTextPlugin."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bdf.data_sources.base_delimited import DelimitedTextPlugin
from bdf.data_sources.basytec_txt import BasytecTxt
from bdf.data_sources.biologic_mpt import BioLogicMPT


# Minimal concrete plugin for infrastructure tests
class _Plugin(DelimitedTextPlugin):
    id = "_test-start-time"
    exts = (".txt",)
    column_synonyms = {
        "Test Time / s": ["time[s]"],
        "Voltage / V": ["u[v]"],
        "Current / A": ["i[a]"],
    }
    start_time_line_regex = r"Start:\s*(.+)"
    start_time_format = "%d.%m.%Y %H:%M:%S"

    def sniff(self, path, head):
        from bdf.data_sources.base import SniffResult
        return SniffResult(self.id, 0.0, "", {})


def _write_txt(path, header_lines, data_lines, sep="\t"):
    """Write a minimal delimited text file with a header block."""
    with open(path, "w", encoding="utf-8") as f:
        for line in header_lines:
            f.write(line + "\n")
        f.write(sep.join(data_lines[0]) + "\n")
        for row in data_lines[1:]:
            f.write(sep.join(row) + "\n")


# _extract_start_time unit tests

class TestExtractStartTime:
    def setup_method(self):
        self.plugin = _Plugin()

    def test_finds_start_time(self, tmp_path):
        p = tmp_path / "data.txt"
        p.write_text("Start: 09.03.2026 11:35:38\nTime[s]\tU[V]\n0\t3.7\n")
        result = self.plugin._extract_start_time(p, "utf-8")
        expected = pd.Timestamp("2026-03-09 11:35:38", tz="UTC").timestamp()
        assert result == pytest.approx(expected)

    def test_returns_none_when_regex_not_set(self, tmp_path):
        p = tmp_path / "data.txt"
        p.write_text("Start: 09.03.2026 11:35:38\nTime[s]\tU[V]\n0\t3.7\n")
        plugin = _Plugin()
        plugin.start_time_line_regex = None
        result = plugin._extract_start_time(p, "utf-8")
        assert result is None

    def test_returns_none_when_format_not_set(self, tmp_path):
        p = tmp_path / "data.txt"
        p.write_text("Start: 09.03.2026 11:35:38\nTime[s]\tU[V]\n0\t3.7\n")
        plugin = _Plugin()
        plugin.start_time_format = None
        result = plugin._extract_start_time(p, "utf-8")
        assert result is None

    def test_returns_none_when_no_match(self, tmp_path):
        p = tmp_path / "data.txt"
        p.write_text("No start time here\nTime[s]\tU[V]\n0\t3.7\n")
        result = self.plugin._extract_start_time(p, "utf-8")
        assert result is None

    def test_returns_none_on_unparseable_value(self, tmp_path):
        p = tmp_path / "data.txt"
        p.write_text("Start: not-a-date\nTime[s]\tU[V]\n0\t3.7\n")
        result = self.plugin._extract_start_time(p, "utf-8")
        assert result is None

    def test_uses_assume_naive_tz(self, tmp_path):
        p = tmp_path / "data.txt"
        p.write_text("Start: 09.03.2026 11:35:38\nTime[s]\tU[V]\n0\t3.7\n")
        plugin = _Plugin()
        plugin.assume_naive_tz = "Europe/London"
        result = plugin._extract_start_time(p, "utf-8")
        expected = pd.Timestamp("2026-03-09 11:35:38", tz="Europe/London").tz_convert("UTC").timestamp()
        assert result == pytest.approx(expected)


# fixup() Unix Time derivation

class TestFixupUnixTime:
    def setup_method(self):
        self.plugin = _Plugin()

    def test_adds_unix_time_from_start_time(self):
        self.plugin._start_time_epoch = 1000.0
        self.plugin._unit_hints = {}
        df = pd.DataFrame({"Test Time / s": [0.0, 1.0, 2.0], "Voltage / V": [3.7, 3.8, 3.9]})
        result = self.plugin.fixup(df)
        assert "Unix Time / s" in result.columns
        assert list(result["Unix Time / s"]) == [1000.0, 1001.0, 1002.0]

    def test_skips_if_unix_time_already_present(self):
        self.plugin._start_time_epoch = 1000.0
        self.plugin._unit_hints = {}
        df = pd.DataFrame({
            "Test Time / s": [0.0, 1.0],
            "Voltage / V": [3.7, 3.8],
            "Unix Time / s": [9999.0, 10000.0],
        })
        result = self.plugin.fixup(df)
        assert list(result["Unix Time / s"]) == [9999.0, 10000.0]

    def test_skips_if_no_start_time_epoch(self):
        self.plugin._start_time_epoch = None
        self.plugin._unit_hints = {}
        df = pd.DataFrame({"Test Time / s": [0.0, 1.0], "Voltage / V": [3.7, 3.8]})
        result = self.plugin.fixup(df)
        assert "Unix Time / s" not in result.columns

    def test_skips_if_no_test_time_column(self):
        self.plugin._start_time_epoch = 1000.0
        self.plugin._unit_hints = {}
        df = pd.DataFrame({"Voltage / V": [3.7, 3.8]})
        result = self.plugin.fixup(df)
        assert "Unix Time / s" not in result.columns


# Full parse → fixup pipeline with synthetic files

class TestFullPipeline:
    def test_basytec_derives_unix_time(self, tmp_path):
        """Basytec file with ~Start of Test header produces Unix Time / s."""
        import bdf

        p = tmp_path / "data.txt"
        p.write_text(
            "Basytec battery test system\n"
            "~Start of Test: 09.03.2026 11:35:38\n"
            "~Time[s]\t~U[V]\t~I[A]\n"
            "0\t3.700\t1.000\n"
            "1\t3.750\t1.000\n"
            "10\t3.800\t1.000\n",
            encoding="latin-1",
        )
        out = bdf.read(p, include_optional=True)
        assert "Unix Time / s" in out.columns
        assert out["Unix Time / s"].dtype == np.float64
        expected_t0 = pd.Timestamp("2026-03-09 11:35:38", tz="UTC").timestamp()
        assert out["Unix Time / s"].iloc[0] == pytest.approx(expected_t0)
        assert out["Unix Time / s"].iloc[1] == pytest.approx(expected_t0 + 1.0)
        assert out["Unix Time / s"].iloc[2] == pytest.approx(expected_t0 + 10.0)

    def test_biologic_derives_unix_time(self, tmp_path):
        """BioLogic MPT file with 'Acquisition started on' header produces Unix Time / s."""
        import bdf

        p = tmp_path / "data.mpt"
        p.write_text(
            "BT-Lab ASCII FILE\n"
            "Nb header lines : 5\n"
            "Acquisition started on : 03/09/2026 11:35:38.000\n"
            "\n"
            "time/s\tEwe/V\tI/mA\n"
            "0,000\t3,700\t100,000\n"
            "1,000\t3,750\t100,000\n"
            "10,000\t3,800\t100,000\n",
            encoding="latin-1",
        )
        out = bdf.read(p, include_optional=True)
        assert "Unix Time / s" in out.columns
        assert pd.api.types.is_float_dtype(out["Unix Time / s"])
        expected_t0 = pd.Timestamp("2026-03-09 11:35:38", tz="UTC").timestamp()
        assert out["Unix Time / s"].iloc[0] == pytest.approx(expected_t0)
        assert out["Unix Time / s"].iloc[2] == pytest.approx(expected_t0 + 10.0)

    def test_no_start_time_header_no_unix_time(self, tmp_path):
        """File without a start time header should not produce Unix Time / s."""
        import bdf

        p = tmp_path / "data.txt"
        p.write_text(
            "Basytec battery test system\n"
            "~Time[s]\t~U[V]\t~I[A]\n"
            "0\t3.700\t1.000\n"
            "1\t3.750\t1.000\n",
            encoding="latin-1",
        )
        out = bdf.read(p, include_optional=True)
        assert "Unix Time / s" not in out.columns


# Plugin attribute declarations

class TestPluginAttributes:
    def test_basytec_has_start_time_regex(self):
        assert BasytecTxt.start_time_line_regex is not None
        assert BasytecTxt.start_time_format == "%d.%m.%Y %H:%M:%S"

    def test_biologic_has_start_time_regex(self):
        assert BioLogicMPT.start_time_line_regex is not None
        assert BioLogicMPT.start_time_format == "%m/%d/%Y %H:%M:%S.%f"

    def test_basytec_regex_matches_header_line(self):
        import re
        pat = re.compile(BasytecTxt.start_time_line_regex, re.IGNORECASE)
        m = pat.search("~Start of Test: 09.03.2026 11:35:38")
        assert m is not None
        assert m.group(1).strip() == "09.03.2026 11:35:38"

    def test_biologic_regex_matches_header_line(self):
        import re
        pat = re.compile(BioLogicMPT.start_time_line_regex, re.IGNORECASE)
        m = pat.search("Acquisition started on : 03/09/2026 11:35:38.000")
        assert m is not None
        assert m.group(1).strip() == "03/09/2026 11:35:38.000"
