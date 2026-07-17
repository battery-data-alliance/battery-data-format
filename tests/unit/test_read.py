"""Test reading from various formats."""

from pathlib import Path

import polars as pl
from polars.testing import assert_frame_equal

import bdf


def test_read_bdf_files(tmp_path: Path) -> None:
    """Read bdf from various files."""
    df1 = pl.DataFrame(
        {
            "Test Time / s": [1.0, 2.0, 3.0],
            "Voltage / V": [4.0, 4.1, 4.2],
            "Current / A": [0.1, 0.1, 0.1],
        }
    )

    for extra_ext in ("", ".bdf", ".a.b.c", ".a.b.c.bdf"):
        p = tmp_path / f"data{extra_ext}.csv"
        df1.write_csv(p)
        df2, _metadata = bdf.read(p, lazy=False)
        assert_frame_equal(df1, df2)

        p = tmp_path / f"data{extra_ext}.parquet"
        df1.write_parquet(p)
        df2, _metadata = bdf.read(p, lazy=False)
        assert_frame_equal(df1, df2)

        p = tmp_path / f"data{extra_ext}.json"
        df1.write_json(p)
        df2, _metadata = bdf.read(p, lazy=False)
        assert_frame_equal(df1, df2)

        p = tmp_path / f"data{extra_ext}.ndjson"
        df1.write_ndjson(p)
        df2, _metadata = bdf.read(p, lazy=False)
        assert_frame_equal(df1, df2)

        p = tmp_path / f"data{extra_ext}.ipc"
        df1.write_ipc(p)
        df2, _metadata = bdf.read(p, lazy=False)
        assert_frame_equal(df1, df2)

        p = tmp_path / f"data{extra_ext}.arrow"
        df1.write_ipc(p)
        df2, _metadata = bdf.read(p, lazy=False)
        assert_frame_equal(df1, df2)

        p = tmp_path / f"data{extra_ext}.feather"
        df1.write_ipc(p)
        df2, _metadata = bdf.read(p, lazy=False)
        assert_frame_equal(df1, df2)
