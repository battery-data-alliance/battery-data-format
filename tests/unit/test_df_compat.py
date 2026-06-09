from __future__ import annotations

import sys
from unittest.mock import patch

import pandas as pd
import polars as pl
import pytest

from bdf._df_compat import _classify_df, _from_polars_lazy, _to_polars_lazy, coerce_dataframe

# ---------------------------------------------------------------------------
# _classify_df
# ---------------------------------------------------------------------------


def test_classify_df_polars_lazyframe() -> None:
    assert _classify_df(pl.DataFrame({"a": [1]}).lazy()) == "polars_lazy"


def test_classify_df_polars_dataframe() -> None:
    assert _classify_df(pl.DataFrame({"a": [1]})) == "polars_df"


def test_classify_df_pandas_dataframe() -> None:
    assert _classify_df(pd.DataFrame({"a": [1]})) == "pandas"


def test_classify_df_unsupported_type_raises_typeerror() -> None:
    with pytest.raises(TypeError, match="Unsupported DataFrame type"):
        _classify_df({"a": [1]})


def test_classify_df_unsupported_type_error_contains_type_name() -> None:
    with pytest.raises(TypeError, match="dict"):
        _classify_df({"a": [1]})


# ---------------------------------------------------------------------------
# _to_polars_lazy
# ---------------------------------------------------------------------------


def test_to_polars_lazy_from_lazyframe_is_identity() -> None:
    lf = pl.DataFrame({"a": [1]}).lazy()
    result = _to_polars_lazy(lf)
    assert result is lf


def test_to_polars_lazy_from_polars_dataframe() -> None:
    df = pl.DataFrame({"a": [1, 2]})
    result = _to_polars_lazy(df)
    assert isinstance(result, pl.LazyFrame)
    assert result.collect().equals(df)


def test_to_polars_lazy_from_pandas_dataframe() -> None:
    pdf = pd.DataFrame({"a": [1, 2]})
    result = _to_polars_lazy(pdf)
    assert isinstance(result, pl.LazyFrame)
    assert result.collect()["a"].to_list() == [1, 2]


# ---------------------------------------------------------------------------
# _from_polars_lazy
# ---------------------------------------------------------------------------


def test_from_polars_lazy_polars_lazy_returns_lazyframe() -> None:
    lf = pl.DataFrame({"a": [1]}).lazy()
    result = _from_polars_lazy(lf, "polars_lazy")
    assert isinstance(result, pl.LazyFrame)


def test_from_polars_lazy_polars_df_returns_dataframe() -> None:
    lf = pl.DataFrame({"a": [1]}).lazy()
    result = _from_polars_lazy(lf, "polars_df")
    assert isinstance(result, pl.DataFrame)
    assert result["a"].to_list() == [1]


def test_from_polars_lazy_pandas_returns_pandas_dataframe() -> None:
    lf = pl.DataFrame({"a": [1]}).lazy()
    result = _from_polars_lazy(lf, "pandas")
    assert isinstance(result, pd.DataFrame)
    assert result["a"].tolist() == [1]


# ---------------------------------------------------------------------------
# coerce_dataframe decorator
# ---------------------------------------------------------------------------


class _FakeOnto:
    """Minimal stand-in for a class with a decorated method."""

    received: pl.LazyFrame | None = None

    @coerce_dataframe
    def double_x(self, df: pl.LazyFrame) -> pl.LazyFrame:
        self.__class__.received = df
        return df.with_columns(pl.col("x") * 2)


def test_coerce_dataframe_inner_receives_lazyframe_from_pandas() -> None:
    _FakeOnto.received = None
    onto = _FakeOnto()
    onto.double_x(pd.DataFrame({"x": [1]}))
    assert isinstance(_FakeOnto.received, pl.LazyFrame)


def test_coerce_dataframe_inner_receives_lazyframe_from_polars_df() -> None:
    _FakeOnto.received = None
    onto = _FakeOnto()
    onto.double_x(pl.DataFrame({"x": [1]}))
    assert isinstance(_FakeOnto.received, pl.LazyFrame)


def test_coerce_dataframe_inner_receives_lazyframe_from_lazyframe() -> None:
    _FakeOnto.received = None
    onto = _FakeOnto()
    onto.double_x(pl.DataFrame({"x": [1]}).lazy())
    assert isinstance(_FakeOnto.received, pl.LazyFrame)


def test_coerce_dataframe_transformation_reflected_in_pandas_output() -> None:
    onto = _FakeOnto()
    result = onto.double_x(pd.DataFrame({"x": [1, 2, 3]}))
    assert isinstance(result, pd.DataFrame)
    assert result["x"].tolist() == [2, 4, 6]


def test_coerce_dataframe_transformation_reflected_in_polars_df_output() -> None:
    onto = _FakeOnto()
    result = onto.double_x(pl.DataFrame({"x": [1, 2, 3]}))
    assert isinstance(result, pl.DataFrame)
    assert result["x"].to_list() == [2, 4, 6]


def test_coerce_dataframe_transformation_reflected_in_lazyframe_output() -> None:
    onto = _FakeOnto()
    result = onto.double_x(pl.DataFrame({"x": [1, 2, 3]}).lazy())
    assert isinstance(result, pl.LazyFrame)
    assert result.collect()["x"].to_list() == [2, 4, 6]


def test_coerce_dataframe_propagates_typeerror_for_unsupported_type() -> None:
    onto = _FakeOnto()
    with pytest.raises(TypeError, match="Unsupported DataFrame type"):
        onto.double_x({"x": [1]})  # type: ignore[arg-type]


def test_df_compat_imports_without_pandas() -> None:
    """_df_compat must not import pandas at module load time."""
    saved = sys.modules.pop("pandas", None)
    saved_bdf_compat = sys.modules.pop("bdf._df_compat", None)
    try:
        with patch.dict(sys.modules, {"pandas": None}):  # type: ignore[dict-item]
            import importlib

            import bdf._df_compat as mod  # noqa: F401

            importlib.reload(mod)
    finally:
        if saved is not None:
            sys.modules["pandas"] = saved
        if saved_bdf_compat is not None:
            sys.modules["bdf._df_compat"] = saved_bdf_compat
