"""Normalisation classes, helpers, and the public normalize() entry point."""

from __future__ import annotations

import logging
import re
import warnings
from collections.abc import Sequence
from typing import TYPE_CHECKING, Iterator

import polars as pl
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
)

if TYPE_CHECKING:
    import pandas as pd  # noqa: F401

from bdf.spec import COLUMN_ONTOLOGY, get_unit_conversion, unit_from_label

_logger = logging.getLogger(__name__)

_DATE_COMPONENT_RE = re.compile(r"%[YymbBdej]")
_UNIT_CAPTURE = r"([A-Za-z0-9./]+)"


class Syn(RootModel[str]):
    """A numeric column synonym declared by exemplar header."""

    model_config = ConfigDict(frozen=True)

    @property
    def exemplar(self) -> str:
        """Return exemplar pattern string."""
        return self.root

    def match(self, header: str, bdf_unit: str) -> tuple[float, float] | None:
        """Return (scale, offset) on match, None on no match or incompatible units."""
        if "{unit}" in self.root:
            parts = self.root.split("{unit}")
            pattern = _UNIT_CAPTURE.join(re.escape(p) for p in parts)
            m = re.fullmatch(pattern, header, re.IGNORECASE)
            if m is None:
                return None
            return get_unit_conversion(m.group(1), bdf_unit)
        return (1.0, 0.0) if self.root.strip().lower() == header.strip().lower() else None

    def exact_match(self, header: str) -> bool:
        """Test exact case-insensitive match against header."""
        return self.root.strip().lower() == header.strip().lower()


class DateTimeSyn(BaseModel):
    """A datetime column synonym: one header synonym plus ordered format strings to try."""

    model_config = ConfigDict(frozen=True)

    syn: Syn = Field(description="Header synonym to match datetime columns.")
    fmts: tuple[str, ...] = Field(description="Ordered list of datetime format strings to attempt parsing.")


SynUnion = Syn | DateTimeSyn


class ResolvedColumn(BaseModel):
    """Resolved mapping of one source header to one BDF column."""

    model_config = ConfigDict(frozen=True)

    source_header: str = Field(description="The column name in the source data.")
    scale: float = Field(default=1.0, description="Scale factor to apply to numeric values.")
    offset: float = Field(default=0.0, description="Offset to apply to numeric values after scaling.")
    datetime_fmts: tuple[str, ...] = Field(
        default=(), description="Datetime format strings for parsing timestamp columns."
    )

    @classmethod
    def from_column_map(cls, bdf_label_key: str, src_header: str) -> tuple[str, ResolvedColumn]:
        """Resolve a BDF label key (e.g. 'Voltage / mV') to (mr_name, ResolvedColumn)."""
        mr_name = COLUMN_ONTOLOGY.mr_name_from_label(bdf_label_key)
        if mr_name is None:
            raise ValueError(f"column_map key {bdf_label_key!r}: label base not found in BDF spec")
        key_unit = unit_from_label(bdf_label_key)
        bdf_unit = getattr(COLUMN_ONTOLOGY, mr_name).unit
        if key_unit is None:
            scale, offset = 1.0, 0.0
        else:
            result = get_unit_conversion(key_unit, bdf_unit)
            if result is None:
                warnings.warn(
                    f"column_map: unit {key_unit!r} in {bdf_label_key!r} not compatible "
                    f"with {bdf_unit!r} for {mr_name}; using scale=1.0",
                    UserWarning,
                    stacklevel=4,
                )
                scale, offset = 1.0, 0.0
            else:
                scale, offset = result
        return mr_name, cls(source_header=src_header, scale=scale, offset=offset)

    @classmethod
    def from_synonyms(
        cls,
        header: str,
        probe: str,
        bdf_unit: str,
        synonyms: Sequence[SynUnion],
    ) -> ResolvedColumn | None:
        """Try to match header against synonyms; return ResolvedColumn or None."""
        for syn in synonyms:
            if isinstance(syn, DateTimeSyn):
                if syn.syn.exact_match(probe):
                    return cls(
                        source_header=header,
                        datetime_fmts=syn.fmts,
                    )
            else:
                result = syn.match(probe, bdf_unit)
                if result is not None:
                    scale, offset = result
                    return cls(
                        source_header=header,
                        scale=scale,
                        offset=offset,
                    )
        return None

    def get_expr(self, mr_name: str) -> pl.Expr:
        """Build polars expression for column transformation with unit conversion and dtype casting."""
        src = self.source_header
        label = getattr(COLUMN_ONTOLOGY, mr_name).label
        if self.datetime_fmts:
            dt_fmts = [f for f in self.datetime_fmts if _DATE_COMPONENT_RE.search(f)]
            dur_fmts = [f for f in self.datetime_fmts if not _DATE_COMPONENT_RE.search(f)]
            parts: list[pl.Expr] = []
            if dt_fmts:
                dt_expr = _datetime_unix_expr if mr_name == "unix_time_second" else _datetime_elapsed_expr
                parts.append(dt_expr(src, dt_fmts))
            if dur_fmts:
                parts.append(_duration_str_expr(src))
            expr = pl.coalesce(parts) if len(parts) > 1 else parts[0]
            return expr.alias(label)
        expr = pl.col(src).cast(pl.Float64, strict=False)
        if self.scale != 1.0:
            expr = expr * self.scale
        if self.offset != 0.0:
            expr = expr + self.offset
        if getattr(COLUMN_ONTOLOGY, mr_name).dtype == "int":
            expr = expr.cast(pl.Int64, strict=False)
        return expr.alias(label)


