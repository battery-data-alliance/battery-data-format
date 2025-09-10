from __future__ import annotations
import re
import pandas as pd

# ---------------------------
# Canonical BDF columns
# ---------------------------
REQUIRED = ["Test Time / s", "Voltage / V", "Current / A"]
OPTIONAL = ["Ambient Temperature / degC", "Step Time / s"]

# ---------------------------
# Vendor -> BDF synonyms (lowercase matching)
# ---------------------------
SYNONYMS = {
    "Test Time / s": [
        # Bio-Logic / NEWARE / Landt
        "time/s", "time / s", "test time / s", "time [s]", "total time (s)", "totaltime(s)", "t (s)",
        "total time(s)", "relative time(h:mm:ss.ms)", "relative time(h:mm:ss)", "record time(h:mm:ss)",
        "relative time(h:min:s.ms)", "record time(h:min:s.ms)", "时间(s)",
        # Landt TXT + CSV
        "test(sec)", "test (sec)", "test(s)", "test_time_s",
        # Basytec (hours; convert → seconds)
        "time[h]",
    ],
    "Step Time / s": [
        # NEWARE / Landt
        "time(s)", "time (s)", "step time(s)", "step time (s)", "work time(s)", "work time(h:min:s.ms)",
        "time(h:min:s.ms)", "step(sec)", "step (sec)", "step_time_s",
        # Basytec (hours; convert → seconds)
        "t-set[h]",
    ],
    "Voltage / V": [
        "voltage / v", "ewe/v", "ecell/v", "voltage(v)", "cell voltage (v)", "电压(v)", "volts",
        "voltage_v",
        # Basytec
        "u[v]",
    ],
    "Current / A": [
        "current / a", "i/a", "current(a)", "amps", "i (a)",
        "i/ma", "current (ma)", "current/ma", "i,ma", "i_ma",
        "电流(a)", "电流(ma)",
        "current_a",
        # Basytec
        "i[a]",
    ],
    "Ambient Temperature / degC": [
        "ambient temperature / degc", "temperature/°c", "temperature / degc",
        "temp (°c)", "temperature(°c)", "env temp(°c)", "温度(°c)",
        # Landt CSV probes
        "temperature_c", "temperature_1_c", "temperature_2_c", "temperature_3_c",
        # Basytec (mojibake/variants)
        "t1[°c]", "t1[c]", "t1[�c]",
    ],

    # ------- Optional extras (kept here for future use) -------
    "Capacity / Ah": [
        "amp-hr", "amp hour", "capacity (ah)", "capacity/ah", "charge capacity (ah)", "discharge capacity (ah)",
        "q,ah", "q (ah)", "q_ah",
    ],
    "Energy / Wh": [
        "watt-hr", "watt hour", "energy (wh)", "energy/wh", "e,wh", "e (wh)", "e_wh",
    ],
    "Charge Capacity / Ah": ["charge_capacity_ah"],
    "Discharge Capacity / Ah": ["discharge_capacity_ah"],
    "Charge Energy / Wh": ["charge_energy_wh"],
    "Discharge Energy / Wh": ["discharge_energy_wh"],

    # Indices
    "Cycle Index": ["cyc#", "cycle", "cycle #", "cycle index", "cyc no.", "cyc no", "cyc", "cycle_index"],
    "Step Index": ["step", "step #", "step index", "step no.", "step no", "step_index"],
    "Channel Index": ["channel_index"],

    # Timestamps
    "Date Time": [
        "dpt-time", "date time", "datetime", "date-time", "time stamp", "timestamp", "date/time",
        "date_time_iso_string",
    ],

    # Landt CSV step labels
    "Step Name": ["step_name"],

    # Landt CSV pressure
    "Pressure / psi": ["pressure_psi"],
}

# ---------------------------
# Matching helpers
# ---------------------------

def _norm(s: str) -> str:
    """Normalize a header cell for matching."""
    s = str(s)
    s = s.lower().strip().lstrip("~").replace("\ufeff", "")
    s = re.sub(r"\s+", " ", s)
    return s

