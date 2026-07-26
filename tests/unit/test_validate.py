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


def test_validate_accepts_bdf_csv_without_bdf_filename_prefix(tmp_path):
    """A file with BDF headers without bdf in path should still work."""
    df = _base_df()
    csv_path = tmp_path / "sample.csv"
    df.to_csv(csv_path, index=False)

    rep = validate(csv_path, report=False, raise_on_error=True)
    assert rep["ok"] is True


def test_validate_rejects_unrecognized_csv(tmp_path):
    df = pd.DataFrame({"foo": [1, 2, 3], "bar": [4, 5, 6]})
    csv_path = tmp_path / "garbage.csv"
    df.to_csv(csv_path, index=False)

    rep = validate(csv_path, report=False, raise_on_error=False)
    assert rep["ok"] is False
    assert rep["kind"] == "not_bdf_artifact"


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


def test_derived_identity_tolerates_csv_roundtrip_noise_near_zero():
    """8-significant-digit CSV round-trips leave ~1e-8-of-scale residue where the
    identity crosses zero; the scale-aware atol must not flag that as a violation."""
    df = _consistent_derived_df()
    # perturb net by 1e-8 of the ~2 Ah column scale near its zero crossing
    df.loc[1, "net_capacity_ah"] += 2.7e-08
    rep = validate_df(df, report=False, raise_on_error=False)
    assert not any(d["check"] == "identity" for d in rep["derived"]["details"])


def test_derived_identity_still_catches_gross_violation():
    """The scale-aware atol stays far below real violations (e.g. swapped columns)."""
    df = _consistent_derived_df()
    df["net_capacity_ah"] = df["charging_capacity_ah"] + df["discharging_capacity_ah"]
    rep = validate_df(df, report=False, raise_on_error=False)
    assert any(d["check"] == "identity" for d in rep["derived"]["details"])


# ---------------------------------------------------------------------------
# Characterization tests for the polars port (0.2.0 API freeze).
# These pin the report structure and warning behavior of validate_df so the
# pandas -> polars port can be verified as behavior-identical.
# ---------------------------------------------------------------------------


def test_report_has_stable_shape_and_keys():
    rep = validate_df(_base_df(), report=False, raise_on_error=False)
    assert set(rep.keys()) == {
        "ok",
        "missing",
        "extras",
        "required",
        "optional",
        "legacy_labels",
        "n_rows",
        "n_cols",
        "time_stats",
        "derived",
    }
    assert rep["ok"] is True
    assert rep["n_rows"] == 3
    assert rep["n_cols"] == 3
    assert rep["missing"] == []
    assert rep["extras"] == []
    assert set(rep["derived"].keys()) == {"issues", "details"}


def test_time_stats_monotonic_shape():
    rep = validate_df(_base_df(), report=False, raise_on_error=False)
    ts = rep["time_stats"]
    assert ts["present"] is True
    assert ts["monotonic"] is True
    assert ts["violations"] == 0
    assert ts["min_drop"] == 0.0


def test_time_stats_nonmonotonic_fields_and_warning():
    df = _base_df()
    df["Test Time / s"] = [0.0, 10.0, 3.0]
    with pytest.warns(RuntimeWarning, match="Non-monotonic"):
        rep = validate_df(df, report=False, raise_on_error=False)
    ts = rep["time_stats"]
    assert ts["monotonic"] is False
    assert ts["violations"] == 1
    assert ts["min_drop"] == -7.0
    assert ts["first_bad_index"] == 2


def test_unknown_columns_reported_as_extras():
    df = _base_df()
    df["Totally Custom / X"] = [1, 2, 3]
    rep = validate_df(df, report=False, raise_on_error=False)
    assert rep["extras"] == ["Totally Custom / X"]
    assert rep["ok"] is True  # extras are allowed


def test_time_stats_absent_when_no_time_column():
    df = pd.DataFrame({"Voltage / V": [3.7], "Current / A": [0.1]})
    rep = validate_df(df, report=False, raise_on_error=False)
    assert rep["time_stats"]["present"] is False
    assert rep["ok"] is False
    assert "Test Time / s" in rep["missing"]
