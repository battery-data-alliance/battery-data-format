# src/bdf/data_sources/digatron_csv.py
from __future__ import annotations

import pandas as pd

from .base_delimited import DelimitedTextPlugin


class DigatronCSV(DelimitedTextPlugin):
    """
    Digatron CSV export.

    Example header:
    Step,Status,Timestamp,Program Duration#s,Step Duration#s,...,Voltage#V,Current#A,Tenv#degC,T1#degC
    """
    id = "digatron-csv"
    exts = (".csv",)
    default_encoding = "utf-8-sig"
    ragged_row_policy = "fold_last"
    drop_units_row = True

    # Enable auto "Unix Time / s" derivation from a timestamp column via base augment()
    # (The base patterns already include ^timestamp$, but we add a second alias to be safe.)
    timestamp_candidate_patterns = (r"^timestamp$", r"^date[_\s-]*time$")
    assume_naive_tz = "UTC"   # if timestamps are naive, treat as UTC then convert to Unix seconds

    # Help the base sniff() recognize the file
    header_token_patterns = (
        r"\btimestamp\b",
        r"\bprogram\s*duration#s\b",
        r"\bstep\s*duration#s\b",
        r"\bvoltage#v\b",
        r"\bcurrent#a\b",
        r"^step,\s*status,\s*step time,\s*prog time\b",
    )

    # Map Digatron headers -> BDF canonical headers (official spec labels)
    column_synonyms = {
        # --- Required ---
        "Test Time / s": ["program duration#s", "prog time", "program time"],
        "Voltage / V":   ["voltage#v", "voltage"],
        "Current / A":   ["current#a", "current"],

        # --- Recommended ---
        "Unix Time / s":      ["timestamp"],
        "Cycle Count / 1":    ["cycle"],
        "Step ID":        ["step"],
        "Step Type":      ["status"],
        "Step Time / s":      ["step time"],
        "Ambient Temperature / degC": ["tenv#degc"],

        # --- Optional (capacities) ---
        "Charging Capacity / Ah":      ["ahcha#ah"],
        "Discharging Capacity / Ah":   ["ahdch#ah"],
        "Step Capacity / Ah":          ["ahstep#ah"],
        "Net Capacity / Ah":           ["ahbal#ah"],
        "Cumulative Capacity / Ah":    ["ahaccu#ah", "ahaccu"],

        # --- Optional (energies) ---
        "Charging Energy / Wh":        ["whcha#wh"],
        "Discharging Energy / Wh":     ["whdch#wh"],
        "Step Energy / Wh":            ["whstep#wh"],
        "Cumulative Energy / Wh":      ["whaccu#wh", "whaccu"],
        "Power / W":                   ["watt", "power#w"],

        # --- Optional (temps) ---
        "Surface Temperature T1 / degC": ["t1#degc", "logtemp001"],
    }

    def fixup(self, df: pd.DataFrame) -> pd.DataFrame:
        """Re-derive test-level capacity/energy columns from primary accumulators.

        Digatron's AhAccu/WhAccu are net (charging - discharging), not
        throughput; AhBal/WhBal are rolling instrument-internal balances that
        diverge from the BDF net definition. This method recomputes the
        test-level derived columns from the two primary accumulators (charging
        and discharging) to ensure consistency with BDF definitions.

        AhStep/WhStep are unsigned magnitudes as exported by the instrument and
        are retained as-is — BDF defines step_capacity_ah and step_energy_wh
        as unsigned quantities.
        """
        chg_cap = "Charging Capacity / Ah"
        dchg_cap = "Discharging Capacity / Ah"
        chg_e = "Charging Energy / Wh"
        dchg_e = "Discharging Energy / Wh"

        if chg_cap not in df.columns or dchg_cap not in df.columns:
            return df

        out = df.copy()

        # Digatron TestSuite exports time in milliseconds despite the "#s"
        # label in column names.  Detect by median dt > 999 time-units
        # (consistent with ≥ 1 s logging intervals recorded in ms) and
        # divide by 1000 to obtain seconds.
        for time_col in ("Test Time / s", "Step Time / s"):
            if time_col in out.columns:
                t = pd.to_numeric(out[time_col], errors="coerce")
                median_dt = t.diff().abs().median()
                if pd.notna(median_dt) and median_dt > 999:
                    import warnings
                    warnings.warn(
                        f"DigatronCSV: '{time_col}' median Δt={median_dt:.0f} — "
                        "values appear to be in milliseconds (Digatron TestSuite known "
                        "export behaviour). Dividing by 1000 to convert to seconds.",
                        UserWarning,
                        stacklevel=2,
                    )
                    out[time_col] = t / 1000.0

        # Coerce to numeric — Digatron files can carry string dtype if the
        # parser did not infer types from the data rows.
        for col in (chg_cap, dchg_cap, chg_e, dchg_e):
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")

        # cumulative = throughput (charging + discharging), always non-decreasing
        out["Cumulative Capacity / Ah"] = out[chg_cap] + out[dchg_cap]
        # net = signed running integral (charging - discharging), can be negative
        out["Net Capacity / Ah"] = out[chg_cap] - out[dchg_cap]

        if chg_e in out.columns and dchg_e in out.columns:
            out["Cumulative Energy / Wh"] = out[chg_e] + out[dchg_e]
            out["Net Energy / Wh"] = out[chg_e] - out[dchg_e]

        # Sanity checks: warn rather than raise so a corrupted file still loads.
        cum = out["Cumulative Capacity / Ah"]
        if not cum.isna().all():
            if (cum.dropna().diff().dropna() < -1e-6).any():
                import warnings
                warnings.warn(
                    "DigatronCSV: 'Cumulative Capacity / Ah' is not monotonically "
                    "non-decreasing — check that AhCha and AhDch columns are correct.",
                    stacklevel=2,
                )
            net = out["Net Capacity / Ah"]
            if (net.dropna().abs() > cum.dropna() + 1e-6).any():
                import warnings
                warnings.warn(
                    "DigatronCSV: |Net Capacity| exceeds Cumulative Capacity at one "
                    "or more rows — check primary accumulator columns.",
                    stacklevel=2,
                )

        return out
