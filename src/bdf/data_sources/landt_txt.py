# src/bdf/data_sources/landt_txt.py
from __future__ import annotations

from .base_delimited import DelimitedTextPlugin


class LandtTXT(DelimitedTextPlugin):
    id = "landt-txt"
    exts = (".txt",)
    default_encoding = "utf-8-sig"
    ragged_row_policy = "fold_last"   # <- important for TXT with ragged whitespace rows

    header_token_patterns = (
        r"^\s*rec#",
        r"\btest\(sec\)\b",
        r"\bvolts?\b",
        r"\bamps?\b",
        r"\bdpt[- ]time\b",
    )

    column_synonyms = {
        "Test Time / s": ["test(sec)", "test (sec)", "test_time_s", "test time (s)", "test time"],
        "Voltage / V":   ["volts", "volt", "voltage", "v"],
        "Current / A":   ["amps", "amp", "current", "a", "i(a)"],
        "Step Time / s": ["dpt-time", "dpt time", "step time (s)", "step_time_s"],
        "Cycle Index":   ["cycle", "cycle#", "cycle index"],
        "Step Index":    ["step", "step#", "step index"],
        "Record Index":  ["rec#", "record", "record#"],
    }

    unit_column_patterns = {
        "Test Time / s": [(r"^test\(sec\)$", "s"), (r"^test_time_s$", "s"), (r"^test time \(s\)$", "s")],
        "Voltage / V":   [(r"^volts?$", "V"), (r"^voltage$", "V"), (r"^v$", "V")],
        "Current / A":   [(r"^amps?$", "A"), (r"^current$", "A"), (r"^a$", "A"), (r"^i\(a\)$", "A")],
        "Step Time / s": [(r"^dpt[- ]time$", "s"), (r"^step_time_s$", "s"), (r"^step time \(s\)$", "s")],
    }
