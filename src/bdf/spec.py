# src/bdf/spec.py
from __future__ import annotations

import contextlib
import importlib.resources
import os
import re
import warnings
from pathlib import Path
from typing import Any

import pint
import polars as pl
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

try:
    from rdflib import Graph
    from rdflib.namespace import OWL, RDF, SKOS
except Exception:  # pragma: no cover - rdflib is a project dependency, keep fallback for safety
    Graph = None
    OWL = RDF = SKOS = None

try:
    import requests as _requests
except Exception:  # pragma: no cover
    _requests = None

"""
Single source of truth for BDF canonical columns.

Each entry defines:
- unit: pint-compatible canonical unit
- label: preferred human label (e.g. "Voltage / V"); {unit} placeholder formatted at construction
- required: bool (True for core required, False otherwise)
- mr_name: machine-readable snake name (official)
- iri: canonical IRI (official)
- synonyms: list[str] of base-name slugs mapping vendor headers to this quantity

Notes:
- Slugs are lowercase with non-alnum -> "-" (same slugger as normalizer).
- Synonyms are unit-agnostic ("voltage" not "voltage#v"); the normalizer parses units.
"""

# --------- Constants ----------

_SLUG = re.compile(r"[^a-z0-9]+")
_REQUIRED_DEFAULT = {"test_time_second", "voltage_volt", "current_ampere"}
_UNIT_ALIAS = {
    "celsius": "degC",
    "degree celsius": "degC",
    "degree_celsius": "degC",
    "deg c": "degC",
    "℃": "degC",
}
_WARNED_ONTOLOGY_SOURCES: set[str] = set()
_SLASH_RE = re.compile(r"^\s*(.+?)\s*/\s*(.+)\s*$")
_BDF_LIVE_URL = "https://w3id.org/battery-data-alliance/ontology/battery-data-format"

ureg = pint.UnitRegistry()
for _alias, _canonical in [
    ("degc", "degC"),
    ("degreec", "degC"),
    ("\xf8c", "degC"),
    ("\xf8C", "degC"),
    ("\xb0c", "degC"),
    ("\xb0C", "degC"),
    ("Sec", "second"),
]:
    with contextlib.suppress(Exception):
        ureg.define(f"{_alias} = {_canonical}")


# --------- Helper functions ----------


def _slugify(text: str) -> str:
    """Lowercase and collapse non-alnum runs to '-'.

    Args:
        text: Text to slugify.

    Returns:
        Lowercased text with non-alphanumeric runs replaced by hyphens.
    """
    return _SLUG.sub("-", text.lower()).strip("-")


def _normalize_unit(unit: str) -> str:
    """Map known unit aliases (e.g. 'celsius') to canonical pint strings.

    Args:
        unit: Unit string to normalize.

    Returns:
        Canonical pint unit string.
    """
    key = unit.strip()
    if not key:
        return key
    return _UNIT_ALIAS.get(key.lower(), key)


def parse_label(label: str) -> tuple[str, str] | None:
    """Split 'Base / unit' into (base, normalised_unit), or None if not parseable.

    Args:
        label: BDF label string in format 'Base / unit'.

    Returns:
        Tuple of (base, normalized_unit) or None if not parseable.
    """
    m = _SLASH_RE.match(str(label))
    if m is None:
        return None
    base = m.group(1).strip()
    unit = _normalize_unit(m.group(2).strip())
    if not base or not unit:
        return None
    return base, unit


