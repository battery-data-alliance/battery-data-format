from pathlib import Path

import pandas as pd
import pytest

from bdf import io


def test_detect_format_known_and_unknown(tmp_path: Path):
    # Known formats
    assert io._detect_format(tmp_path / "file.bdf.csv") == "csv"
    assert io._detect_format(tmp_path / "file.bdf.parquet") == "parquet"
    assert io._detect_format(tmp_path / "file.bdf.json") == "json"


def test_save_and_load_roundtrip_csv_parquet_json(tmp_path: Path):
    df = pd.DataFrame(
        {
            "Test Time / s": [0, 1, 2],
            "Voltage / V": [3.7, 3.6, 3.5],
            "Current / A": [0.1, 0.1, 0.1],
        }
    )

    for fname in ("sample.bdf.csv", "sample.bdf.parquet", "sample.bdf.json"):
        path = tmp_path / fname
        io.save(df, path, index=False)
        loaded = io.load(path)
        pd.testing.assert_frame_equal(df, loaded)


def test_detect_format_unknown_raises(tmp_path: Path):
    bad = tmp_path / "file.unknown"
    bad.touch()
    with pytest.raises(ValueError):
        io._detect_format(bad)
