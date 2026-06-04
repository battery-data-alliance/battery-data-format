# src/bdf/derive.py
"""
Derive BDF canonical columns from the three required base measurements
(Test Time / s, Voltage / V, Current / A).

Two use-cases
-------------
fill_missing=True  — populate columns that the vendor file did not export.
validate=True      — warn when vendor-supplied values diverge from the
                     trapezoidal re-computation (useful for QA).
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

_T = "Test Time / s"
_V = "Voltage / V"
_I = "Current / A"

# Columns that derive() can compute, grouped by whether Step ID is needed.
# Exposing these lets callers inspect what is derivable without calling derive().
DERIVABLE_COLUMNS: tuple[str, ...] = (
    "Power / W",
    "Charging Capacity / Ah",
    "Discharging Capacity / Ah",
    "Cumulative Capacity / Ah",
    "Net Capacity / Ah",
    "Charging Energy / Wh",
    "Discharging Energy / Wh",
    "Cumulative Energy / Wh",
    "Net Energy / Wh",
)

DERIVABLE_STEP_COLUMNS: tuple[str, ...] = (
    "Step Capacity / Ah",
    "Step Energy / Wh",
    "Step Time / s",
    "Step Count / 1",
    "Step Index / 1",
)


def derive(
    df: pd.DataFrame,
    *,
    fill_missing: bool = True,
    validate: bool = False,
    rtol: float = 0.01,
) -> pd.DataFrame:
    """
    Compute BDF derived quantities from the three required base columns.

    Parameters
    ----------
    df:
        DataFrame containing at minimum ``Test Time / s``, ``Voltage / V``,
        and ``Current / A`` as numeric columns.
    fill_missing:
        When True (default), add computed columns that are absent from *df*.
    validate:
        When True, compare computed values against any existing columns and
        emit a ``UserWarning`` for columns whose max relative error exceeds
        *rtol*.

        Note: instruments that reset capacity accumulators at technique
        boundaries (e.g. BioLogic EC-Lab resets ``Q charge`` to 0 at each
        technique) will produce expected divergence in ``Charging Capacity /
        Ah`` and ``Discharging Capacity / Ah`` after the first reset.
        Validation warnings for those columns may therefore be benign; compare
        individual step segments rather than the full column to isolate genuine
        errors.
    rtol:
        Relative tolerance used by *validate* (default 1 %).  The error is
        normalised by the maximum absolute value in the existing column.

    Returns
    -------
    pd.DataFrame
        A copy of *df* with derived columns added (when *fill_missing* is
        True) or the original *df* unchanged (when both *fill_missing* and
        *validate* are False).

    Raises
    ------
    ValueError
        If any of the three required columns is absent.
    """
    for col in (_T, _V, _I):
        if col not in df.columns:
            raise ValueError(
                f"derive() requires column '{col}' — not found in DataFrame"
            )

    t = pd.to_numeric(df[_T], errors="coerce").to_numpy(dtype=float)
    v = pd.to_numeric(df[_V], errors="coerce").to_numpy(dtype=float)
    i = pd.to_numeric(df[_I], errors="coerce").to_numpy(dtype=float)

    # Sanity-check the time column for implausible units.  A median Δt > 1000
    # strongly suggests the values are in milliseconds rather than seconds,
    # which would make every time-derived quantity (capacity, energy) 1000× too
    # large.  Warn so the user can fix the source plugin.
    if len(t) > 1:
        median_dt = float(np.nanmedian(np.abs(np.diff(t))))
        if median_dt > 1000:
            warnings.warn(
                f"derive: '{_T}' median Δt={median_dt:.0f} — values may be in "
                "milliseconds rather than seconds.  All time-integrated quantities "
                "(capacity, energy) will be incorrect.  Check the source plugin's "
                "time-unit handling.",
                UserWarning,
                stacklevel=2,
            )

    step_id = df["Step ID"].to_numpy() if "Step ID" in df.columns else None

    computed = _compute_all(t, v, i, step_id=step_id)

    if not fill_missing and not validate:
        return df

    out = df.copy()

    for col, vals in computed.items():
        present = col in out.columns
        if fill_missing and not present:
            out[col] = vals
        if validate and present:
            _check_column(out, col, vals, rtol=rtol)

    return out


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def _compute_all(
    t: np.ndarray,
    v: np.ndarray,
    i: np.ndarray,
    step_id: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """Return {column_name: derived_array} for all computable columns."""
    n = len(t)
    if n == 0:
        return {}

    # dt[k] = t[k] - t[k-1]; dt[0] = 0 (no area under first point).
    # Clip negatives to 0 so non-monotonic time doesn't produce negative area.
    dt = np.empty(n)
    dt[0] = 0.0
    dt[1:] = np.diff(t)
    dt = np.clip(dt, 0.0, None)

    p = v * i  # instantaneous power (W)

    result: dict[str, np.ndarray] = {}
    result["Power / W"] = p

    # --- capacity (A·s → Ah via ÷3600) ---
    i_pos = np.maximum(i, 0.0)
    i_neg = np.maximum(-i, 0.0)

    chg_cap  = _cumtrapz(i_pos, dt) / 3600.0
    dchg_cap = _cumtrapz(i_neg, dt) / 3600.0

    result["Charging Capacity / Ah"]    = chg_cap
    result["Discharging Capacity / Ah"] = dchg_cap
    result["Cumulative Capacity / Ah"]  = chg_cap + dchg_cap
    result["Net Capacity / Ah"]         = chg_cap - dchg_cap

    # --- energy (W·s → Wh via ÷3600) ---
    p_pos = np.maximum(p, 0.0)
    p_neg = np.maximum(-p, 0.0)

    chg_e  = _cumtrapz(p_pos, dt) / 3600.0
    dchg_e = _cumtrapz(p_neg, dt) / 3600.0

    result["Charging Energy / Wh"]    = chg_e
    result["Discharging Energy / Wh"] = dchg_e
    result["Cumulative Energy / Wh"]  = chg_e + dchg_e
    result["Net Energy / Wh"]         = chg_e - dchg_e

    # --- step-level (require Step ID) ---
    if step_id is not None:
        i_abs = np.abs(i)
        p_abs = np.abs(p)
        ones  = np.ones(n, dtype=float)

        result["Step Capacity / Ah"] = _cumtrapz_by_step(i_abs, dt, step_id) / 3600.0
        result["Step Energy / Wh"]   = _cumtrapz_by_step(p_abs, dt, step_id) / 3600.0
        result["Step Time / s"]      = _cumtrapz_by_step(ones,  dt, step_id)

        step_changes = np.concatenate([[True], step_id[1:] != step_id[:-1]])
        group_id = np.cumsum(step_changes)

        # Step Count: 1-based monotonic counter, +1 at each new step, never
        # resets and never repeats — a unique id for each step execution
        # (distinct from Step ID, the vendor schedule identifier which can recur).
        result["Step Count / 1"] = group_id.astype(float)

        # Step Index: 1-based positional counter for data points *within* a step,
        # resets to 1 at each step transition.
        result["Step Index / 1"] = (
            pd.Series(np.ones(n, dtype=float))
            .groupby(group_id)
            .cumsum()
            .to_numpy(dtype=float)
        )

    return result


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------

def _cumtrapz(f: np.ndarray, dt: np.ndarray) -> np.ndarray:
    """
    Running trapezoidal integral.

    result[0] = 0
    result[k] = result[k-1] + 0.5 * (f[k-1] + f[k]) * dt[k]
    """
    contrib = np.empty(len(f))
    contrib[0] = 0.0
    contrib[1:] = 0.5 * (f[:-1] + f[1:]) * dt[1:]
    return np.cumsum(contrib)


def _cumtrapz_by_step(
    f: np.ndarray,
    dt: np.ndarray,
    step_id: np.ndarray,
) -> np.ndarray:
    """
    Running trapezoidal integral that resets to zero at each step transition.

    The interval spanning a step boundary is excluded from both steps (its
    contribution is set to zero) to match the behaviour of instrument
    accumulators that reset at the first sample of a new step.
    """
    n = len(f)
    if n == 0:
        return np.empty(0, dtype=float)

    # Per-interval trapezoidal contribution
    contrib = np.empty(n)
    contrib[0] = 0.0
    contrib[1:] = 0.5 * (f[:-1] + f[1:]) * dt[1:]

    # Mark step-boundary rows and zero their cross-boundary contribution
    step_changes = np.empty(n, dtype=bool)
    step_changes[0] = True
    step_changes[1:] = step_id[1:] != step_id[:-1]
    contrib[step_changes] = 0.0

    # Segmented cumulative sum: group by step, cumsum within group
    group_id = np.cumsum(step_changes)
    return (
        pd.Series(contrib, dtype=float)
        .groupby(group_id)
        .cumsum()
        .to_numpy(dtype=float)
    )


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------

def _check_column(
    df: pd.DataFrame,
    col: str,
    derived: np.ndarray,
    rtol: float,
) -> None:
    existing = pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)
    mask = np.isfinite(existing) & np.isfinite(derived)
    if not mask.any():
        return

    scale = np.abs(existing[mask]).max()
    if scale < 1e-9:
        return  # both arrays are effectively zero — no meaningful comparison

    rel_err = np.abs(existing[mask] - derived[mask]) / scale
    max_err = float(rel_err.max())
    if max_err > rtol:
        warnings.warn(
            f"derive: '{col}' max relative error {max_err:.1%} "
            f"(tolerance {rtol:.1%}). "
            "Vendor value may differ from trapezoidal re-computation due to "
            "higher-frequency hardware integration or accumulator resets at "
            "technique boundaries.",
            UserWarning,
            stacklevel=3,
        )