def get_unit_conversion(src_unit: str | None, dst_unit: str) -> tuple[float, float] | None:
    """Return (scale, offset) for src→dst unit conversion, None if incompatible.

    Args:
        src_unit: Source unit string (or None for dimensionless).
        dst_unit: Destination unit string.

    Returns:
        Tuple of (scale, offset) for conversion, or None if incompatible.
    """
    if src_unit is None or dst_unit in ("1", "", None):
        if (src_unit is None or src_unit.strip() in ("", "1")) and dst_unit in ("1", "", None):
            return (1.0, 0.0)
        return None
    s = src_unit.strip()
    t = dst_unit.strip()
    if s.lower() == t.lower():
        return (1.0, 0.0)
    try:
        qty_t = ureg.Quantity(1, t)
        tgt_units = qty_t.units
        if ureg.Quantity(1, s).dimensionality != qty_t.dimensionality:
            return None
        at_zero = float(ureg.Quantity(0, s).to(tgt_units).magnitude)
        at_one = float(ureg.Quantity(1, s).to(tgt_units).magnitude)
        scale = round(at_one - at_zero, 15)
        offset = round(at_zero, 15)
        return (scale, offset)
    except pint.errors.PintError:
        return None


def unit_from_label(label: str) -> str | None:
    """Return the unit portion of a 'Base / unit' label, or None.

    Args:
        label: BDF label string in format 'Base / unit'.

    Returns:
        Unit string, or None if label is not parseable.
    """
    parsed = parse_label(label)
    return parsed[1] if parsed else None


# --------- Ontology loading helpers ----------


def _pick_labels(graph: Any, subject: Any, predicate: Any) -> list[str]:
    """Extract English string literals for a given subject/predicate pair.

    Args:
        graph: RDFlib graph object.
        subject: RDF subject node.
        predicate: RDF predicate node.

    Returns:
        List of English string literals found for the subject/predicate pair.
    """
    out: list[str] = []
    for lit in graph.objects(subject, predicate):
        try:
            text = str(lit)
        except Exception:
            continue
        if getattr(lit, "language", None) not in (None, "en"):
            continue
        if text:
            out.append(text)
    return out


def _ontology_source() -> str:
    """Return BDF_ONTOLOGY_PATH (or BDF_ONTOLOGY) env var, stripped.

    Returns:
        Value of BDF_ONTOLOGY_PATH env var, or BDF_ONTOLOGY, or empty string.
    """
    return (os.getenv("BDF_ONTOLOGY_PATH") or os.getenv("BDF_ONTOLOGY") or "").strip()


def _warn_ontology_once(source: str, message: str) -> None:
    """Emit a warning for a given source at most once per process.

    Args:
        source: Unique identifier for the warning source.
        message: Warning message to emit.
    """
    if source not in _WARNED_ONTOLOGY_SOURCES:
        _WARNED_ONTOLOGY_SOURCES.add(source)
        warnings.warn(message, stacklevel=2)


def _graph_from_bytes(data: bytes, format: str | None = None) -> Any:
    """Parse bytes into an rdflib Graph; return None on failure.

    Args:
        data: Bytes to parse as RDF.
        format: RDF format (e.g. 'turtle', 'xml'). Auto-detected if None.

    Returns:
        Parsed RDFlib Graph, or None if parsing fails.
    """
    if Graph is None:
        return None
    try:
        g = Graph()
        if format:
            g.parse(data=data, format=format)
        else:
            g.parse(data=data)
        return g
    except Exception:
        return None


def _synonym_slugs(base: str, mr_name: str, alt_labels: list[str], notations: list[str]) -> list[str]:
    """Build sorted list of base-name slugs for all aliases of a quantity.

    Args:
        base: Base name of the quantity.
        mr_name: Machine-readable name.
        alt_labels: Alternative labels from SKOS.
        notations: Notation values from SKOS.

    Returns:
        Sorted list of unique slugified synonyms.
    """
    syns = {_slugify(base), _slugify(mr_name)}
    for label in alt_labels + notations:
        base_part = label.split(" / ", 1)[0].strip()
        # Normalize word separators before slugifying
        slug = _slugify(base_part.replace("/", " ").replace("#", " ").replace("_", " "))
        if slug:
            syns.add(slug)
    return sorted(s for s in syns if s)


