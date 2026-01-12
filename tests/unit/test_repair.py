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
