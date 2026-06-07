from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any, Callable, TypeVar

import polars as pl

if TYPE_CHECKING:
    import pandas as pd

AnyDF = TypeVar("AnyDF", "pd.DataFrame", pl.DataFrame, pl.LazyFrame)


def _classify_df(df: object) -> str:
    """Return 'pandas', 'polars_df', or 'polars_lazy'; raise TypeError for unknown types.

    Args:
        df: Object to classify.

    Returns:
        One of 'pandas', 'polars_df', 'polars_lazy'.

    Raises:
        TypeError: If df is not a supported DataFrame type.
    """
    module = type(df).__module__
    if module.startswith("pandas"):
        return "pandas"
    if isinstance(df, pl.LazyFrame):
        return "polars_lazy"
    if isinstance(df, pl.DataFrame):
        return "polars_df"
    raise TypeError(f"Unsupported DataFrame type: {type(df).__qualname__!r}")


def _to_polars_lazy(df: object) -> pl.LazyFrame:
    """Convert any supported DataFrame type to pl.LazyFrame.

    Args:
        df: pandas DataFrame, polars DataFrame, or polars LazyFrame.

    Returns:
        Equivalent pl.LazyFrame.
    """
    kind = _classify_df(df)
    if kind == "polars_lazy":
        return df  # type: ignore[return-value]
    if kind == "polars_df":
        assert isinstance(df, pl.DataFrame)
        return df.lazy()
    return pl.from_pandas(df).lazy()  # type: ignore[arg-type]


def _from_polars_lazy(result: pl.LazyFrame, kind: str) -> Any:
    """Convert a pl.LazyFrame back to the target type.

    Args:
        result: LazyFrame to convert.
        kind: One of 'pandas', 'polars_df', 'polars_lazy'.

    Returns:
        DataFrame in the requested type.
    """
    if kind == "polars_df":
        return result.collect()
    if kind == "pandas":
        return result.collect().to_pandas()
    return result


def coerce_dataframe(fn: Callable[..., pl.LazyFrame]) -> Callable[..., Any]:
    """Coerce the df arg (first positional after self) to pl.LazyFrame; coerce output back.

    The wrapped function receives a pl.LazyFrame and returns a pl.LazyFrame.
    The wrapper coerces the return value to match the type of the original input.

    Args:
        fn: Method with signature (self, df: pl.LazyFrame, ...) -> pl.LazyFrame.

    Returns:
        Wrapper with signature (self, df: AnyDF, ...) -> AnyDF.
    """
    @functools.wraps(fn)
    def wrapper(self: Any, df: Any, *args: Any, **kwargs: Any) -> Any:
        kind = _classify_df(df)
        result = fn(self, _to_polars_lazy(df), *args, **kwargs)
        return _from_polars_lazy(result, kind)

    return wrapper
