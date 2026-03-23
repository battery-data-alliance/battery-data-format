import pandas as pd

from bdf.normalize.normalize import _enforce_normalized_dtypes, _is_hms_duration_series


def test_is_hms_duration_series_true_for_hms_values():
    """Returns True for HH:MM:SS(.f) string values."""
    s = pd.Series(["00:00:10", "12:34:56.789", None])

    assert _is_hms_duration_series(s)


def test_is_hms_duration_series_false_for_numeric_strings():
    """Returns False for plain numeric strings."""
    s = pd.Series(["1.0", "2.5", None])

    assert not _is_hms_duration_series(s)


def test_is_hms_duration_series_true_for_timedelta_dtype():
    """Returns True for timedelta-typed series."""
    s = pd.Series([pd.Timedelta(seconds=10), pd.Timedelta(seconds=20.5)])

    assert _is_hms_duration_series(s)


def test_is_hms_duration_series_false_for_datetime_dtype():
    """Returns False for datetime-typed series."""
    s = pd.to_datetime(pd.Series(["2026-01-01T00:00:10Z", "2026-01-01T00:00:20Z"]))

    assert not _is_hms_duration_series(s)


def test_enforce_normalized_dtypes_time_column_numeric_strings_are_seconds():
    """Parses numeric-string time values as seconds."""
    df = pd.DataFrame(
        {
            "Test Time / s": ["1.0", "2.5"],
            "Voltage / V": ["3.50", "3.60"],
            "Current / A": ["0.10", "0.20"],
        }
    )

    out = _enforce_normalized_dtypes(df)

    assert out["Test Time / s"].tolist() == [1.0, 2.5]


def test_enforce_normalized_dtypes_time_column_hms_is_duration_seconds():
    """Parses HH:MM:SS(.f) time values into seconds."""
    df = pd.DataFrame(
        {
            "Test Time / s": ["00:00:10", "00:01:02.5"],
            "Voltage / V": ["3.50", "3.60"],
            "Current / A": ["0.10", "0.20"],
        }
    )

    out = _enforce_normalized_dtypes(df)

    assert out["Test Time / s"].tolist() == [10.0, 62.5]


def test_enforce_normalized_dtypes_time_column_timedelta_is_seconds():
    """Converts timedelta time values to numeric seconds."""
    df = pd.DataFrame(
        {
            "Test Time / s": [pd.Timedelta(seconds=10), pd.Timedelta(seconds=62.5)],
            "Voltage / V": ["3.50", "3.60"],
            "Current / A": ["0.10", "0.20"],
        }
    )

    out = _enforce_normalized_dtypes(df)

    assert out["Test Time / s"].tolist() == [10.0, 62.5]


def test_enforce_normalized_dtypes_preserves_numeric_time_and_parses_numeric_strings():
    """Preserves numeric time columns and coerces other numeric strings."""
    df = pd.DataFrame(
        {
            "Test Time / s": [1.0, 2.0],
            "Step Time / s": [0.5, 1.5],
            "Voltage / V": ["3.50", "3.60"],
            "Cycle Number / 1": ["1", "2"],
        }
    )

    out = _enforce_normalized_dtypes(df)

    assert out["Test Time / s"].tolist() == [1.0, 2.0]
    assert out["Step Time / s"].tolist() == [0.5, 1.5]
    assert out["Voltage / V"].tolist() == [3.5, 3.6]
    assert out["Cycle Number / 1"].tolist() == [1, 2]
    assert str(out["Cycle Number / 1"].dtype) == "Int64"