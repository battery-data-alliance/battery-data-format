# src/bdf/repair.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Optional SciPy robust stats (preferred), with graceful fallback
try:
    from scipy import stats as sps  # type: ignore
except Exception:
    sps = None  # type: ignore

TIME_COL = "Test Time / s"
DEFAULT_OUTLIER_COLS = ("Voltage / V", "Current / A")

__all__ = ["fix_time", "clean_bdf", "CleanReport"]

# -----------------------------
# Reporting
# -----------------------------
@dataclass
class CleanReport:
    n_rows_in: int
    n_rows_out: int
    time_method: str
    n_time_resets: int
    outlier_method: str
    z_thresh: float
    per_column_outliers: Dict[str, int]
    notes: List[str]

    def __str__(self) -> str:
        lines = [
            f"Rows: {self.n_rows_in} → {self.n_rows_out}",
            f"Time fix: {self.time_method} (resets={self.n_time_resets})",
            f"Outliers: {self.outlier_method} (z>{self.z_thresh:g})",
        ]
        if self.per_column_outliers:
            lines.append(
                "Per-column outliers: "
                + ", ".join(f"{k}={v}" for k, v in self.per_column_outliers.items())
            )
        if self.notes:
            lines.append("Notes:")
            lines += [f"  - {n}" for n in self.notes]
        return "\n".join(lines)


# -----------------------------
# Time helpers
# -----------------------------
def _compute_eps_from_diffs(diffs: np.ndarray) -> float:
    """Auto epsilon = 0.1 * median(positive diffs), floored at 1e-9."""
    pos = diffs[diffs > 0]
    med = float(np.nanmedian(pos)) if pos.size else 0.0
    return max(1e-9, 0.1 * med)


def _median_positive_dt(ts: np.ndarray) -> float:
    diffs = np.diff(ts)
    pos = diffs[diffs > 0]
    if pos.size == 0:
        return 1.0
    return float(np.nanmedian(pos))


def _fix_time_between_neighbors(
    t: pd.Series, eps: float | str = "auto"
) -> Tuple[pd.Series, int]:
    """
    Make time monotonic by placing each non-monotonic block strictly between its
    two monotonic neighbors. Keeps all rows and preserves ordering.

    For a block starting at i where t[i] < t[i-1]-eps and ending before the
    first r where t[r] >= t[i-1]+eps, linearly interpolate times for i..r-1
    between t[i-1] and t[r]. If no r exists, use median_dt to synthesize a right neighbor.
    """
    ts = pd.to_numeric(t, errors="coerce").to_numpy(dtype="float64")
    n = ts.size
    if n <= 1:
        return pd.Series(ts, index=t.index), 0

    diffs = np.diff(ts, prepend=ts[0])
    eps_val = _compute_eps_from_diffs(diffs) if eps == "auto" else float(eps)
    median_dt = _median_positive_dt(ts)

    tc = ts.copy()
    i = 1
    resets = 0
    while i < n:
        if tc[i] >= tc[i - 1] - eps_val:
            i += 1
            continue

        # start of non-monotonic block
        left_time = tc[i - 1]
        j = i
        # find first index where we recover past left_time (by eps)
        while j < n and ts[j] < left_time + eps_val:
            j += 1

        block_len = j - i
        if block_len <= 0:
            i += 1
            continue

        if j < n:
            right_time = ts[j]
            span = max(right_time - left_time, median_dt * (block_len + 1))
        else:
            span = median_dt * (block_len + 1)
            right_time = left_time + span

        step = span / (block_len + 1)
        for k in range(block_len):
            tc[i + k] = left_time + step * (k + 1)

        resets += 1
        i = j

    return pd.Series(tc, index=t.index), resets


def _fix_time_sort(df: pd.DataFrame) -> pd.DataFrame:
    """Stable sort by time and drop exact duplicate timestamps (keep first)."""
    d = df.sort_values(TIME_COL, kind="mergesort").copy()
    d = d.loc[~d[TIME_COL].duplicated(keep="first")].reset_index(drop=True)
    return d


