# src/bdf/data_sources/arbin_csv.py
from __future__ import annotations

from .base_delimited import DelimitedTextPlugin


class ArbinCSV(DelimitedTextPlugin):
    """Arbin CSV export. Single header row, UTF-8 BOM, comma-separated.
    Columns carry explicit units in parentheses; time in seconds, capacity in Ah.
    'Date Time' column (MM/DD/YYYY HH:MM:SS.f) is used for Unix Time / s.
    """
    id = "arbin-csv"
    exts = (".csv",)
    default_encoding = "utf-8-sig"

    header_token_patterns = (
        r"\btest time \(s\)\b",
        r"\bvoltage \(v\)\b",
        r"\bcurrent \(a\)\b",
        r"\bcycle index\b",
        r"\bstep index\b",
    )

    # 'Date Time' matches the base CyclerPlugin timestamp_candidate_patterns
    # (r"^date[_\s/:-]*time$") so Unix Time / s is derived automatically via augment().
    # Format MM/DD/YYYY HH:MM:SS.f is handled by pd.to_datetime auto-detection.

    column_synonyms = {
        "Test Time / s":          ["test time (s)"],
        "Step Time / s":          ["step time (s)"],
        "Voltage / V":            ["voltage (v)"],
        "Current / A":            ["current (a)"],
        "Cycle Count / 1":        ["cycle index"],
        "Step Index / 1":         ["step index"],
        "Charging Capacity / Ah": ["charge capacity (ah)"], # cumulative over experiment
        "Discharging Capacity / Ah": ["discharge capacity (ah)"], # cumulative over experiment
        "Charging Energy / Wh":   ["charge energy (wh)"], # cumulative over experiment
        "Discharging Energy / Wh": ["discharge energy (wh)"], # cumulative over experiment
        "Cumulative Capacity / Ah": ["capacity (ah)"], # cumulative over a step
        "Power / W":              ["power (w)"],
        "Internal Resistance / ohm": ["internal resistance (ohm)"],
        "Surface Temperature T1 / degC": ["aux_temperature_1 (c)"],
    }

    unit_column_patterns = {
        "Test Time / s":          [(r"^test time \(s\)$", "s")],
        "Step Time / s":          [(r"^step time \(s\)$", "s")],
        "Voltage / V":            [(r"^voltage \(v\)$", "V")],
        "Current / A":            [(r"^current \(a\)$", "A")],
        "Charging Capacity / Ah": [(r"^charge capacity \(ah\)$", "Ah")],
        "Discharging Capacity / Ah": [(r"^discharge capacity \(ah\)$", "Ah")],
        "Charging Energy / Wh":   [(r"^charge energy \(wh\)$", "Wh")],
        "Discharging Energy / Wh": [(r"^discharge energy \(wh\)$", "Wh")],
        "Power / W":              [(r"^power \(w\)$", "W")],
        "Internal Resistance / ohm": [
            (r"^internal resistance \(ohm\)$", "ohm"),
        ],
        "Surface Temperature T1 / degC": [(r"^aux_temperature_1 \(c\)$", "degC")],
    }
