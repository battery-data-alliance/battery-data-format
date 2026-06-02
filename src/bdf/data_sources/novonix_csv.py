from __future__ import annotations

from .base_delimited import DelimitedTextPlugin


class NovonixCSV(DelimitedTextPlugin):
    """
    Novonix UHPC CSV with INI-like preamble and a [Data] section.
    Time columns are typically in HOURS (h); the normalizer converts to seconds (s).
    This plugin stays declarative: no per-plugin methods.
    """

    id = "novonix-csv"
    exts = (".csv",)
    default_encoding = "utf-8-sig"

    # --- Sectioned header ---
    data_section_marker = r"^\[Data\]$"
    data_header_offset = 1      # the row after [Data] is the (first non-empty) header

    # --- Detection ---
    # These strings are in the preamble, so they appear in the file head we sniff
    magic = ("[Summary]", "[Data]", "Novonix UHPC data file", "Novonix")

    # Heuristics in case we fall back to generic header detection
    header_token_patterns = (
        r"\bRun\s*Time\s*\(h\)\b",
        r"\bStep\s*Time\s*\(h\)\b",
        r"\bPotential\s*\(V\)\b|\bVoltage\s*\(V\)\b",
        r"\bCurrent\s*\(A\)\b",
        r"\bCapacity\s*\(Ah\)\b|\bEnergy\s*\(Wh\)\b|\bPower\s*\(W\)\b",
        r"\bCycle\s*Number\b|\bStep\s*Number\b|\bStep\s*position\b",
        r"\bDate\s*and\s*Time\b",
    )

    # Base augment() will derive Unix Time / s if a timestamp-like column is found
    timestamp_candidate_patterns = (
        r"^date\s*and\s*time$",
        r"^timestamp$",
        r"^absolute[_\s-]*time$",
    )
    assume_naive_tz = "UTC"

    # --- Column synonyms ---
    # Left side is canonical BDF label; right side is a list of vendor headers (case-insensitive).
    column_synonyms = {
        # Required canon
        "Test Time / s": [
            "run time (h)", "run-time (h)", "runtime (h)", "test time (h)", "testtime(h)"
        ],
        "Voltage / V": [
            "potential (v)", "voltage (v)", "cell voltage (v)"
        ],
        "Current / A": [
            "current (a)", "cell current (a)"
        ],

        # Recommended / optional
        "Step Time / s": [
            "step time (h)", "steptime(h)"
        ],
        "Cycle Count / 1": [
            "cycle number", "cycle", "cycle #", "cycle#"
        ],
        "Step ID": [
            "step number", "step", "step #", "step#"
        ],
        # NOTE: Novonix "Step position" is a start-of-step flag (1 on the first
        # row of each step, 0 elsewhere).  It is NOT a sequential index, so it
        # is not mapped here.  Step Index / 1 is computed by derive().
        # "Step Index / 1": ["step position"],
        "Ambient Temperature / degC": [
            "temperature (°c)", "temperature (c)", "ambient temperature (c)", "ambient temp (c)"
        ],
        "Surface Temperature T1 / degC": [
            "circuit temperature (°c)", "circuit temp (°c)", "circuit temperature (c)"
        ],

        # Capacities / energies / power
        "Net Capacity / Ah": [
            "capacity (ah)", "net capacity (ah)"
        ],
        # NOTE: Novonix "Energy (Wh)" is a signed step-level accumulator that
        # resets to zero at each step transition.  BDF "Net Energy / Wh" is a
        # cumulative test-level quantity.  These are different things, so no
        # mapping is made here; Net Energy / Wh is always populated by derive().
        "Power / W": [
            "power(w)", "power (w)"
        ],

        # Absolute time (optional)
        "Unix Time / s": [
            "unix time (s)", "unixtime (s)"
        ],
    }

    # --- Lightweight unit hints for simple post-read fixups (optional) ---
    # (The main normalization path with Pint covers full conversions;
    # these help ensure correctness if a plugin column already maps to canon
    # but its vendor unit is linear like h->s, mA->A, etc.)
    unit_column_patterns = {
        "Test Time / s": [
            (r"\brun\s*time\s*\(h\)\b", "h"),
            (r"\btest\s*time\s*\(h\)\b", "h"),
        ],
        "Step Time / s": [
            (r"\bstep\s*time\s*\(h\)\b", "h"),
        ],
        "Current / A": [
            (r"\bcurrent\s*\(ma\)\b", "mA"),
            (r"\bcurrent\s*\(ua\)\b|\bcurrent\s*\(µa\)\b|\bcurrent\s*\(μa\)\b", "uA"),
        ],
        "Voltage / V": [
            (r"\bpotential\s*\(mv\)\b|\bvoltage\s*\(mv\)\b", "mV"),
        ],
    }
