# src/bdf/normalize/spec.py
from __future__ import annotations

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
- Slugs are lowercase with non-alnum → "-" (same slugger as normalizer).
- Synonyms are unit-agnostic ("voltage" not "voltage#v"); the normalizer parses units.
"""

COLUMNS = {
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
            "test-time", "time", "program-duration", "elapsed-time",
        ],
    },
    "voltage_volt": {
        "unit": "V",
        "label_template": "Voltage / V",
        "required": True,
        "mr_name": "voltage_volt",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#voltage_volt",
        "synonyms": [
            "voltage", "u", "cell-voltage",
        ],
    },
    "current_ampere": {
        "unit": "A",
        "label_template": "Current / A",
        "required": True,
        "mr_name": "current_ampere",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#current_ampere",
        "synonyms": [
            "current", "i", "cell-current",
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
            "unix-time", "timestamp", "date-time", "datetime",
        ],
    },
    "cycle_count": {
        "unit": "1",
        "label_template": "Cycle Count / {unit}",
        "required": False,
        "mr_name": "cycle_count",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_count",
        "synonyms": [
            "cycle", "cycle-index", "cycle-no", "cycle-number",
        ],
    },
    "step_count": {
        "unit": "1",
        "label_template": "Step Count / {unit}",
        "required": False,
        "mr_name": "step_count",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_count",
        "synonyms": [
            "step", "step-no", "step-number", "step-id",
        ],
    },
    "ambient_temperature_celsius": {
        "unit": "degC",
        "label_template": "Ambient Temperature / {unit}",
        "required": False,
        "mr_name": "ambient_temperature_celsius",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_temperature_celsius",
        "synonyms": [
            "ambient-temperature", "temperature", "tenv", "env-temp", "chamber-temp",
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
            "step-index", "point-index", "sample-index",
        ],
    },
    "charging_capacity_ah": {
        "unit": "Ah",
        "label_template": "Charging Capacity / {unit}",
        "required": False,
        "mr_name": "charging_capacity_ah",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_capacity_ah",
        "synonyms": [
            "ahcha", "charge-capacity", "capacity-charge",
        ],
    },
    "discharging_capacity_ah": {
        "unit": "Ah",
        "label_template": "Discharging Capacity / {unit}",
        "required": False,
        "mr_name": "discharging_capacity_ah",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_capacity_ah",
        "synonyms": [
            "ahdch", "discharge-capacity", "capacity-discharge",
        ],
    },
    "step_capacity_ah": {
        "unit": "Ah",
        "label_template": "Step Capacity / {unit}",
        "required": False,
        "mr_name": "step_capacity_ah",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_capacity_ah",
        "synonyms": [
            "ahstep", "capacity-step",
        ],
    },
    "net_capacity_ah": {
        "unit": "Ah",
        "label_template": "Net Capacity / {unit}",
        "required": False,
        "mr_name": "net_capacity_ah",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#net_capacity_ah",
        "synonyms": [
            "net-capacity", "capacity-net",
        ],
    },
    "cumulative_capacity_ah": {
        "unit": "Ah",
        "label_template": "Cumulative Capacity / {unit}",
        "required": False,
        "mr_name": "cumulative_capacity_ah",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#cumulative_capacity_ah",
        "synonyms": [
            "ahaccu", "ahbal", "capacity-accumulated", "accumulated-capacity", "total-capacity",
        ],
    },
    "charging_energy_wh": {
        "unit": "Wh",
        "label_template": "Charging Energy / {unit}",
        "required": False,
        "mr_name": "charging_energy_wh",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_energy_wh",
        "synonyms": [
            "whcha", "energy-charge",
        ],
    },
    "discharging_energy_wh": {
        "unit": "Wh",
        "label_template": "Discharging Energy / {unit}",
        "required": False,
        "mr_name": "discharging_energy_wh",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_energy_wh",
        "synonyms": [
            "whdch", "energy-discharge",
        ],
    },
    "step_energy_wh": {
        "unit": "Wh",
        "label_template": "Step Energy / {unit}",
        "required": False,
        "mr_name": "step_energy_wh",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_energy_wh",
        "synonyms": [
            "whstep", "energy-step",
        ],
    },
    "net_energy_wh": {
        "unit": "Wh",
        "label_template": "Net Energy / {unit}",
        "required": False,
        "mr_name": "net_energy_wh",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#net_energy_wh",
        "synonyms": [
            "net-energy", "energy-net",
        ],
    },
    "cumulative_energy_wh": {
        "unit": "Wh",
        "label_template": "Cumulative Energy / {unit}",
        "required": False,
        "mr_name": "cumulative_energy_wh",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#cumulative_energy_wh",
        "synonyms": [
            "whaccu", "energy-accumulated", "accumulated-energy", "total-energy",
        ],
    },
    "power_watt": {
        "unit": "W",
        "label_template": "Power / {unit}",
        "required": False,
        "mr_name": "power_watt",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#power_watt",
        "synonyms": [
            "power", "pwr",
        ],
    },
    "internal_resistance_ohm": {
        "unit": "ohm",
        "label_template": "Internal Resistance / {unit}",
        "required": False,
        "mr_name": "internal_resistance_ohm",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#internal_resistance_ohm",
        "synonyms": [
            "internal-resistance", "rint", "ir", "dcir", "ohmic-resistance", "resistance",
        ],
    },
    "ambient_pressure_pa": {
        "unit": "Pa",
        "label_template": "Ambient Pressure / {unit}",
        "required": False,
        "mr_name": "ambient_pressure_pa",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_pressure_pa",
        "synonyms": [
            "ambient-pressure", "pamb", "baro-pressure",
        ],
    },
    "applied_pressure_pa": {
        "unit": "Pa",
        "label_template": "Applied Pressure / {unit}",
        "required": False,
        "mr_name": "applied_pressure_pa",
        "iri": "https://w3id.org/battery-data-alliance/ontology/battery-data-format#applied_pressure_pa",
        "synonyms": [
            "applied-pressure", "press", "papp",
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

# --------- Helpers consumed by the normalizer ----------

def _label_for(quantity: str) -> str:
    spec = COLUMNS[quantity]
    return spec["label_template"].format(unit=spec["unit"])

def unit_for(quantity: str) -> str:
    return COLUMNS[quantity]["unit"]

def required_labels() -> tuple[str, ...]:
    return tuple(_label_for(q) for q, s in COLUMNS.items() if s["required"])

def optional_labels() -> tuple[str, ...]:
    return tuple(_label_for(q) for q, s in COLUMNS.items() if not s["required"])

def base_synonym_index() -> dict[str, str]:
    """
    Build a mapping from base-name slug to quantity key (machine-readable name).
    """
    idx: dict[str, str] = {}
    for q, s in COLUMNS.items():
        for base in s.get("synonyms", []):
            idx[base] = q
    return idx

# ---------- variableMeasured inference helpers ----------

def _left_of_label(label: str) -> str:
    return label.split("/", 1)[0].strip()

def _ensure_spec():
    """Lazy import spec to avoid import-order issues during development/CI."""
    global _SPEC
    if _SPEC is None:
        try:
            from bdf.normalize import spec as _loaded  # type: ignore
            _SPEC = _loaded
        except Exception:
            _SPEC = None

def _spec_match_by_left(left: str) -> Optional[Dict[str, Any]]:
    """Return the spec column entry dict for a given preferred-label 'left' text."""
    _ensure_spec()
    if not _SPEC or not hasattr(_SPEC, "COLUMNS"):
        return None
    for _mr, meta in _SPEC.COLUMNS.items():  # type: ignore[attr-defined]
        canon = meta.get("label_template", "")
        if _left_of_label(canon).lower() == left.lower():
            return meta
    return None

def _required_pvs_from_spec() -> List[Dict[str, Any]]:
    """
    Build default PropertyValue list for required quantities directly from spec.COLUMNS.
    Uses label_template → left name, unit, and iri.
    If spec is unavailable, returns an empty list (caller will decide on hard fallback).
    """
    _ensure_spec()
    pvs: List[Dict[str, Any]] = []
    if _SPEC and hasattr(_SPEC, "COLUMNS"):
        for _mr, meta in _SPEC.COLUMNS.items():  # type: ignore[attr-defined]
            if meta.get("required"):
                label = meta.get("label_template", "")
                name = _left_of_label(label) if label else meta.get("mr_name", _mr)
                unit_text = meta.get("unit")
                iri = meta.get("iri")
                pvs.append(PropertyValue(name=name, property_id=iri, unit_text=unit_text).to_schema_org())
    return pvs

