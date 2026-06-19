# src/bdf/data_sources/digatron_csv.py
from __future__ import annotations

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
    assume_naive_tz = "UTC"  # if timestamps are naive, treat as UTC then convert to Unix seconds

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
        "Voltage / V": ["voltage#v", "voltage"],
        "Current / A": ["current#a", "current"],
        # --- Recommended ---
        "Unix Time / s": ["timestamp"],
        "Cycle Count / 1": ["cycle"],
        "Step Index / 1": ["step"],
        "Step Time / s": ["step time"],
        "Ambient Temperature / degC": ["tenv#degc"],
        # --- Optional (capacities) ---
        "Charging Capacity / Ah": ["ahcha#ah"],
        "Discharging Capacity / Ah": ["ahdch#ah"],
        "Step Capacity / Ah": ["ahstep#ah"],
        "Net Capacity / Ah": ["ahbal#ah"],
        "Cumulative Capacity / Ah": ["ahaccu#ah", "ahaccu"],
        # --- Optional (energies) ---
        "Charging Energy / Wh": ["whcha#wh"],
        "Discharging Energy / Wh": ["whdch#wh"],
        "Step Energy / Wh": ["whstep#wh"],
        "Cumulative Energy / Wh": ["whaccu#wh", "whaccu"],
        "Power / W": ["watt", "power#w"],
        # --- Optional (temps) ---
        "Surface Temperature T1 / degC": ["t1#degc", "logtemp001"],
    }
