import pandas as pd

from bdf.repair import clean, fix_time


def test_fix_time_sorts_and_segments():
    df = pd.DataFrame(
        {
            "Test Time / s": [0.0, 1.0, 0.5, 2.0],
            "Voltage / V": [3.7, 3.6, 3.65, 3.5],
            "Current / A": [0.1, 0.1, 0.1, 0.1],
        }
    )
    fixed = fix_time(df, method="segment")
    assert fixed["Test Time / s"].is_monotonic_increasing


def test_clean_reports_and_fixes_time(tmp_path):
    df = pd.DataFrame(
        {
            "Test Time / s": [0, 1, 2, 1, 3],
            "Voltage / V": [3.7, 3.6, 3.5, 3.55, 3.4],
            "Current / A": [0.1, 0.1, 0.1, 0.1, 0.1],
        }
    )
    cleaned, report = clean(df, time_fix="segment", outlier="none")
    assert cleaned["Test Time / s"].is_monotonic_increasing
    assert report.n_time_resets >= 1
    assert report.n_rows_out == len(cleaned)


# -----------------------------------------------------------
# Repair edge-case tests
# -----------------------------------------------------------

def test_fix_time_all_zero_timestamps():
    """All-zero timestamps should remain zero (already monotonic non-decreasing)."""
    df = pd.DataFrame(
        {
            "Test Time / s": [0.0, 0.0, 0.0, 0.0],
            "Voltage / V": [3.7, 3.6, 3.5, 3.4],
            "Current / A": [0.1, 0.1, 0.1, 0.1],
        }
    )
    fixed = fix_time(df, method="segment")
    # All zeros are non-decreasing, so no resets should occur
    assert list(fixed["Test Time / s"]) == [0.0, 0.0, 0.0, 0.0]


def test_fix_time_single_row():
    """A single-row DataFrame should pass through unchanged."""
    df = pd.DataFrame(
        {
            "Test Time / s": [42.0],
            "Voltage / V": [3.7],
            "Current / A": [0.1],
        }
    )
    fixed = fix_time(df, method="segment")
    assert len(fixed) == 1
    assert fixed["Test Time / s"].iloc[0] == 42.0


def test_fix_time_large_gap_between_segments():
    """A large forward jump in time should be preserved (not a reset)."""
    df = pd.DataFrame(
        {
            "Test Time / s": [0.0, 1.0, 2.0, 1000.0, 1001.0, 1002.0],
            "Voltage / V": [3.7, 3.6, 3.5, 3.4, 3.3, 3.2],
            "Current / A": [0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
        }
    )
    fixed = fix_time(df, method="segment")
    assert fixed["Test Time / s"].is_monotonic_increasing
    # The large gap should be preserved
    assert fixed["Test Time / s"].iloc[3] >= 100.0


def test_fix_time_already_monotonic_is_noop():
    """Already-monotonic data should pass through unchanged."""
    times = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    df = pd.DataFrame(
        {
            "Test Time / s": times,
            "Voltage / V": [3.7, 3.6, 3.5, 3.4, 3.3, 3.2],
            "Current / A": [0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
        }
    )
    fixed = fix_time(df, method="segment")
    assert list(fixed["Test Time / s"]) == times


def test_clean_all_zero_timestamps():
    """clean() with all-zero timestamps should not raise."""
    df = pd.DataFrame(
        {
            "Test Time / s": [0.0, 0.0, 0.0],
            "Voltage / V": [3.7, 3.6, 3.5],
            "Current / A": [0.1, 0.1, 0.1],
        }
    )
    cleaned, report = clean(df, time_fix="segment", outlier="none")
    assert report.n_rows_out == 3


def test_clean_single_row():
    """clean() with a single-row DataFrame should work without errors."""
    df = pd.DataFrame(
        {
            "Test Time / s": [5.0],
            "Voltage / V": [3.7],
            "Current / A": [0.1],
        }
    )
    cleaned, report = clean(df, time_fix="segment", outlier="none")
    assert report.n_rows_out == 1
    assert report.n_rows_in == 1
