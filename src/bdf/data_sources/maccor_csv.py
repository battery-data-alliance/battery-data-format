# src/bdf/data_sources/maccor_csv.py
from __future__ import annotations

from .base_delimited import DelimitedTextPlugin


class MaccorCSV(DelimitedTextPlugin):
    """Maccor CSV export. Two metadata header lines followed by the column row.
    'DPT Time' (DD-Mon-YY H:MM:SS AM/PM) is used for Unix Time / s via augment().
    Start time can also be extracted from the 'Date of Test:' header line as a fallback.
    Capacity and Energy columns are net (cumulative per step) values in Ah / Wh.
    """
    id = "maccor-csv"
    exts = (".csv",)
    default_encoding = "utf-8"

    header_token_patterns = (
        r"\btest time \(sec\)\b",
        r"\bvoltage\b",
        r"\bcurrent\b",
        r"\bdpt time\b",
    )

    # DPT Time contains absolute wall-clock timestamps for each row
    timestamp_candidate_patterns = (
        r"^dpt\s*time$",
        r"^date\s*time$",
    )
    timestamp_format = "%d-%b-%y %I:%M:%S %p"

    # Extract start time from "Date of Test:,DD-Mon-YY H:MM:SS AM/PM" header line
    start_time_line_regex = r"Date of Test:\s*,\s*(.+)"
    start_time_format = "%d-%b-%y %I:%M:%S %p"

    column_synonyms = {
        "Test Time / s":    ["test time (sec)"],
        "Step Time / s":    ["step time (sec)"],
        "Voltage / V":      ["voltage"],
        "Current / A":      ["current"],
        "Cycle Count / 1":  ["cycle c"],
        "Step Index / 1":   ["step"],
        "Cumulative Capacity / Ah": ["capacity"],
        "Net Energy / Wh":  ["energy"],
        "Surface Temperature T1 / degC": ["temp 1", "temperature 1"],
    }

    unit_column_patterns = {
        "Test Time / s":    [(r"^test time \(sec\)$", "s")],
        "Step Time / s":    [(r"^step time \(sec\)$", "s")],
        "Voltage / V":      [(r"^voltage$", "V")],
        "Current / A":      [(r"^current$", "A")],
        "Cumulative Capacity / Ah": [(r"^capacity$", "Ah")], # Cumulative over a step
        "Cumulative Energy / Wh":  [(r"^energy$", "Wh")], # Cumulative over a step
        "Surface Temperature T1 / degC": [
            (r"^temp 1$", "degC"),
            (r"^temperature 1$", "degC"),
        ],
    }
