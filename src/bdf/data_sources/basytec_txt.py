# src/bdf/data_sources/basytec_txt.py
from __future__ import annotations

from .base_delimited import DelimitedTextPlugin


class BasytecTxt(DelimitedTextPlugin):
    """Basytec TXT/DAT; header often like: ~Time[h] ... U[V] I[A] ..."""
    id = "basytec-txt"
    exts = (".txt", ".dat")
    default_encoding = "latin-1"
    header_prefix_to_strip = "~"   # <- base will strip leading "~" from header cells

    # Lightweight sniffing (base uses these + exts)
    magic = ("resultfile from basytec battery test system", "basytec battery test system")
    header_token_patterns = (
        r"\btime\[(s|h|min)\]",    # Time[h] / Time[min] / Time[s]
        r"\bu\[(v|mv)\]",          # U[V] / U[mV]
        r"\bi\[(a|ma|ua)\]",       # I[A] / I[mA] / I[uA]
    )

    # Map vendor headers -> canonical BDF names (case-insensitive)
    column_synonyms = {
        "Test Time / s": ["time[s]", "time[h]", "time[min]", "time[h:min:s]", "time"],
        "Voltage / V":   ["u[v]", "voltage[v]", "u", "voltage"],
        "Current / A":   ["i[a]", "current[a]", "i", "current"],
        # Optional (kept if present)
        "Ambient Temperature / degC": ["t1[°c]", "t1[c]", "t1[degc]", "temp[°c]", "temperature[°c]"],
        "Net Capacity / Ah": ["ah[ah]"],
        "Step Index / 1": ["line"],
    }
    

    # Extract start time from the "~Start of Test: DD.MM.YYYY HH:MM:SS" header line
    start_time_line_regex = r"~Start of Test:\s*(.+)"
    start_time_format = "%d.%m.%Y %H:%M:%S"

    # Unit hints so the base .fixup() converts to canonical units:
    #   Current -> A, Time -> s, Voltage -> V, Capacity -> Ah
    unit_column_patterns = {
        "Test Time / s": [
            (r"^time\[s\]$", "s"),
            (r"^time\[h\]$", "h"),
            (r"^time\[min\]$", "min"),
            (r"^time\[h:min:s\]$", "hms"),
        ],
        "Voltage / V": [
            (r"^u\[v\]$",  "V"),
            (r"^u\[mv\]$", "mV"),
        ],
        "Current / A": [
            (r"^i\[a\]$",  "A"),
            (r"^i\[ma\]$", "mA"),
            (r"^i\[ua\]$", "uA"),
        ],
        "Net Capacity / Ah": [
            (r"^ah\[ah\]$", "Ah"),
        ],
    }