def _normkey(s: str) -> str:
    """Lowercase + strip non-alphanumerics so 'Test Time / s' ~ 'test_time_s' ~ 'Test(Sec)'."""
    return re.sub(r"[^a-z0-9]+", "", str(s).strip().lower())

def _find_col(df: pd.DataFrame, candidates_lc: list[str]) -> tuple[str | None, str | None]:
    """
    Return (original_column_name, matched_candidate) by comparing normalized keys.
    First try exact normalized equality, then normalized substring fallback.
    """
    lut = {_normkey(c): c for c in df.columns}  # normalized -> original
    # exact normalized match
    for cand in candidates_lc:
        nk = _normkey(cand)
        if nk in lut:
            return lut[nk], cand
    # substring fallback
    for cand in candidates_lc:
        nk = _normkey(cand)
        for key_norm, orig in lut.items():
            if nk and nk in key_norm:
                return orig, cand
    return None, None

# ---------------------------
# Time conversions
# ---------------------------

def _to_seconds_from_hms(series: pd.Series) -> pd.Series:
    # normalize decimal comma to dot (e.g., "00:01:02,5")
    s = series.astype(str).str.strip().str.replace(",", ".", regex=False)
    td = pd.to_timedelta(s, errors="coerce")
    return td.dt.total_seconds()

def _to_seconds_from_datetime(series: pd.Series) -> pd.Series:
    dt = pd.to_datetime(series, errors="coerce", utc=True)
    if dt.notna().any():
        t0 = dt[dt.notna()].iloc[0]
        return (dt - t0).dt.total_seconds()
    return pd.Series([pd.NA] * len(series), index=series.index, dtype="float")

def _derive_test_time_seconds(df: pd.DataFrame, plugin_id: str | None = None) -> pd.Series | None:
    """
    Prefer explicit cumulative seconds if available, else try h:m:s strings,
    else derive from absolute timestamps.
    Special-case Basytec: strongly prefer Time[h] (hours) → seconds.
    """
    # --- Basytec priority: Time[h] (hours) -> seconds ---
    if plugin_id and "basytec" in plugin_id.lower():
        col, _ = _find_col(df, ["time[h]"])
        if col is not None:
            s = pd.to_numeric(df[col], errors="coerce")
            if s.notna().any():
                return s * 3600.0

    # 1) NEWARE explicit cumulative seconds
    ordered = [
        "total time(s)", "total time (s)",
        # Landt CSV explicit seconds
        "test_time_s",
        # Other numeric seconds / common variants
        "time/s", "test time / s", "time [s]", "totaltime(s)", "时间(s)", "t (s)",
        # Landt TXT variants
        "test(sec)", "test (sec)", "test(s)",
    ]
    col, _ = _find_col(df, ordered)
    if col is not None:
        s = df[col]
        if pd.api.types.is_numeric_dtype(s):
            return pd.to_numeric(s, errors="coerce")
        if s.astype(str).str.contains(":").any():
            return _to_seconds_from_hms(s)
        return pd.to_numeric(s, errors="coerce")

    # 2) Other cumulative time fields (hms style)
    hms_cum = [
        "relative time(h:mm:ss.ms)", "relative time(h:mm:ss)",
        "record time(h:mm:ss)", "relative time(h:min:s.ms)", "record time(h:min:s.ms)"
    ]
    col, _ = _find_col(df, hms_cum)
    if col is not None:
        return _to_seconds_from_hms(df[col])

    # 3) Absolute timestamps → seconds since start
    dt_candidates = ["date_time_iso_string", "datetime", "date time", "date/time", "时间", "dpt-time"]
    col, _ = _find_col(df, dt_candidates)
    if col is not None:
        return _to_seconds_from_datetime(df[col])

    return None

