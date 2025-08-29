from __future__ import annotations
import re
import pandas as pd

# Canonical BDF columns
REQUIRED = ["Test Time / s", "Voltage / V", "Current / A"]
OPTIONAL = ["Ambient Temperature / degC", "Step Time / s"]  # <-- add Step Time

# Vendor -> BDF synonyms (lowercase matching)
SYNONYMS = {
    "Test Time / s": [
        # Bio-Logic (seconds)
        "time/s", "time / s", "test time / s", "time [s]", "total time (s)", "totaltime(s)", "t (s)",
        # NEWARE numeric seconds (prefer 'total time(s)' when both exist)
        "total time(s)", "total time (s)",
        # NEWARE hms strings that represent cumulative time
        "relative time(h:mm:ss.ms)", "relative time(h:mm:ss)", "record time(h:mm:ss)",
        "relative time(h:min:s.ms)", "record time(h:min:s.ms)",
        # Chinese (common)
        "时间(s)",
    ],
    "Step Time / s": [
        # NEWARE step time (resets each step)
        "time(s)", "time (s)", "step time(s)", "step time (s)", "work time(s)", "work time(h:min:s.ms)",
        "time(h:min:s.ms)",  # often step-relative in some exports
    ],
    "Voltage / V": [
        "voltage / v", "ewe/v", "ecell/v", "voltage(v)", "cell voltage (v)", "电压(v)",
    ],
    "Current / A": [
        "current / a", "i/a", "current(a)", "amps", "i (a)",
        "i/ma", "current (ma)", "current/ma", "i,ma", "i_ma",
        "电流(a)", "电流(ma)",
    ],
    "Ambient Temperature / degC": [
        "ambient temperature / degc", "temperature/°c", "temperature / degc",
        "temp (°c)", "temperature(°c)", "env temp(°c)", "温度(°c)",
    ],
}

def _norm(s: str) -> str:
    s = s.lower().strip().replace("\ufeff", "")
    s = re.sub(r"\s+", " ", s)
    return s

def _find_col(df: pd.DataFrame, candidates_lc: list[str]) -> tuple[str | None, str | None]:
    """Return (original_column_name, matched_key) ignoring case/spacing/punct."""
    cols_map = {_norm(c): c for c in df.columns}
    for key in candidates_lc:
        nk = _norm(key)
        if nk in cols_map:
            return cols_map[nk], key
    cols_map2 = {re.sub(r"[^\w]+", "", k): v for k, v in cols_map.items()}
    for key in candidates_lc:
        nk = re.sub(r"[^\w]+", "", key.lower())
        if nk in cols_map2:
            return cols_map2[nk], key
    return None, None

def _to_seconds_from_hms(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip().str.replace(",", ".", regex=False)
    td = pd.to_timedelta(s, errors="coerce")
    return td.dt.total_seconds()

def _to_seconds_from_datetime(series: pd.Series) -> pd.Series:
    dt = pd.to_datetime(series, errors="coerce", utc=True)
    if dt.notna().any():
        t0 = dt[dt.notna()].iloc[0]
        return (dt - t0).dt.total_seconds()
    return pd.Series([pd.NA] * len(series), index=series.index, dtype="float")

def _derive_test_time_seconds(df: pd.DataFrame) -> pd.Series | None:
    """Prefer NEWARE 'Total Time(s)' → Test Time / s; else fall back to other cumulative time hints."""
    # 1) Strong preference: explicit total test time (NEWARE)
    prefer = ["total time(s)", "total time (s)"]
    col, _ = _find_col(df, prefer)
    if col is not None:
        s = pd.to_numeric(df[col], errors="coerce")
        if s.notna().any():
            return s

    # 2) Other cumulative time fields (hms style)
    hms_cum = [
        "relative time(h:mm:ss.ms)", "relative time(h:mm:ss)",
        "record time(h:mm:ss)", "relative time(h:min:s.ms)", "record time(h:min:s.ms)"
    ]
    col, _ = _find_col(df, hms_cum)
    if col is not None:
        s = _to_seconds_from_hms(df[col])
        if s.notna().any():
            return s

    # 3) Numeric seconds that may already be total (Bio-Logic / some exports)
    numeric_secs = ["time/s", "test time / s", "time [s]", "totaltime(s)", "total time (s)", "时间(s)"]
    col, _ = _find_col(df, numeric_secs)
    if col is not None:
        s = pd.to_numeric(df[col], errors="coerce")
        if s.notna().any():
            return s

    # 4) Absolute timestamps → seconds since start
    dt_candidates = ["datetime", "date time", "date/time", "时间"]
    col, _ = _find_col(df, dt_candidates)
    if col is not None:
        s = _to_seconds_from_datetime(df[col])
        if s.notna().any():
            return s

    return None

def _derive_step_time_seconds(df: pd.DataFrame) -> pd.Series | None:
    """Map NEWARE step time ('Time(s)') to Step Time / s; also accept hms step-relative fields."""
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

def to_bdf(df_vendor: pd.DataFrame, *, plugin_id: str | None = None) -> pd.DataFrame:
    """Map vendor columns to BDF canonical names and convert units."""
    df = df_vendor.copy()
    produced: dict[str, pd.Series] = {}

    # ---- Voltage / Current / Temperature ----
    for canon in ("Voltage / V", "Current / A", "Ambient Temperature / degC"):
        syns = SYNONYMS.get(canon, [])
        orig, matched_key = _find_col(df, [s.lower() for s in syns + [canon]])
        if orig is None:
            continue
        series = pd.to_numeric(df[orig], errors="coerce")
        if canon == "Current / A":
            src_lc = (matched_key or orig).lower()
            if "ma" in src_lc:
                series = series * 1e-3
        produced[canon] = series

    # ---- Test Time / s (cumulative) ----
    tsec = _derive_test_time_seconds(df)
    if tsec is not None:
        produced["Test Time / s"] = tsec

    # ---- Step Time / s (resets per step) ----
    step_t = _derive_step_time_seconds(df)
    if step_t is not None:
        produced["Step Time / s"] = step_t

    # ---- Build output ----
    out_cols = [c for c in REQUIRED if c in produced] + [c for c in OPTIONAL if c in produced]
    out = pd.DataFrame({c: produced[c] for c in out_cols})

    # Validate required columns are present
    missing = [c for c in REQUIRED if c not in out.columns]
    if missing:
        raise ValueError(
            "Missing required BDF columns after normalization: "
            f"{missing}. Vendor columns were: {list(df.columns)}"
        )

    # Clean up & rebase time to start at zero if needed
    out = out.dropna(subset=[c for c in REQUIRED], how="all")
    if "Test Time / s" in out:
        out = out[out["Test Time / s"].notna()]
        if out["Test Time / s"].min() > 0:
            out["Test Time / s"] = out["Test Time / s"] - out["Test Time / s"].iloc[0]

    return out.reset_index(drop=True)
