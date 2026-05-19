# src/bdf/spec.py
from __future__ import annotations

import copy
import os
import re
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:
    from rdflib import Graph
    from rdflib.namespace import OWL, RDF, SKOS
except Exception:  # pragma: no cover - rdflib is a project dependency, keep fallback for safety
    Graph = None
    OWL = RDF = SKOS = None

"""
Single source of truth for BDF canonical columns.

Each entry defines:
- unit: pint-compatible canonical unit
- label_template: preferred label, with "{unit}" placeholder
- required: bool (True for core required, False otherwise)
- mr_name: machine-readable snake name (official)
- iri: canonical IRI (official)
- synonyms: list[str] of base-name slugs mapping vendor headers to this quantity

Notes:
- Slugs are lowercase with non-alnum -> "-" (same slugger as normalizer).
- Synonyms are unit-agnostic ("voltage" not "voltage#v"); the normalizer parses units.
"""

_STATIC_COLUMNS = {
    # -------------------------
    # Required quantities
    # -------------------------
    "test_time_second": {
        "unit": "s",
        "label_template": "Test Time / s",
        "required": True,
        "mr_name": "test_time_second",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#test_time_second",
        "synonyms": [
            "test-time",
            "time",
            "program-duration",
            "elapsed-time",
        ],
    },
    "voltage_volt": {
        "unit": "V",
        "label_template": "Voltage / V",
        "required": True,
        "mr_name": "voltage_volt",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#voltage_volt",
        "synonyms": [
            "voltage",
            "u",
            "cell-voltage",
        ],
    },
    "current_ampere": {
        "unit": "A",
        "label_template": "Current / A",
        "required": True,
        "mr_name": "current_ampere",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#current_ampere",
        "synonyms": [
            "current",
            "i",
            "cell-current",
        ],
    },
    # -------------------------
    # Recommended quantities
    # -------------------------
    "unix_time_second": {
        "unit": "s",
        "label_template": "Unix Time / s",
        "required": False,
        "mr_name": "unix_time_second",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#unix_time_second",
        "synonyms": [
            "unix-time",
            "timestamp",
            "date-time",
            "datetime",
        ],
    },
    "cycle_count": {
        "unit": "1",
        "label_template": "Cycle Count / {unit}",
        "required": False,
        "mr_name": "cycle_count",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_count",
        "synonyms": [
            "cycle",
            "cycle-index",
            "cycle-no",
            "cycle-number",
        ],
    },
    "step_count": {
        "unit": "1",
        "label_template": "Step Count / {unit}",
        "required": False,
        "mr_name": "step_count",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_count",
        "synonyms": [
            "step",
            "step-no",
            "step-number",
            "step-id",
        ],
    },
    "ambient_temperature_celsius": {
        "unit": "degC",
        "label_template": "Ambient Temperature / {unit}",
        "required": False,
        "mr_name": "ambient_temperature_celsius",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_temperature_celsius",
        "synonyms": [
            "ambient-temperature",
            "temperature",
            "tenv",
            "env-temp",
            "chamber-temp",
        ],
    },
    # -------------------------
    # Optional quantities
    # -------------------------
    "step_index": {
        "unit": "1",
        "label_template": "Step Index / {unit}",
        "required": False,
        "mr_name": "step_index",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_index",
        "synonyms": [
            "step-index",
            "point-index",
            "sample-index",
        ],
    },
    "charging_capacity_ah": {
        "unit": "Ah",
        "label_template": "Charging Capacity / {unit}",
        "required": False,
        "mr_name": "charging_capacity_ah",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_capacity_ah",
        "synonyms": [
            "ahcha",
            "charge-capacity",
            "capacity-charge",
        ],
    },
    "discharging_capacity_ah": {
        "unit": "Ah",
        "label_template": "Discharging Capacity / {unit}",
        "required": False,
        "mr_name": "discharging_capacity_ah",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_capacity_ah",
        "synonyms": [
            "ahdch",
            "discharge-capacity",
            "capacity-discharge",
        ],
    },
    "step_capacity_ah": {
        "unit": "Ah",
        "label_template": "Step Capacity / {unit}",
        "required": False,
        "mr_name": "step_capacity_ah",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_capacity_ah",
        "synonyms": [
            "ahstep",
            "capacity-step",
        ],
    },
    "net_capacity_ah": {
        "unit": "Ah",
        "label_template": "Net Capacity / {unit}",
        "required": False,
        "mr_name": "net_capacity_ah",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#net_capacity_ah",
        "synonyms": [
            "net-capacity",
            "capacity-net",
        ],
    },
    "cumulative_capacity_ah": {
        "unit": "Ah",
        "label_template": "Cumulative Capacity / {unit}",
        "required": False,
        "mr_name": "cumulative_capacity_ah",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#cumulative_capacity_ah",
        "synonyms": [
            "ahaccu",
            "ahbal",
            "capacity-accumulated",
            "accumulated-capacity",
            "total-capacity",
        ],
    },
    "charging_energy_wh": {
        "unit": "Wh",
        "label_template": "Charging Energy / {unit}",
        "required": False,
        "mr_name": "charging_energy_wh",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_energy_wh",
        "synonyms": [
            "whcha",
            "energy-charge",
        ],
    },
    "discharging_energy_wh": {
        "unit": "Wh",
        "label_template": "Discharging Energy / {unit}",
        "required": False,
        "mr_name": "discharging_energy_wh",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_energy_wh",
        "synonyms": [
            "whdch",
            "energy-discharge",
        ],
    },
    "step_energy_wh": {
        "unit": "Wh",
        "label_template": "Step Energy / {unit}",
        "required": False,
        "mr_name": "step_energy_wh",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_energy_wh",
        "synonyms": [
            "whstep",
            "energy-step",
        ],
    },
    "net_energy_wh": {
        "unit": "Wh",
        "label_template": "Net Energy / {unit}",
        "required": False,
        "mr_name": "net_energy_wh",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#net_energy_wh",
        "synonyms": [
            "net-energy",
            "energy-net",
        ],
    },
    "cumulative_energy_wh": {
        "unit": "Wh",
        "label_template": "Cumulative Energy / {unit}",
        "required": False,
        "mr_name": "cumulative_energy_wh",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#cumulative_energy_wh",
        "synonyms": [
            "whaccu",
            "energy-accumulated",
            "accumulated-energy",
            "total-energy",
        ],
    },
    "power_watt": {
        "unit": "W",
        "label_template": "Power / {unit}",
        "required": False,
        "mr_name": "power_watt",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#power_watt",
        "synonyms": [
            "power",
            "pwr",
        ],
    },
    "internal_resistance_ohm": {
        "unit": "ohm",
        "label_template": "Internal Resistance / {unit}",
        "required": False,
        "mr_name": "internal_resistance_ohm",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#internal_resistance_ohm",
        "synonyms": [
            "internal-resistance",
            "rint",
            "ir",
            "dcir",
            "ohmic-resistance",
            "resistance",
        ],
    },
    "ambient_pressure_pa": {
        "unit": "Pa",
        "label_template": "Ambient Pressure / {unit}",
        "required": False,
        "mr_name": "ambient_pressure_pa",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_pressure_pa",
        "synonyms": [
            "ambient-pressure",
            "pamb",
            "baro-pressure",
        ],
    },
    "applied_pressure_pa": {
        "unit": "Pa",
        "label_template": "Applied Pressure / {unit}",
        "required": False,
        "mr_name": "applied_pressure_pa",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#applied_pressure_pa",
        "synonyms": [
            "applied-pressure",
            "press",
            "papp",
        ],
    },
    # Surface temperatures (T1..T5)
    "temperature_t1_celsius": {
        "unit": "degC",
        "label_template": "Surface Temperature T1 / {unit}",
        "required": False,
        "mr_name": "temperature_t1_celsius",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t1_celsius",
        "synonyms": ["t1", "surface-temperature-t1"],
    },
    "temperature_t2_celsius": {
        "unit": "degC",
        "label_template": "Surface Temperature T2 / {unit}",
        "required": False,
        "mr_name": "temperature_t2_celsius",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t2_celsius",
        "synonyms": ["t2", "surface-temperature-t2"],
    },
    "temperature_t3_celsius": {
        "unit": "degC",
        "label_template": "Surface Temperature T3 / {unit}",
        "required": False,
        "mr_name": "temperature_t3_celsius",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t3_celsius",
        "synonyms": ["t3", "surface-temperature-t3"],
    },
    "temperature_t4_celsius": {
        "unit": "degC",
        "label_template": "Surface Temperature T4 / {unit}",
        "required": False,
        "mr_name": "temperature_t4_celsius",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t4_celsius",
        "synonyms": ["t4", "surface-temperature-t4"],
    },
    "temperature_t5_celsius": {
        "unit": "degC",
        "label_template": "Surface Temperature T5 / {unit}",
        "required": False,
        "mr_name": "temperature_t5_celsius",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t5_celsius",
        "synonyms": ["t5", "surface-temperature-t5"],
    },
}

