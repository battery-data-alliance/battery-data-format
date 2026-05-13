from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .base import CyclerPlugin, SniffResult


def _sniff_neware(path: Path, head: bytes, exts: tuple[str, ...], pid: str) -> SniffResult:
    score = 0.0
    reasons: list[str] = []
    if path.suffix.lower() in exts:
        score += 0.35
        reasons.append("ext")
    if head.startswith(b"NEWARE"):
        score += 0.6
        reasons.append("magic")
    return SniffResult(pid, min(score, 1.0), "+".join(reasons), {})


def _read_fastnda(path: Path) -> pd.DataFrame:
    import fastnda  # type: ignore

    df = fastnda.read(str(path))
    if hasattr(df, "to_pandas"):
        return df.to_pandas()
    return pd.DataFrame(df)


class _NewareNDABase(CyclerPlugin):
    exts = (".nda", ".ndax")

    column_synonyms = {
        "Test Time / s": ["Time", "total_time_s"],
        "Voltage / V": ["Voltage", "voltage_V"],
        "Current / A": ["Current(mA)", "current_mA"],
        "Cycle Count / 1": ["Cycle", "cycle_count"],
        "Step ID / 1": ["Step", "step_count", "Step_Index", "step_index"],
        "Unix Time / s": ["unix_time_s"],
        "Charging Capacity / Ah": ["Charge_Capacity(mAh)"],
        "Discharging Capacity / Ah": ["Discharge_Capacity(mAh)"],
        "Charging Energy / Wh": ["Charge_Energy(mWh)"],
        "Discharging Energy / Wh": ["Discharge_Energy(mWh)"],
    }

    def sniff(self, path: Path, head: bytes) -> SniffResult:
        return _sniff_neware(path, head, self.exts, self.id)

    def augment(self, df_raw: pd.DataFrame) -> pd.DataFrame:
        df = df_raw
        if "capacity_mAh" in df_raw.columns or "energy_mWh" in df_raw.columns:
            df = df_raw.copy()
        if "capacity_mAh" in df.columns:
            cap = pd.to_numeric(df["capacity_mAh"], errors="coerce")
            df["Charge_Capacity(mAh)"] = cap.clip(lower=0)
            df["Discharge_Capacity(mAh)"] = (-cap).clip(lower=0)
        if "energy_mWh" in df.columns:
            eng = pd.to_numeric(df["energy_mWh"], errors="coerce")
            df["Charge_Energy(mWh)"] = eng.clip(lower=0)
            df["Discharge_Energy(mWh)"] = (-eng).clip(lower=0)
        return super().augment(df)

    def fixup(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df
        meta = out.attrs.get("bdf:columns", {}) if hasattr(out, "attrs") else {}

        def _median_abs(series: pd.Series) -> float:
            vals = pd.to_numeric(series, errors="coerce").to_numpy(dtype="float64")
            if vals.size == 0:
                return float("nan")
            return float(np.nanmedian(np.abs(vals)))

        def _source_has_milli(col: str, tokens: tuple[str, ...]) -> bool:
            src = str(meta.get(col, {}).get("sourceHeader", "")).lower()
            return any(tok in src for tok in tokens)

        # Current: scale only if source indicates mA and values look large enough.
        if "Current / A" in out.columns and _source_has_milli("Current / A", ("ma", "milliamp")):
            med = _median_abs(out["Current / A"])
            if np.isfinite(med) and med > 20.0:
                out["Current / A"] = pd.to_numeric(out["Current / A"], errors="coerce") / 1000.0

        # Capacity/Energy: scale only if source indicates mAh/mWh and values look large enough.
        for col, tokens, threshold in (
            ("Charging Capacity / Ah", ("mah", "milliamp"), 10.0),
            ("Discharging Capacity / Ah", ("mah", "milliamp"), 10.0),
            ("Charging Energy / Wh", ("mwh", "milliwatt"), 10.0),
            ("Discharging Energy / Wh", ("mwh", "milliwatt"), 10.0),
        ):
            if col in out.columns and _source_has_milli(col, tokens):
                med = _median_abs(out[col])
                if np.isfinite(med) and med > threshold:
                    out[col] = pd.to_numeric(out[col], errors="coerce") / 1000.0
        return out


class NewareNDA(_NewareNDABase):
    id = "neware-nda"

    def parse(self, path: Path) -> pd.DataFrame:
        try:
            import NewareNDA as _neware  # type: ignore
        except Exception as exc:
            try:
                return _read_fastnda(path)
            except Exception:
                raise ImportError(
                    "Reading .nda/.ndax requires NewareNDA. Install batterydf "
                    "or install fastnda and use plugin 'neware-nda-fast'."
                ) from exc
        return _neware.read(str(path))


class NewareNDAFast(_NewareNDABase):
    id = "neware-nda-fast"

    def parse(self, path: Path) -> pd.DataFrame:
        try:
            return _read_fastnda(path)
        except Exception as exc:
            raise ImportError(
                "Reading .nda/.ndax with the fast backend requires fastnda "
                "(Python>=3.10, numpy>=2.2)."
            ) from exc