def _parse_graph(g: Any) -> dict[str, dict[str, Any]]:
    """Extract quantity dicts from an rdflib OWL graph keyed by mr_name.

    Args:
        g: Parsed RDFlib graph object.

    Returns:
        Dictionary mapping mr_name to quantity metadata dicts.
    """
    out: dict[str, dict[str, Any]] = {}
    for subject in g.subjects(RDF.type, OWL.Class):
        iri = str(subject)
        if "#" not in iri:
            continue
        mr_name = iri.rsplit("#", 1)[-1]
        pref_labels = _pick_labels(g, subject, SKOS.prefLabel)
        if not pref_labels:
            continue
        parsed = parse_label(pref_labels[0])
        if not parsed:
            continue
        base, unit = parsed
        deprecated = next(
            (str(lit).lower() == "true" for lit in g.objects(subject, OWL.deprecated)),
            False,
        )
        alt_labels = _pick_labels(g, subject, SKOS.altLabel)
        notations = _pick_labels(g, subject, SKOS.notation)
        notation = next((s for n in notations if (s := str(n).strip())), mr_name)
        out[mr_name] = {
            "unit": unit,
            "label_template": f"{base} / {{unit}}" if unit != "1" else f"{base} / 1",
            "required": mr_name in _REQUIRED_DEFAULT,
            "mr_name": mr_name,
            "notation": notation,
            "iri": iri,
            "deprecated": deprecated,
            "synonyms": _synonym_slugs(base, mr_name, alt_labels, notations),
        }
    return out


