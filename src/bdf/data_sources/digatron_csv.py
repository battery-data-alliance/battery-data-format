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
        """Re-derive all capacity/energy columns from primary accumulators.

        Digatron's AhAccu/WhAccu are net (charging - discharging), not
        throughput; AhBal/WhBal are rolling instrument-internal balances that
        diverge from the BDF net definition; AhStep/WhStep are unsigned.
        This method recomputes every derived column from the two primary
        test-level accumulators (charging and discharging) to ensure
        consistency with BDF definitions.
        """
        chg_cap = "Charging Capacity / Ah"
        dchg_cap = "Discharging Capacity / Ah"
        chg_e = "Charging Energy / Wh"
        dchg_e = "Discharging Energy / Wh"
        step_col = "Step ID"

        if chg_cap not in df.columns or dchg_cap not in df.columns:
            return df

        out = df.copy()

        # --- Test-level derived columns ---
        # cumulative = throughput (charging + discharging), always non-decreasing
        out["Cumulative Capacity / Ah"] = out[chg_cap] + out[dchg_cap]
        # net = signed running integral (charging - discharging), can be negative
        out["Net Capacity / Ah"] = out[chg_cap] - out[dchg_cap]

        if chg_e in out.columns and dchg_e in out.columns:
            out["Cumulative Energy / Wh"] = out[chg_e] + out[dchg_e]
            out["Net Energy / Wh"] = out[chg_e] - out[dchg_e]

        # --- Step-level derived columns (signed: positive=charge, negative=discharge) ---
        if step_col not in df.columns:
            return out

        step_group = (df[step_col] != df[step_col].shift()).cumsum()

        def _signed_delta(pos_col: str, neg_col: str, result_col: str) -> None:
            if pos_col not in out.columns or neg_col not in out.columns:
                return
            start_pos = out.groupby(step_group)[pos_col].transform("first")
            start_neg = out.groupby(step_group)[neg_col].transform("first")
            out[result_col] = (out[pos_col] - start_pos) - (out[neg_col] - start_neg)

        _signed_delta(chg_cap, dchg_cap, "Step Capacity / Ah")
        _signed_delta(chg_e, dchg_e, "Step Energy / Wh")
        return out
