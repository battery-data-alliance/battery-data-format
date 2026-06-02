from __future__ import annotations

import re
from dataclasses import dataclass

from . import spec

_SLUG = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")


@dataclass(frozen=True)
class IngestAliasInfo:
    quantity: str
    label: str
    unit: str
    source_unit: str | None
    deprecated: bool = False


# Package-level ingestion aliases (source-agnostic, kept separate from ontology).
# Only include aliases with stable semantics and compatible units.
_INGEST_ALIASES: dict[str, dict[str, object]] = {
    "test_time_second": {
        "aliases": [
            "test_time",
            "test time",
            "test-time",
        ],
        "source_unit": "s",
    },
    "voltage_volt": {
        "aliases": [
            "potential",
        ],
    },
    "current_ampere": {
        "aliases": [
            "current",
        ],
    },
    "cycle_count": {
        "aliases": [
            "cycle_num",
            "cycle number",
            "cycle-number",
        ],
    },
    "step_count": {
        "aliases": [
            "step_num",
            "step number",
            "step-number",
        ],
    },
    "unix_time_second": {
        "aliases": [
            "epoch_time_utc",
            "epoch time utc",
            "epoch-time-utc",
        ],
        "source_unit": "s",
    },
    "charging_capacity_ah": {
        "aliases": [
            "test_cumulated_charge_capacity",
            "test cumulative charge capacity",
            "test-cumulated-charge-capacity",
        ],
    },
    "discharging_capacity_ah": {
        "aliases": [
            "test_cumulated_discharge_capacity",
            "test cumulative discharge capacity",
            "test-cumulated-discharge-capacity",
        ],
    },
    "net_capacity_ah": {
        "aliases": [
            "test_net_capacity",
            "test net capacity",
            "test-net-capacity",
        ],
    },
    "charging_energy_wh": {
        "aliases": [
            "test_cumulated_charge_energy",
            "test cumulative charge energy",
            "test-cumulated-charge-energy",
        ],
    },
    "discharging_energy_wh": {
        "aliases": [
            "test_cumulated_discharge_energy",
            "test cumulative discharge energy",
            "test-cumulated-discharge-energy",
        ],
    },
    "net_energy_wh": {
        "aliases": [
            "test_net_energy",
            "test net energy",
            "test-net-energy",
        ],
    },
    "ambient_temperature_celsius": {
        "aliases": [
            "temperature_chamber",
            "temperature chamber",
            "temperature-chamber",
        ],
    },
    # A single, unqualified surface/skin thermocouple maps to channel T1.
    # "cell temperature" is deliberately excluded — it is ambiguous (could be
    # surface, can, or internal) and must stay unmapped.
    "temperature_t1_celsius": {
        "aliases": [
            "surface_temp",
            "surface temp",
            "surface-temp",
            "surface_temperature",
            "surface temperature",
            "skin_temp",
            "skin temperature",
        ],
        "source_unit": "degC",
    },
}


def load_ingest_alias_index() -> dict[str, IngestAliasInfo]:
    out: dict[str, IngestAliasInfo] = {}
    for quantity, item in _INGEST_ALIASES.items():
        if quantity not in spec.COLUMNS:
            continue
        if bool(spec.COLUMNS[quantity].get("deprecated")):
            continue
        aliases = item.get("aliases") or []
        source_unit = item.get("source_unit")
        if source_unit is not None:
            source_unit = str(source_unit)
        info = IngestAliasInfo(
            quantity=quantity,
            label=spec._label_for(quantity),
            unit=spec.unit_for(quantity),
            source_unit=source_unit,
            deprecated=False,
        )
        for alias in aliases:
            alias_text = str(alias).strip()
            if not alias_text:
                continue
            slug = _slugify(alias_text.replace("/", " ").replace("#", " "))
            if slug:
                out.setdefault(slug, info)
    return out