def _datetime_unix_expr(src: str, fmts: list[str]) -> pl.Expr:
    """Parse datetimes to unix timestamp seconds."""
    candidates = [pl.col(src).str.to_datetime(f, strict=False) for f in fmts]
    parsed = pl.coalesce(candidates) if len(candidates) > 1 else candidates[0]
    return parsed.dt.timestamp("us").cast(pl.Float64) / 1e6


def _datetime_elapsed_expr(src: str, fmts: list[str]) -> pl.Expr:
    """Parse datetimes to seconds elapsed since first row."""
    ts = _datetime_unix_expr(src, fmts)
    return ts - ts.first()


def _duration_str_expr(src: str) -> pl.Expr:
    """Parse HH:MM:SS[.fff] duration string to seconds. Handles hours > 23."""
    h = pl.col(src).str.extract(r"^(\d+):\d+:[\d.]+", 1).cast(pl.Float64)
    m = pl.col(src).str.extract(r"^\d+:(\d+):[\d.]+", 1).cast(pl.Float64)
    s = pl.col(src).str.extract(r"^\d+:\d+:([\d.]+)", 1).cast(pl.Float64)
    return h * 3600 + m * 60 + s


class TableNormalizer(BaseModel):
    """Column-mapping model: one optional field per BDF mr_name.

    Fields accept ``tuple[Syn | DateTimeSyn, ...]`` (synonym-based, for CSV/Excel) or
    ``ResolvedColumn`` (direct, for MAT). Iterating yields ``(mr_name, spec)``
    for non-None fields in declaration order. ``tuple`` (not ``list``) keeps
    instances hashable so they can live in a ``frozenset``.
    """

    model_config = ConfigDict(frozen=True)

    test_time_second: tuple[SynUnion, ...] | ResolvedColumn | None = None
    voltage_volt: tuple[SynUnion, ...] | ResolvedColumn | None = None
    current_ampere: tuple[SynUnion, ...] | ResolvedColumn | None = None
    unix_time_second: tuple[SynUnion, ...] | ResolvedColumn | None = None
    cycle_count: tuple[SynUnion, ...] | ResolvedColumn | None = None
    step_count: tuple[SynUnion, ...] | ResolvedColumn | None = None
    ambient_temperature_celsius: tuple[SynUnion, ...] | ResolvedColumn | None = None
    step_index: tuple[SynUnion, ...] | ResolvedColumn | None = None
    step_time_second: tuple[SynUnion, ...] | ResolvedColumn | None = None
    charging_capacity_ah: tuple[SynUnion, ...] | ResolvedColumn | None = None
    discharging_capacity_ah: tuple[SynUnion, ...] | ResolvedColumn | None = None
    step_capacity_ah: tuple[SynUnion, ...] | ResolvedColumn | None = None
    net_capacity_ah: tuple[SynUnion, ...] | ResolvedColumn | None = None
    cumulative_capacity_ah: tuple[SynUnion, ...] | ResolvedColumn | None = None
    charging_energy_wh: tuple[SynUnion, ...] | ResolvedColumn | None = None
    discharging_energy_wh: tuple[SynUnion, ...] | ResolvedColumn | None = None
    step_energy_wh: tuple[SynUnion, ...] | ResolvedColumn | None = None
    net_energy_wh: tuple[SynUnion, ...] | ResolvedColumn | None = None
    cumulative_energy_wh: tuple[SynUnion, ...] | ResolvedColumn | None = None
    power_watt: tuple[SynUnion, ...] | ResolvedColumn | None = None
    internal_resistance_ohm: tuple[SynUnion, ...] | ResolvedColumn | None = None
    ambient_pressure_pa: tuple[SynUnion, ...] | ResolvedColumn | None = None
    applied_pressure_pa: tuple[SynUnion, ...] | ResolvedColumn | None = None
    temperature_t1_celsius: tuple[SynUnion, ...] | ResolvedColumn | None = None
    temperature_t2_celsius: tuple[SynUnion, ...] | ResolvedColumn | None = None
    temperature_t3_celsius: tuple[SynUnion, ...] | ResolvedColumn | None = None
    temperature_t4_celsius: tuple[SynUnion, ...] | ResolvedColumn | None = None
    temperature_t5_celsius: tuple[SynUnion, ...] | ResolvedColumn | None = None

    def __iter__(self) -> Iterator[tuple[str, tuple[SynUnion, ...] | ResolvedColumn]]:  # type: ignore[override]
        """Iterate over (mr_name, field_value) for all non-None fields in declaration order."""
        for mr_name in type(self).model_fields:
            val = getattr(self, mr_name)
            if val is not None:
                yield mr_name, val

    def resolve(self, headers: list[str]) -> dict[str, ResolvedColumn]:
        """Return mr_name → ResolvedColumn for all headers that match a synonym field.

        ResolvedColumn fields are passed through as-is. Each source header is
        claimed at most once (first match in declaration order wins).
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
        """Count resolved columns whose source header is present in headers."""
        resolved = self.resolve(headers)
        return sum(1 for rc in resolved.values() if rc.source_header in headers)

    def known_header_names(self) -> list[str]:
        """Source-header names from ResolvedColumn fields only (known, not synonyms)."""
        names: list[str] = []
        for _, spec in self:
            if isinstance(spec, ResolvedColumn):
                names.append(spec.source_header)
        return names

    @classmethod
    def from_column_map(cls, column_map: dict[str, str]) -> "TableNormalizer":
        """Convert a BDF label-key dict to a TableNormalizer via ResolvedColumn.from_column_map."""
        if not column_map:
            raise ValueError("column_map must not be empty")
        kwargs: dict[str, ResolvedColumn] = {}
        for bdf_label_key, src_header in column_map.items():
            mr_name, rc = ResolvedColumn.from_column_map(bdf_label_key, src_header)
            kwargs[mr_name] = rc
        return cls(**kwargs)

    def normalize(
        self,
        df: pl.DataFrame | pl.LazyFrame | pd.DataFrame,
        *,
        include_optional: bool = True,
        extra_columns: dict[str, str] | None = None,
    ) -> pl.DataFrame | pl.LazyFrame | pd.DataFrame:
        """Resolve headers → BDF columns, apply unit conversion, return df_out.

        Accepts ``pl.DataFrame``, ``pl.LazyFrame``, or ``pandas.DataFrame``. Return type matches input.
        """
        if not isinstance(df, (pl.DataFrame, pl.LazyFrame)):
            lf = pl.from_pandas(df).lazy()
            result = self.normalize(
                lf,
                include_optional=include_optional,
                extra_columns=extra_columns,
            )
            assert isinstance(result, pl.LazyFrame)
            return result.collect().to_pandas()  # type: ignore[union-attr]

        schema = df.collect_schema() if isinstance(df, pl.LazyFrame) else df.schema
        headers = list(schema.names())

        resolved = self.resolve(headers)

        if not include_optional:
            resolved = {mr: r for mr, r in resolved.items() if getattr(COLUMN_ONTOLOGY, mr).required}

        exprs: list[pl.Expr] = []

        for mr_name, rc in resolved.items():
            if rc.source_header not in headers:
                _logger.info(
                    "normalize: source header %r not present in DataFrame; skipping",
                    rc.source_header,
                )
                continue
            exprs.append(rc.get_expr(mr_name))

        if extra_columns:
            for src, out in extra_columns.items():
                if src not in headers:
                    warnings.warn(
                        f"extra_columns source {src!r} not in DataFrame columns; skipping",
                        UserWarning,
                        stacklevel=3,
                    )
                    continue
                exprs.append(pl.col(src).alias(out))

        if not exprs:
            return df

        out = df.select(exprs)
        out_cols = set((out.collect_schema() if isinstance(out, pl.LazyFrame) else out.schema).names())
        missing = [s.label for mr, s in COLUMN_ONTOLOGY if s.required and s.label not in out_cols]
        if missing:
            warnings.warn(
                f"normalize: required BDF columns missing from output: {missing}",
                UserWarning,
                stacklevel=3,
            )
        return out


# ---------------------------------------------------------------------------
# Built-in vendor normalizers
#
# Each constant is a mechanics-agnostic header→BDF mapping. ``Plugin``
# entries in ``plugins.py`` reference these by key; one normalizer can back
# several file formats (e.g. ``"neware"`` backs both the CSV and XLSX sources).
# ---------------------------------------------------------------------------

_ARBIN_DT_FMTS = ("%m/%d/%Y %H:%M:%S%.f", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S")
_DIGATRON_DT_FMTS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")
_MACCOR_DT_FMTS = ("%d-%b-%y %I:%M:%S %p", "%d-%b-%y %H:%M:%S", "%Y-%m-%d %H:%M:%S")
_NEWARE_DT_FMTS = ("%Y-%m-%d %H:%M:%S%.f", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S")

ARBIN = TableNormalizer(
    test_time_second=(Syn("test time ({unit})"),),
    voltage_volt=(Syn("voltage ({unit})"),),
    current_ampere=(Syn("current ({unit})"),),
    unix_time_second=(DateTimeSyn(syn=Syn("date time"), fmts=_ARBIN_DT_FMTS),),
    cycle_count=(Syn("cycle index"),),
    step_count=(Syn("step index"),),
    step_index=(Syn("data point"),),
    step_time_second=(Syn("step time ({unit})"),),
    ambient_temperature_celsius=(Syn("aux_temperature_1 ({unit})"),),
    charging_capacity_ah=(Syn("charge capacity ({unit})"),),
    discharging_capacity_ah=(Syn("discharge capacity ({unit})"),),
    charging_energy_wh=(Syn("charge energy ({unit})"),),
    discharging_energy_wh=(Syn("discharge energy ({unit})"),),
    power_watt=(Syn("power ({unit})"),),
    internal_resistance_ohm=(
        Syn("internal resistance ({unit})"),
        Syn("acr ({unit})"),
    ),
)

BASYTEC = TableNormalizer(
    test_time_second=(
        Syn("time[{unit}]"),
        Syn("time"),
        DateTimeSyn(syn=Syn("time[h:min:s]"), fmts=("%H:%M:%S.%f",)),
    ),
    voltage_volt=(
        Syn("u[{unit}]"),
        Syn("voltage[{unit}]"),
        Syn("u"),
        Syn("voltage"),
    ),
    current_ampere=(
        Syn("i[{unit}]"),
        Syn("current[{unit}]"),
        Syn("i"),
        Syn("current"),
    ),
    ambient_temperature_celsius=(
        Syn("t1[{unit}]"),
        Syn("t1[°C]"),
        Syn("temp[{unit}]"),
        Syn("temp[°C]"),
        Syn("temperature[{unit}]"),
        Syn("temperature[°C]"),
    ),
    net_capacity_ah=(Syn("ah[{unit}]"),),
    step_index=(Syn("line"),),
)

BIOLOGIC = TableNormalizer(
    test_time_second=(
        Syn("time/{unit}"),
        Syn("time / {unit}"),
        Syn("t ({unit})"),
        Syn("time [{unit}]"),
        Syn("relative time({unit})"),
    ),
    voltage_volt=(
        Syn("ewe/{unit}"),
        Syn("ecell/{unit}"),
        Syn("u/{unit}"),
        Syn("u[{unit}]"),
        Syn("ewe ({unit})"),
        Syn("<ewe>/{unit}"),
    ),
    current_ampere=(
        Syn("i[{unit}]"),
        Syn("current / {unit}"),
        Syn("current({unit})"),
        Syn("i({unit})"),
        Syn("i/{unit}"),
        Syn("<i>/{unit}"),
    ),
    cycle_count=(Syn("cycle number"), Syn("z cycle")),
    step_index=(Syn("Ns"),),
    step_time_second=(Syn("step time/{unit}"),),
    ambient_temperature_celsius=(
        Syn("temperature/{unit}"),
        Syn("temperature/°C"),
        Syn("temperature/\xf8c"),
        Syn("temperature/c"),
        Syn("temp/{unit}"),
        Syn("temp/°C"),
        Syn("temp/\xf8c"),
        Syn("temp/c"),
        Syn("t/{unit}"),
        Syn("t/°C"),
        Syn("t/\xf8c"),
        Syn("t/c"),
    ),
    charging_capacity_ah=(
        Syn("q charge/{unit}"),
        Syn("q charge /{unit}"),
    ),
    discharging_capacity_ah=(
        Syn("q discharge/{unit}"),
        Syn("q discharge /{unit}"),
    ),
    step_capacity_ah=(Syn("dq/{unit}"),),
    cumulative_capacity_ah=(
        Syn("(q-qo)/{unit}"),
        Syn("capacity/{unit}"),
    ),
    charging_energy_wh=(Syn("energy charge/{unit}"),),
    discharging_energy_wh=(Syn("energy discharge/{unit}"),),
    cumulative_energy_wh=(Syn("|energy|/{unit}"),),
    power_watt=(Syn("p/{unit}"),),
    internal_resistance_ohm=(Syn("r/{unit}"),),
)

DIGATRON = TableNormalizer(
    test_time_second=(
        Syn("program duration#{unit}"),
        Syn("prog time"),
        Syn("program time"),
    ),
    voltage_volt=(Syn("voltage#{unit}"), Syn("voltage")),
    current_ampere=(Syn("current#{unit}"), Syn("current")),
    unix_time_second=(DateTimeSyn(syn=Syn("timestamp"), fmts=_DIGATRON_DT_FMTS),),
    cycle_count=(Syn("cycle"),),
    step_index=(Syn("step"),),
    step_time_second=(Syn("step time"),),
    charging_capacity_ah=(Syn("AhCha#{unit}"),),
    discharging_capacity_ah=(Syn("AhDch#{unit}"),),
    step_capacity_ah=(Syn("AhStep#{unit}"),),
    net_capacity_ah=(Syn("AhBal#{unit}"),),
    cumulative_capacity_ah=(Syn("AhAccu#{unit}"), Syn("AhAccu")),
    charging_energy_wh=(Syn("WhCha#{unit}"),),
    discharging_energy_wh=(Syn("WhDch#{unit}"),),
    step_energy_wh=(Syn("WhStep#{unit}"),),
    cumulative_energy_wh=(Syn("WhAccu#{unit}"), Syn("WhAccu")),
    power_watt=(Syn("watt"), Syn("power#{unit}")),
    temperature_t1_celsius=(Syn("t1#{unit}"), Syn("logtemp001")),
)

LANDT_CSV = TableNormalizer(
    test_time_second=(Syn("test_time_s"),),
    voltage_volt=(Syn("voltage_v"),),
    current_ampere=(Syn("current_a"),),
    cycle_count=(Syn("cycle_index"),),
    step_count=(Syn("step_index"),),
    step_time_second=(Syn("step_time_s"),),
)

LANDT_TXT = TableNormalizer(
    test_time_second=(
        Syn("test({unit})"),
        Syn("test ({unit})"),
        Syn("test_time_s"),
        Syn("test time ({unit})"),
        Syn("test time"),
    ),
    voltage_volt=(
        Syn("volts"),
        Syn("volt"),
        Syn("voltage"),
        Syn("V"),
    ),
    current_ampere=(
        Syn("amps"),
        Syn("amp"),
        Syn("current"),
        Syn("A"),
        Syn("i({unit})"),
    ),
    cycle_count=(
        Syn("cycle"),
        Syn("cycle#"),
        Syn("cycle index"),
    ),
    step_count=(
        Syn("step"),
        Syn("step#"),
        Syn("step index"),
    ),
    step_index=(
        Syn("rec#"),
        Syn("record"),
        Syn("record#"),
    ),
    step_time_second=(
        Syn("dpt-time"),
        Syn("dpt time"),
        Syn("step time ({unit})"),
        Syn("step_time_s"),
    ),
)

MACCOR = TableNormalizer(
    test_time_second=(
        Syn("test time ({unit})"),
        Syn("test time({unit})"),
    ),
    voltage_volt=(Syn("voltage"),),
    current_ampere=(Syn("current"),),
    unix_time_second=(DateTimeSyn(syn=Syn("dpt time"), fmts=_MACCOR_DT_FMTS),),
    cycle_count=(Syn("cycle c"),),
    step_count=(Syn("step"),),
    step_time_second=(Syn("step time ({unit})"),),
    ambient_temperature_celsius=(Syn("temp 1"),),
    net_capacity_ah=(Syn("capacity"),),
    net_energy_wh=(Syn("energy"),),
)

NEWARE = TableNormalizer(
    test_time_second=(
        Syn("total time({unit})"),
        Syn("test time({unit})"),
        Syn("totaltime({unit})"),
        Syn("totaltime_s"),
        Syn("total time"),
        Syn("总时间({unit})"),
        Syn("测试时间({unit})"),
    ),
    voltage_volt=(
        Syn("voltage({unit})"),
        Syn("电压({unit})"),
        Syn("voltage [{unit}]"),
    ),
    current_ampere=(
        Syn("current({unit})"),
        Syn("电流({unit})"),
        Syn("current [{unit}]"),
    ),
    unix_time_second=(
        DateTimeSyn(syn=Syn("date"), fmts=_NEWARE_DT_FMTS),
        DateTimeSyn(syn=Syn("datetime"), fmts=_NEWARE_DT_FMTS),
        DateTimeSyn(syn=Syn("date_time"), fmts=_NEWARE_DT_FMTS),
    ),
    cycle_count=(Syn("cycle"),),
    step_count=(Syn("step"),),
    step_index=(Syn("record"),),
    step_time_second=(
        Syn("time({unit})"),
        Syn("relative time({unit})"),
        Syn("state time({unit})"),
        Syn("steptime({unit})"),
        Syn("step time({unit})"),
        Syn("steptime_s"),
        Syn("时间({unit})"),
    ),
    ambient_temperature_celsius=(
        Syn("temperature(°c)"),
        Syn("温度(°c)"),
    ),
    charging_capacity_ah=(
        Syn("charge capacity({unit})"),
        Syn("chg.capacity({unit})"),
    ),
    discharging_capacity_ah=(
        Syn("discharge capacity({unit})"),
        Syn("dchg.capacity({unit})"),
    ),
)

NOVONIX = TableNormalizer(
    test_time_second=(
        Syn("run time ({unit})"),
        Syn("run-time ({unit})"),
        Syn("runtime ({unit})"),
        Syn("test time ({unit})"),
        Syn("testtime({unit})"),
    ),
    voltage_volt=(
        Syn("potential ({unit})"),
        Syn("voltage ({unit})"),
        Syn("cell voltage ({unit})"),
    ),
    current_ampere=(
        Syn("current ({unit})"),
        Syn("cell current ({unit})"),
    ),
    unix_time_second=(
        Syn("unix time ({unit})"),
        Syn("unixtime ({unit})"),
        DateTimeSyn(syn=Syn("date and time"), fmts=("%Y-%m-%d %H:%M:%S",)),
    ),
    cycle_count=(
        Syn("cycle number"),
        Syn("cycle"),
        Syn("cycle #"),
        Syn("cycle#"),
    ),
    step_count=(
        Syn("step number"),
        Syn("step #"),
        Syn("step#"),
    ),
    step_index=(Syn("step position"),),
    step_time_second=(Syn("step time ({unit})"), Syn("steptime({unit})")),
    ambient_temperature_celsius=(
        Syn("temperature (°c)"),
        Syn("temperature (c)"),
        Syn("ambient temperature (°c)"),
        Syn("ambient temperature (c)"),
        Syn("ambient temp (°c)"),
        Syn("ambient temp (c)"),
    ),
    temperature_t1_celsius=(
        Syn("circuit temperature (°c)"),
        Syn("circuit temperature (c)"),
        Syn("circuit temp (°c)"),
        Syn("circuit temp (c)"),
    ),
    net_capacity_ah=(
        Syn("capacity ({unit})"),
        Syn("net capacity ({unit})"),
    ),
    net_energy_wh=(Syn("energy ({unit})"), Syn("net energy ({unit})")),
    power_watt=(Syn("power({unit})"), Syn("power ({unit})")),
)

NORMALIZERS: dict[str, TableNormalizer] = {
    "arbin": ARBIN,
    "basytec": BASYTEC,
    "biologic": BIOLOGIC,
    "digatron": DIGATRON,
    "landt_csv": LANDT_CSV,
    "landt_txt": LANDT_TXT,
    "maccor": MACCOR,
    "neware": NEWARE,
    "novonix": NOVONIX,
}


def detect_normalizer(
    column_names: list[str],
    normalizers: "Sequence[TableNormalizer]",
) -> "TableNormalizer | None":
    """Return the highest-scoring normalizer for ``column_names``, or ``None`` if all score zero."""
    scored = {n: n.score_columns(column_names) for n in normalizers}
    best_score = max(scored.values(), default=0)
    if best_score == 0:
        return None
    return max(scored, key=scored.__getitem__)


def normalize(
    df: pl.DataFrame | pl.LazyFrame | pd.DataFrame,
    *,
    include_optional: bool = True,
    normalizer: "TableNormalizer | dict[str, str] | None" = None,
    extra_columns: dict[str, str] | None = None,
) -> pl.DataFrame | pl.LazyFrame | pd.DataFrame:
    """Map vendor columns to BDF canonical names with unit conversion and dtype casting.

    Accepts ``pl.DataFrame``, ``pl.LazyFrame``, or ``pandas.DataFrame``. Return type matches input.

    Pass ``normalizer`` to use explicit normalisation instructions. When omitted, a
    built-in :class:`TableNormalizer` is detected from the column headers by scoring over
    :data:`NORMALIZERS`.
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
        if best is None and not extra_columns:
            return df
        norm = best if best is not None else TableNormalizer()

    return norm.normalize(
        df,
        include_optional=include_optional,
        extra_columns=extra_columns,
    )


