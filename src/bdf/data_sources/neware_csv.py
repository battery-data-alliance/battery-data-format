# src/bdf/data_sources/neware_csv.py
from __future__ import annotations

import pandas as pd

from .base_delimited import DelimitedTextPlugin


class NewareCSV(DelimitedTextPlugin):
    """Neware/BTS CSV export (EN/中文). 'Time(s)' is STEP time; 'Total/Test Time(s)' is TEST time."""
    id = "neware-csv"
    exts = (".csv",)

    # Encoding & robustness
    default_encoding = "utf-8-sig"
    encodings_try = ("utf-8", "gb18030", "cp1252", "latin-1")
    ragged_row_policy = "fold_last"

    # Help base sniff() lock on quickly
    header_token_patterns = (
        r"\bVoltage\(V\)\b|\b电压\(V\)\b",
        r"\bCurrent\(A\)\b|\b电流\(A\)\b",
        r"\bTime\(s\)\b|\b时间\(s\)\b",
        r"\b(Total|Test)\s*Time\(s\)\b|\b总时间\(s\)\b|\b测试时间\(s\)\b",
        r"\bDateTime\b|\bDatetime\b",
        r"\bCycle\b",
        r"\bStep\b",
        r"\bRecord\b",
    )

    column_synonyms = {
        # REQUIRED BDF
        "Test Time / s": [
            "Total Time(s)", "Test Time(s)", "TotalTime(s)", "TotalTime_S", "Total Time",
            "总时间(s)", "测试时间(s)"
        ],
        "Voltage / V":   ["Voltage(V)", "电压(V)"],
        "Current / A":   ["Current(A)", "电流(A)"],

        # STEP time (Neware 'Time(s)' is step time, not test time)
        "Step Time / s": [
            "Time(s)", "Relative Time(s)", "State Time(s)",
            "StepTime(s)", "Step Time(s)", "StepTime_S",
            "时间(s)"
        ],

        # Helpful optional columns
        "Cycle Count / 1":  ["Cycle"],
        "Step ID":          ["Step"],
        "Record Index / 1": ["Record"],
        "Date Time ISO":    ["DateTime", "Datetime", "DATE_TIME"],

        # Capacity/energy — canonical labels with Ah/Wh units.
        # Neware exports in mAh/mWh; fixup() scales to Ah/Wh.
        "Charging Capacity / Ah":    ["Charge Capacity(mAh)", "Chg.Capacity(mAh)"],
        "Discharging Capacity / Ah": ["Discharge Capacity(mAh)", "DChg.Capacity(mAh)"],
        "Charging Energy / Wh":      ["Charge Energy(mWh)"],
        "Discharging Energy / Wh":   ["Discharge Energy(mWh)"],

        # Temperature
        "Ambient Temperature / degC": ["Temperature(°C)", "温度(°C)"],
    }

    unit_column_patterns = {
        "Test Time / s": [
            (r"^(Total|Test)\s*Time\(s\)$", "s"),
            (r"^TotalTime\(s\)$", "s"),
            (r"^TotalTime_S$", "s"),
            (r"^总时间\(s\)$", "s"),
            (r"^测试时间\(s\)$", "s"),
        ],
        "Step Time / s": [
            (r"^Time\(s\)$", "s"),
            (r"^Relative Time\(s\)$", "s"),
            (r"^State Time\(s\)$", "s"),
            (r"^StepTime\(s\)$", "s"),
            (r"^Step Time\(s\)$", "s"),
            (r"^StepTime_S$", "s"),
            (r"^时间\(s\)$", "s"),
        ],
        "Voltage / V": [
            (r"^Voltage\(V\)$", "V"),
            (r"^电压\(V\)$", "V"),
        ],
        "Current / A": [
            (r"^Current\(A\)$", "A"),
            (r"^电流\(A\)$", "A"),
        ],
        "Ambient Temperature / degC": [
            (r"^Temperature\(°C\)$", "degC"),
            (r"^温度\(°C\)$", "degC"),
        ],
        # Unit hints for capacity/energy so fixup() knows to scale ÷1000
        "Charging Capacity / Ah":    [
            (r"^Charge Capacity\(mAh\)$", "mAh"),
            (r"^Chg\.Capacity\(mAh\)$", "mAh"),
        ],
        "Discharging Capacity / Ah": [
            (r"^Discharge Capacity\(mAh\)$", "mAh"),
            (r"^DChg\.Capacity\(mAh\)$", "mAh"),
        ],
        "Charging Energy / Wh":      [(r"^Charge Energy\(mWh\)$", "mWh")],
        "Discharging Energy / Wh":   [(r"^Discharge Energy\(mWh\)$", "mWh")],
    }

    def fixup(self, df: pd.DataFrame) -> pd.DataFrame:
        """Scale Neware mAh/mWh capacity and energy columns to BDF canonical Ah/Wh."""
        out = super().fixup(df)
        hints = getattr(self, "_unit_hints", {})

        def _scale_if_milli(col: str, milli_aliases: tuple[str, ...]) -> None:
            if col not in out.columns:
                return
            unit = (hints.get(col) or "").lower()
            if unit in milli_aliases:
                out[col] = pd.to_numeric(out[col], errors="coerce") / 1000.0

        mah = ("mah", "ma*h", "ma.h")
        for col in ("Charging Capacity / Ah", "Discharging Capacity / Ah"):
            _scale_if_milli(col, mah)

        mwh = ("mwh", "mw*h", "mw.h")
        for col in ("Charging Energy / Wh", "Discharging Energy / Wh"):
            _scale_if_milli(col, mwh)

        return out
