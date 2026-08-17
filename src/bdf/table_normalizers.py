"""Normalisation classes, helpers, and the public normalize() entry point."""

from __future__ import annotations

import logging
import re
import warnings
import zoneinfo
from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Iterator, Union

import polars as pl
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

if TYPE_CHECKING:
    import pandas as pd  # noqa: F401


from bdf._df_compat import coerce_dataframe  # noqa: E402
from bdf.normalization import (
    _ARBIN_DT_FMTS,
    _DIGATRON_DT_FMTS,
    _LANDT_DT_FMTS,
    _MACCOR_DT_FMTS,
    _NEWARE_DT_FMTS,
    AbsoluteTimeNormalization,
    ElapsedTimeNormalization,
    IdentityNormalization,
    LinearNormalization,
    Normalization,
    RelativeTimeNormalization,
    _is_self_describing,
)
from bdf.spec import _UNIT_CAPTURE, COLUMN_ONTOLOGY, get_unit_conversion

_logger = logging.getLogger(__name__)

# ResolvedColumn.normalization is typed as this discriminated union, not the
# bare Normalization base, so a JSON round-trip (model_dump_json then
# model_validate_json) reconstructs the declared kind instead of the fieldless
# base class. Every concrete kind carries every other kind's Normalization
# behaviour, so a caller reading .scalar()/.expr() sees no difference.
_NormalizationField = Annotated[
    Union[
        IdentityNormalization,
        LinearNormalization,
        AbsoluteTimeNormalization,
        RelativeTimeNormalization,
        ElapsedTimeNormalization,
    ],
    Field(discriminator="kind"),
]


