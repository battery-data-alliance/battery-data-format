"""Normalisation classes, helpers, and the public normalize() entry point."""

from __future__ import annotations

import logging
import re
import warnings
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Iterator

import polars as pl
from pydantic import (
    BaseModel,
    ConfigDict,
    PrivateAttr,
    RootModel,
)

if TYPE_CHECKING:
    import pandas as pd  # noqa: F401

    from bdf.sources import Source  # noqa: F401

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

    syn: Syn
    fmts: tuple[str, ...]


SynUnion = Syn | DateTimeSyn


class ResolvedColumn(BaseModel):
    """Resolved mapping of one source header to one BDF column."""

    model_config = ConfigDict(frozen=True)

    source_header: str
    scale: float = 1.0
    offset: float = 0.0
    datetime_fmts: tuple[str, ...] = ()

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


class MetadataParser(BaseModel):
    """Fixed-field model for BDF-approved metadata extraction from preamble lines."""

    model_config = ConfigDict(frozen=True)

    start_time: str | None = None

    _compiled: dict[str, re.Pattern[str]] = PrivateAttr(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        """Compile each non-None pattern field to regex."""
        for field_name in type(self).model_fields:
            pattern = getattr(self, field_name)
            if pattern is not None:
                self._compiled[field_name] = re.compile(pattern, re.IGNORECASE)

    def parse(self, lines: list[str]) -> dict[str, str]:
        """Apply each non-None pattern to lines; return first match per key."""
        result: dict[str, str] = {}
        for field_name, rx in self._compiled.items():
            for line in lines:
                m = rx.search(line)
                if m:
                    result[field_name] = m.group(1).strip()
                    break
        return result


class Normalizer(BaseModel):
    """Column-mapping model: one optional field per BDF mr_name.

    Fields accept ``list[Syn | DateTimeSyn]`` (synonym-based, for CSV/Excel) or
    ``ResolvedColumn`` (direct, for MAT). Iterating yields ``(mr_name, spec)``
    for non-None fields in declaration order.
    """

    model_config = ConfigDict(frozen=True)

    test_time_second: list[SynUnion] | ResolvedColumn | None = None
    voltage_volt: list[SynUnion] | ResolvedColumn | None = None
    current_ampere: list[SynUnion] | ResolvedColumn | None = None
    unix_time_second: list[SynUnion] | ResolvedColumn | None = None
    cycle_count: list[SynUnion] | ResolvedColumn | None = None
    step_count: list[SynUnion] | ResolvedColumn | None = None
    ambient_temperature_celsius: list[SynUnion] | ResolvedColumn | None = None
    step_index: list[SynUnion] | ResolvedColumn | None = None
    step_time_second: list[SynUnion] | ResolvedColumn | None = None
    charging_capacity_ah: list[SynUnion] | ResolvedColumn | None = None
    discharging_capacity_ah: list[SynUnion] | ResolvedColumn | None = None
    step_capacity_ah: list[SynUnion] | ResolvedColumn | None = None
    net_capacity_ah: list[SynUnion] | ResolvedColumn | None = None
    cumulative_capacity_ah: list[SynUnion] | ResolvedColumn | None = None
    charging_energy_wh: list[SynUnion] | ResolvedColumn | None = None
    discharging_energy_wh: list[SynUnion] | ResolvedColumn | None = None
    step_energy_wh: list[SynUnion] | ResolvedColumn | None = None
    net_energy_wh: list[SynUnion] | ResolvedColumn | None = None
    cumulative_energy_wh: list[SynUnion] | ResolvedColumn | None = None
    power_watt: list[SynUnion] | ResolvedColumn | None = None
    internal_resistance_ohm: list[SynUnion] | ResolvedColumn | None = None
    ambient_pressure_pa: list[SynUnion] | ResolvedColumn | None = None
    applied_pressure_pa: list[SynUnion] | ResolvedColumn | None = None
    temperature_t1_celsius: list[SynUnion] | ResolvedColumn | None = None
    temperature_t2_celsius: list[SynUnion] | ResolvedColumn | None = None
    temperature_t3_celsius: list[SynUnion] | ResolvedColumn | None = None
    temperature_t4_celsius: list[SynUnion] | ResolvedColumn | None = None
    temperature_t5_celsius: list[SynUnion] | ResolvedColumn | None = None

    def __iter__(self) -> Iterator[tuple[str, list[SynUnion] | ResolvedColumn]]:  # type: ignore[override]
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

    def score(self, headers: list[str]) -> int:
        """Count resolved columns whose source header is present in headers."""
        resolved = self.resolve(headers)
        return sum(1 for rc in resolved.values() if rc.source_header in headers)

    def normalize(
        self,
        df: pl.DataFrame | pl.LazyFrame | pd.DataFrame,
        *,
        include_optional: bool = True,
        column_map: dict[str, str] | None = None,
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
                column_map=column_map,
                extra_columns=extra_columns,
            )
            assert isinstance(result, pl.LazyFrame)
            return result.collect().to_pandas()  # type: ignore[union-attr]

        schema = df.collect_schema() if isinstance(df, pl.LazyFrame) else df.schema
        headers = list(schema.names())

        resolved = self.resolve(headers)

        if column_map:
            for bdf_label_key, src_header in column_map.items():
                mr_name, rc = ResolvedColumn.from_column_map(bdf_label_key, src_header)
                resolved[mr_name] = rc

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


def _detect_source(headers: list[str]) -> Source | None:
    """Return best-matching Source for headers, or None if no match."""
    from .sources import REGISTRY  # lazy: avoids circular import (sources → normalizer)

    best: Source | None = None
    best_score = 0
    for n in REGISTRY.values():
        sc = n.score(headers)
        if sc > best_score:
            best = n
            best_score = sc
    return best


def normalize(
    df: pl.DataFrame | pl.LazyFrame | pd.DataFrame,
    source: str | Source | None = None,
    *,
    include_optional: bool = True,
    column_map: dict[str, str] | None = None,
    extra_columns: dict[str, str] | None = None,
) -> pl.DataFrame | pl.LazyFrame | pd.DataFrame:
    """Map vendor columns to BDF canonical names with unit conversion and dtype casting.

    Accepts ``pl.DataFrame``, ``pl.LazyFrame``, or ``pandas.DataFrame``. Return type matches input.
    """
    from .sources import Source, get_normalizer  # lazy: avoids circular import

    if isinstance(df, (pl.DataFrame, pl.LazyFrame)):
        schema = df.collect_schema() if isinstance(df, pl.LazyFrame) else df.schema
        headers = list(schema.names())
    else:
        headers = list(df.columns)

    src: Source | None
    if isinstance(source, Source):
        src = source
    elif isinstance(source, str):
        src = get_normalizer(source)
    else:
        src = _detect_source(headers)

    if src is None and not column_map and not extra_columns:
        return df

    normalizer: Normalizer = src.normalizer if src is not None else Normalizer()
    return normalizer.normalize(
        df,
        include_optional=include_optional,
        column_map=column_map,
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
    "MetadataParser",
    "Normalizer",
    "unit_from_label",
    "get_unit_conversion",
    "normalize",
    "canonicalize_legacy_labels",
]
