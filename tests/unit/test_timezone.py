from bdf.data_sources.base_delimited import DelimitedTextPlugin
from pathlib import Path

import pandas as pd
import pytest

import bdf


def test_timezone_parameter_applied_to_plugin(tmp_path, monkeypatch):
    """Verify that timezone parameter is applied to plugin before parse()."""
    raw = tmp_path / "raw.dat"
    raw.write_text("dummy")

    captured_timezone = None

    class MockPlugin:
        id = "mock"
        assume_naive_tz = "UTC"
        column_synonyms = {
            "Test Time / s": ["time"],
            "Voltage / V": ["voltage"],
            "Current / A": ["current"],
        }

        def parse(self, path: Path):
            nonlocal captured_timezone
            captured_timezone = self.assume_naive_tz
            return pd.DataFrame(
                {
                    "time": [0, 1],
                    "voltage": [3.7, 3.6],
                    "current": [0.1, 0.1],
                }
            )

        def augment(self, df_raw: pd.DataFrame):
            return df_raw

        def fixup(self, df: pd.DataFrame):
            return df

    monkeypatch.setattr(
        bdf,
        "_candidate_plugins",
        lambda path, *, plugin, plugin_hint: [MockPlugin()],
    )

    # Test with timezone parameter
    bdf.read(raw, timezone="America/New_York", validate=True)
    assert captured_timezone == "America/New_York"

    # Test without timezone parameter (should remain default)
    bdf.read(raw, validate=True)
    assert captured_timezone == "UTC"


def test_timezone_parameter_with_naive_timestamp_column(tmp_path, monkeypatch):
    """Verify timezone affects parsing of naive timestamp columns."""
    raw = tmp_path / "raw.dat"
    raw.write_text("dummy")

    class TimestampPlugin:
        id = "timestamp-plugin"
        assume_naive_tz = "UTC"
        timestamp_candidate_patterns = ("Date Time",)
        column_synonyms = {
            "Test Time / s": ["test time (s)"],
            "Voltage / V": ["voltage (v)"],
            "Current / A": ["current (a)"],
        }

        def parse(self, path: Path):
            # A naive datetime (no timezone info)
            return pd.DataFrame(
                {
                    "Date Time": pd.to_datetime(
                        ["2024-01-15 12:00:00", "2024-01-15 12:00:01"]
                    ),
                    "test time (s)": [0, 1],
                    "voltage (v)": [3.7, 3.6],
                    "current (a)": [0.1, 0.1],
                }
            )

        def augment(self, df_raw: pd.DataFrame):
            # _ensure_unix_time will convert naive "Date Time" using self.assume_naive_tz
            from bdf.time import parse_unix_time

            if "Unix Time / s" not in df_raw.columns:
                unix = parse_unix_time(
                    df_raw["Date Time"],
                    fmt=None,
                    tz=self.assume_naive_tz,
                    min_success=0.5,
                )
                df_raw = df_raw.copy()
                df_raw["Unix Time / s"] = unix
            return df_raw

        def fixup(self, df: pd.DataFrame):
            return df

    monkeypatch.setattr(
        bdf,
        "_candidate_plugins",
        lambda path, *, plugin, plugin_hint: [TimestampPlugin()],
    )

    # Read with UTC (default behavior)
    df_utc = bdf.read(raw, timezone="UTC", validate=False)
    utc_epoch = df_utc["Unix Time / s"].iloc[0]

    # Read with no timezone (should default to plugin's assume_naive_tz, which is UTC)
    df_none = bdf.read(raw, timezone=None, validate=False)
    none_epoch = df_none["Unix Time / s"].iloc[0]
    assert none_epoch == utc_epoch

    # Read with America/New_York (UTC-5 in winter, UTC-4 in summer)
    df_ny = bdf.read(raw, timezone="America/New_York", validate=False)
    ny_epoch = df_ny["Unix Time / s"].iloc[0]

    # Same wall-clock time (2024-01-15 12:00:00) interpreted as NY is 5 hours later in UTC
    # than when interpreted as UTC, so NY epoch should be 5 hours greater
    assert ny_epoch - utc_epoch == pytest.approx(5 * 3600, abs=1)


