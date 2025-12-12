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
