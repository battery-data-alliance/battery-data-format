import numpy as np
import pandas as pd
import pytest

from bdf.repair import (
    _compute_eps_from_diffs,
    _global_mad_z,
    _hampel_mask,
    _slope_mask,
    _window_len_from_seconds,
    clean,
    fix_time,
)


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


# ---------------------------------------------------------------------------
# Characterization tests for the polars port (0.2.0 API freeze).
#
# These pin the CURRENT behavior of repair.py — exact values, report fields,
# and user-visible note strings — so the pandas -> polars port can be verified
# as behavior-identical. Where behavior is quirky (e.g. negative start times
# are not rebased), the quirk is pinned deliberately; changing it is a
# separate, reviewable decision, not a silent side effect of the port.
# ---------------------------------------------------------------------------


def _smooth_df(n=100, dt=10.0, spike_at=None, spike_value=100.0):
    """Linear ramp voltage/current over a regular time grid, optional spike."""
    t = np.arange(n, dtype=float) * dt
    v = np.linspace(3.7, 3.5, n)
    i = np.full(n, 0.1)
    if spike_at is not None:
        v = v.copy()
        v[spike_at] = spike_value
    return pd.DataFrame({"Test Time / s": t, "Voltage / V": v, "Current / A": i})


# ---- helper-level numerics ----


def test_compute_eps_from_diffs_exact():
    assert _compute_eps_from_diffs(np.array([1.0, 1.0, 1.0])) == 0.1
    assert _compute_eps_from_diffs(np.array([2.0, 4.0, 6.0])) == pytest.approx(0.4)
    # no positive diffs -> floor
    assert _compute_eps_from_diffs(np.array([0.0, -1.0])) == 1e-9


def test_window_len_from_seconds_odd_and_fallback():
    t = pd.Series(np.arange(100, dtype=float) * 10.0)
    # 600 s / 10 s = 60 -> bumped to odd 61
    assert _window_len_from_seconds(t, 600.0) == 61
    # 50 s / 10 s = 5 -> already odd, floor of 5 applies
    assert _window_len_from_seconds(t, 50.0) == 5
    # unusable time -> fallback
    assert _window_len_from_seconds(pd.Series([np.nan, np.nan]), 600.0) == 41


def test_global_mad_z_flags_extreme_spike_only():
    x = np.linspace(3.7, 3.5, 100)
    x[50] = 100.0
    z, med, madn = _global_mad_z(x)
    assert madn > 0
    assert abs(z[50]) > 8.0
    mask = np.abs(z) > 8.0
    assert mask.sum() == 1


def test_hampel_flags_spike_and_clean_series_flags_nothing():
    df = _smooth_df(spike_at=50)
    t = df["Test Time / s"]
    hampel = _hampel_mask(df["Voltage / V"], time_s=t, seconds=300.0, k=6.0)
    assert bool(hampel.iloc[50])
    clean_v = _smooth_df()["Voltage / V"]
    assert not _hampel_mask(clean_v, time_s=t, seconds=300.0, k=6.0).any()


def test_slope_mask_needs_derivative_spread():
    # pinned: on a perfectly linear series the derivative's MAD is 0, so the
    # slope gate is guarded to all-False even for a huge spike.
    df = _smooth_df(spike_at=50)
    t = df["Test Time / s"]
    assert not _slope_mask(df["Voltage / V"], time_s=t, z=8.0).any()
    # with any real spread in the derivative, the spike is flagged
    v = df["Voltage / V"] + 0.001 * np.sin(np.arange(len(df)))
    slope = _slope_mask(v, time_s=t, z=8.0)
    assert bool(slope.iloc[50])


# ---- fix_time methods ----


def test_fix_time_auto_recomputes_from_date_col():
    df = pd.DataFrame(
        {
            "Test Time / s": [0.0, 999.0, 5.0],  # garbage on purpose
            "Date Time ISO": ["2024-01-01T00:00:00", "2024-01-01T00:00:10", "2024-01-01T00:00:20"],
        }
    )
    out = fix_time(df, method="auto")
    assert out["Test Time / s"].tolist() == [0.0, 10.0, 20.0]


def test_fix_time_recompute_without_timestamps_raises():
    df = pd.DataFrame({"Test Time / s": [0.0, 1.0]})
    with pytest.raises(ValueError, match="Cannot recompute"):
        fix_time(df, method="recompute")


def test_fix_time_sort_drops_duplicate_timestamps():
    df = pd.DataFrame({"Test Time / s": [0.0, 10.0, 10.0, 5.0], "Voltage / V": [1.0, 2.0, 3.0, 4.0]})
    out = fix_time(df, method="sort")
    assert out["Test Time / s"].tolist() == [0.0, 5.0, 10.0]
    # stable sort keeps the FIRST row for a duplicated timestamp
    assert out["Voltage / V"].tolist() == [1.0, 4.0, 2.0]


def test_fix_time_drop_removes_decreasing_rows():
    df = pd.DataFrame({"Test Time / s": [0.0, 10.0, 3.0, 20.0]})
    out = fix_time(df, method="drop", eps=1.0)
    assert out["Test Time / s"].tolist() == [0.0, 10.0, 20.0]