def _derive_step_time_seconds(df: pd.DataFrame, plugin_id: str | None = None) -> pd.Series | None:
    """
    Map vendor step time to 'Step Time / s'.
    Special-case Basytec: t-Set[h] (hours) → seconds.
    """
    # --- Basytec priority: t-Set[h] (hours) -> seconds ---
    if plugin_id and "basytec" in plugin_id.lower():
        col, _ = _find_col(df, ["t-set[h]"])
        if col is not None:
            s = pd.to_numeric(df[col], errors="coerce")
            if s.notna().any():
                return s * 3600.0

    candidates = SYNONYMS["Step Time / s"]
    col, _ = _find_col(df, [c.lower() for c in candidates])
    if col is None:
        return None
    series = df[col]
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    if series.astype(str).str.contains(":").any():
        return _to_seconds_from_hms(series)
    return pd.to_numeric(series, errors="coerce")

# ---------------------------
# Main normalization
# ---------------------------

def to_bdf(df_vendor: pd.DataFrame, *, plugin_id: str | None = None) -> pd.DataFrame:
    """
    Map vendor columns to BDF canonical names and convert units.
    - Handles Bio-Logic, NEWARE, Landt (CSV/TXT), and Basytec headers.
    - For Basytec, prefers Time[h] → seconds (and t-Set[h] for step-time).
    """
    df = df_vendor.copy()
    produced: dict[str, pd.Series] = {}

    # --- Optional fast path for Landt CSV (exact snake_case names) ---
    if plugin_id == "landt-csv":
        vmap = {c.lower(): c for c in df.columns}
        for src, canon in [
            ("test_time_s", "Test Time / s"),
            ("voltage_v",   "Voltage / V"),
            ("current_a",   "Current / A"),
            ("step_time_s", "Step Time / s"),
        ]:
            if src in vmap and canon not in produced:
                series = df[vmap[src]]
                if canon.endswith("/ s"):  # time columns
                    if pd.api.types.is_numeric_dtype(series):
                        produced[canon] = pd.to_numeric(series, errors="coerce")
                    elif series.astype(str).str.contains(":").any():
                        produced[canon] = _to_seconds_from_hms(series)
                    else:
                        produced[canon] = pd.to_numeric(series, errors="coerce")
                else:
                    produced[canon] = pd.to_numeric(series, errors="coerce")

    # --- Voltage / Current / Temperature via synonyms ---
    for canon in ("Voltage / V", "Current / A", "Ambient Temperature / degC"):
        syns = SYNONYMS.get(canon, [])
        orig, matched_key = _find_col(df, [s.lower() for s in syns] + [canon])
        if orig is None:
            continue
        series = pd.to_numeric(df[orig], errors="coerce")
        # Normalize current units if header indicates mA
        if canon == "Current / A":
            src_lc = (matched_key or orig).lower()
            if "ma" in src_lc:  # headers like "i/ma" or "current (ma)"
                series = series * 1e-3
        produced[canon] = series

    # --- Test Time / s (cumulative) ---
    if "Test Time / s" not in produced:
        tsec = _derive_test_time_seconds(df, plugin_id=plugin_id)
        if tsec is not None:
            produced["Test Time / s"] = tsec

    # --- Step Time / s (resets per step) ---
    step_t = _derive_step_time_seconds(df, plugin_id=plugin_id)
    if step_t is not None:
        produced["Step Time / s"] = step_t

    # --- Build output (required + declared optional only) ---
    out_cols = [c for c in REQUIRED if c in produced] + [c for c in OPTIONAL if c in produced]
    out = pd.DataFrame({c: produced[c] for c in out_cols})

    # --- Validate required columns are present ---
    missing = [c for c in REQUIRED if c not in out.columns]
    if missing:
        raise ValueError(
            "Missing required BDF columns after normalization: "
            f"{missing}. Vendor columns were: {list(df.columns)}"
        )

    # --- Clean up & rebase time to start at zero if needed ---
    out = out.dropna(subset=[c for c in REQUIRED], how="all")
    if "Test Time / s" in out:
        out = out[out["Test Time / s"].notna()]
        if not out.empty and out["Test Time / s"].min() > 0:
            out["Test Time / s"] = out["Test Time / s"] - out["Test Time / s"].iloc[0]

    return out.reset_index(drop=True)
