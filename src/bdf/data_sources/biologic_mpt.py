from __future__ import annotations

import pandas as pd

from .base_delimited import DelimitedTextPlugin


class BioLogicMPT(DelimitedTextPlugin):
    id = "biologic-mpt"
    exts = (".mpt",)
    default_encoding = "latin-1"
    decimal = ","

    magic = ("BT-Lab ASCII FILE", "EC-Lab ASCII FILE")
    header_lines_field_regex = r"Nb\s+header\s+lines\s*:\s*(\d+)"
    header_token_patterns = (r"\btime/s\b", r"\bewe/v\b", r"\becell/v\b", r"\bi/ma\b", r"current\s*/\s*a")

    column_synonyms = {
        # Required
        "Test Time / s": ["time/s", "time / s", "t (s)", "time [s]", "relative time(s)"],
        "Voltage / V": [
            "ewe/v", "ecell/v", "u/v", "u[v]",
            "ewe (v)", "ewe/v (v)", "<ewe>/v",
        ],
        "Current / A": [
            "i/ma", "i[a]", "current / a", "current(a)", "i / ma", "<i>/ma", "i(a)", "i/a",
        ],
        # Recommended
        "Cycle Count / 1": ["cycle number", "z cycle"],
        "Step Index / 1": ["ns"],
        "Ambient Temperature / degC": [
            "temperature/c",
            "temperature/degc",
            "temperature/\N{DEGREE SIGN}c",
            "temperature/\xf8c",
            "temp/c",
            "temp/degc",
            "temp/\N{DEGREE SIGN}c",
            "temp/\xf8c",
            "t/c",
            "t/\N{DEGREE SIGN}c",
            "t/\xf8c",
        ],
        # Optional capacities/energies/power/resistance
        "Charging Capacity / Ah": ["q charge/mA.h", "q charge /mA.h"],
        "Discharging Capacity / Ah": ["q discharge/mA.h", "q discharge /mA.h"],
        "Step Capacity / Ah": ["dq/mA.h"],
        "Cumulative Capacity / Ah": ["(q-qo)/mA.h", "capacity/mA.h"],
        "Charging Energy / Wh": ["energy charge/w.h"],
        "Discharging Energy / Wh": ["energy discharge/w.h"],
        "Cumulative Energy / Wh": ["|energy|/w.h"],
        "Power / W": ["p/w"],
        "Internal Resistance / Ohm": ["r/ohm"],
    }

    unit_column_patterns = {
        "Test Time / s": [
            (r"^time/s$", "s"),
            (r"^time\s*/\s*s$", "s"),
            (r"^time \[s\]$", "s"),
            (r"^relative time\(s\)$", "s"),
        ],
        "Voltage / V": [
            (r"^ewe/v$", "V"),
            (r"^ecell/v$", "V"),
            (r"^u/v$", "V"),
            (r"^u\[v\]$", "V"),
        ],
        "Current / A": [
            (r"^i/ma$", "mA"),
            (r"^<i>/ma$", "mA"),
            (r"^i/a$", "A"),
            (r"\bcurrent\s*/\s*a\b", "A"),
        ],
        "Cycle Count / 1": [
            (r"^cycle number$", "1"),
            (r"^z cycle$", "1"),
        ],
        "Step Index / 1": [
            (r"^ns$", "1"),
        ],
        "Ambient Temperature / degC": [
            (r"^temperature/c$", "degC"),
            (r"^temperature/degc$", "degC"),
            (r"^temperature/\xb0c$", "degC"),
            (r"^temperature/\xf8c$", "degC"),
            (r"^temp/c$", "degC"),
            (r"^temp/degc$", "degC"),
            (r"^temp/\xb0c$", "degC"),
            (r"^temp/\xf8c$", "degC"),
            (r"^t/c$", "degC"),
            (r"^t/\xb0c$", "degC"),
            (r"^t/\xf8c$", "degC"),
        ],
        "Charging Capacity / Ah": [
            (r"^q\s*charge/m?a?\.h$", "mA*h"),
        ],
        "Discharging Capacity / Ah": [
            (r"^q\s*discharge/m?a?\.h$", "mA*h"),
        ],
        "Step Capacity / Ah": [
            (r"^dq/m?a?\.h$", "mA*h"),
        ],
        "Cumulative Capacity / Ah": [
            (r"^\(q-qo\)/m?a?\.h$", "mA*h"),
            (r"^capacity/m?a?\.h$", "mA*h"),
        ],
        "Charging Energy / Wh": [
            (r"^energy charge/w\.h$", "W*h"),
        ],
        "Discharging Energy / Wh": [
            (r"^energy discharge/w\.h$", "W*h"),
        ],
        "Cumulative Energy / Wh": [
            (r"^\|energy\|/w\.h$", "W*h"),
        ],
        "Power / W": [
            (r"^p/w$", "W"),
        ],
        "Internal Resistance / Ohm": [
            (r"^r/ohm$", "ohm"),
        ],
    }

    def fixup(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Extend base fixups to downscale Biologic mAh/mWh style units to Ah/Wh when hints were detected.
        """
        out = super().fixup(df)
        hints = getattr(self, "_unit_hints", {})

        def _scale(col: str, scale: float, aliases: tuple[str, ...]) -> None:
            if col not in out.columns:
                return
            unit = (hints.get(col) or "").lower()
            if unit in aliases:
                out[col] = pd.to_numeric(out[col], errors="coerce") * scale

        mah_units = ("ma*h", "mah", "ma.h")
        for col in (
            "Charging Capacity / Ah",
            "Discharging Capacity / Ah",
            "Cumulative Capacity / Ah",
            "Step Capacity / Ah",
        ):
            _scale(col, 1.0 / 1000.0, mah_units)

        mwh_units = ("mw*h", "mwh", "mw.h")
        for col in ("Charging Energy / Wh", "Discharging Energy / Wh", "Cumulative Energy / Wh"):
            _scale(col, 1.0 / 1000.0, mwh_units)

        return out
