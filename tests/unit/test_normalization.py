"""Unit tests for src/bdf/normalization.py."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from bdf.normalization import (
    _ARBIN_DT_FMTS,
    _DIGATRON_DT_FMTS,
    _LANDT_DT_FMTS,
    _MACCOR_DT_FMTS,
    _NEWARE_DT_FMTS,
    AbsoluteTimeNormalization,
    ElapsedTimeNormalization,
    LinearNormalization,
    RelativeTimeNormalization,
)


def test_linear_conversion_is_plain_arithmetic():
    """A scale alone converts the value by plain multiplication."""
    assert LinearNormalization(scale=0.001).scalar(1000) == pytest.approx(1.0)


def test_offset_converts_a_temperature():
    """An offset alone converts a temperature by plain addition."""
    assert LinearNormalization(offset=-273.15).scalar(298.15) == pytest.approx(25.0)


def test_linear_conversion_reads_numeric_text():
    """A metadata rule hands over text, so the scalar form coerces it the way the expression form casts it."""
    assert LinearNormalization(scale=0.001).scalar("5000") == pytest.approx(5.0)


def test_linear_conversion_states_a_value_it_cannot_read():
    """Text that names no number raises ValueError, which a caller's rule prefixes with its target path."""
    with pytest.raises(ValueError, match="not a number"):
        LinearNormalization(scale=0.001).scalar("five thousand")


def test_equal_declarations_are_interchangeable():
    """Two datetime normalizations declaring the same formats tuple are interchangeable."""
    first = AbsoluteTimeNormalization(formats=("%Y-%m-%d",))
    second = AbsoluteTimeNormalization(formats=("%Y-%m-%d",))
    assert first == second
    assert hash(first) == hash(second)
    assert frozenset({first, second}) == {first}


def test_representative_vector_agrees_between_scalar_and_expr():
    """The scalar and expression forms of a linear normalization agree on a numeric vector."""
    normalization = LinearNormalization(scale=2.5, offset=-3.0)
    values = [1.0, 2.0, 3.0, 4.5, -10.0]
    scalar_results = [normalization.scalar(value) for value in values]
    frame = pl.DataFrame({"value": values})
    expr_results = frame.select(normalization.expr(pl.col("value"))).to_series().to_list()
    assert scalar_results == pytest.approx(expr_results)


@pytest.mark.parametrize(
    "formats,text,expected",
    [
        (_ARBIN_DT_FMTS, "04/30/2024 08:00:00.000", datetime(2024, 4, 30, 8, 0, 0, tzinfo=timezone.utc).timestamp()),
        (
            _DIGATRON_DT_FMTS,
            "2024-04-30 08:00:00.000+02:00",
            datetime(2024, 4, 30, 8, 0, 0, tzinfo=timezone(timedelta(hours=2))).timestamp(),
        ),
        (_LANDT_DT_FMTS, "2024-04-30 08:00:00", datetime(2024, 4, 30, 8, 0, 0, tzinfo=timezone.utc).timestamp()),
        (_MACCOR_DT_FMTS, "30-Apr-24 08:00:00 AM", datetime(2024, 4, 30, 8, 0, 0, tzinfo=timezone.utc).timestamp()),
        (_NEWARE_DT_FMTS, "2024-04-30 08:00:00.000", datetime(2024, 4, 30, 8, 0, 0, tzinfo=timezone.utc).timestamp()),
    ],
    ids=["arbin", "digatron", "landt", "maccor", "neware"],
)
def test_every_vendor_datetime_tuple_agrees(formats, text, expected):
    """The scalar and expression forms of an absolute time normalization agree for each vendor tuple."""
    normalization = AbsoluteTimeNormalization(formats=formats)
    scalar_result = normalization.scalar(text)
    expr_result = pl.select(normalization.expr(pl.lit(text))).item()
    assert scalar_result == pytest.approx(expected, abs=1e-6)
    assert expr_result == pytest.approx(expected, abs=1e-6)


def test_clock_format_span_agrees_above_23_hours():
    """A clock-format span above 23 hours converts to the same number of seconds in both forms."""
    normalization = ElapsedTimeNormalization()
    text = "25:30:15"
    expected = 25 * 3600 + 30 * 60 + 15
    scalar_result = normalization.scalar(text)
    expr_result = pl.select(normalization.expr(pl.lit(text))).item()
    assert scalar_result == pytest.approx(expected)
    assert expr_result == pytest.approx(expected)


def test_relative_time_scalar_call_refuses():
    """The scalar form of a relative time normalization has no column to subtract from."""
    normalization = RelativeTimeNormalization()
    with pytest.raises(NotImplementedError):
        normalization.scalar("2024-04-30 08:00:00")


def test_naive_timestamp_is_localised():
    """A naive timestamp is localised to the caller's tz before it converts to epoch seconds."""
    normalization = AbsoluteTimeNormalization(formats=("%Y-%m-%d %H:%M:%S",))
    result = normalization.scalar("2024-04-30 08:00:00", tz="Europe/Oslo")
    expected = datetime(2024, 4, 30, 8, 0, 0, tzinfo=ZoneInfo("Europe/Oslo")).timestamp()
    assert result == pytest.approx(expected, abs=1e-6)


def test_explicit_offset_is_honoured():
    """A timestamp with an explicit offset keeps that offset and ignores tz."""
    normalization = AbsoluteTimeNormalization(formats=("%Y-%m-%d %H:%M:%S%:z",))
    result = normalization.scalar("2024-04-30 08:00:00+02:00", tz="America/New_York")
    expected = datetime(2024, 4, 30, 8, 0, 0, tzinfo=timezone(timedelta(hours=2))).timestamp()
    assert result == pytest.approx(expected, abs=1e-6)


def test_tz_aware_format_wins_ahead_of_a_later_naive_one_in_declared_order():
    """A tz-aware format earlier in a mixed tuple is tried, and wins, ahead of a later naive one."""
    normalization = AbsoluteTimeNormalization(formats=("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"))
    result = normalization.scalar("2024-01-01T10:00:00+02:00", tz="America/New_York")
    expected = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone(timedelta(hours=2))).timestamp()
    assert result == pytest.approx(expected, abs=1e-6)


def test_empty_declaration_reads_iso_text():
    """An empty formats tuple draws the shared ISO tail and reads ISO text."""
    normalization = AbsoluteTimeNormalization()
    result = normalization.scalar("2024-05-06T07:08:09.123Z")
    expected = datetime(2024, 5, 6, 7, 8, 9, 123000, tzinfo=timezone.utc).timestamp()
    assert result == pytest.approx(expected, abs=1e-6)


def test_vendor_tuple_gets_no_tail():
    """A one-format vendor tuple that fails to parse names that format as the whole set tried."""
    normalization = AbsoluteTimeNormalization(formats=_LANDT_DT_FMTS)
    with pytest.raises(ValueError, match=re.escape(_LANDT_DT_FMTS[0])):
        normalization.scalar("2024-05-06T07:08:09.123Z")


@pytest.mark.parametrize("value", [1714456800, "1714456800"], ids=["int", "digit-string"])
def test_epoch_number_is_not_exempt_from_format_parsing(value):
    """An integer, and a digit string alike, fail naming the value and the declared formats."""
    normalization = AbsoluteTimeNormalization(formats=("%Y-%m-%d",))
    with pytest.raises(ValueError, match=re.escape(repr(value))) as excinfo:
        normalization.scalar(value)
    assert "%Y-%m-%d" in str(excinfo.value)
