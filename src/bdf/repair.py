# src/bdf/repair.py
"""Time repair and outlier cleaning for BDF tables.

Polars-native internals; accepts polars (eager or lazy) or pandas frames and
returns the same kind it was given. All numeric heavy lifting runs on numpy
arrays, so results are identical across input types.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

if TYPE_CHECKING:
    import pandas as pd  # noqa: F401

# Optional SciPy robust stats (preferred), with graceful fallback
try:
    from scipy import stats as sps  # type: ignore
except Exception:
    sps = None  # type: ignore

from . import spec
from ._df_compat import _classify_df, _to_polars_lazy

TIME_COL = spec.COLUMN_ONTOLOGY.test_time_second.formatted_label
DEFAULT_OUTLIER_COLS = (
    spec.COLUMN_ONTOLOGY.voltage_volt.formatted_label,
    spec.COLUMN_ONTOLOGY.current_ampere.formatted_label,
)

__all__ = ["fix_time", "clean", "CleanReport"]


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
            f"Rows: {self.n_rows_in} -> {self.n_rows_out}",
            f"Time fix: {self.time_method} (resets={self.n_time_resets})",
            f"Outliers: {self.outlier_method} (z>{self.z_thresh:g})",
        ]
        if self.per_column_outliers:
            lines.append("Per-column outliers: " + ", ".join(f"{k}={v}" for k, v in self.per_column_outliers.items()))
        if self.notes:
            lines.append("Notes:")
            lines += [f"  - {n}" for n in self.notes]
        return "\n".join(lines)


# -----------------------------
# Frame boundary helpers
# -----------------------------
def _to_polars_eager(df) -> tuple[pl.DataFrame, str]:
    """Convert any supported frame to an eager pl.DataFrame, remembering its kind."""
    kind = _classify_df(df)
    return _to_polars_lazy(df).collect(), kind


def _from_polars_eager(df: pl.DataFrame, kind: str):
    """Convert an eager pl.DataFrame back to the caller's frame kind."""
    if kind == "pandas":
        return df.to_pandas()
    if kind == "polars_lazy":
        return df.lazy()
    return df


def _numeric(df: pl.DataFrame, col: str) -> np.ndarray:
    """Column as float64 numpy array; non-numeric values become NaN."""
    s = df[col]
    s = s.cast(pl.Float64, strict=False) if s.dtype == pl.Utf8 else s.cast(pl.Float64)
    return s.fill_null(float("nan")).to_numpy()


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


def _fix_time_between_neighbors(ts: np.ndarray, eps: float | str = "auto") -> Tuple[np.ndarray, int]:
    """
    Make time monotonic by placing each non-monotonic block strictly between its
    two monotonic neighbors. Keeps all rows and preserves ordering.

    For a block starting at i where t[i] < t[i-1]-eps and ending before the
    first r where t[r] >= t[i-1]+eps, linearly interpolate times for i..r-1
    between t[i-1] and t[r]. If no r exists, use median_dt to synthesize a right neighbor.
    """
    ts = ts.astype("float64")
    n = ts.size
    if n <= 1:
        return ts, 0

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

    return tc, resets


def _fix_time_sort(df: pl.DataFrame, time_col: str = TIME_COL) -> pl.DataFrame:
    """Stable sort by time and drop exact duplicate timestamps (keep first)."""
    return df.sort(time_col, maintain_order=True).unique(subset=[time_col], keep="first", maintain_order=True)


# -----------------------------
# Outlier helpers (SciPy-aware)
# -----------------------------
def _window_len_from_seconds(time_s: np.ndarray, seconds: float, fallback: int = 41) -> int:
    t = time_s[np.isfinite(time_s)]
    if t.size > 1:
        dt = np.median(np.diff(t))
        if np.isfinite(dt) and dt > 0:
            w = int(round(seconds / dt))
            if w % 2 == 0:
                w += 1  # prefer odd window
            return max(5, w)
    return fallback


