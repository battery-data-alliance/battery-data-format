# tests/test_validate_bdf.py
from pathlib import Path
import pandas as pd
from bdf.validate import validate_df

def test_validate_basic_ok():
    df = pd.DataFrame({
        "Test Time / s": [0, 1, 2],
        "Voltage / V": [4.20, 4.19, 4.18],
        "Current / A": [0.5, 0.5, 0.4],
    })
    rep = validate_df(df)
    assert rep.ok and not rep.errors