def canonicalize_legacy_labels(df):
    """Rename legacy BDF column labels (notation/deprecated prefLabels) to current preferred labels.

    Returns (df_renamed, had_legacy) where had_legacy is True if any renaming occurred.
    Works on pandas DataFrames (the BDF artifact loading path).
    """
    import re

    from .ontology_labels import load_alias_index
    from .units import parse_from_header

    _slug_re = re.compile(r"[^a-z0-9]+")

    def _slugify(text: str) -> str:
        return _slug_re.sub("-", text.lower()).strip("-")

    alias_idx = load_alias_index()

    notation_to_canonical: dict[str, str] = {}
    deprecated_pref_to_canonical: dict[str, str] = {}
    base_preferred: dict[str, str] = {}
    for q, s in COLUMN_ONTOLOGY:
        if s.deprecated:
            continue
        base = s.label.split(" / ", 1)[0].strip().lower()
        base_preferred.setdefault(base, q)
    for q, s in COLUMN_ONTOLOGY:
        pref = s.label
        target_q = q
        if s.deprecated:
            base = pref.split(" / ", 1)[0].strip().lower()
            target_q = base_preferred.get(base, q)
            deprecated_pref_to_canonical[pref] = getattr(COLUMN_ONTOLOGY, target_q).label
        notation_to_canonical[s.effective_notation] = getattr(COLUMN_ONTOLOGY, target_q).label

    allowed = {s.label for _, s in COLUMN_ONTOLOGY if not s.deprecated}
    renames: dict[str, str] = {}
    for col in df.columns:
        if col in allowed:
            continue
        canonical = deprecated_pref_to_canonical.get(str(col))
        if canonical:
            renames[col] = canonical
            continue
        canonical = notation_to_canonical.get(str(col))
        if canonical:
            renames[col] = canonical
            continue
        base, _unit, _src = parse_from_header(str(col))
        base_slug = _slugify(base.replace("/", " ").replace("#", " "))
        full_slug = _slugify(str(col).replace("/", " ").replace("#", " "))
        alias = alias_idx.get(base_slug) or alias_idx.get(full_slug)
        if alias:
            renames[col] = alias.label

    if renames:
        return df.rename(columns=renames), True
    return df, False


__all__ = [
    "Syn",
    "DateTimeSyn",
    "SynUnion",
    "ResolvedColumn",
    "TableNormalizer",
    "NORMALIZERS",
    "unit_from_label",
    "get_unit_conversion",
    "normalize",
    "detect_normalizer",
    "canonicalize_legacy_labels",
]