def _rolling(series: np.ndarray, window: int, min_periods: int, stat: str, q: float | None = None) -> np.ndarray:
    """Centered rolling statistic over a float array, NaN-aware like pandas.

    NaNs are treated as missing: they don't contribute to the statistic and
    don't count toward ``min_periods``.
    """
    s = pl.Series(series).fill_nan(None)
    if stat == "median":
        out = s.rolling_median(window_size=window, min_samples=min_periods, center=True)
    else:
        assert stat == "quantile" and q is not None
        out = s.rolling_quantile(
            quantile=q, interpolation="linear", window_size=window, min_samples=min_periods, center=True
        )
    return out.fill_null(float("nan")).to_numpy()


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


def _local_robust_z(x: np.ndarray, *, time_s: np.ndarray, seconds: float, z: float) -> np.ndarray:
    """
    Local robust z using rolling IQR (σ ≈ IQR/1.349).
    Flags |z_local| > z within the window.
    """
    w = _window_len_from_seconds(time_s, seconds)
    mp = max(3, w // 3)
    med = _rolling(x, w, mp, "median")
    q1 = _rolling(x, w, mp, "quantile", 0.25)
    q3 = _rolling(x, w, mp, "quantile", 0.75)
    sigma = (q3 - q1) / 1.349
    with np.errstate(divide="ignore", invalid="ignore"):
        rz = (x - med) / np.where(sigma == 0, np.nan, sigma)
    return np.nan_to_num(np.abs(rz), nan=0.0) > z


def _hampel_mask(x: np.ndarray, *, time_s: np.ndarray, seconds: float, k: float = 6.0) -> np.ndarray:
    """
    Hampel filter: rolling median ± k * MADN.
    Flags samples deviating more than k scaled MAD from rolling median.
    """
    w = _window_len_from_seconds(time_s, seconds)
    mp = max(3, w // 3)
    med = _rolling(x, w, mp, "median")
    abs_dev = np.abs(x - med)
    mad = _rolling(abs_dev, w, mp, "median")
    madn = 1.4826 * mad
    with np.errstate(divide="ignore", invalid="ignore"):
        score = np.abs(x - med) / np.where(madn == 0, np.nan, madn)
    return np.nan_to_num(score, nan=0.0) > k


def _slope_mask(x: np.ndarray, *, time_s: np.ndarray, z: float = 8.0) -> np.ndarray:
    """
    Slope gate: robust z on derivative ds/dt using global MAD.
    Catches single-sample spikes that might pass level-based gates.
    """
    dx = np.diff(x, prepend=np.nan)
    dt = np.diff(time_s, prepend=np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        deriv = dx / dt
    zder, _, madn = _global_mad_z(deriv)
    m = (np.abs(zder) > z) if madn > 0 else np.zeros_like(deriv, dtype=bool)
    m[~np.isfinite(deriv)] = False
    return m


def _robust_outlier_mask(
    x: np.ndarray,
    *,
    z_mad: float = 8.0,
    z_huber: float = 6.0,
    local_seconds: float | None = 600.0,  # default ON (10 min)
    local_z: float = 6.0,
    hampel_seconds: float | None = 300.0,  # default ON (5 min)
    hampel_k: float = 6.0,
    slope_gate: bool = True,
    slope_z: float = 8.0,
    method: str = "hybrid",  # 'mad' | 'huber' | 'hybrid'
    min_n: int = 30,
    time_s: np.ndarray | None = None,
) -> np.ndarray:
    """
    Robust outlier mask using (global) MAD & optional Huber, plus neighborhood gates:
      - Local rolling IQR z
      - Hampel filter
      - Slope z on derivative
    Combine as: (GLOBAL AND (LOCAL OR HAMPEL)) OR SLOPE.
    """
    valid = np.isfinite(x)
    if valid.sum() < min_n:
        return np.zeros_like(x, dtype=bool)

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
        m_local = _local_robust_z(xv, time_s=time_s, seconds=local_seconds, z=local_z) if local_seconds else None
        m_hampel = _hampel_mask(xv, time_s=time_s, seconds=hampel_seconds, k=hampel_k) if hampel_seconds else None
        if m_local is not None and m_hampel is not None:
            m_neigh = m_local | m_hampel
        elif m_local is not None:
            m_neigh = m_local
        elif m_hampel is not None:
            m_neigh = m_hampel

    m = m_global if m_neigh is None else (m_global & m_neigh)
    if slope_gate and time_s is not None:
        m = m | _slope_mask(xv, time_s=time_s, z=slope_z)
    return m


def _interp_over_time(y: np.ndarray, mask: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Replace masked values with linear interpolation over time, extrapolating flat at the edges."""
    out = y.copy()
    good = np.isfinite(y) & ~mask & np.isfinite(t)
    if good.sum() == 0:
        return out
    bad = mask | ~np.isfinite(y)
    out[bad] = np.interp(t[bad], t[good], y[good])
    return out


# -----------------------------
# Public API -  simple time repair
# -----------------------------
def fix_time(
    df,
    *,
    method: str = "auto",  # 'auto'|'segment'|'sort'|'drop'|'recompute'
    time_col: str = TIME_COL,
    date_col: str = "Date Time ISO",
    eps: float | str = "auto",
    inplace: bool = False,
):
    """
    Repair non-monotonic test time.

    Accepts polars (eager or lazy) or pandas frames; returns the same kind.

    Methods:
      - 'auto': if Date Time ISO exists & usable, recompute from timestamps; else 'segment'.
      - 'segment': preserve order; interpolate within each decreasing block.
      - 'sort': stable sort by time ascending; drop exact duplicate timestamps.
      - 'drop': drop rows where time decreases by more than 'eps'.
      - 'recompute': force recompute from Date Time ISO; raises if no valid timestamps.

    ``inplace=True`` mutates the original frame for pandas input only; polars
    frames are immutable, so the flag has no effect for them.
    """
    g, kind = _to_polars_eager(df)
    if time_col not in g.columns:
        result = g
    else:
        result = _fix_time_polars(g, method=method, time_col=time_col, date_col=date_col, eps=eps)

    if kind == "pandas" and inplace:
        out = result.to_pandas()
        df.drop(df.index, inplace=True)
        for c in out.columns:
            df[c] = out[c].to_numpy()
        return df
    return _from_polars_eager(result, kind)


def _fix_time_polars(g: pl.DataFrame, *, method: str, time_col: str, date_col: str, eps: float | str) -> pl.DataFrame:
    if method in ("auto", "recompute"):
        if date_col in g.columns:
            t = g[date_col].cast(pl.Utf8, strict=False).str.to_datetime(strict=False, time_unit="us")
            if t.null_count() < len(t):
                t0 = t.drop_nulls()[0]
                elapsed = (t - t0).dt.total_microseconds() / 1e6
                return g.with_columns(elapsed.alias(time_col))
        if method == "recompute":
            raise ValueError(f"Cannot recompute from '{date_col}': no valid timestamps.")

    if method in ("auto", "segment"):
        fixed, _ = _fix_time_between_neighbors(_numeric(g, time_col), eps=eps)
        return g.with_columns(pl.Series(time_col, fixed))

    if method == "sort":
        return _fix_time_sort(g, time_col)

    if method == "drop":
        s = _numeric(g, time_col)
        d = np.diff(s, prepend=s[0] if s.size else 0.0)
        if s.size:
            d[0] = 0.0
        eps_val = _compute_eps_from_diffs(d) if eps == "auto" else float(eps)
        keep = d >= -eps_val
        if keep.size:
            keep[0] = True
        return g.filter(pl.Series(keep))

    raise ValueError(f"Unknown method: {method!r}")


# -----------------------------
# Public API -  full cleaner
# -----------------------------
def clean(
    df,
    *,
    time_fix: str = "segment",  # 'segment' | 'sort' | 'drop' | 'none'
    outlier: str = "none",  # 'none' | 'drop' | 'clip' | 'interp'
    z_thresh: float = 8.0,  # used for MAD/global & clip bounds
    columns: Optional[List[str]] = None,  # columns to outlier-clean
    time_eps: float | str = "auto",  # threshold for detecting time drops
    # robust detection knobs
    outlier_detect: str = "hybrid",  # 'mad' | 'huber' | 'hybrid'
    local_seconds: Optional[float] = 600.0,  # local window (sec) for neighborhood z (None to disable)
    local_z: float = 6.0,
    z_huber: float = 6.0,
    hampel_seconds: Optional[float] = 300.0,
    hampel_k: float = 6.0,
    slope_gate: bool = True,
    slope_z: float = 8.0,
) -> Tuple["pd.DataFrame | pl.DataFrame | pl.LazyFrame", CleanReport]:
    """
    Clean a BDF-normalized table.

    Accepts polars (eager or lazy) or pandas frames; the returned table matches
    the input kind.

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
    d, kind = _to_polars_eager(df)
    if TIME_COL not in d.columns:
        raise ValueError(f"Missing '{TIME_COL}'. Did you normalize to BDF?")

    notes: List[str] = []
    n_in = len(d)
    cols = [c for c in (columns or DEFAULT_OUTLIER_COLS) if c in d.columns]

    # ---- Fix time ----
    t_numeric = _numeric(d, TIME_COL)
    diffs = np.diff(t_numeric, prepend=t_numeric[0] if n_in else 0.0)
    eps_val = _compute_eps_from_diffs(diffs) if time_eps == "auto" else float(time_eps)
    n_resets_detected = int((diffs < -eps_val).sum())

    if time_fix == "segment":
        fixed, n_resets_detected = _fix_time_between_neighbors(t_numeric, eps=time_eps)
        d = d.with_columns(pl.Series(TIME_COL, fixed))
        time_method_used = "segment"
    elif time_fix == "sort":
        d = _fix_time_sort(d)
        time_method_used = "sort"
        n_resets_detected = 0
    elif time_fix == "drop":
        keep = np.concatenate(([True], diffs[1:] >= -eps_val)) if n_in else np.array([], dtype=bool)
        dropped = int((~keep).sum())
        if dropped:
            notes.append(f"Dropped {dropped} rows due to time decreases.")
        d = d.filter(pl.Series(keep))
        time_method_used = "drop"
    elif time_fix == "none":
        time_method_used = "none"
    else:
        raise ValueError("time_fix must be one of: 'segment','sort','drop','none'")

    # Rebase to start at zero if positive
    t_now = _numeric(d, TIME_COL)
    tmin = np.nanmin(t_now) if t_now.size else float("nan")
    if np.isfinite(tmin) and tmin > 0:
        d = d.with_columns(pl.Series(TIME_COL, t_now - float(tmin)))

    # ---- Outliers ----
    per_col: Dict[str, int] = {}
    if outlier != "none" and cols:
        t_arr = _numeric(d, TIME_COL)
        masks: Dict[str, np.ndarray] = {}
        for c in cols:
            masks[c] = _robust_outlier_mask(
                _numeric(d, c),
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
                time_s=t_arr,
            )
            per_col[c] = int(masks[c].sum())

        if outlier == "drop":
            any_bad = np.logical_or.reduce(list(masks.values())) if masks else np.zeros(len(d), dtype=bool)
            d = d.filter(pl.Series(~any_bad))
            notes.append(f"Dropped {int(any_bad.sum())} rows due to outliers in {', '.join(cols)}.")
        elif outlier == "clip":
            # robust bounds via MAD (SciPy if available), fallback to IQR
            for c in masks:
                s = _numeric(d, c)
                med = float(np.nanmedian(s))
                if sps is not None:
                    madn = float(sps.median_abs_deviation(s, nan_policy="omit", scale="normal"))
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
                d = d.with_columns(pl.Series(c, np.clip(s, lo, hi)))
            notes.append("Clipped outliers to robust bounds (MAD/IQR).")
        elif outlier == "interp":
            tx = _numeric(d, TIME_COL)
            for c, m in masks.items():
                s = _numeric(d, c)
                d = d.with_columns(pl.Series(c, _interp_over_time(s, m, tx)))
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
    return _from_polars_eager(d, kind), rep
