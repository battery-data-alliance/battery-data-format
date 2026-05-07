"""Tests for Biologic mpr/mpt files."""

from pandas.testing import assert_series_equal

import bdf


def test_parse_mpr(biologic_file_pair):
    """
    Ensure columns agree between mpr and mpt parsing.
    """
    mpr_path, mpt_path = biologic_file_pair
    df1 = bdf.read(mpr_path)
    df2 = bdf.read(mpt_path)

    common_cols = set(df1.columns) & set(df2.columns)
    assert len(common_cols) >= 3  # Should at minimum contain required columns
    for col in common_cols:
        assert_series_equal(
            df1[col],
            df2[col],
            check_dtype=False,
            check_exact=False,
            rtol=0.0011,  # gcpl.issue_230 has some mismatch
        )
