from __future__ import annotations

from .base_delimited import DelimitedTextPlugin


class BioLogicMPT(DelimitedTextPlugin):
    id = "biologic-mpt"
    exts = (".mpt",)
    default_encoding = "latin-1"

    magic = ("BT-Lab ASCII FILE", "EC-Lab ASCII FILE")
    header_lines_field_regex = r"Nb\s+header\s+lines\s*:\s*(\d+)"
    header_token_patterns = (r"\btime/s\b", r"\bewe/v\b", r"\becell/v\b", r"\bi/ma\b", r"current\s*/\s*a")

    column_synonyms = {
        "Test Time / s": ["time/s", "time / s", "t (s)", "time [s]", "relative time(s)"],
        "Voltage / V":   ["ewe/v", "ecell/v", "u/v", "u[v]"],
        "Current / A":   ["i/ma", "i[a]", "current / a", "current(a)", "i / ma", "<i>/ma"],
        "Ambient Temperature / degC": ["temperature/°c", "temperature/degc", "temp/°c", "t/°c"],
    }

    unit_column_patterns = {
        "Current / A": [
            (r"^i/ma$", "mA"),
            (r"\bcurrent\s*/\s*a\b", "A"),
        ]
    }
