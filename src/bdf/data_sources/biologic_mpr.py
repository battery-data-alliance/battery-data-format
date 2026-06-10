from __future__ import annotations

from pathlib import Path

import pandas as pd

from .base import CyclerPlugin, SniffResult


class BioLogicMPR(CyclerPlugin):
    id = "biologic-mpr"
    exts = (".mpr",)

    column_synonyms = {
        # Required
        "Test Time / s": ["time"],
        "Voltage / V": ["Ewe", "Ece", "<Ewe>", "<Ece>", "Ecell"],
        "Current / A": ["I", "<I>"],  # mA -> converted in fixup
        # Recommended
        "Unix Time / s": ["uts"],
        "Cycle Count / 1": ["cycle number"],
        "Ambient Temperature / degC": ["Temperature"],
        # Optional
        "Step Index / 1": [],
        "Charging Capacity / Ah": [],
        "Discharging Capacity / Ah": [],
        "Step Capacity / Ah": [],
        "Net Capacity / Ah": ["Q-Qo"],  # mA h -> converted in fixup
        "Cumulative Capacity / Ah": [],
        "Charging Energy / Wh": ["Energy we charge", "Energy ce charge"],
        "Discharging Energy / Wh": ["Energy we discharge", "Energy ce discharge"],
        "Step Energy / Wh": [],
        "Net Energy / Wh": ["Energy we", "Energy ce"],
        "Cumulative Energy / Wh": ["|Energy|"],
        "Power / W": ["Pwe"],
        "Internal Resistance / Ohm": ["Rwe"],
        "Ambient Pressure / Pa": [],
        "Applied Pressure / Pa": [],
        "Surface Temperature T1 / degC": [],
        "Surface Temperature T2 / degC": [],
        "Surface Temperature T3 / degC": [],
        "Surface Temperature T4 / degC": [],
        "Surface Temperature T5 / degC": [],
        # EIS
        "Frequency / Hz": ["freq"],
        "Real Impedance / ohm": ["Re(Z)"],
        "Imaginary Impedance / ohm": ["-Im(Z)"],
        "Absolute Impedance / ohm": ["|Z|"],
        "Phase / deg": ["Phase(Z)"],
    }

    def sniff(self, path: Path, head: bytes) -> SniffResult:
        score = 0.0
        reasons: list[str] = []
        if path.suffix.lower() in {".mpr"}:
            score += 0.5
            reasons.append("ext")
        if head.startswith(b"BIO-LOGIC"):
            score += 0.5
            reasons.append("magic")
        return SniffResult(self.id, min(score, 1.0), "+".join(reasons), {})

    def parse(self, path: Path) -> pd.DataFrame:
        try:
            import yadg
        except ImportError as e:
            raise ImportError(
                "yadg is required to read .mpr files.\n"
                "Install it with: pip install yadg"
            ) from e

        return yadg.extractors.extract("eclab.mpr", path).to_dataset().to_dataframe().reset_index()

    def augment(self, df_raw: pd.DataFrame) -> pd.DataFrame:
        """
        Add current column if missing, e.g. only dq recorded, or OCV technique.
        """
        cols = set(df_raw.columns)
        if not (set(self.column_synonyms["Current / A"]) & cols):
            if ({"dq", "dQ"} & cols) and "uts" in cols:
                # dq is mA h, multiply by 3600 to get mA s
                # Then multiply by diff(time) / s to get current in mA
                # mA -> A happens in fixup, when changing from mpr units to bdf units
                dq_col = next(col for col in ("dq", "dQ") if col in cols)
                dt = df_raw["uts"].diff().fillna(float("inf"))
                df_raw["I"] = 3600 * df_raw[dq_col] / dt
            else:
                df_raw["I"] = 0  # e.g. OCV
        return df_raw

    def fixup(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Fix units after normalizing to bdf.
        """
        df["Current / A"] *= 1e-3
        if "Net Capacity / Ah" in df.columns:
            df["Net Capacity / Ah"] *= 1e-3
        if "Imaginary Impedance / ohm" in df.columns:
            df["Imaginary Impedance / ohm"] *= -1
        return df
