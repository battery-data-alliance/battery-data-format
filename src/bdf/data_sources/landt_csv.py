# src/bdf/data_sources/landt_csv.py
from __future__ import annotations

from .base_delimited import DelimitedTextPlugin


class LandtCSV(DelimitedTextPlugin):
    """Landt modern CSV export (snake_case headers, SI units)."""

    id = "landt-csv"
    exts = (".csv",)
    default_encoding = "utf-8-sig"
    ragged_row_policy = "fold_last"  # <- handle extra delimiters/trailing commas

    header_token_patterns = (
        r"\bchannel_index\b",
        r"\bcycle_index\b",
        r"\bstep_index\b",
        r"\bdate_time_iso_string\b",
        r"\btest_time_s\b",
        r"\bstep_time_s\b",
        r"\bcurrent_a\b",
        r"\bvoltage_v\b",
    )

    column_synonyms = {
        "Test Time / s": ["test_time_s"],
        "Voltage / V": ["voltage_v"],
        "Current / A": ["current_a"],
        "Step Time / s": ["step_time_s"],
        "Cycle Index": ["cycle_index"],
        "Step Index": ["step_index"],
        "Channel Index": ["channel_index"],
        "Date Time ISO": ["date_time_iso_string"],
        "Charging Capacity / Ah": ["charge_capacity_Ah"],
        "Discharging Capacity / Ah": ["discharge_capacity_Ah"],
        "Charging Energy / Wh": ["charge_energy_Wh"],
        "Discharging Energy / Wh": ["discharge_energy_Wh"],
        "Ambient Temperature / degC": ["temperature_1_C"],
        "Surface Temperature T2 / degC": ["temperature_2_C"],
        "Surface Temperature T3 / degC": ["temperature_3_C"],
    }

    unit_column_patterns = {
        "Test Time / s": [(r"^test_time_s$", "s")],
        "Voltage / V": [(r"^voltage_v$", "V")],
        "Current / A": [(r"^current_a$", "A")],
        "Step Time / s": [(r"^step_time_s$", "s")],
    }