# -----------------------------
# Outlier helpers (SciPy-aware)
# -----------------------------
def _window_len_from_seconds(time_s: pd.Series, seconds: float, fallback: int = 41) -> int:
    t = pd.to_numeric(time_s, errors="coerce")
    if t.notna().sum() > 1:
        dt = np.median(np.diff(t.dropna().to_numpy()))
        if np.isfinite(dt) and dt > 0:
            w = int(round(seconds / dt))
            if w % 2 == 0:
                w += 1  # prefer odd window
            return max(5, w)
    return fallback


def _global_mad_z(x: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Robust z via MAD (σ ≈ MAD*1.4826). Returns (z, median, madn)."""
    med = float(np.nanmedian(x))
    if sps is not None:
        madn = float(sps.median_abs_deviation(x, nan_policy="omit", scale="normal"))
    else:
        mad = float(np.nanmedian(np.abs(x - med)))
        madn = 1.4826 * mad
    if not np.isfinite(madn) or madn <= 0:
        return np.zeros_like(x), med, 0.0
    return (x - med) / madn, med, madn


def _global_huber_z(x: np.ndarray, c: float = 1.345) -> tuple[np.ndarray, float, float]:
    """
    Robust z via Huber M-estimator (requires SciPy). Returns (z, loc, scale).
    If SciPy missing or scale <= 0, returns zeros.
    """
    if sps is None or not hasattr(sps, "huber"):
        return np.zeros_like(x), float("nan"), 0.0
    try:
        loc, scale = sps.huber(x, c=c)
    except Exception:
        return np.zeros_like(x), float("nan"), 0.0
    if not np.isfinite(scale) or scale <= 0:
        return np.zeros_like(x), loc, 0.0
    return (x - loc) / scale, loc, scale


def _local_robust_z(
    s: pd.Series, *, time_s: pd.Series, seconds: float, z: float
) -> pd.Series:
    """
    Local robust z using rolling IQR (σ ≈ IQR/1.349).
    Flags |z_local| > z within the window.
    """
    w = _window_len_from_seconds(time_s, seconds)
    x = pd.to_numeric(s, errors="coerce")
    med = x.rolling(w, center=True, min_periods=max(3, w // 3)).median()
    q1 = x.rolling(w, center=True, min_periods=max(3, w // 3)).quantile(0.25)
    q3 = x.rolling(w, center=True, min_periods=max(3, w // 3)).quantile(0.75)
    sigma = (q3 - q1) / 1.349
    rz = (x - med) / sigma.replace(0, np.nan)
    return (rz.abs() > z).fillna(False)


def _hampel_mask(
    s: pd.Series, *, time_s: pd.Series, seconds: float, k: float = 6.0
) -> pd.Series:
    """
    Hampel filter: rolling median ± k * MADN.
    Flags samples deviating more than k scaled MAD from rolling median.
    """
    w = _window_len_from_seconds(time_s, seconds)
    x = pd.to_numeric(s, errors="coerce")
    med = x.rolling(w, center=True, min_periods=max(3, w // 3)).median()
    abs_dev = (x - med).abs()
    mad = abs_dev.rolling(w, center=True, min_periods=max(3, w // 3)).median()
    madn = 1.4826 * mad
    return ((x - med).abs() / madn.replace(0, np.nan) > k).fillna(False)


def _slope_mask(
    s: pd.Series, *, time_s: pd.Series, z: float = 8.0
) -> pd.Series:
    """
    Slope gate: robust z on derivative ds/dt using global MAD.
    Catches single-sample spikes that might pass level-based gates.
    """
    x = pd.to_numeric(s, errors="coerce").to_numpy(dtype="float64")
    t = pd.to_numeric(time_s, errors="coerce").to_numpy(dtype="float64")
    dx = np.diff(x, prepend=np.nan)
    dt = np.diff(t, prepend=np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        deriv = dx / dt
    zder, _, madn = _global_mad_z(deriv)
    m = (np.abs(zder) > z) if madn > 0 else np.zeros_like(deriv, dtype=bool)
    m[~np.isfinite(deriv)] = False
    return pd.Series(m, index=s.index)


def _robust_outlier_mask(
    s: pd.Series,
    *,
    z_mad: float = 8.0,
    z_huber: float = 6.0,
    local_seconds: float | None = 600.0,   # default ON (10 min)
    local_z: float = 6.0,
    hampel_seconds: float | None = 300.0,  # default ON (5 min)
    hampel_k: float = 6.0,
    slope_gate: bool = True,
    slope_z: float = 8.0,
    method: str = "hybrid",                # 'mad' | 'huber' | 'hybrid'
    min_n: int = 30,
    time_s: pd.Series | None = None,
) -> pd.Series:
    """
    Robust outlier mask using (global) MAD & optional Huber, plus neighborhood gates:
      - Local rolling IQR z
      - Hampel filter
      - Slope z on derivative
    Combine as: (GLOBAL AND (LOCAL OR HAMPEL)) OR SLOPE.
    """
    x = pd.to_numeric(s, errors="coerce").to_numpy(dtype="float64", na_value=np.nan)
    valid = np.isfinite(x)
    if valid.sum() < min_n:
        return pd.Series(False, index=s.index)

    xv = x.copy()
    xv[~valid] = np.nan

    z1, _, madn = _global_mad_z(xv)
    if method == "mad":
        m_global = (np.abs(z1) > z_mad) if madn > 0 else np.zeros_like(x, dtype=bool)
    elif method == "huber":
        z2, _, scale = _global_huber_z(xv)
        m_global = (np.abs(z2) > z_huber) if scale > 0 else np.zeros_like(x, dtype=bool)
    else:  # 'hybrid'
        z2, _, scale = _global_huber_z(xv)
        m1 = (np.abs(z1) > z_mad) if madn > 0 else np.zeros_like(x, dtype=bool)
        m2 = (np.abs(z2) > z_huber) if scale > 0 else m1  # fall back to MAD if Huber unavailable
        m_global = m1 & m2  # conservative: both must agree

    # neighborhood gates
    m_neigh = None
    if time_s is not None:
        m_local = (
            _local_robust_z(s, time_s=time_s, seconds=local_seconds, z=local_z)
            if local_seconds
            else None
        )
        m_hampel = (
            _hampel_mask(s, time_s=time_s, seconds=hampel_seconds, k=hampel_k)
            if hampel_seconds
            else None
        )
        if m_local is not None and m_hampel is not None:
            m_neigh = m_local | m_hampel
        elif m_local is not None:
            m_neigh = m_local
        elif m_hampel is not None:
            m_neigh = m_hampel

    m = m_global if m_neigh is None else (m_global & m_neigh)
    if slope_gate and time_s is not None:
        m = m | _slope_mask(s, time_s=time_s, z=slope_z).values

    out = np.zeros_like(x, dtype=bool)
    out[:] = m
    return pd.Series(out, index=s.index)


def _interp_inplace(y: pd.Series, x: Optional[pd.Series]) -> pd.Series:
    if x is not None:
        yi = pd.to_numeric(y, errors="coerce").astype("float64")
        xi = pd.to_numeric(x, errors="coerce")
        return yi.interpolate(method="values", x=xi, limit_direction="both")
    return y.interpolate(limit_direction="both")


# -----------------------------
# Public API — simple time repair
# -----------------------------
def fix_time(
    df: pd.DataFrame,
    *,
    method: str = "auto",               # 'auto'|'segment'|'sort'|'drop'|'recompute'
    time_col: str = TIME_COL,
    date_col: str = "Date Time ISO",
    eps: float | str = "auto",
    inplace: bool = False,
) -> pd.DataFrame:
    """
    Repair non-monotonic test time.

    Methods:
      - 'auto': if Date Time ISO exists & usable, recompute from timestamps; else 'segment'.
      - 'segment': preserve order; interpolate within each decreasing block.
      - 'sort': stable sort by time ascending; drop exact duplicate timestamps.
      - 'drop': drop rows where time decreases by more than 'eps'.
      - 'recompute': force recompute from Date Time ISO; raises if no valid timestamps.
    """
    g = df if inplace else df.copy()
    if time_col not in g.columns:
        return g

    if method in ("auto", "recompute"):
        if date_col in g.columns:
            t = pd.to_datetime(g[date_col], errors="coerce")
            if t.notna().any():
                t0 = t[t.notna()].iloc[0]
                g[time_col] = (t - t0).dt.total_seconds()
                return g
        if method == "recompute":
            raise ValueError(f"Cannot recompute from '{date_col}': no valid timestamps.")

    if method in ("auto", "segment"):
        g[time_col], _ = _fix_time_between_neighbors(g[time_col], eps=eps)
        return g

    if method == "sort":
        g.sort_values(by=[time_col], kind="mergesort", inplace=True)
        g.drop_duplicates(subset=[time_col], keep="first", inplace=True)
        g.reset_index(drop=True, inplace=True)
        return g

    if method == "drop":
        s = pd.to_numeric(g[time_col], errors="coerce")
        d = s.diff().fillna(0.0)
        if eps == "auto":
            eps = _compute_eps_from_diffs(d.to_numpy())
        keep = d >= -float(eps)
        keep.iloc[0] = True
        g = g.loc[keep].reset_index(drop=True)
        return g

    raise ValueError(f"Unknown method: {method!r}")


# -----------------------------
# Public API — full cleaner
# -----------------------------
def clean_bdf(
    df: pd.DataFrame,
    *,
    time_fix: str = "segment",  # 'segment' | 'sort' | 'drop' | 'none'
    outlier: str = "none",      # 'none' | 'drop' | 'clip' | 'interp'
    z_thresh: float = 8.0,      # used for MAD/global & clip bounds
    columns: Optional[List[str]] = None,   # columns to outlier-clean
    time_eps: float | str = "auto",        # threshold for detecting time drops
    # robust detection knobs
    outlier_detect: str = "hybrid",        # 'mad' | 'huber' | 'hybrid'
    local_seconds: Optional[float] = 600.0, # local window (sec) for neighborhood z (None to disable)
    local_z: float = 6.0,
    z_huber: float = 6.0,
    hampel_seconds: Optional[float] = 300.0,
    hampel_k: float = 6.0,
    slope_gate: bool = True,
    slope_z: float = 8.0,
) -> Tuple[pd.DataFrame, CleanReport]:
    """
    Clean a BDF-normalized DataFrame.

    - time_fix:
        'segment'  -> place non-monotonic blocks between neighbors (keeps rows; default)
        'sort'     -> stable sort by time; drop duplicate timestamps
        'drop'     -> drop rows where time decreases beyond 'time_eps'
        'none'     -> leave time as-is
    - outlier (action on flagged rows/values):
        'drop'     -> drop any row where selected columns are flagged as outliers
        'clip'     -> winsorize flagged values back to robust bounds
        'interp'   -> replace flagged values with NaN and linearly interpolate
        'none'     -> no outlier clean
    - outlier_detect (how to flag):
        'mad'      -> global MAD z-score only
        'huber'    -> global Huber z-score only (SciPy; falls back to MAD if unavailable)
        'hybrid'   -> BOTH global MAD and Huber must flag (reduces false positives).
    - local_seconds / hampel_seconds / slope_gate:
        Neighborhood & derivative gates to catch single-sample spikes and suppress
        false positives on slow drifts. Combined as: (GLOBAL AND (LOCAL OR HAMPEL)) OR SLOPE.
    """
    if TIME_COL not in df.columns:
        raise ValueError(f"Missing '{TIME_COL}'. Did you normalize to BDF?")

    notes: List[str] = []
    d = df.copy()
    n_in = len(d)
    cols = [c for c in (columns or DEFAULT_OUTLIER_COLS) if c in d.columns]

    # ---- Fix time ----
    t_numeric = pd.to_numeric(d[TIME_COL], errors="coerce")
    diffs = np.diff(t_numeric.to_numpy(dtype="float64"), prepend=t_numeric.iloc[0])
    eps_val = _compute_eps_from_diffs(diffs) if time_eps == "auto" else float(time_eps)
    n_resets_detected = int((diffs < -eps_val).sum())

    if time_fix == "segment":
        d[TIME_COL], n_resets_detected = _fix_time_between_neighbors(d[TIME_COL], eps=time_eps)
        time_method_used = "segment"
    elif time_fix == "sort":
        d = _fix_time_sort(d)
        time_method_used = "sort"
        n_resets_detected = 0
    elif time_fix == "drop":
        keep = np.concatenate(([True], diffs[1:] >= -eps_val))
        dropped = int((~keep).sum())
        if dropped:
            notes.append(f"Dropped {dropped} rows due to time decreases.")
        d = d.loc[keep].reset_index(drop=True)
        time_method_used = "drop"
    elif time_fix == "none":
        time_method_used = "none"
    else:
        raise ValueError("time_fix must be one of: 'segment','sort','drop','none'")

    # Rebase to start at zero if positive
    tmin = pd.to_numeric(d[TIME_COL], errors="coerce").min()
    if np.isfinite(tmin) and tmin > 0:
        d[TIME_COL] = pd.to_numeric(d[TIME_COL], errors="coerce") - float(tmin)

    # ---- Outliers ----
    per_col: Dict[str, int] = {}
    if outlier != "none" and cols:
        masks: Dict[str, pd.Series] = {}
        for c in cols:
            masks[c] = _robust_outlier_mask(
                d[c],
                z_mad=z_thresh,
                z_huber=z_huber,
                local_seconds=local_seconds,
                local_z=local_z,
                hampel_seconds=hampel_seconds,
                hampel_k=hampel_k,
                slope_gate=slope_gate,
                slope_z=slope_z,
                method=outlier_detect,
                min_n=30,
                time_s=d[TIME_COL],
            )
            per_col[c] = int(masks[c].sum())

        if outlier == "drop":
            any_bad = np.logical_or.reduce([m.values for m in masks.values()]) if masks else np.zeros(len(d), dtype=bool)
            d = d.loc[~any_bad].reset_index(drop=True)
            notes.append(f"Dropped {int(any_bad.sum())} rows due to outliers in {', '.join(cols)}.")
        elif outlier == "clip":
            # robust bounds via MAD (SciPy if available), fallback to IQR
            for c, m in masks.items():
                s = pd.to_numeric(d[c], errors="coerce")
                med = float(np.nanmedian(s))
                if sps is not None:
                    madn = float(sps.median_abs_deviation(s.to_numpy(), nan_policy="omit", scale="normal"))
                else:
                    mad = float(np.nanmedian(np.abs(s - med)))
                    madn = 1.4826 * mad
                if madn and madn > 0:
                    lo, hi = med - z_thresh * madn, med + z_thresh * madn
                else:
                    # fallback to IQR
                    q1, q3 = np.nanpercentile(s, [25, 75])
                    iqr = q3 - q1
                    if iqr == 0:
                        continue
                    sigma = iqr / 1.349
                    lo, hi = med - z_thresh * sigma, med + z_thresh * sigma
                d[c] = s.clip(lo, hi)
            notes.append("Clipped outliers to robust bounds (MAD/IQR).")
        elif outlier == "interp":
            tx = pd.to_numeric(d[TIME_COL], errors="coerce")
            for c, m in masks.items():
                s = pd.to_numeric(d[c], errors="coerce")
                s = s.mask(m, np.nan)
                d[c] = _interp_inplace(s, tx)
            notes.append("Interpolated outliers linearly over time.")
        else:
            raise ValueError("outlier must be one of: 'none','drop','clip','interp'")

    rep = CleanReport(
        n_rows_in=n_in,
        n_rows_out=len(d),
        time_method=time_method_used,
        n_time_resets=n_resets_detected,
        outlier_method=outlier,
        z_thresh=z_thresh,
        per_column_outliers=per_col,
        notes=notes,
    )
    return d, rep


