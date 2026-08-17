"""Declarative conversion from one extracted value or column to canonical form."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, ClassVar, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict

from bdf._month_names import month_numeral, numeral_replacement_map

# ---------------------------------------------------------------------------
# Vendor format constants
#
# One tuple per vendor, shared by that vendor's table column declarations and
# its metadata declarations.
# ---------------------------------------------------------------------------

_ARBIN_DT_FMTS = (
    "%m/%d/%Y %H:%M:%S%.f",
    "%m/%d/%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S%.f",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
)
_DIGATRON_DT_FMTS = (
    "%Y-%m-%d %H:%M:%S%.f%:z",
    "%Y-%m-%d %H:%M:%S%:z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)
_LANDT_DT_FMTS = ("%Y-%m-%d %H:%M:%S",)
_MACCOR_DT_FMTS = ("%d-%b-%y %I:%M:%S %p", "%d-%b-%y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M")
_NEWARE_DT_FMTS = ("%Y-%m-%d %H:%M:%S%.f", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S")

# The tail a declaration draws when it declares no format of its own: ISO 8601
# year-first shapes, with the offset, Zulu, and fractional-second spellings.
ISO_DATETIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%S%.f%z",
    "%Y-%m-%dT%H:%M:%S%.fZ",
    "%Y-%m-%dT%H:%M:%S%.f",
    "%Y-%m-%d %H:%M:%S%.f",
    "%Y-%m-%d",
)

# ---------------------------------------------------------------------------
# Self-describing (tz-aware) vs naive format classification
#
# A format is self-describing when its parsed value already states its own
# zone, so the caller's tz never applies to it. ``%z``, ``%:z``, and ``%Z``
# all carry a zone or offset. A format ending in a literal capital ``Z`` (not
# the ``%Z`` directive) is the ISO 8601 Zulu spelling for UTC: it carries no
# directive, but the character is a self-describing UTC marker by convention.
# ---------------------------------------------------------------------------

_TZ_COMPONENT_RE = re.compile(r"%:?[zZ]")


def _is_self_describing(fmt: str) -> bool:
    """Return whether ``fmt``'s own spelling states its own zone.

    Args:
        fmt: A single datetime format string.

    Returns:
        True for a format carrying a %z/%:z/%Z directive, or ending in a
        literal Zulu ``Z`` rather than the ``%Z`` directive.
    """
    return bool(_TZ_COMPONENT_RE.search(fmt)) or (fmt.endswith("Z") and not fmt.endswith("%Z"))


def _split_tz_fmts(fmts: Sequence[str]) -> tuple[list[str], list[str]]:
    """Split format strings into (tz_aware, naive) by whether each is self-describing.

    Args:
        fmts: Datetime format strings to classify.

    Returns:
        Tuple of (self-describing formats, formats a caller's tz must localise).
    """
    tz_aware = [f for f in fmts if _is_self_describing(f)]
    naive = [f for f in fmts if not _is_self_describing(f)]
    return tz_aware, naive


def _month_numeral_expr(expr: pl.Expr) -> pl.Expr:
    """Rewrite ``expr`` with every month name token replaced by its two-digit month number.

    Args:
        expr: Polars expression over raw text that can carry a localised
            month name.

    Returns:
        Polars expression producing the rewritten text, vectorised over the
        whole column in one pass.
    """
    mapping = numeral_replacement_map()
    return expr.str.replace_many(mapping, leftmost=True) if mapping else expr


def _month_token_mask(expr: pl.Expr) -> pl.Expr:
    """Return whether ``expr`` carries a month name token :func:`_month_numeral_expr` would rewrite.

    Args:
        expr: Polars expression over raw text that can carry a localised
            month name.

    Returns:
        Polars boolean expression, true where a month name token matched.
    """
    mapping = numeral_replacement_map()
    return expr.str.contains_any(list(mapping.keys())) if mapping else pl.lit(False)


# A format carrying either month-name directive. Such a format reaches Polars
# only after both it and the text are rewritten off %b/%B, for the reason
# bdf._month_names states.
_MONTH_NAME_DIRECTIVE_RE = re.compile(r"%[bB]")


def _numeralize_format(fmt: str) -> str:
    """Return ``fmt`` with every month-name directive rewritten to ``%m``.

    Args:
        fmt: A single datetime format string.

    Returns:
        ``fmt`` unchanged when it carries no ``%b``/``%B`` directive.
    """
    return _MONTH_NAME_DIRECTIVE_RE.sub("%m", fmt)


_DST_AMBIGUOUS_STRATEGY: Literal["earliest"] = "earliest"
_DST_NON_EXISTENT_STRATEGY: Literal["null"] = "null"


class Normalization(BaseModel, ABC):
    """Converts one extracted value or one extracted column to canonical form.

    Abstract: a caller declares one of the concrete kinds below, and each kind
    states both forms of the same conversion.
    """

    model_config = ConfigDict(frozen=True)

    @abstractmethod
    def scalar(self, value, *, tz: str = "UTC") -> Any:
        """Convert one extracted value to its canonical value.

        Args:
            value: The raw extracted value.
            tz: IANA timezone applied where the kind reads it.

        Returns:
            The canonical value.
        """

    @abstractmethod
    def expr(self, expr: pl.Expr, *, tz: str = "UTC") -> pl.Expr:
        """Convert one extracted column to its canonical values.

        Args:
            expr: Polars expression over the raw extracted column.
            tz: IANA timezone applied where the kind reads it.

        Returns:
            Polars expression producing canonical values.
        """


class IdentityNormalization(Normalization):
    """Returns the value or expression unchanged."""

    kind: Literal["identity"] = "identity"
    """Discriminates this kind in a serialized (JSON) declaration."""

    def scalar(self, value, *, tz: str = "UTC"):
        """Return ``value`` unchanged.

        Args:
            value: The raw extracted value.
            tz: Unused by this kind.

        Returns:
            ``value`` unchanged.
        """
        return value

    def expr(self, expr: pl.Expr, *, tz: str = "UTC") -> pl.Expr:
        """Return ``expr`` unchanged.

        Args:
            expr: Polars expression over the raw extracted column.
            tz: Unused by this kind.

        Returns:
            ``expr`` unchanged.
        """
        return expr


class LinearNormalization(Normalization):
    """Scales and offsets a value: ``x -> scale * x + offset``."""

    kind: Literal["linear"] = "linear"
    """Discriminates this kind in a serialized (JSON) declaration."""

    scale: float = 1.0
    offset: float = 0.0

    def scalar(self, value, *, tz: str = "UTC"):
        """Convert ``value`` by ``scale`` and ``offset``.

        Args:
            value: The raw extracted value.
            tz: Unused by this kind.

        Returns:
            ``scale * value + offset``.

        Raises:
            ValueError: ``value`` is not a number; the expression form casts
                such a value to null, and this form states it instead.
        """
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{value!r} is not a number: {exc}") from exc
        return self.scale * number + self.offset

    def expr(self, expr: pl.Expr, *, tz: str = "UTC") -> pl.Expr:
        """Convert ``expr`` by ``scale`` and ``offset``.

        Args:
            expr: Polars expression over the raw extracted column.
            tz: Unused by this kind.

        Returns:
            Polars expression for ``scale * expr + offset``.
        """
        result = expr.cast(pl.Float64, strict=False)
        if self.scale != 1.0:
            result = result * self.scale
        if self.offset != 0.0:
            result = result + self.offset
        return result


class AbsoluteTimeNormalization(Normalization):
    """Converts timestamp text to epoch seconds by the first format that parses.

    An empty ``formats`` tuple draws :data:`ISO_DATETIME_FORMATS`.
    """

    kind: Literal["absolute_time"] = "absolute_time"
    """Discriminates this kind in a serialized (JSON) declaration."""

    formats: tuple[str, ...] = ()

    @property
    def effective_formats(self) -> tuple[str, ...]:
        """The formats actually tried.

        Returns:
            ``formats`` when it is non-empty, otherwise :data:`ISO_DATETIME_FORMATS`.
        """
        return self.formats or ISO_DATETIME_FORMATS

    def _try_formats(
        self, candidate: str, tz_aware_fmts: Sequence[str], naive_fmts: Sequence[str], tz: str
    ) -> float | None:
        """Parse ``candidate`` with the first format of ``tz_aware_fmts`` or ``naive_fmts`` that fits it.

        Args:
            candidate: The datetime text to parse.
            tz_aware_fmts: Self-describing formats, tried first, in declared
                order.
            naive_fmts: Formats a caller's ``tz`` localises, tried after.
            tz: IANA timezone applied to naive candidates.

        Returns:
            Epoch seconds (with sub-second precision), or None when no
            candidate format parsed ``candidate``.
        """
        series = pl.Series("value", [candidate], dtype=pl.String)
        for fmt in tz_aware_fmts:
            parsed = series.str.to_datetime(fmt, strict=False)
            if parsed.null_count() == 0:
                return parsed.dt.timestamp("us").item() / 1e6
        for fmt in naive_fmts:
            parsed = series.str.to_datetime(fmt, strict=False)
            if parsed.null_count():
                continue
            localised = parsed.dt.replace_time_zone(
                tz,
                ambiguous=_DST_AMBIGUOUS_STRATEGY,
                non_existent=_DST_NON_EXISTENT_STRATEGY,
            )
            if localised.null_count():
                continue
            return localised.dt.timestamp("us").item() / 1e6
        return None

    def _parse_epoch(self, text: str, tz: str) -> float | None:
        """Parse ``text`` with the first candidate format of ``effective_formats`` that fits it.

        Args:
            text: The datetime text to parse.
            tz: IANA timezone applied to naive candidates.

        Returns:
            Epoch seconds (with sub-second precision), or None when no
            candidate format parsed ``text`` or its month-numeral rewrite.
        """
        month_name_fmts: list[str] = []
        plain_fmts: list[str] = []
        for fmt in self.effective_formats:
            (month_name_fmts if _MONTH_NAME_DIRECTIVE_RE.search(fmt) else plain_fmts).append(fmt)

        tz_aware_fmts, naive_fmts = _split_tz_fmts(plain_fmts)
        result = self._try_formats(text, tz_aware_fmts, naive_fmts, tz)
        if result is not None:
            return result
        if not month_name_fmts:
            return None
        # A month-name format is tried against the numeral rewrite alone, and
        # only where the rewrite found a month name: plain digits must never
        # parse under a format that declares one.
        variant = month_numeral(text)
        if variant == text:
            return None
        numeral_tz_aware, numeral_naive = _split_tz_fmts([_numeralize_format(fmt) for fmt in month_name_fmts])
        return self._try_formats(variant, numeral_tz_aware, numeral_naive, tz)

    def _epoch_expr(self, expr: pl.Expr, tz: str) -> pl.Expr:
        """Parse ``expr`` with every candidate format of ``effective_formats``, as epoch seconds.

        Args:
            expr: Polars expression over the raw extracted timestamp column.
            tz: IANA timezone applied to naive candidates.

        Returns:
            Polars expression producing epoch seconds, self-describing
            formats tried ahead of naive formats localised to ``tz``.
        """
        month_name_fmts: list[str] = []
        plain_fmts: list[str] = []
        for fmt in self.effective_formats:
            (month_name_fmts if _MONTH_NAME_DIRECTIVE_RE.search(fmt) else plain_fmts).append(fmt)

        def _candidates(source: pl.Expr, source_tz_aware: Sequence[str], source_naive: Sequence[str]) -> list[pl.Expr]:
            # timestamp() per candidate avoids coalesce supertype conflict (tz-aware vs tz-naive)
            result = [source.str.to_datetime(f, strict=False).dt.timestamp("us") for f in source_tz_aware]
            result += [
                source.str.to_datetime(f, strict=False)
                .dt.replace_time_zone(tz, ambiguous=_DST_AMBIGUOUS_STRATEGY, non_existent=_DST_NON_EXISTENT_STRATEGY)
                .dt.timestamp("us")
                for f in source_naive
            ]
            return result

        tz_aware_fmts, naive_fmts = _split_tz_fmts(plain_fmts)
        candidates = _candidates(expr, tz_aware_fmts, naive_fmts)
        if month_name_fmts:
            # The same numeral rewrite the scalar form makes, as a second
            # vectorised pass coalesced alongside the first, masked to the
            # rows a month name token actually matched.
            numeral_tz_aware, numeral_naive = _split_tz_fmts([_numeralize_format(fmt) for fmt in month_name_fmts])
            mask = _month_token_mask(expr)
            candidates += [
                pl.when(mask).then(candidate).otherwise(None)
                for candidate in _candidates(_month_numeral_expr(expr), numeral_tz_aware, numeral_naive)
            ]
        parsed = pl.coalesce(candidates) if len(candidates) > 1 else candidates[0]
        return parsed.cast(pl.Float64) / 1e6

    def scalar(self, value, *, tz: str = "UTC"):
        """Parse ``value`` under the first matching format of ``formats``.

        Args:
            value: The raw extracted timestamp text.
            tz: IANA timezone applied to a format that carries no offset.

        Returns:
            The epoch seconds.

        Raises:
            ValueError: No declared format parses ``value``; the message
                names ``value`` and the formats tried.
        """
        epoch = self._parse_epoch(str(value), tz)
        if epoch is None:
            raise ValueError(f"{value!r} matched none of the formats tried: {list(self.effective_formats)}")
        return epoch

    def expr(self, expr: pl.Expr, *, tz: str = "UTC") -> pl.Expr:
        """Parse ``expr`` under the first matching format of ``formats``.

        Args:
            expr: Polars expression over the raw extracted timestamp column.
            tz: IANA timezone applied to a format that carries no offset.

        Returns:
            Polars expression producing epoch seconds.
        """
        return self._epoch_expr(expr, tz)


class RelativeTimeNormalization(AbsoluteTimeNormalization):
    """Parses timestamp text as absolute time, then subtracts the column's first value.

    Subclasses :class:`AbsoluteTimeNormalization`: the relative expression is
    the absolute expression minus the column's first value, so this kind
    inherits ``formats`` and ``effective_formats``.
    """

    kind: Literal["relative_time"] = "relative_time"  # type: ignore[assignment]
    """Discriminates this kind in a serialized (JSON) declaration."""

    def scalar(self, value, *, tz: str = "UTC"):
        """Refuse: one value carries no column to subtract its first value from.

        Args:
            value: The raw extracted timestamp text.
            tz: IANA timezone applied to a format that carries no offset.

        Raises:
            NotImplementedError: Always; a relative time has no scalar form.
        """
        raise NotImplementedError("RelativeTimeNormalization has no scalar form: one value carries no column origin")

    def expr(self, expr: pl.Expr, *, tz: str = "UTC") -> pl.Expr:
        """Parse ``expr`` and subtract its first parsed value.

        Args:
            expr: Polars expression over the raw extracted timestamp column.
            tz: IANA timezone applied to a format that carries no offset.

        Returns:
            Polars expression producing seconds elapsed since the column's
            first value.
        """
        ts = self._epoch_expr(expr, tz)
        return ts - ts.first()


class ElapsedTimeNormalization(Normalization):
    """Converts clock-format span text (``HH:MM:SS[.fff]``) to seconds.

    Declares no formats: it reads a fixed grammar that accepts an hours field
    above 23.
    """

    kind: Literal["elapsed_time"] = "elapsed_time"
    """Discriminates this kind in a serialized (JSON) declaration."""

    # The one grammar both forms read: hours, minutes, and seconds, with the
    # hours field accepting any non-negative integer, including above 23.
    _SPAN_PATTERN: ClassVar[str] = r"^\s*(\d+):(\d+):([\d.]+)\s*$"
    _SPAN_RE: ClassVar[re.Pattern[str]] = re.compile(_SPAN_PATTERN)

    def scalar(self, value, *, tz: str = "UTC"):
        """Convert clock-format span text ``value`` to seconds.

        Args:
            value: The raw extracted clock-format span text.
            tz: Unused by this kind.

        Returns:
            The number of seconds the span states.

        Raises:
            ValueError: ``value`` states no clock-format span.
        """
        match = self._SPAN_RE.match(str(value))
        if match is None:
            raise ValueError(f"{value!r} states no clock-format span (HH:MM:SS[.fff])")
        hours, minutes, seconds = match.groups()
        return float(hours) * 3600 + float(minutes) * 60 + float(seconds)

    def expr(self, expr: pl.Expr, *, tz: str = "UTC") -> pl.Expr:
        """Convert clock-format span text ``expr`` to seconds.

        Args:
            expr: Polars expression over the raw extracted span column.
            tz: Unused by this kind.

        Returns:
            Polars expression producing the number of seconds each span
            states.
        """
        hours = expr.str.extract(self._SPAN_PATTERN, 1).cast(pl.Float64)
        minutes = expr.str.extract(self._SPAN_PATTERN, 2).cast(pl.Float64)
        seconds = expr.str.extract(self._SPAN_PATTERN, 3).cast(pl.Float64)
        return hours * 3600 + minutes * 60 + seconds