class Syn(BaseModel):
    """A column synonym declared by exemplar header, with an optional declared normalization."""

    model_config = ConfigDict(frozen=True)

    hdr: str
    """Exemplar header string to match against source column names."""
    assumed: bool = False
    """True when no real-file sample exercises this synonym (see test_synonym_coverage)."""
    source_unit: str | None = None
    """Fixed source unit for exact, non-templated aliases."""
    legacy: bool = False
    """Raise a warning that this column is legacy and has been converted."""
    reverse_sign: bool = False
    """Flip sign of column in addition to unit conversion
    e.g. negative impedance or discharge-positive current columns."""
    normalization: _NormalizationField | None = Field(
        default=None,
        description="A declared normalization (e.g. a datetime kind); wins over unit matching when set.",
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_str(cls, data: object) -> object:
        """If a Syn is declared as a bare string, coerce to a dict for Pydantic parsing.

        Args:
            data: Raw value passed to the Syn constructor.

        Returns:
            ``{"hdr": data}`` when ``data`` is a str, otherwise ``data`` unchanged.
        """
        return {"hdr": data} if isinstance(data, str) else data

    def match(self, header: str, bdf_unit: str | None) -> Normalization | None:
        """Return a normalization on match, None on no match or incompatible units.

        Args:
            header: Column name to match against the synonym pattern.
            bdf_unit: Target unit for conversion, or None for dimensionless columns.

        Returns:
            The declared ``normalization`` on an exact match when one is set
            (a header carrying no unit; the ``bdf_unit`` is not consulted),
            ``IdentityNormalization()`` on an exact unitless match,
            ``LinearNormalization(scale, offset)`` on a unit conversion (even
            where the conversion happens to be 1:1), or None on no match or
            incompatible units.
        """
        if self.normalization is not None:
            return self.normalization if self.exact_match(header) else None
        templated = "{unit}" in self.hdr
        if templated:
            if bdf_unit is None:
                return None
            parts = self.hdr.split("{unit}")
            pattern = _UNIT_CAPTURE.join(re.escape(p) for p in parts)
            m = re.fullmatch(pattern, header)
            if m is None:
                return None
            result = get_unit_conversion(m.group(1), bdf_unit)
        else:
            if self.hdr.strip() != header.strip():
                return None
            result = get_unit_conversion(self.source_unit, bdf_unit) if self.source_unit is not None else (1.0, 0.0)
        if result is None:
            return None
        scale, offset = result
        if self.reverse_sign:
            scale = -scale
        if not templated and self.source_unit is None and not self.reverse_sign:
            return IdentityNormalization()
        return LinearNormalization(scale=scale, offset=offset)

    def exact_match(self, header: str) -> bool:
        """Test exact case-insensitive match against header.

        Args:
            header: Column name to match.

        Returns:
            True if the header matches the exemplar (case-insensitive).
        """
        return self.hdr.strip() == header.strip()


class ResolvedColumn(BaseModel):
    """Resolved mapping of one source header to one BDF column."""

    model_config = ConfigDict(frozen=True)

    source_header: str = Field(description="The column name in the source data.")
    normalization: _NormalizationField = Field(
        default_factory=IdentityNormalization, description="Conversion applied to the resolved column."
    )
    legacy: bool = False  # Resolved from a legacy column, warn user

    @classmethod
    def from_bdf_label(cls, bdf_label_key: str, src_header: str) -> tuple[str, ResolvedColumn]:
        """Resolve a BDF label key (e.g. 'Voltage / mV') to (mr_name, ResolvedColumn).

        Args:
            bdf_label_key: BDF label in format 'Base / unit' (e.g. 'Voltage / mV').
            src_header: Source column name in the input data.

        Returns:
            Tuple of (mr_name, ResolvedColumn) mapping the source header.

        Raises:
            ValueError: If label base is not found in BDF spec.
        """
        match = COLUMN_ONTOLOGY.quantity_from_label(bdf_label_key)
        if match is None:
            raise ValueError(f"column_map key {bdf_label_key!r}: label base not found in BDF spec")
        quantity, key_unit = match
        scale, offset = 1.0, 0.0
        if key_unit is not None:
            result = quantity.convert_from(key_unit)
            if result is None:
                warnings.warn(
                    f"column_map: unit {key_unit!r} in {bdf_label_key!r} not compatible "
                    f"with {quantity.unit!r} for {quantity.mr_name}; using scale=1.0",
                    UserWarning,
                    stacklevel=4,
                )
            else:
                scale, offset = result
        normalization: Normalization = (
            IdentityNormalization()
            if scale == 1.0 and offset == 0.0
            else LinearNormalization(scale=scale, offset=offset)
        )
        return quantity.mr_name, cls(source_header=src_header, normalization=normalization)

    @classmethod
    def from_synonyms(
        cls,
        header: str,
        probe: str,
        bdf_unit: str | None,
        synonyms: Sequence[Syn],
    ) -> ResolvedColumn | None:
        """Try to match header against synonyms; return ResolvedColumn or None.

        Args:
            header: Original column name from the source.
            probe: Normalized header (stripped, with leading ~ removed).
            bdf_unit: Target BDF unit for conversion.
            synonyms: Sequence of Syn objects to match against.

        Returns:
            ResolvedColumn with the matched normalization, or None if no match.
        """
        for syn in synonyms:
            normalization = syn.match(probe, bdf_unit)
            if normalization is not None:
                return cls(source_header=header, normalization=normalization, legacy=syn.legacy)
        return None

    def get_expr(self, mr_name: str, tz: str = "UTC") -> pl.Expr:
        """Build polars expression for column transformation with normalization and dtype casting.

        Args:
            mr_name: Machine-readable column name (e.g. 'voltage_volt').
            tz: IANA timezone applied by the normalization where it reads it.
                Around daylight-saving clock changes, some local times do not map to one
                exact instant. If clocks move back from UTC+1 to UTC+0, ``01:30`` can mean
                either ``00:30 UTC`` or ``01:30 UTC``; this parser uses ``00:30 UTC`` for
                the resulting ``Unix Time / s`` value. If clocks move forward and skip
                ``01:30``, that row becomes null.

        Returns:
            Polars expression that applies the normalization and dtype conversion.
        """
        src = self.source_header
        label = getattr(COLUMN_ONTOLOGY, mr_name).formatted_label
        dtype = getattr(COLUMN_ONTOLOGY, mr_name).dtype
        expr = self.normalization.expr(pl.col(src), tz=tz)
        if dtype == "str":
            expr = expr.cast(pl.Utf8, strict=False)
        elif dtype == "int":
            # Through Float64 first: a direct Utf8 -> Int64 cast returns null
            # for a decimal-formatted numeral (e.g. "2.0"), and every
            # delimited and Excel parser hands this path Utf8.
            expr = expr.cast(pl.Float64, strict=False).cast(pl.Int64, strict=False)
        else:
            expr = expr.cast(pl.Float64, strict=False)
        return expr.alias(label)


def _validate_tz(tz: str) -> None:
    """Validate ``tz`` against the IANA timezone database, raising a clean error.

    Builds no series and no frame: a caller collects nothing to validate a
    name, which keeps a lazy read lazy.

    Args:
        tz: IANA timezone name to validate.

    Raises:
        ValueError: If ``tz`` is not a recognized IANA timezone name.
    """
    try:
        zoneinfo.ZoneInfo(tz)
    except (ValueError, zoneinfo.ZoneInfoNotFoundError) as e:
        raise ValueError(f"invalid tz {tz!r}: no such time zone") from e


class TableNormalizer(BaseModel):
    """Column-mapping model: one optional field per BDF mr_name.

    Fields accept ``tuple[Syn, ...]`` (synonym-based, for CSV/Excel) or
    ``ResolvedColumn`` (direct, for MAT). Iterating yields ``(mr_name, spec)``
    for non-None fields in declaration order. ``tuple`` (not ``list``) keeps
    instances hashable so they can live in a ``frozenset``.
    """

    model_config = ConfigDict(frozen=True)

    test_time_second: tuple[Syn, ...] | ResolvedColumn | None = None
    voltage_volt: tuple[Syn, ...] | ResolvedColumn | None = None
    current_ampere: tuple[Syn, ...] | ResolvedColumn | None = None
    unix_time_second: tuple[Syn, ...] | ResolvedColumn | None = None
    cycle_count: tuple[Syn, ...] | ResolvedColumn | None = None
    step_count: tuple[Syn, ...] | ResolvedColumn | None = None
    step_id: tuple[Syn, ...] | ResolvedColumn | None = None
    step_type: tuple[Syn, ...] | ResolvedColumn | None = None
    ambient_temperature_celsius: tuple[Syn, ...] | ResolvedColumn | None = None
    step_record_index: tuple[Syn, ...] | ResolvedColumn | None = None
    record_index: tuple[Syn, ...] | ResolvedColumn | None = None
    step_time_second: tuple[Syn, ...] | ResolvedColumn | None = None
    charging_capacity_ah: tuple[Syn, ...] | ResolvedColumn | None = None
    step_charging_capacity_ah: tuple[Syn, ...] | ResolvedColumn | None = None
    cycle_charging_capacity_ah: tuple[Syn, ...] | ResolvedColumn | None = None
    schedule_charging_capacity_ah: tuple[Syn, ...] | ResolvedColumn | None = None
    discharging_capacity_ah: tuple[Syn, ...] | ResolvedColumn | None = None
    step_discharging_capacity_ah: tuple[Syn, ...] | ResolvedColumn | None = None
    cycle_discharging_capacity_ah: tuple[Syn, ...] | ResolvedColumn | None = None
    schedule_discharging_capacity_ah: tuple[Syn, ...] | ResolvedColumn | None = None
    net_capacity_ah: tuple[Syn, ...] | ResolvedColumn | None = None
    step_net_capacity_ah: tuple[Syn, ...] | ResolvedColumn | None = None
    cycle_net_capacity_ah: tuple[Syn, ...] | ResolvedColumn | None = None
    cumulative_capacity_ah: tuple[Syn, ...] | ResolvedColumn | None = None
    step_cumulative_capacity_ah: tuple[Syn, ...] | ResolvedColumn | None = None
    cycle_cumulative_capacity_ah: tuple[Syn, ...] | ResolvedColumn | None = None
    charging_energy_wh: tuple[Syn, ...] | ResolvedColumn | None = None
    step_charging_energy_wh: tuple[Syn, ...] | ResolvedColumn | None = None
    cycle_charging_energy_wh: tuple[Syn, ...] | ResolvedColumn | None = None
    schedule_charging_energy_wh: tuple[Syn, ...] | ResolvedColumn | None = None
    discharging_energy_wh: tuple[Syn, ...] | ResolvedColumn | None = None
    step_discharging_energy_wh: tuple[Syn, ...] | ResolvedColumn | None = None
    cycle_discharging_energy_wh: tuple[Syn, ...] | ResolvedColumn | None = None
    schedule_discharging_energy_wh: tuple[Syn, ...] | ResolvedColumn | None = None
    net_energy_wh: tuple[Syn, ...] | ResolvedColumn | None = None
    step_net_energy_wh: tuple[Syn, ...] | ResolvedColumn | None = None
    cycle_net_energy_wh: tuple[Syn, ...] | ResolvedColumn | None = None
    cumulative_energy_wh: tuple[Syn, ...] | ResolvedColumn | None = None
    step_cumulative_energy_wh: tuple[Syn, ...] | ResolvedColumn | None = None
    cycle_cumulative_energy_wh: tuple[Syn, ...] | ResolvedColumn | None = None
    power_watt: tuple[Syn, ...] | ResolvedColumn | None = None
    internal_resistance_ohm: tuple[Syn, ...] | ResolvedColumn | None = None
    dc_internal_resistance_ohm: tuple[Syn, ...] | ResolvedColumn | None = None
    ac_internal_resistance_ohm: tuple[Syn, ...] | ResolvedColumn | None = None
    real_impedance_ohm: tuple[Syn, ...] | ResolvedColumn | None = None
    imaginary_impedance_ohm: tuple[Syn, ...] | ResolvedColumn | None = None
    absolute_impedance_ohm: tuple[Syn, ...] | ResolvedColumn | None = None
    phase_degree: tuple[Syn, ...] | ResolvedColumn | None = None
    frequency_hertz: tuple[Syn, ...] | ResolvedColumn | None = None
    ambient_pressure_pa: tuple[Syn, ...] | ResolvedColumn | None = None
    applied_pressure_pa: tuple[Syn, ...] | ResolvedColumn | None = None
    surface_pressure_pa: tuple[Syn, ...] | ResolvedColumn | None = None
    temperature_t1_celsius: tuple[Syn, ...] | ResolvedColumn | None = None
    temperature_t2_celsius: tuple[Syn, ...] | ResolvedColumn | None = None
    temperature_t3_celsius: tuple[Syn, ...] | ResolvedColumn | None = None
    temperature_t4_celsius: tuple[Syn, ...] | ResolvedColumn | None = None
    temperature_t5_celsius: tuple[Syn, ...] | ResolvedColumn | None = None
    surface_temperature_celsius: tuple[Syn, ...] | ResolvedColumn | None = None

    def __iter__(self) -> Iterator[tuple[str, tuple[Syn, ...] | ResolvedColumn]]:  # type: ignore[override]
        """Iterate over (mr_name, field_value) for all non-None fields in declaration order.

        Yields:
            Tuples of (mr_name, field_value) for each set field, in declaration order.
        """
        for mr_name in type(self).model_fields:
            val = getattr(self, mr_name)
            if val is not None:
                yield mr_name, val

    def extend(self, **kwargs: Syn | tuple[Syn, ...] | ResolvedColumn) -> "TableNormalizer":
        """Return a copy with extra synonyms appended (or fields set) per kwarg.

        Each kwarg is a BDF field name (e.g. ``voltage_volt``). If the field
        currently holds a synonym tuple, the new synonym(s) are appended after
        the built-ins (built-ins are tried first). If the field is unset
        (``None``), the value is set directly. If the field currently holds a
        ``ResolvedColumn`` (MAT-style direct mapping), there is nothing to
        append to, so the field is replaced and a ``UserWarning`` is emitted.

        Args:
            **kwargs: BDF field names mapped to a synonym, a tuple of synonyms,
                or a ``ResolvedColumn`` to merge into that field.

        Returns:
            New TableNormalizer with the given fields extended.

        Raises:
            ValueError: If a kwarg key is not a valid TableNormalizer field name.
        """
        updates: dict[str, tuple[Syn, ...] | ResolvedColumn] = {}
        for field, value in kwargs.items():
            if field not in type(self).model_fields:
                raise ValueError(f"extend: unknown TableNormalizer field {field!r}")
            if isinstance(value, Syn):
                value = (value,)
            current = getattr(self, field)
            if isinstance(current, ResolvedColumn):
                warnings.warn(
                    f"extend: replacing ResolvedColumn on field {field!r}; ResolvedColumn fields cannot be appended to",
                    UserWarning,
                    stacklevel=2,
                )
                updates[field] = value
            elif current is None:
                updates[field] = value
            else:
                updates[field] = (*current, *value)
        return self.model_copy(update=updates)

    def resolve(self, headers: list[str]) -> dict[str, ResolvedColumn]:
        """Return mr_name → ResolvedColumn for all headers that match a synonym field.

        ResolvedColumn fields are passed through as-is. Each source header is
        claimed at most once (first match in declaration order wins).

        Args:
            headers: List of source column names to resolve.

        Returns:
            Dictionary mapping mr_name to ResolvedColumn for matched columns.
        """
        probes = {h: h.strip().lstrip("~").strip() for h in headers}
        claimed: set[str] = set()
        result: dict[str, ResolvedColumn] = {}
        for mr_name, field_val in self:
            if isinstance(field_val, ResolvedColumn):
                result[mr_name] = field_val
                if field_val.source_header in headers:
                    claimed.add(field_val.source_header)
            else:
                unit = getattr(COLUMN_ONTOLOGY, mr_name).unit
                for header in headers:
                    if header in claimed:
                        continue
                    matched = ResolvedColumn.from_synonyms(header, probes[header], unit, field_val)
                    if matched is not None:
                        result[mr_name] = matched
                        claimed.add(header)
                        break
        return result

    def score_columns(self, headers: list[str]) -> int:
        """Count resolved columns whose source header is present in headers.

        Args:
            headers: List of source column names.

        Returns:
            Count of columns that resolve via synonyms or ResolvedColumn mappings.
        """
        resolved = self.resolve(headers)
        return sum(1 for resolved_column in resolved.values() if resolved_column.source_header in headers)

    def known_header_names(self) -> list[str]:
        """Source-header names from ResolvedColumn fields only (known, not synonyms).

        Returns:
            List of source header names defined via ResolvedColumn fields.
        """
        names: list[str] = []
        for _, spec in self:
            if isinstance(spec, ResolvedColumn):
                names.append(spec.source_header)
        return names

    @classmethod
    def from_column_map(cls, column_map: dict[str, str]) -> "TableNormalizer":
        """Convert a BDF label-key dict to a TableNormalizer via ResolvedColumn.from_bdf_label.

        Args:
            column_map: Dictionary mapping BDF labels (e.g. 'Voltage / mV') to source header names.

        Returns:
            TableNormalizer instance with ResolvedColumn entries.

        Raises:
            ValueError: If column_map is empty or contains invalid BDF labels.
        """
        if not column_map:
            raise ValueError("column_map must not be empty")
        kwargs: dict[str, ResolvedColumn] = {}
        for bdf_label_key, src_header in column_map.items():
            mr_name, resolved_column = ResolvedColumn.from_bdf_label(bdf_label_key, src_header)
            kwargs[mr_name] = resolved_column
        return cls(**kwargs)

    @coerce_dataframe
    def normalize(
        self,
        df: pl.LazyFrame,
        *,
        validate: bool = True,
        include_unknown: bool = False,
        tz: str = "UTC",
    ) -> pl.LazyFrame:
        """Resolve headers → BDF columns, apply unit conversion, return df_out.

        Accepts ``pl.DataFrame``, ``pl.LazyFrame``, or ``pandas.DataFrame``. Return type matches input.
        ``validate`` defaults to True: missing required BDF columns raise instead of warn, and
        non-BDF columns trigger a ``UserWarning`` (see ``COLUMN_ONTOLOGY.validate_df``). Pass
        ``validate=False`` to fall back to a soft warning instead of raising.

        Args:
            df: Input dataframe in any supported format.
            validate: Validate column names against the BDF ontology when True (default;
                raises on missing required columns instead of warning).
            include_unknown: Keep columns outside of the BDF spec in the dataframe (default False).
            tz: IANA timezone applied to naive (no embedded offset) ``unix_time_second``
                datetime formats. Defaults to ``"UTC"``; emits a ``UserWarning`` when a
                naive format is in play and ``tz`` is left at its default. Around
                daylight-saving clock changes, repeated local times are converted to the
                earlier possible ``Unix Time / s`` value. For example, if clocks move back
                from UTC+1 to UTC+0, ``01:30`` is treated as ``00:30 UTC`` rather than
                ``01:30 UTC``. Local times skipped when clocks move forward become null.

        Returns:
            Normalized dataframe in the same format as input.

        Raises:
            ValueError: If ``tz`` is not a recognized IANA timezone name.
            BDFValidationError: If ``validate=True`` and required BDF columns are missing.
        """
        _validate_tz(tz)

        headers = list(df.collect_schema().names())

        resolved = self.resolve(headers)

        legacy_pairs = [
            (rc.source_header, getattr(COLUMN_ONTOLOGY, mr_name).formatted_label)
            for mr_name, rc in resolved.items()
            if rc.legacy
        ]
        if legacy_pairs:
            detail = ", ".join(f"{old!r} -> {new!r}" for old, new in legacy_pairs)
            warnings.warn(
                f"Legacy BDF column labels detected and normalized to preferred labels: {detail}",
                UserWarning,
                stacklevel=3,
            )

        unix_rc = resolved.get("unix_time_second")
        unix_norm = unix_rc.normalization if unix_rc is not None else None
        if (
            isinstance(unix_norm, (AbsoluteTimeNormalization, RelativeTimeNormalization))
            and tz == "UTC"
            and any(not _is_self_describing(f) for f in unix_norm.effective_formats)
        ):
            warnings.warn(
                "tz defaulted to UTC; pass tz=... if data was recorded in a different timezone",
                UserWarning,
                stacklevel=3,
            )

        exprs: list[pl.Expr] = []

        for mr_name, resolved_column in resolved.items():
            if resolved_column.source_header not in headers:
                _logger.info(
                    "normalize: source header %r not present in DataFrame; skipping",
                    resolved_column.source_header,
                )
                continue
            exprs.append(resolved_column.get_expr(mr_name, tz))

        if include_unknown:
            claimed_headers = {rc.source_header for rc in resolved.values()}
            unknown = [h for h in headers if h not in claimed_headers]
            exprs.extend([pl.col(h) for h in unknown])

        if exprs:
            df = df.select(exprs)

        COLUMN_ONTOLOGY.validate_df(df, raise_on_error=validate)
        return df


# ---------------------------------------------------------------------------
# Built-in vendor normalizers
#
# Each constant is a mechanics-agnostic header→BDF mapping. ``Plugin``
# entries in ``plugins.py`` reference these by key; one normalizer can back
# several file formats (e.g. ``"neware"`` backs both the CSV and XLSX sources).
# ---------------------------------------------------------------------------

_ACCESS_UNIX_EPOCH_DAYS = 25569.0
_SECONDS_PER_DAY = 86400.0

# Arbin exports use two header dialects: MITS CSV/newer Excel use spaces before the
# parenthesised unit ("Test Time (s)"); older MITS Excel uses underscores and no space
# ("Test_Time(s)"). Both are covered below and share this one normalizer across the
# arbin_csv and arbin_xlsx plugins.
ARBIN = TableNormalizer(
    test_time_second=(
        Syn(hdr="Test Time ({unit})"),
        Syn(hdr="Test_Time({unit})"),
    ),
    voltage_volt=(
        Syn(hdr="Voltage ({unit})"),
        Syn(hdr="Voltage({unit})"),
    ),
    current_ampere=(
        Syn(hdr="Current ({unit})"),
        Syn(hdr="Current({unit})"),
    ),
    unix_time_second=(
        Syn(hdr="Date Time", normalization=AbsoluteTimeNormalization(formats=_ARBIN_DT_FMTS)),
        Syn(hdr="Date_Time", normalization=AbsoluteTimeNormalization(formats=_ARBIN_DT_FMTS)),
    ),
    cycle_count=(
        Syn(hdr="Cycle Index"),
        Syn(hdr="Cycle_Index"),
    ),
    step_id=(
        Syn(hdr="Step Index"),
        Syn(hdr="Step_Index"),
    ),
    record_index=(
        Syn(hdr="Data Point"),
        Syn(hdr="Data_Point"),
    ),
    step_time_second=(
        Syn(hdr="Step Time ({unit})"),
        Syn(hdr="Step_Time({unit})"),
    ),
    temperature_t1_celsius=(
        Syn(hdr="Aux_Temperature_1 (C)"),
        Syn(hdr="Aux_Temperature_1 ({unit})"),
    ),
    # Arbin's accumulators reset at operator-authored schedule points ('Set
    # variable(s)') and can be assigned arbitrary values ('Set value'), per
    # Arbin's MITS team, so they carry the schedule-scoped terms from ontology
    # 1.3.0, not the never-resetting test-scoped ones.
    schedule_charging_capacity_ah=(Syn(hdr="Charge Capacity ({unit})"),),
    schedule_discharging_capacity_ah=(Syn(hdr="Discharge Capacity ({unit})"),),
    schedule_charging_energy_wh=(Syn(hdr="Charge Energy ({unit})"),),
    schedule_discharging_energy_wh=(Syn(hdr="Discharge Energy ({unit})"),),
    power_watt=(Syn(hdr="Power ({unit})"),),
    ac_internal_resistance_ohm=(Syn(hdr="ACR ({unit})"),),
    dc_internal_resistance_ohm=(Syn(hdr="Internal Resistance ({unit})"),),
)

# All synonyms are assumed=True until a .res sample lands in the test corpus
# (Abbta's CC-BY file from PR #60 is the candidate; tracked in the follow-up
# issue). The synonym-coverage gate requires recorded headers otherwise.
ARBIN_RES = TableNormalizer(
    test_time_second=(Syn(hdr="Test_Time", source_unit="s"),),
    voltage_volt=(Syn(hdr="Voltage", source_unit="V"),),
    current_ampere=(Syn(hdr="Current", source_unit="A"),),
    # Access day-fraction datetimes are naive local wall-clock; this fixed
    # scale/offset treats them as UTC (no tz support on the ResolvedColumn
    # path). Acceptable for the .res use case; revisit if tz-correct absolute
    # time is needed.
    unix_time_second=ResolvedColumn(
        source_header="DateTime",
        normalization=LinearNormalization(
            scale=_SECONDS_PER_DAY,
            offset=-_ACCESS_UNIX_EPOCH_DAYS * _SECONDS_PER_DAY,
        ),
    ),
    cycle_count=(Syn(hdr="Cycle_Index"),),
    step_id=(Syn(hdr="Step_Index"),),
    record_index=(Syn(hdr="Data_Point"),),
    step_time_second=(Syn(hdr="Step_Time", source_unit="s"),),
    # Arbin accumulators reset at operator-defined schedule points, so they map
    # to the schedule-scoped terms from ontology 1.3.0 (see the csv/xlsx
    # normalizer above).
    schedule_charging_capacity_ah=(Syn(hdr="Charge_Capacity", source_unit="Ah"),),
    schedule_discharging_capacity_ah=(Syn(hdr="Discharge_Capacity", source_unit="Ah"),),
    schedule_charging_energy_wh=(Syn(hdr="Charge_Energy", source_unit="Wh"),),
    schedule_discharging_energy_wh=(Syn(hdr="Discharge_Energy", source_unit="Wh"),),
    dc_internal_resistance_ohm=(Syn(hdr="Internal_Resistance", source_unit="ohm"),),
    absolute_impedance_ohm=(Syn(hdr="AC_Impedance", source_unit="ohm"),),
    phase_degree=(Syn(hdr="ACI_Phase_Angle", source_unit="degree"),),
)

BASYTEC = TableNormalizer(
    test_time_second=(
        Syn(hdr="Time[{unit}]", assumed=True),
        Syn(hdr="Time", assumed=True),
        Syn(hdr="Time[h:min:s]", assumed=True, normalization=ElapsedTimeNormalization()),
    ),
    voltage_volt=(
        Syn(hdr="U[{unit}]"),
        Syn(hdr="Voltage[{unit}]", assumed=True),
        Syn(hdr="U", assumed=True),
        Syn(hdr="Voltage", assumed=True),
    ),
    current_ampere=(
        Syn(hdr="I[{unit}]"),
        Syn(hdr="Current[{unit}]", assumed=True),
        Syn(hdr="I", assumed=True),
        Syn(hdr="Current", assumed=True),
    ),
    temperature_t1_celsius=(
        Syn(hdr="T1[{unit}]"),
        Syn(hdr="T1[°C]"),
        Syn(hdr="Temp[{unit}]", assumed=True),
        Syn(hdr="Temp[°C]", assumed=True),
        Syn(hdr="Temperature[{unit}]", assumed=True),
        Syn(hdr="Temperature[°C]", assumed=True),
    ),
    net_capacity_ah=(Syn(hdr="Ah[{unit}]", assumed=True),),
    step_id=(Syn(hdr="Line"),),
    record_index=(Syn(hdr="DataSet"),),
    power_watt=(Syn(hdr="P[{unit}]", assumed=True),),
    ac_internal_resistance_ohm=(Syn(hdr="R-AC", assumed=True),),
    dc_internal_resistance_ohm=(Syn(hdr="R-DC", assumed=True),),
)

BIOLOGIC = TableNormalizer(
    unix_time_second=(Syn(hdr="uts/s"),),
    test_time_second=(
        Syn(hdr="time/{unit}"),
        Syn(hdr="time / {unit}", assumed=True),
        Syn(hdr="t ({unit})", assumed=True),
        Syn(hdr="time [{unit}]", assumed=True),
        Syn(hdr="relative time({unit})", assumed=True),
    ),
    voltage_volt=(
        Syn(hdr="Ecell/{unit}"),
        Syn(hdr="Ewe/{unit}"),
        Syn(hdr="u/{unit}", assumed=True),
        Syn(hdr="u[{unit}]", assumed=True),
        Syn(hdr="Ewe ({unit})", assumed=True),
        Syn(hdr="<Ewe>/{unit}", assumed=True),
    ),
    current_ampere=(
        Syn(hdr="I/{unit}"),
        Syn(hdr="I[{unit}]", assumed=True),
        Syn(hdr="Current / {unit}", assumed=True),
        Syn(hdr="Current({unit})", assumed=True),
        Syn(hdr="I({unit})", assumed=True),
        Syn(hdr="<I>/{unit}", assumed=True),
    ),
    cycle_count=(
        Syn(hdr="cycle number"),
        Syn(hdr="z cycle", assumed=True),
    ),
    step_id=(Syn(hdr="Ns"),),
    step_time_second=(Syn(hdr="step time/{unit}"),),
    temperature_t1_celsius=(
        Syn(hdr="Temperature/{unit}", assumed=True),
        Syn(hdr="Temperature/°C", assumed=True),
        Syn(hdr="Temperature/\xf8c", assumed=True),
        Syn(hdr="Temperature/c", assumed=True),
        Syn(hdr="Temp/{unit}", assumed=True),
        Syn(hdr="Temp/°C", assumed=True),
        Syn(hdr="Temp/\xf8c", assumed=True),
        Syn(hdr="Temp/c", assumed=True),
        Syn(hdr="T/{unit}", assumed=True),
        Syn(hdr="T/°C", assumed=True),
        Syn(hdr="T/\xf8c", assumed=True),
        Syn(hdr="T/c", assumed=True),
    ),
    net_capacity_ah=(Syn(hdr="(Q-Qo)/{unit}"),),
    charging_energy_wh=(Syn(hdr="Energy charge/{unit}"),),
    discharging_energy_wh=(Syn(hdr="Energy discharge/{unit}"),),
    cumulative_energy_wh=(Syn(hdr="|Energy|/{unit}", assumed=True),),
    net_energy_wh=(Syn(hdr="Energy/{unit}"),),
    power_watt=(
        Syn(hdr="P/{unit}"),
        Syn(hdr="Pwe/{unit}"),
    ),
    internal_resistance_ohm=(Syn(hdr="R/{unit}"),),
    frequency_hertz=(Syn(hdr="freq/{unit}"),),
    real_impedance_ohm=(Syn(hdr="Re(Z)/{unit}"),),
    imaginary_impedance_ohm=(Syn(hdr="-Im(Z)/{unit}", reverse_sign=True),),
    phase_degree=(Syn(hdr="Phase(Z)/{unit}"),),
    absolute_impedance_ohm=(Syn(hdr="|Z|/{unit}"),),
)

DIGATRON = TableNormalizer(
    test_time_second=(
        Syn(hdr="Program Duration#{unit}"),
        Syn(hdr="Prog Time", assumed=True),
        Syn(hdr="Program Time", assumed=True),
    ),
    voltage_volt=(
        Syn(hdr="Voltage#{unit}"),
        Syn(hdr="Voltage", assumed=True),
    ),
    current_ampere=(
        Syn(hdr="Current#{unit}"),
        Syn(hdr="Current", assumed=True),
    ),
    unix_time_second=(Syn(hdr="Timestamp", normalization=AbsoluteTimeNormalization(formats=_DIGATRON_DT_FMTS)),),
    step_id=(Syn(hdr="Step"),),
    step_time_second=(
        Syn(hdr="Step Duration#{unit}"),
        Syn(hdr="Step Time", assumed=True),
    ),
    step_type=(Syn(hdr="Status"),),
    ambient_temperature_celsius=(Syn(hdr="Tenv#{unit}"),),
    temperature_t1_celsius=(
        Syn(hdr="T1#{unit}"),
        Syn(hdr="logtemp001", assumed=True),
    ),
    charging_capacity_ah=(Syn(hdr="AhCha#{unit}"),),
    discharging_capacity_ah=(Syn(hdr="AhDch#{unit}"),),
    net_capacity_ah=(
        Syn(hdr="AhAccu#{unit}"),
        Syn(hdr="AhAccu", assumed=True),
    ),
    step_cumulative_capacity_ah=(Syn(hdr="AhStep#{unit}"),),
    charging_energy_wh=(Syn(hdr="WhCha#{unit}"),),
    discharging_energy_wh=(Syn(hdr="WhDch#{unit}"),),
    net_energy_wh=(
        Syn(hdr="WhAccu#{unit}"),
        Syn(hdr="WhAccu", assumed=True),
    ),
    step_cumulative_energy_wh=(Syn(hdr="WhStep#{unit}"),),
    power_watt=(
        # no power column in this file
        Syn(hdr="Watt", assumed=True),
        Syn(hdr="Power#{unit}", assumed=True),
    ),
)

LANDT_CSV = TableNormalizer(
    test_time_second=(Syn(hdr="test_time_s"),),
    voltage_volt=(Syn(hdr="voltage_V"),),
    current_ampere=(Syn(hdr="current_A"),),
    cycle_count=(Syn(hdr="cycle_index"),),
    step_id=(Syn(hdr="step_index"),),
    step_time_second=(Syn(hdr="step_time_s"),),
    record_index=(Syn(hdr="channel_index"),),
    unix_time_second=(
        Syn(hdr="date_time_iso_string", normalization=AbsoluteTimeNormalization(formats=("%m/%d/%Y %H:%M:%S",))),
    ),
    step_charging_capacity_ah=(Syn(hdr="charge_capacity_{unit}"),),
    step_discharging_capacity_ah=(Syn(hdr="discharge_capacity_{unit}"),),
    step_charging_energy_wh=(Syn(hdr="charge_energy_{unit}"),),
    step_discharging_energy_wh=(Syn(hdr="discharge_energy_{unit}"),),
    temperature_t1_celsius=(Syn(hdr="temperature_1_{unit}"),),
    temperature_t2_celsius=(Syn(hdr="temperature_2_{unit}"),),
    temperature_t3_celsius=(Syn(hdr="temperature_3_{unit}"),),
    step_type=(Syn(hdr="step_name"),),
)

LANDT_TXT = TableNormalizer(
    test_time_second=(
        Syn(hdr="Test({unit})"),
        Syn(hdr="Test ({unit})", assumed=True),
        Syn(hdr="test_time_s", assumed=True),
        Syn(hdr="Test Time ({unit})", assumed=True),
        Syn(hdr="Test Time", assumed=True),
    ),
    voltage_volt=(
        Syn(hdr="Volts"),
        Syn(hdr="Volt", assumed=True),
        Syn(hdr="Voltage", assumed=True),
        Syn(hdr="V", assumed=True),
    ),
    current_ampere=(
        Syn(hdr="Amps"),
        Syn(hdr="Amp", assumed=True),
        Syn(hdr="Current", assumed=True),
        Syn(hdr="A", assumed=True),
        Syn(hdr="I({unit})", assumed=True),
    ),
    cycle_count=(
        Syn(hdr="Cyc#"),
        Syn(hdr="Cycle", assumed=True),
        Syn(hdr="Cycle#", assumed=True),
        Syn(hdr="Cycle Index", assumed=True),
    ),
    step_id=(
        Syn(hdr="Step"),
        Syn(hdr="Step#", assumed=True),
        Syn(hdr="Step Index", assumed=True),
    ),
    record_index=(
        Syn(hdr="Rec#"),
        Syn(hdr="Record", assumed=True),
        Syn(hdr="Record#", assumed=True),
    ),
    unix_time_second=(Syn(hdr="DPt-Time", normalization=AbsoluteTimeNormalization(formats=_LANDT_DT_FMTS)),),
    step_time_second=(
        Syn(hdr="Step({unit})"),
        Syn(hdr="Step Time ({unit})", assumed=True),
        Syn(hdr="step_time_s", assumed=True),
    ),
    step_cumulative_capacity_ah=(Syn(hdr="Amp-hr"),),
    step_cumulative_energy_wh=(Syn(hdr="Watt-hr"),),
    # none: State (single-char code; ~step_type), ES (event/status flag)
)

MACCOR = TableNormalizer(
    test_time_second=(
        Syn(hdr="Test Time ({unit})", assumed=True),
        Syn(hdr="Test Time({unit})", assumed=True),
        Syn(hdr="Test Time [{unit}]"),
    ),
    voltage_volt=(
        Syn(hdr="Voltage", assumed=True),
        Syn(hdr="Voltage [{unit}]"),
    ),
    current_ampere=(
        Syn(hdr="Current", assumed=True),
        Syn(hdr="Current [{unit}]"),
    ),
    unix_time_second=(Syn(hdr="DPT Time", normalization=AbsoluteTimeNormalization(formats=_MACCOR_DT_FMTS)),),
    cycle_count=(Syn(hdr="Cycle C"),),
    step_count=(Syn(hdr="Step"),),
    record_index=(Syn(hdr="Rec"),),
    step_time_second=(
        Syn(hdr="Step Time ({unit})", assumed=True),
        Syn(hdr="Step Time [{unit}]"),
    ),
    temperature_t1_celsius=(
        Syn(hdr="Temp 1", assumed=True),
        Syn(hdr="Temperature Cell [{unit}]"),
    ),
    ambient_temperature_celsius=(Syn(hdr="Temperature Chamber [{unit}]"),),
    step_cumulative_capacity_ah=(
        Syn(hdr="Capacity", assumed=True),
        Syn(hdr="Capacity [{unit}]"),
    ),
    step_cumulative_energy_wh=(
        Syn(hdr="Energy", assumed=True),
        Syn(hdr="Energy [{unit}]"),
    ),
)

NEWARE = TableNormalizer(
    test_time_second=(
        Syn(hdr="Total Time", assumed=True, normalization=RelativeTimeNormalization(formats=_NEWARE_DT_FMTS)),
        Syn(hdr="Total Time({unit})"),
        Syn(hdr="Test Time({unit})", assumed=True),
        Syn(hdr="TotalTime({unit})", assumed=True),
        Syn(hdr="totaltime_s", assumed=True),
        Syn(hdr="总时间({unit})", assumed=True),
        Syn(hdr="测试时间({unit})", assumed=True),
    ),
    voltage_volt=(
        Syn(hdr="Voltage({unit})"),
        Syn(hdr="电压({unit})", assumed=True),
        Syn(hdr="Voltage [{unit}]", assumed=True),
    ),
    current_ampere=(
        Syn(hdr="Current({unit})"),
        Syn(hdr="电流({unit})", assumed=True),
        Syn(hdr="Current [{unit}]", assumed=True),
    ),
    unix_time_second=(
        Syn(hdr="Date", normalization=AbsoluteTimeNormalization(formats=_NEWARE_DT_FMTS)),
        Syn(hdr="DateTime", assumed=True, normalization=AbsoluteTimeNormalization(formats=_NEWARE_DT_FMTS)),
        Syn(hdr="Date_Time", assumed=True, normalization=AbsoluteTimeNormalization(formats=_NEWARE_DT_FMTS)),
    ),
    cycle_count=(
        Syn(hdr="Cycle Index"),
        Syn(hdr="Cycle", assumed=True),
    ),
    step_id=(
        Syn(hdr="Step Index"),
        Syn(hdr="Step", assumed=True),
    ),
    step_time_second=(
        Syn(hdr="Time", assumed=True, normalization=RelativeTimeNormalization(formats=_NEWARE_DT_FMTS)),
        Syn(hdr="Time({unit})"),
        Syn(hdr="Relative Time({unit})", assumed=True),
        Syn(hdr="State Time({unit})", assumed=True),
        Syn(hdr="StepTime({unit})", assumed=True),
        Syn(hdr="Step Time({unit})", assumed=True),
        Syn(hdr="steptime_s", assumed=True),
        Syn(hdr="时间({unit})", assumed=True),
    ),
    step_cumulative_capacity_ah=(Syn(hdr="Capacity({unit})"),),
    step_charging_capacity_ah=(
        Syn(hdr="Chg. Cap.({unit})"),
        Syn(hdr="Chg.Capacity({unit})", assumed=True),
        Syn(hdr="Charge Capacity({unit})", assumed=True),
    ),
    step_discharging_capacity_ah=(
        Syn(hdr="DChg. Cap.({unit})"),
        Syn(hdr="DChg.Capacity({unit})", assumed=True),
        Syn(hdr="Discharge Capacity({unit})", assumed=True),
    ),
    step_charging_energy_wh=(
        # no energy column in inspected files
        Syn(hdr="Chg. Energy({unit})"),
        Syn(hdr="Chg.Energy({unit})", assumed=True),
        Syn(hdr="Charge Energy({unit})", assumed=True),
    ),
    step_discharging_energy_wh=(
        # no energy column in inspected files
        Syn(hdr="DChg. Energy({unit})"),
        Syn(hdr="DChg.Energy({unit})", assumed=True),
        Syn(hdr="Discharge Energy({unit})", assumed=True),
    ),
    temperature_t1_celsius=(
        Syn(hdr="Temperature(°C)", assumed=True),
        Syn(hdr="温度(°C)", assumed=True),
    ),
)

NOVONIX = TableNormalizer(
    test_time_second=(
        Syn(hdr="Run Time ({unit})"),
        Syn(hdr="Run-Time ({unit})", assumed=True),
        Syn(hdr="Runtime ({unit})", assumed=True),
        Syn(hdr="Test Time ({unit})", assumed=True),
        Syn(hdr="TestTime({unit})", assumed=True),
    ),
    voltage_volt=(
        Syn(hdr="Potential ({unit})"),
        Syn(hdr="Voltage ({unit})", assumed=True),
        Syn(hdr="Cell Voltage ({unit})", assumed=True),
    ),
    current_ampere=(
        Syn(hdr="Current ({unit})"),
        Syn(hdr="Cell Current ({unit})", assumed=True),
    ),
    unix_time_second=(
        Syn(hdr="Date and Time", normalization=AbsoluteTimeNormalization(formats=("%Y-%m-%d %H:%M:%S",))),
        Syn(hdr="Unix Time ({unit})", assumed=True),
        Syn(hdr="UnixTime ({unit})", assumed=True),
    ),
    cycle_count=(
        Syn(hdr="Cycle Number"),
        Syn(hdr="Cycle", assumed=True),
        Syn(hdr="Cycle #", assumed=True),
        Syn(hdr="Cycle#", assumed=True),
    ),
    step_count=(
        Syn(hdr="Step Number"),
        Syn(hdr="Step #", assumed=True),
        Syn(hdr="Step#", assumed=True),
    ),
    step_id=(Syn(hdr="Step position"),),
    step_type=(Syn(hdr="Step Type"),),
    step_time_second=(
        Syn(hdr="Step Time ({unit})"),
        Syn(hdr="StepTime({unit})", assumed=True),
    ),
    temperature_t1_celsius=(
        Syn(hdr="Temperature (°C)"),
        Syn(hdr="Temperature (C)", assumed=True),
    ),
    temperature_t2_celsius=(
        Syn(hdr="Circuit Temperature (°C)"),
        Syn(hdr="Circuit Temperature (C)", assumed=True),
        Syn(hdr="Circuit Temp (°C)", assumed=True),
        Syn(hdr="Circuit Temp (C)", assumed=True),
    ),
    ambient_temperature_celsius=(
        Syn(hdr="Ambient Temperature (°C)", assumed=True),
        Syn(hdr="Ambient Temperature (C)", assumed=True),
        Syn(hdr="Ambient Temp (°C)", assumed=True),
        Syn(hdr="Ambient Temp (C)", assumed=True),
    ),
    net_capacity_ah=(
        Syn(hdr="Capacity ({unit})"),
        Syn(hdr="Net Capacity ({unit})", assumed=True),
    ),
    step_net_energy_wh=(Syn(hdr="Energy ({unit})"),),
    net_energy_wh=(Syn(hdr="Net Energy ({unit})", assumed=True),),
    power_watt=(
        Syn(hdr="Power({unit})"),
        Syn(hdr="Power ({unit})", assumed=True),
    ),
)

# Sign convention: PyBaMM is discharge-positive but BDF is charge-positive (see
# current_ampere / net_capacity_ah in the ontology), so current and the signed
# capacity integral are negated. PyBaMM "Discharge capacity" is the running signed
# integral (Q - Q0), so scale=-1 turns it into net_capacity_ah (charge - discharge).
#
# "Step" is a 0-based index that resets each cycle, so the same value recurs in
# successive cycles -- this matches step_id, not the within-step datapoint counter.
PYBAMM = TableNormalizer(
    test_time_second=ResolvedColumn(source_header="Time [s]"),
    voltage_volt=ResolvedColumn(source_header="Voltage [V]"),
    current_ampere=ResolvedColumn(source_header="Current [A]", normalization=LinearNormalization(scale=-1.0)),
    net_capacity_ah=ResolvedColumn(
        source_header="Discharge capacity [A.h]", normalization=LinearNormalization(scale=-1.0)
    ),
    temperature_t1_celsius=(Syn(hdr="X-averaged cell temperature [{unit}]"),),
    cycle_count=ResolvedColumn(source_header="Cycle"),
    step_id=ResolvedColumn(source_header="Step"),
)

NDA_NORMALIZER = TableNormalizer(
    test_time_second=(Syn(hdr="total_time_{unit}"),),
    voltage_volt=(Syn(hdr="voltage_{unit}"),),
    current_ampere=(Syn(hdr="current_{unit}"),),
    unix_time_second=(Syn(hdr="unix_time_{unit}"),),
    step_time_second=(Syn(hdr="step_time_{unit}"),),
    cycle_count=(Syn(hdr="cycle_count"),),
    step_count=(Syn(hdr="step_count"),),
    step_id=(Syn(hdr="step_index"),),
    step_type=(Syn(hdr="step_type"),),
    record_index=(Syn(hdr="index"),),
    step_net_capacity_ah=(Syn(hdr="capacity_{unit}"),),
    step_net_energy_wh=(Syn(hdr="energy_{unit}"),),
)


def _build_bdf_normalizer() -> TableNormalizer:
    """Build the normalizer mapping on-disk BDF labels to current labels.

    Returns:
        TableNormalizer whose synonyms cover canonical BDF label templates plus
        notation/deprecated aliases, used to round-trip already-BDF-formatted tables.
    """
    kwargs: dict[str, tuple[Syn, ...]] = {}

    base_preferred: dict[str, str] = {}
    for mr_name, q in COLUMN_ONTOLOGY:
        if q.deprecated:
            continue
        base = q.formatted_label.split(" / ", 1)[0].strip().lower()
        base_preferred.setdefault(base, mr_name)

    def _append(target_mr: str, syn: Syn) -> None:
        existing = kwargs.setdefault(target_mr, ())
        if syn not in existing:
            kwargs[target_mr] = (*existing, syn)

    # Append synonyms to TableNormalizer
    # Append the deprecated quantities first, so their concrete synonyms
    # (e.g. "Test Time / ms") take priority over generic templates (e.g.
    # "Time Time / {unit}"), and deprecation warnings get raised correctly.
    for mr_name, q in COLUMN_ONTOLOGY:
        if not q.deprecated:
            continue
        # Prefer the ontology's explicit dcterms:isReplacedBy link
        if q.replaced_by and q.replaced_by in TableNormalizer.model_fields:
            target_mr = q.replaced_by
        else:
            base = q.formatted_label.split(" / ", 1)[0].strip().lower()
            target_mr = base_preferred.get(base, mr_name)
        if target_mr not in TableNormalizer.model_fields:
            continue

        # Use formatted_label for deprecated terms, not a generic template
        _append(target_mr, Syn(hdr=q.formatted_label, source_unit=q.unit, legacy=True))
        _append(target_mr, Syn(hdr=q.effective_notation, source_unit=q.unit, legacy=True))

    # Then append all non-deprecated synonyms
    for mr_name, q in COLUMN_ONTOLOGY:
        if q.deprecated or mr_name not in TableNormalizer.model_fields:
            continue
        _append(mr_name, Syn(hdr=q.label_template, legacy=False))
        _append(mr_name, Syn(hdr=q.effective_notation, source_unit=q.unit, legacy=False))
    return TableNormalizer(**kwargs)


BDF_NORMALIZER = _build_bdf_normalizer()


NORMALIZERS: dict[str, TableNormalizer] = {
    "arbin": ARBIN,
    "arbin_res": ARBIN_RES,
    "basytec": BASYTEC,
    "biologic": BIOLOGIC,
    "digatron": DIGATRON,
    "landt_csv": LANDT_CSV,
    "landt_txt": LANDT_TXT,
    "maccor": MACCOR,
    "neware": NEWARE,
    "novonix": NOVONIX,
    "neware_nda": NDA_NORMALIZER,
    "pybamm": PYBAMM,
    "bdf": BDF_NORMALIZER,
}


def detect_normalizer(
    column_names: list[str],
    normalizers: "Sequence[TableNormalizer]",
) -> "TableNormalizer | None":
    """Return the highest-scoring normalizer for ``column_names``, or ``None`` if all score zero.

    Args:
        column_names: List of source column names to score.
        normalizers: Sequence of TableNormalizer instances to evaluate.

    Returns:
        The normalizer with the highest score, or None if all scores are zero.
    """
    scored = {n: n.score_columns(column_names) for n in normalizers}
    best_score = max(scored.values(), default=0)
    if best_score == 0:
        return None
    return max(scored, key=scored.__getitem__)


def normalize(
    df: pl.DataFrame | pl.LazyFrame | pd.DataFrame,
    *,
    normalizer: "TableNormalizer | dict[str, str] | None" = None,
    validate: bool = True,
    include_unknown: bool = False,
    tz: str = "UTC",
) -> pl.DataFrame | pl.LazyFrame | pd.DataFrame:
    """Map vendor columns to BDF canonical names with unit conversion and dtype casting.

    Accepts ``pl.DataFrame``, ``pl.LazyFrame``, or ``pandas.DataFrame``. Return type matches input.
    ``validate`` defaults to True: this checks required columns even if no normalizer can be
    auto-detected from ``df``'s headers (see ``TableNormalizer.normalize``). Pass
    ``validate=False`` to fall back to a soft warning instead of raising.

    Args:
        df: Input dataframe in any supported format.
        normalizer: Explicit TableNormalizer, column map dict, or None for auto-detection.
        validate: Validate column names against the BDF ontology when True (default;
            raises on missing required columns instead of warning).
        include_unknown: Keep columns outside of the BDF spec in the dataframe (default False).
        tz: IANA timezone applied to naive ``unix_time_second`` datetime formats. Defaults
            to ``"UTC"``; emits a ``UserWarning`` when a naive format is in play and ``tz``
            is left at its default. Around daylight-saving clock changes, repeated local
            times are converted to the earlier possible ``Unix Time / s`` value. For
            example, if clocks move back from UTC+1 to UTC+0, ``01:30`` is treated as
            ``00:30 UTC`` rather than ``01:30 UTC``. Local times skipped when clocks move
            forward become null.

    Returns:
        Normalized dataframe in the same format as input.

    Raises:
        ValueError: If ``tz`` is not a recognized IANA timezone name.
        BDFValidationError: If ``validate=True`` and required BDF columns are missing.
    """
    if isinstance(df, (pl.DataFrame, pl.LazyFrame)):
        schema = df.collect_schema() if isinstance(df, pl.LazyFrame) else df.schema
        headers = list(schema.names())
    else:
        headers = list(df.columns)

    norm: TableNormalizer
    if normalizer is not None:
        norm = normalizer if isinstance(normalizer, TableNormalizer) else TableNormalizer.from_column_map(normalizer)
    else:
        best = detect_normalizer(headers, list(NORMALIZERS.values()))
        if best is None:
            if not validate:
                return df
            norm = TableNormalizer()
        else:
            norm = best if best is not None else TableNormalizer()

    return norm.normalize(
        df,
        validate=validate,
        include_unknown=include_unknown,
        tz=tz,
    )
