"""Regression coverage for src/bdf/normalization.py: month-name parity and narrow-field ambiguity."""

from __future__ import annotations

from datetime import datetime, timezone

import polars as pl
import pytest

from bdf.normalization import _MACCOR_DT_FMTS, AbsoluteTimeNormalization

_VENDOR_TUPLES_WITH_MONTH_NAME = {"maccor": _MACCOR_DT_FMTS}


def test_localised_month_name_parses_the_same_in_both_forms():
    """A localised month name yields the same epoch through the scalar form and the expression form."""
    normalization = AbsoluteTimeNormalization(formats=("%d-%b-%y",))
    text = "13-Mai-24"
    scalar_result = normalization.scalar(text)
    expr_result = pl.select(normalization.expr(pl.lit(text))).item()
    expected = datetime(2024, 5, 13, 0, 0, 0, tzinfo=timezone.utc).timestamp()
    assert scalar_result == pytest.approx(expected, abs=1e-6)
    assert expr_result == pytest.approx(scalar_result, abs=1e-6)


@pytest.mark.parametrize(
    "formats",
    list(_VENDOR_TUPLES_WITH_MONTH_NAME.values()),
    ids=list(_VENDOR_TUPLES_WITH_MONTH_NAME),
)
def test_every_vendor_tuple_with_a_month_directive_agrees_on_a_localised_spelling(formats):
    """Every shipped vendor tuple declaring %b or %B agrees between forms on a localised month name."""
    normalization = AbsoluteTimeNormalization(formats=formats)
    text = "13-Mai-24 08:00:00 AM"
    scalar_result = normalization.scalar(text)
    expr_result = pl.select(normalization.expr(pl.lit(text))).item()
    assert scalar_result == pytest.approx(expr_result, abs=1e-6)


def test_wide_month_name_beginning_with_its_own_abbreviation_does_not_shadow():
    """A wide month name that starts with its own abbreviation ("Januar" starts with "Jan") parses in full.

    A vectorised token replacement that matches the shortest pattern first,
    or that does not anchor to the leftmost match, stops at "Jan" and leaves
    "uar" unreplaced, so the two forms disagree on this case even though
    neither disagrees on a month name that shares no such prefix.
    """
    normalization = AbsoluteTimeNormalization(formats=("%d-%B-%Y",))
    text = "13-Januar-2024"
    scalar_result = normalization.scalar(text)
    expr_result = pl.select(normalization.expr(pl.lit(text))).item()
    expected = datetime(2024, 1, 13, 0, 0, 0, tzinfo=timezone.utc).timestamp()
    assert scalar_result == pytest.approx(expected, abs=1e-6)
    assert expr_result == pytest.approx(scalar_result, abs=1e-6)


@pytest.mark.parametrize("fmt", ["%d-%B-%y", "%d-%b-%y"], ids=["wide-format", "abbreviated-format"])
@pytest.mark.parametrize(
    "text",
    ["13-January-24", "13-Jan-24", "13-Januar-24"],
    ids=["wide-spelling", "abbreviated-spelling", "localised-spelling"],
)
def test_month_name_format_converts_every_spelling_without_panicking(fmt, text):
    """A month-name format converts a wide, an abbreviated, and a localised month spelling, on both forms, without panicking.

    Polars matches an abbreviated English month name through its own table
    even under a wide-month directive, then slices by the wide name's
    length and panics; a wide spelling handed to an abbreviated directive
    panics the same way. Neither directive ever parses month-name text
    directly: the text and the format are both rewritten onto a plain
    two-digit month number first, so no spelling, of either width, can
    reach the directive that panics.
    """
    normalization = AbsoluteTimeNormalization(formats=(fmt,))
    scalar_result = normalization.scalar(text)
    expr_result = pl.select(normalization.expr(pl.lit(text))).item()
    expected = datetime(2024, 1, 13, 0, 0, 0, tzinfo=timezone.utc).timestamp()
    assert scalar_result == pytest.approx(expected, abs=1e-6)
    assert expr_result == pytest.approx(scalar_result, abs=1e-6)


def test_a_short_token_that_names_no_month_is_left_alone_on_both_forms():
    """A CLDR token shorter than a month-name candidate ("pm") never reaches the vectorised rewrite.

    The scalar form rewrites a run of at least three letters alone, so a
    two-letter token stays as it is. A vectorised map built without that
    filter carries "pm" as month 07, rewrites the meridiem marker to a
    numeral, and leaves the row unparsed where the scalar form parsed it.
    """
    normalization = AbsoluteTimeNormalization(formats=_MACCOR_DT_FMTS)
    text = "01-jan-20 01:00:00 pm"
    scalar_result = normalization.scalar(text)
    expr_result = pl.select(normalization.expr(pl.lit(text))).item()
    expected = datetime(2020, 1, 1, 13, 0, 0, tzinfo=timezone.utc).timestamp()
    assert scalar_result == pytest.approx(expected, abs=1e-6)
    assert expr_result == pytest.approx(scalar_result, abs=1e-6)


def test_upper_case_month_spelling_parses_the_same_in_both_forms():
    """An upper-case month name, the spelling a Maccor export writes, parses on both forms.

    The scalar form lowercases a token before it reads the month table. The
    vectorised form matches a stored key exactly, so the map must carry the
    upper-case spelling as well.
    """
    normalization = AbsoluteTimeNormalization(formats=("%d-%b-%y %H:%M:%S",))
    text = "01-JAN-20 13:00:00"
    scalar_result = normalization.scalar(text)
    expr_result = pl.select(normalization.expr(pl.lit(text))).item()
    expected = datetime(2020, 1, 1, 13, 0, 0, tzinfo=timezone.utc).timestamp()
    assert scalar_result == pytest.approx(expected, abs=1e-6)
    assert expr_result == pytest.approx(scalar_result, abs=1e-6)


def test_month_name_format_leaves_numeric_text_unparsed_on_both_forms():
    """A month-name directive reads a name and not a number, so %d-%B-%y leaves "01-02-24" unparsed on both forms."""
    normalization = AbsoluteTimeNormalization(formats=("%d-%B-%y",))
    text = "01-02-24"
    with pytest.raises(ValueError, match="matched none"):
        normalization.scalar(text)
    expr_result = pl.select(normalization.expr(pl.lit(text))).item()
    assert expr_result is None