def _merge_columns(
    base: dict[str, dict[str, Any]],
    ontology: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Merge ontology entries into base, preferring non-deprecated ontology values.

    Args:
        base: Baseline quantity definitions.
        ontology: Ontology-loaded quantity definitions.

    Returns:
        Merged dictionary with ontology values preferred over baseline.
    """
    for quantity, item in ontology.items():
        if quantity not in base:
            base[quantity] = {**item, "deprecated": item.get("deprecated", False)}
            continue
        current = base[quantity]
        incoming_deprecated = bool(item.get("deprecated"))
        # Deprecated ontology entries are read-compat only: don't redefine canonical labels/units.
        if not incoming_deprecated:
            current["unit"] = item.get("unit", current["unit"])
            current["label_template"] = item.get("label_template", current["label_template"])
            current["iri"] = item.get("iri", current["iri"])
        current["mr_name"] = quantity
        current["notation"] = item.get("notation", current.get("notation", quantity))
        current["deprecated"] = current.get("deprecated", False) or incoming_deprecated
        syns = set(current.get("synonyms", [])) | set(item.get("synonyms", []))
        current["synonyms"] = sorted(s for s in syns if s)
    return base


def _load_with_priority(baseline: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Resolve ontology: env var → live URL → snapshot → static baseline.

    Args:
        baseline: Static baseline quantity definitions.

    Returns:
        Quantity definitions with ontology merged in priority order.
    """
    # Shallow copy; synonyms lists are the only mutable nested values
    base = {k: {**v, "synonyms": list(v["synonyms"])} for k, v in baseline.items()}

    # 1. Env var (warns on explicit failure)
    source = _ontology_source()
    if source:
        if Graph is None:
            return base
        try:
            g = Graph()
            g.parse(source)
        except Exception as exc:
            _warn_ontology_once(source, f"Failed to load ontology spec from '{source}': {exc}")
            return base
        ontology = _parse_graph(g)
        return _merge_columns(base, ontology) if ontology else base

    # 2. Live URL (silent fail → next step)
    if _requests is not None and Graph is not None:
        with contextlib.suppress(Exception):
            resp = _requests.get(_BDF_LIVE_URL, timeout=5)
            resp.raise_for_status()
            g = _graph_from_bytes(resp.content)
            if g is not None:
                ontology = _parse_graph(g)
                if ontology:
                    return _merge_columns(base, ontology)

    # 3. Bundled snapshot (silent fail → next step)
    with contextlib.suppress(Exception):
        ref = importlib.resources.files("bdf.data").joinpath("bdf-ontology-snapshot.ttl")
        g = _graph_from_bytes(ref.read_bytes(), format="turtle")
        if g is not None:
            ontology = _parse_graph(g)
            if ontology:
                return _merge_columns(base, ontology)

    # 4. Static baseline
    return base


# --------- Pydantic models ----------


class Quantity(BaseModel):
    """One BDF physical quantity: unit, human label, and lookup metadata."""

    unit: str
    label_template: str
    dtype: str = "float"
    required: bool = False
    mr_name: str
    iri: str
    synonyms: list[str]
    deprecated: bool = False
    notation: str = ""

    @model_validator(mode="before")
    @classmethod
    def _resolve_label_and_dtype(cls, data: Any) -> Any:
        if isinstance(data, dict):
            unit = data.get("unit", "")
            label_template = data.get("label_template", "")
            if unit != "1" and "{unit}" not in label_template and " / " in label_template:
                base = label_template.split(" / ", 1)[0]
                data["label_template"] = f"{base} / {{unit}}"
            elif unit == "1" and "{unit}" in label_template:
                data["label_template"] = label_template.replace("{unit}", "1")
            if "dtype" not in data:
                data["dtype"] = "int" if unit == "1" else "float"
        return data

    @field_validator("dtype")
    @classmethod
    def _validate_dtype(cls, v: str) -> str:
        if v not in ("int", "float"):
            raise ValueError(f"dtype must be 'int' or 'float', got {v!r}")
        return v

    @property
    def formatted_label(self) -> str:
        """Return the label with {unit} replaced by the actual unit string."""
        return self.label_template.format(unit=self.unit) if "{unit}" in self.label_template else self.label_template

    def unit_conversion(self, dst_unit: str) -> tuple[float, float] | None:
        """Return (scale, offset) to convert self.unit → dst_unit, or None.

        Args:
            dst_unit: Destination unit string.

        Returns:
            Tuple of (scale, offset) for conversion, or None if incompatible.
        """
        return get_unit_conversion(self.unit, dst_unit)

    @property
    def effective_notation(self) -> str:
        """notation field if set, otherwise mr_name."""
        return (self.notation or self.mr_name).strip() or self.mr_name


_IRI = "https://w3id.org/battery-data-alliance/ontology/battery-data-format#"


class ColumnOntology(BaseModel):
    """Registry of all BDF canonical quantities. Iterate as (mr_name, Quantity) pairs."""

    model_config = ConfigDict(extra="allow")

    # -------------------------
    # Required quantities
    # -------------------------
    test_time_second: Quantity = Quantity(
        unit="s",
        label_template="Test Time / {unit}",
        required=True,
        mr_name="test_time_second",
        iri=f"{_IRI}test_time_second",
        synonyms=["test-time", "time", "program-duration", "elapsed-time"],
    )
    voltage_volt: Quantity = Quantity(
        unit="V",
        label_template="Voltage / {unit}",
        required=True,
        mr_name="voltage_volt",
        iri=f"{_IRI}voltage_volt",
        synonyms=["voltage", "u", "cell-voltage"],
    )
    current_ampere: Quantity = Quantity(
        unit="A",
        label_template="Current / {unit}",
        required=True,
        mr_name="current_ampere",
        iri=f"{_IRI}current_ampere",
        synonyms=["current", "i", "cell-current"],
    )
    # -------------------------
    # Recommended quantities
    # -------------------------
    unix_time_second: Quantity = Quantity(
        unit="s",
        label_template="Unix Time / {unit}",
        required=False,
        mr_name="unix_time_second",
        iri=f"{_IRI}unix_time_second",
        synonyms=["unix-time", "timestamp", "date-time", "datetime"],
    )
    cycle_count: Quantity = Quantity(
        unit="1",
        label_template="Cycle Count / 1",
        required=False,
        mr_name="cycle_count",
        iri=f"{_IRI}cycle_count",
        synonyms=["cycle", "cycle-index", "cycle-no", "cycle-number"],
    )
    step_count: Quantity = Quantity(
        unit="1",
        label_template="Step Count / 1",
        required=False,
        mr_name="step_count",
        iri=f"{_IRI}step_count",
        synonyms=["step", "step-no", "step-number", "step-id"],
    )
    ambient_temperature_celsius: Quantity = Quantity(
        unit="degC",
        label_template="Ambient Temperature / {unit}",
        required=False,
        mr_name="ambient_temperature_celsius",
        iri=f"{_IRI}ambient_temperature_celsius",
        synonyms=["ambient-temperature", "temperature", "tenv", "env-temp", "chamber-temp"],
    )
    # -------------------------
    # Optional quantities
    # -------------------------
    step_index: Quantity = Quantity(
        unit="1",
        label_template="Step Index / 1",
        required=False,
        mr_name="step_index",
        iri=f"{_IRI}step_index",
        synonyms=["step-index", "point-index", "sample-index"],
    )
    charging_capacity_ah: Quantity = Quantity(
        unit="Ah",
        label_template="Charging Capacity / {unit}",
        required=False,
        mr_name="charging_capacity_ah",
        iri=f"{_IRI}charging_capacity_ah",
        synonyms=["ahcha", "charge-capacity", "capacity-charge"],
    )
    discharging_capacity_ah: Quantity = Quantity(
        unit="Ah",
        label_template="Discharging Capacity / {unit}",
        required=False,
        mr_name="discharging_capacity_ah",
        iri=f"{_IRI}discharging_capacity_ah",
        synonyms=["ahdch", "discharge-capacity", "capacity-discharge"],
    )
    step_capacity_ah: Quantity = Quantity(
        unit="Ah",
        label_template="Step Capacity / {unit}",
        required=False,
        mr_name="step_capacity_ah",
        iri=f"{_IRI}step_capacity_ah",
        synonyms=["ahstep", "capacity-step"],
    )
    net_capacity_ah: Quantity = Quantity(
        unit="Ah",
        label_template="Net Capacity / {unit}",
        required=False,
        mr_name="net_capacity_ah",
        iri=f"{_IRI}net_capacity_ah",
        synonyms=["net-capacity", "capacity-net"],
    )
    cumulative_capacity_ah: Quantity = Quantity(
        unit="Ah",
        label_template="Cumulative Capacity / {unit}",
        required=False,
        mr_name="cumulative_capacity_ah",
        iri=f"{_IRI}cumulative_capacity_ah",
        synonyms=["ahaccu", "ahbal", "capacity-accumulated", "accumulated-capacity", "total-capacity"],
    )
    charging_energy_wh: Quantity = Quantity(
        unit="Wh",
        label_template="Charging Energy / {unit}",
        required=False,
        mr_name="charging_energy_wh",
        iri=f"{_IRI}charging_energy_wh",
        synonyms=["whcha", "energy-charge"],
    )
    discharging_energy_wh: Quantity = Quantity(
        unit="Wh",
        label_template="Discharging Energy / {unit}",
        required=False,
        mr_name="discharging_energy_wh",
        iri=f"{_IRI}discharging_energy_wh",
        synonyms=["whdch", "energy-discharge"],
    )
    step_energy_wh: Quantity = Quantity(
        unit="Wh",
        label_template="Step Energy / {unit}",
        required=False,
        mr_name="step_energy_wh",
        iri=f"{_IRI}step_energy_wh",
        synonyms=["whstep", "energy-step"],
    )
    net_energy_wh: Quantity = Quantity(
        unit="Wh",
        label_template="Net Energy / {unit}",
        required=False,
        mr_name="net_energy_wh",
        iri=f"{_IRI}net_energy_wh",
        synonyms=["net-energy", "energy-net"],
    )
    cumulative_energy_wh: Quantity = Quantity(
        unit="Wh",
        label_template="Cumulative Energy / {unit}",
        required=False,
        mr_name="cumulative_energy_wh",
        iri=f"{_IRI}cumulative_energy_wh",
        synonyms=["whaccu", "energy-accumulated", "accumulated-energy", "total-energy"],
    )
    power_watt: Quantity = Quantity(
        unit="W",
        label_template="Power / {unit}",
        required=False,
        mr_name="power_watt",
        iri=f"{_IRI}power_watt",
        synonyms=["power", "pwr"],
    )
    internal_resistance_ohm: Quantity = Quantity(
        unit="ohm",
        label_template="Internal Resistance / {unit}",
        required=False,
        mr_name="internal_resistance_ohm",
        iri=f"{_IRI}internal_resistance_ohm",
        synonyms=["internal-resistance", "rint", "ir", "dcir", "ohmic-resistance", "resistance"],
    )
    ambient_pressure_pa: Quantity = Quantity(
        unit="Pa",
        label_template="Ambient Pressure / {unit}",
        required=False,
        mr_name="ambient_pressure_pa",
        iri=f"{_IRI}ambient_pressure_pa",
        synonyms=["ambient-pressure", "pamb", "baro-pressure"],
    )
    applied_pressure_pa: Quantity = Quantity(
        unit="Pa",
        label_template="Applied Pressure / {unit}",
        required=False,
        mr_name="applied_pressure_pa",
        iri=f"{_IRI}applied_pressure_pa",
        synonyms=["applied-pressure", "press", "papp"],
    )
    # Surface temperatures (T1..T5)
    temperature_t1_celsius: Quantity = Quantity(
        unit="degC",
        label_template="Surface Temperature T1 / {unit}",
        required=False,
        mr_name="temperature_t1_celsius",
        iri=f"{_IRI}temperature_t1_celsius",
        synonyms=["t1", "surface-temperature-t1"],
    )
    temperature_t2_celsius: Quantity = Quantity(
        unit="degC",
        label_template="Surface Temperature T2 / {unit}",
        required=False,
        mr_name="temperature_t2_celsius",
        iri=f"{_IRI}temperature_t2_celsius",
        synonyms=["t2", "surface-temperature-t2"],
    )
    temperature_t3_celsius: Quantity = Quantity(
        unit="degC",
        label_template="Surface Temperature T3 / {unit}",
        required=False,
        mr_name="temperature_t3_celsius",
        iri=f"{_IRI}temperature_t3_celsius",
        synonyms=["t3", "surface-temperature-t3"],
    )
    temperature_t4_celsius: Quantity = Quantity(
        unit="degC",
        label_template="Surface Temperature T4 / {unit}",
        required=False,
        mr_name="temperature_t4_celsius",
        iri=f"{_IRI}temperature_t4_celsius",
        synonyms=["t4", "surface-temperature-t4"],
    )
    temperature_t5_celsius: Quantity = Quantity(
        unit="degC",
        label_template="Surface Temperature T5 / {unit}",
        required=False,
        mr_name="temperature_t5_celsius",
        iri=f"{_IRI}temperature_t5_celsius",
        synonyms=["t5", "surface-temperature-t5"],
    )

    def model_post_init(self, __context: Any) -> None:
        if self.model_extra:
            coerced = {k: Quantity.model_validate(v) if isinstance(v, dict) else v for k, v in self.model_extra.items()}
            object.__setattr__(self, "__pydantic_extra__", coerced)

    def base_synonym_index(self) -> dict[str, str]:
        """Build a mapping from base-name slug to quantity key (machine-readable name).

        Returns:
            Dictionary mapping base-name slugs to mr_name keys.
        """
        idx: dict[str, str] = {}
        for q_name, q in self:
            if q.deprecated:
                continue
            left = q.label_template.split(" / ", 1)[0]
            left_slug = _slugify(left)
            if left_slug:
                idx.setdefault(left_slug, q_name)
            notation_slug = _slugify(q.effective_notation)
            if notation_slug:
                idx.setdefault(notation_slug, q_name)
            for base in q.synonyms:
                slug = _slugify(str(base))
                if slug:
                    idx.setdefault(slug, q_name)
        return idx

    def required_labels(self) -> tuple[str, ...]:
        """Labels of all non-deprecated required quantities.

        Returns:
            Tuple of formatted labels for required quantities.
        """
        return tuple(q.formatted_label for _, q in self if q.required and not q.deprecated)

    def optional_labels(self) -> tuple[str, ...]:
        """Labels of all non-deprecated optional quantities.

        Returns:
            Tuple of formatted labels for optional quantities.
        """
        return tuple(q.formatted_label for _, q in self if not q.required and not q.deprecated)

    def validate(self, df: pl.DataFrame | pl.LazyFrame) -> None:
        """Check ``df`` column names against BDF canonical labels.

        Raises ``BDFValidationError`` if required columns are absent.
        Warns (``UserWarning``) if extra non-BDF columns are present.
        Passes silently when all columns are canonical BDF labels.
        """

        cols = set(df.collect_schema().names()) if isinstance(df, pl.LazyFrame) else set(df.columns)

        canonical = {q.formatted_label for _, q in self if not q.deprecated}
        required = {q.formatted_label for _, q in self if q.required and not q.deprecated}

        missing = required - cols
        if missing:
            from bdf.validate import BDFValidationError  # lazy — validate imports spec

            raise BDFValidationError(f"Missing required BDF columns: {sorted(missing)}")

        extra = cols - canonical
        if extra:
            warnings.warn(f"Non-BDF columns present: {sorted(extra)}", UserWarning, stacklevel=2)

    def mr_name_from_label(self, label: str) -> str | None:
        """Return the mr_name whose label_template base matches label, or None.

        Args:
            label: BDF label in format 'Base / unit'.

        Returns:
            Machine-readable name (mr_name) if found, None otherwise.
        """
        parsed = parse_label(label)
        if parsed is None:
            return None
        query_base = parsed[0].lower()
        for mr_name, q in self:
            tmpl_base = q.label_template.split(" / ")[0].strip().lower()
            if tmpl_base == query_base:
                return mr_name
        return None

    @classmethod
    def build(cls) -> "ColumnOntology":
        """Load ontology (env var → live → snapshot → static) and return a new ColumnOntology.

        Returns:
            New ColumnOntology instance with ontology merged in priority order.
        """
        baseline = {k: v.model_dump() for k, v in cls()}
        return cls(**_load_with_priority(baseline))


# --------- Module-level singleton ----------

COLUMN_ONTOLOGY: ColumnOntology = ColumnOntology.build()


__all__ = [
    "ColumnOntology",
    "Quantity",
    "COLUMN_ONTOLOGY",
    "ureg",
    "parse_label",
    "unit_from_label",
    "get_unit_conversion",
]


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="BDF spec utilities")
    parser.add_argument(
        "--capture-snapshot", action="store_true", help="Fetch live ontology and write bundled snapshot"
    )
    args = parser.parse_args()

    if args.capture_snapshot:
        if Graph is None:
            print("rdflib not available", file=sys.stderr)
            sys.exit(1)
        if _requests is None:
            print("requests not available", file=sys.stderr)
            sys.exit(1)
        print(f"Fetching {_BDF_LIVE_URL} ...", file=sys.stderr)
        try:
            resp = _requests.get(_BDF_LIVE_URL, timeout=30)
            resp.raise_for_status()
            g = _graph_from_bytes(resp.content)
            if g is None:
                raise ValueError("failed to parse ontology graph")
        except Exception as exc:
            print(f"Failed: {exc}", file=sys.stderr)
            sys.exit(1)
        serialized = g.serialize(format="turtle")
        out_path = Path(__file__).parent / "data" / "bdf-ontology-snapshot.ttl"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(serialized, bytes):
            out_path.write_bytes(serialized)
        else:
            out_path.write_text(serialized, encoding="utf-8")
        print(f"Saved snapshot to {out_path}", file=sys.stderr)