# --------- Ontology-backed runtime columns ----------

_SLUG = re.compile(r"[^a-z0-9]+")
_REQUIRED_DEFAULT = {"test_time_second", "voltage_volt", "current_ampere"}
_UNIT_ALIAS = {
    "celsius": "degC",
    "degree celsius": "degC",
    "degree_celsius": "degC",
    "deg c": "degC",
    "℃": "degC",
}
_CACHED_SIGNATURE: tuple[str, float | None] | None = None
_CACHED_COLUMNS: dict[str, dict[str, Any]] | None = None
_WARNED_ONTOLOGY_SOURCES: set[str] = set()


def _slugify(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")


def _normalize_unit(unit: str) -> str:
    key = unit.strip()
    if not key:
        return key
    return _UNIT_ALIAS.get(key.lower(), key)


def _split_pref_label(label: str) -> tuple[str, str] | None:
    text = str(label).strip()
    if " / " not in text:
        return None
    base, unit = text.split(" / ", 1)
    base = base.strip()
    unit = _normalize_unit(unit.strip())
    if not base or not unit:
        return None
    return base, unit


def _pick_labels(graph: Graph, subject, predicate) -> list[str]:
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
    return (os.getenv("BDF_ONTOLOGY_PATH") or os.getenv("BDF_ONTOLOGY") or "").strip()


def _source_signature(source: str) -> tuple[str, float | None]:
    if not source:
        return "", None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        p = Path(source).expanduser()
    if p.exists():
        return str(p.resolve()), p.stat().st_mtime
    return source, None


def _warn_ontology_once(source: str, message: str) -> None:
    if source in _WARNED_ONTOLOGY_SOURCES:
        return
    _WARNED_ONTOLOGY_SOURCES.add(source)
    warnings.warn(message, stacklevel=2)


def _build_from_ontology(source: str) -> dict[str, dict[str, Any]]:
    if not source or Graph is None:
        return {}
    try:
        g = Graph()
        g.parse(source)
    except Exception as exc:
        _warn_ontology_once(source, f"Failed to load ontology spec from '{source}': {exc}")
        return {}

    out: dict[str, dict[str, Any]] = {}
    for subject in g.subjects(RDF.type, OWL.Class):
        iri = str(subject)
        if "#" not in iri:
            continue
        mr_name = iri.rsplit("#", 1)[-1]
        pref_labels = _pick_labels(g, subject, SKOS.prefLabel)
        if not pref_labels:
            continue
        deprecated = False
        for lit in g.objects(subject, OWL.deprecated):
            try:
                deprecated = str(lit).lower() == "true"
            except Exception:
                continue
        parsed = _split_pref_label(pref_labels[0])
        if not parsed:
            continue
        base, unit = parsed
        notations = _pick_labels(g, subject, SKOS.notation)
        notation = ""
        for n in notations:
            s = str(n).strip()
            if s:
                notation = s
                break
        if not notation:
            notation = mr_name

        syns: set[str] = {_slugify(base), _slugify(mr_name)}
        for alias in _pick_labels(g, subject, SKOS.altLabel) + _pick_labels(g, subject, SKOS.notation):
            alias_text = alias.split(" / ", 1)[0].strip()
            slug = _slugify(alias_text.replace("/", " ").replace("#", " ").replace("_", " "))
            if slug:
                syns.add(slug)

        out[mr_name] = {
            "unit": unit,
            "label_template": f"{base} / {{unit}}",
            "required": mr_name in _REQUIRED_DEFAULT,
            "mr_name": mr_name,
            "notation": notation,
            "iri": iri,
            "deprecated": deprecated,
            "synonyms": sorted(s for s in syns if s),
        }
    return out


def _merge_columns(
    base: dict[str, dict[str, Any]],
    ontology: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged = copy.deepcopy(base)
    for _quantity, current in merged.items():
        current.setdefault("deprecated", False)
    for quantity, item in ontology.items():
        if quantity in merged:
            current = merged[quantity]
            incoming_deprecated = bool(item.get("deprecated"))
            # Deprecated ontology entries are read-compat only: do not let them
            # redefine canonical labels/units for existing quantities.
            if not incoming_deprecated:
                current["unit"] = item.get("unit", current.get("unit"))
                current["label_template"] = item.get("label_template", current.get("label_template"))
                current["iri"] = item.get("iri", current.get("iri"))
            current["mr_name"] = quantity
            current["notation"] = item.get("notation", current.get("notation", quantity))
            current["deprecated"] = bool(current.get("deprecated", False)) or incoming_deprecated
            current["required"] = bool(current.get("required", False))
            syns = set(current.get("synonyms", [])) | set(item.get("synonyms", []))
            current["synonyms"] = sorted(s for s in syns if s)
            continue
        item = dict(item)
        item.setdefault("deprecated", False)
        merged[quantity] = item

    ordered: dict[str, dict[str, Any]] = {}
    for quantity in base:
        ordered[quantity] = merged[quantity]
    for quantity in sorted(q for q in merged if q not in ordered):
        ordered[quantity] = merged[quantity]
    return ordered


def _build_columns() -> dict[str, dict[str, Any]]:
    base = copy.deepcopy(_STATIC_COLUMNS)
    source = _ontology_source()
    if not source:
        return base
    ontology = _build_from_ontology(source)
    if not ontology:
        return base
    return _merge_columns(base, ontology)


def _current_columns(*, refresh: bool = False) -> dict[str, dict[str, Any]]:
    global _CACHED_COLUMNS, _CACHED_SIGNATURE
    signature = _source_signature(_ontology_source())
    if refresh or _CACHED_COLUMNS is None or signature != _CACHED_SIGNATURE:
        _CACHED_COLUMNS = _build_columns()
        _CACHED_SIGNATURE = signature
    return _CACHED_COLUMNS


def refresh_columns() -> None:
    _current_columns(refresh=True)


class _ColumnsProxy(Mapping[str, dict[str, Any]]):
    def __getitem__(self, key: str) -> dict[str, Any]:
        return _current_columns()[key]

    def __iter__(self):
        return iter(_current_columns())

    def __len__(self) -> int:
        return len(_current_columns())


COLUMNS: Mapping[str, dict[str, Any]] = _ColumnsProxy()


# --------- Helpers consumed by the normalizer ----------


def _label_for(quantity: str) -> str:
    col = _current_columns()[quantity]
    return col["label_template"].format(unit=col["unit"])


def notation_for(quantity: str) -> str:
    col = _current_columns()[quantity]
    notation = str(col.get("notation") or col.get("mr_name") or quantity).strip()
    return notation or quantity


def unit_for(quantity: str) -> str:
    return _current_columns()[quantity]["unit"]


def required_labels() -> tuple[str, ...]:
    return tuple(
        _label_for(q) for q, s in _current_columns().items() if s["required"] and not bool(s.get("deprecated"))
    )


def optional_labels() -> tuple[str, ...]:
    return tuple(
        _label_for(q) for q, s in _current_columns().items() if not s["required"] and not bool(s.get("deprecated"))
    )


def base_synonym_index() -> dict[str, str]:
    """
    Build a mapping from base-name slug to quantity key (machine-readable name).
    """
    idx: dict[str, str] = {}
    for q, s in _current_columns().items():
        if bool(s.get("deprecated")):
            continue
        left = str(s.get("label_template", "")).split(" / ", 1)[0]
        left_slug = _slugify(left)
        if left_slug:
            idx.setdefault(left_slug, q)
        notation_slug = _slugify(notation_for(q))
        if notation_slug:
            idx.setdefault(notation_slug, q)
        for base in s.get("synonyms", []):
            slug = _slugify(str(base))
            if slug:
                idx.setdefault(slug, q)
    return idx
