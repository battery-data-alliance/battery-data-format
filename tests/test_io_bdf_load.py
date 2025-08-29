# tests/test_io_bdf_load.py
from pathlib import Path
import pandas as pd
from bdf.io import load, is_bdf

def test_load_and_is_bdf(tmp_path: Path):
    p = tmp_path / "t.bdf.csv"
    df = pd.DataFrame({
        "Test Time / s": [0, 1, 2],
        "Voltage / V": [4.2, 4.19, 4.18],
        "Current / A": [0.5, 0.5, 0.5],
    })
    df.to_csv(p, index=False)
    got = load(p)
    assert is_bdf(got)
