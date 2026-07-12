import warnings

import pandas as pd
import pytest

from bdf import BDFValidationError, validate, validate_df


def _base_df():
    return pd.DataFrame(
        {
            "Test Time / s": [0, 1, 2],
            "Voltage / V": [3.7, 3.6, 3.5],
            "Current / A": [0.1, 0.1, 0.1],
        }
    )


def test_validate_df_ok_and_report():
    df = _base_df()
    rep = validate_df(df, report=False, raise_on_error=True)
    assert rep["ok"] is True
    assert not rep["missing"]


def test_validate_df_missing_columns_raises():
    df = _base_df().drop(columns=["Voltage / V"])
    with pytest.raises(BDFValidationError):
        validate_df(df)


def test_validate_function_on_dataframe_and_path(tmp_path):
    df = _base_df()
    csv_path = tmp_path / "sample.bdf.csv"
    df.to_csv(csv_path, index=False)

    rep_df = validate(df, report=False, raise_on_error=False)
    assert rep_df["ok"] is True

    rep_path = validate(csv_path, report=False, raise_on_error=True)
    assert rep_path["ok"] is True


def test_validate_accepts_notation_headers(tmp_path):
    df = pd.DataFrame(
        {
            "test_time_second": [0, 1, 2],
            "voltage_volt": [3.7, 3.6, 3.5],
            "current_ampere": [0.1, 0.1, 0.1],
        }
    )
    csv_path = tmp_path / "notation.bdf.csv"
    df.to_csv(csv_path, index=False)
    rep = validate(csv_path, report=False, raise_on_error=True)
    assert rep["ok"] is True


def _consistent_derived_df():
    """A small, internally-consistent frame with derived columns."""
    charge = [0.0, 1.0, 2.0, 2.0]  # accumulates during charge, then flat
    discharge = [0.0, 0.0, 0.0, 1.5]  # accumulates during discharge
    return pd.DataFrame(
        {
            "test_time_second": [0, 1, 2, 3],
            "voltage_volt": [3.7, 3.8, 3.9, 3.6],
            "current_ampere": [1.0, 1.0, 0.0, -1.0],
            "step_index": [1, 2, 1, 2],
            "cycle_count": [0, 0, 1, 1],
            "charging_capacity_ah": charge,
            "discharging_capacity_ah": discharge,
            "cumulative_capacity_ah": [c + d for c, d in zip(charge, discharge, strict=True)],
            "net_capacity_ah": [c - d for c, d in zip(charge, discharge, strict=True)],
        }
    )


def test_derived_columns_consistent_no_issues():
    rep = validate_df(_consistent_derived_df(), report=False, raise_on_error=False)
    assert rep["derived"]["issues"] == []


def test_derived_identity_violation_flagged_and_warns():
    df = _consistent_derived_df()
    # Break the cumulative = charging + discharging identity.
    df["cumulative_capacity_ah"] = df["charging_capacity_ah"] - df["discharging_capacity_ah"]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rep = validate_df(df, report=False, raise_on_error=False)
    checks = {d["check"] for d in rep["derived"]["details"]}
    assert "identity" in checks
    assert any(issubclass(w.category, RuntimeWarning) for w in caught)


def test_step_index_holding_step_id_flagged():
    df = _consistent_derived_df()
    # Values that never reset to 1 -> looks like a program Step ID, not step_index.
    df["step_index"] = [4, 4, 9, 9]
    rep = validate_df(df, report=False, raise_on_error=False)
    assert any(d["check"] == "step_index_min" for d in rep["derived"]["details"])


def test_cycle_count_non_monotonic_flagged():
    df = _consistent_derived_df()
    df["cycle_count"] = [0, 1, 0, 1]
    rep = validate_df(df, report=False, raise_on_error=False)
    assert any(d["check"] == "monotonic" and d["column"] == "cycle_count" for d in rep["derived"]["details"])