def test_timezone_parameter_with_parse_function(tmp_path, monkeypatch):
    """Verify timezone parameter works with parse() convenience function."""
    raw = tmp_path / "raw.dat"
    raw.write_text("dummy")

    captured_timezone = None

    class MockPlugin:
        id = "mock"
        assume_naive_tz = "UTC"
        column_synonyms = {
            "Test Time / s": ["time"],
            "Voltage / V": ["voltage"],
            "Current / A": ["current"],
        }

        def parse(self, path: Path):
            nonlocal captured_timezone
            captured_timezone = self.assume_naive_tz
            return pd.DataFrame(
                {
                    "time": [0, 1],
                    "voltage": [3.7, 3.6],
                    "current": [0.1, 0.1],
                }
            )

        def augment(self, df_raw: pd.DataFrame):
            return df_raw

        def fixup(self, df: pd.DataFrame):
            return df

    monkeypatch.setattr(
        bdf,
        "_candidate_plugins",
        lambda path, *, plugin, plugin_hint: [MockPlugin()],
    )

    # parse() should also accept and pass through timezone parameter
    bdf.parse(raw, timezone="Europe/London")
    assert captured_timezone == "Europe/London"


def test_timezone_parameter_unix_time_conversion_real_data(tmp_path, monkeypatch):
    """
    Verify Unix Time column is correctly converted when reading vendor data 
    with different timezone parameters.
    
    Generates a DataFrame with naive datetime, writes to CSV, then reads back
    with different timezone parameters. The plugin's timestamp_candidate_patterns
    identifies the timestamp column, and BDF's normalization logic automatically
    converts it to Unix Time using the timezone parameter. Confirms Unix Time 
    differs correctly based on timezone interpretation.
    """
    # Create a CSV file with naive timestamp strings (use vendor-style column names
    # to avoid being detected as BDF artifact)
    csv_file = tmp_path / "test_data.csv"
    csv_content = """time (s),voltage (v),current (a),timestamp
0.0,3.707,0.0,2024-01-15 12:00:00
1.0,3.706,0.0,2024-01-15 12:00:01
2.0,3.705,0.0,2024-01-15 12:00:02
"""
    csv_file.write_text(csv_content)

    class TimestampParsingPlugin(DelimitedTextPlugin):
        """Mock plugin that identifies timestamps for BDF normalization to handle."""
        id = "timestamp-test"
        assume_naive_tz = "UTC"
        timestamp_candidate_patterns = ("timestamp",)
        timestamp_formats = ("%Y-%m-%d %H:%M:%S",)
        column_synonyms = {
            "Test Time / s": ["time (s)"],
            "Voltage / V": ["voltage (v)"],
            "Current / A": ["current (a)"],
        }

        def parse(self, path: Path):
            # Parse CSV; leave timestamp as string for BDF to detect and convert
            return pd.read_csv(path)


    monkeypatch.setattr(
        bdf,
        "_candidate_plugins",
        lambda path, *, plugin, plugin_hint: [TimestampParsingPlugin()],
    )

    # Read with UTC timezone
    df_utc = bdf.read(csv_file, timezone="UTC", validate=False)
    assert "Unix Time / s" in df_utc.columns
    utc_times = df_utc["Unix Time / s"].values
    
    # Read with America/New_York timezone (UTC-5 in winter, Jan-15)
    df_ny = bdf.read(csv_file, timezone="America/New_York", validate=False)
    assert "Unix Time / s" in df_ny.columns
    ny_times = df_ny["Unix Time / s"].values
    
    # Same wall-clock time "2024-01-15 12:00:00" interpreted as NY time
    # is 5 hours behind UTC, so NY epoch should be 5 hours GREATER
    expected_diff = 5 * 3600
    
    # Check that Unix Time differs by approximately 5 hours between timezones
    actual_diffs = ny_times - utc_times
    assert actual_diffs == pytest.approx(expected_diff, abs=1)