def test_fix_time_missing_column_returns_unchanged():
    df = pd.DataFrame({"Voltage / V": [1.0]})
    out = fix_time(df)
    assert out.equals(df)


def test_fix_time_unknown_method_raises():
    with pytest.raises(ValueError, match="Unknown method"):
        fix_time(pd.DataFrame({"Test Time / s": [0.0]}), method="bogus")


# ---- clean(): time paths, rebase, report ----


def test_clean_time_drop_reports_note_and_rowcount():
    df = _smooth_df(n=40)
    df.loc[20, "Test Time / s"] = 3.0  # big backwards jump
    out, rep = clean(df, time_fix="drop", outlier="none")
    assert rep.n_rows_in == 40
    assert rep.n_rows_out == 39
    assert rep.time_method == "drop"
    assert rep.notes == ["Dropped 1 rows due to time decreases."]


def test_clean_time_none_leaves_time_untouched():
    df = _smooth_df(n=40)
    df.loc[20, "Test Time / s"] = 3.0
    out, rep = clean(df, time_fix="none", outlier="none")
    assert rep.time_method == "none"
    assert out["Test Time / s"].iloc[20] == 3.0
    # but detected resets are still counted
    assert rep.n_time_resets == 1


def test_clean_invalid_time_fix_raises():
    with pytest.raises(ValueError, match="time_fix must be one of"):
        clean(_smooth_df(), time_fix="bogus")


def test_clean_missing_time_column_raises():
    with pytest.raises(ValueError, match="Missing 'Test Time / s'"):
        clean(pd.DataFrame({"Voltage / V": [1.0]}))


def test_clean_rebases_positive_start_to_zero():
    df = _smooth_df(n=40)
    df["Test Time / s"] = df["Test Time / s"] + 100.0
    out, _ = clean(df, time_fix="none", outlier="none")
    assert out["Test Time / s"].iloc[0] == 0.0


def test_clean_does_not_rebase_negative_start():
    # pinned quirk: rebase only fires when tmin > 0
    df = _smooth_df(n=40)
    df["Test Time / s"] = df["Test Time / s"] - 5.0
    out, _ = clean(df, time_fix="none", outlier="none")
    assert out["Test Time / s"].iloc[0] == -5.0


# ---- clean(): outlier actions ----


def test_clean_outlier_drop_removes_spike_row():
    df = _smooth_df(spike_at=50)
    out, rep = clean(df, time_fix="none", outlier="drop")
    assert rep.n_rows_out == 99
    assert rep.per_column_outliers["Voltage / V"] == 1
    assert rep.per_column_outliers["Current / A"] == 0
    assert 100.0 not in out["Voltage / V"].values
    assert rep.notes == ["Dropped 1 rows due to outliers in Voltage / V, Current / A."]


def test_clean_outlier_clip_bounds_spike():
    df = _smooth_df(spike_at=50)
    out, rep = clean(df, time_fix="none", outlier="clip", z_thresh=8.0)
    v = out["Voltage / V"]
    assert len(out) == 100  # no rows dropped
    assert v.iloc[50] < 100.0
    # clipped to med + z*madn, which stays near the data's own scale
    assert v.iloc[50] < 5.0
    assert rep.notes == ["Clipped outliers to robust bounds (MAD/IQR)."]


def test_clean_outlier_interp_replaces_spike_with_neighbors():
    df = _smooth_df(spike_at=50)
    out, rep = clean(df, time_fix="none", outlier="interp")
    v = out["Voltage / V"]
    expected = np.linspace(3.7, 3.5, 100)[50]  # linear trend value
    # neighbors are on the trend, so time-linear interpolation lands on it
    assert v.iloc[50] == pytest.approx(expected, abs=1e-6)
    assert rep.notes == ["Interpolated outliers linearly over time."]


def test_clean_outlier_short_series_below_min_n_flags_nothing():
    df = _smooth_df(n=20, spike_at=10)
    out, rep = clean(df, time_fix="none", outlier="drop")
    assert rep.n_rows_out == 20
    assert rep.per_column_outliers == {"Voltage / V": 0, "Current / A": 0}


def test_clean_outlier_respects_columns_argument():
    df = _smooth_df(spike_at=50)
    out, rep = clean(df, time_fix="none", outlier="drop", columns=["Current / A"])
    # spike lives in Voltage, which we did not select -> nothing flagged
    assert rep.n_rows_out == 100
    assert rep.per_column_outliers == {"Current / A": 0}


def test_clean_outlier_mad_method_flags_spike():
    df = _smooth_df(spike_at=50)
    out, rep = clean(df, time_fix="none", outlier="drop", outlier_detect="mad")
    assert rep.per_column_outliers["Voltage / V"] == 1


def test_clean_report_str_contains_summary_lines():
    df = _smooth_df(spike_at=50)
    _, rep = clean(df, time_fix="segment", outlier="drop")
    text = str(rep)
    assert "Rows: 100 -> 99" in text
    assert "Time fix: segment" in text
    assert "Outliers: drop (z>8)" in text
    assert "Per-column outliers: Voltage / V=1, Current / A=0" in text
