# tests/unit/test_derive.py
"""
Unit tests for bdf.derive().

Each test uses a minimal synthetic DataFrame with analytically known answers
so the expected values can be computed exactly by hand.

Integration method: trapezoidal rule.
  result[0] = 0
  result[k] = result[k-1] + 0.5 * (f[k-1] + f[k]) * dt[k]
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

from bdf.derive import derive, DERIVABLE_COLUMNS, DERIVABLE_STEP_COLUMNS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _df(t, v, i, step_id=None, **extra) -> pd.DataFrame:
    """Build a minimal BDF DataFrame."""
    d = {
        "Test Time / s": np.asarray(t, dtype=float),
        "Voltage / V":   np.asarray(v, dtype=float),
        "Current / A":   np.asarray(i, dtype=float),
    }
    if step_id is not None:
        d["Step ID"] = np.asarray(step_id)
    d.update(extra)
    return pd.DataFrame(d)


# ---------------------------------------------------------------------------
# Power
# ---------------------------------------------------------------------------

def test_power_elementwise():
    """Power = V × I, computed row by row."""
    df = _df(t=[0, 1, 2], v=[3.0, 4.0, 2.0], i=[1.0, 2.0, -1.0])
    out = derive(df)
    np.testing.assert_allclose(out["Power / W"], [3.0, 8.0, -2.0])


def test_power_does_not_overwrite_existing():
    """fill_missing=True must not overwrite a column that is already present."""
    df = _df(t=[0, 1], v=[3.0, 4.0], i=[1.0, 2.0],
             **{"Power / W": np.array([99.0, 99.0])})
    out = derive(df, fill_missing=True)
    np.testing.assert_array_equal(out["Power / W"], [99.0, 99.0])


# ---------------------------------------------------------------------------
# Capacity — constant current
# ---------------------------------------------------------------------------

def test_charging_capacity_constant_current():
    """
    Constant 1 A charge for 3600 s → 1.000 Ah at final row.

    Trapezoidal with uniform 1800 s steps:
      row 0: 0 Ah
      row 1: 0 + 0.5*(1+1)*1800 / 3600 = 0.500 Ah
      row 2: 0.5 + 0.5*(1+1)*1800 / 3600 = 1.000 Ah
    """
    df = _df(t=[0, 1800, 3600], v=[3.7, 3.7, 3.7], i=[1.0, 1.0, 1.0])
    out = derive(df)
    np.testing.assert_allclose(out["Charging Capacity / Ah"], [0.0, 0.5, 1.0])


def test_discharging_capacity_constant_current():
    """Constant −1 A discharge for 3600 s → 1.000 Ah (unsigned)."""
    df = _df(t=[0, 1800, 3600], v=[3.7, 3.7, 3.7], i=[-1.0, -1.0, -1.0])
    out = derive(df)
    np.testing.assert_allclose(out["Discharging Capacity / Ah"], [0.0, 0.5, 1.0])
    np.testing.assert_allclose(out["Charging Capacity / Ah"],    [0.0, 0.0, 0.0])


def test_cumulative_and_net_capacity():
    """
    Charge at 1 A for 1800 s then discharge at 1 A for 1800 s.

    t = [0, 1800, 3600]
    I = [1, 1, -1]   (direction changes at row 2)

    Charging:    [0, 0.5, 0.5 + 0 = 0.5]   (last interval: 0.5*(1+(-1))*1800/3600 = 0)
    Discharging: [0, 0,   0 + 0.5*(0+1)*1800/3600 = 0.25]  hmm...

    Let me recalculate carefully:
      dt = [0, 1800, 1800]
      i_pos = [1, 1, 0]
      contrib_chg[0] = 0
      contrib_chg[1] = 0.5*(1+1)*1800/3600 = 0.5
      contrib_chg[2] = 0.5*(1+0)*1800/3600 = 0.25
      chg = [0, 0.5, 0.75]

      i_neg = [0, 0, 1]
      contrib_dchg[0] = 0
      contrib_dchg[1] = 0.5*(0+0)*1800/3600 = 0
      contrib_dchg[2] = 0.5*(0+1)*1800/3600 = 0.25
      dchg = [0, 0, 0.25]

      cumulative = chg + dchg = [0, 0.5, 1.0]
      net = chg - dchg = [0, 0.5, 0.5]
    """
    df = _df(t=[0, 1800, 3600], v=[3.7, 3.7, 3.7], i=[1.0, 1.0, -1.0])
    out = derive(df)
    np.testing.assert_allclose(out["Charging Capacity / Ah"],    [0.0,  0.5,  0.75], atol=1e-10)
    np.testing.assert_allclose(out["Discharging Capacity / Ah"], [0.0,  0.0,  0.25], atol=1e-10)
    np.testing.assert_allclose(out["Cumulative Capacity / Ah"],  [0.0,  0.5,  1.0],  atol=1e-10)
    np.testing.assert_allclose(out["Net Capacity / Ah"],         [0.0,  0.5,  0.5],  atol=1e-10)


# ---------------------------------------------------------------------------
# Energy
# ---------------------------------------------------------------------------

def test_charging_energy_constant_power():
    """
    V=4 V, I=0.5 A → P=2 W constant for 3600 s → 2 Wh at final row.

    t=[0,1800,3600], contrib:
      row 0: 0
      row 1: 0.5*(2+2)*1800/3600 = 1.0 Wh
      row 2: 1.0 + 1.0 = 2.0 Wh
    """
    df = _df(t=[0, 1800, 3600], v=[4.0, 4.0, 4.0], i=[0.5, 0.5, 0.5])
    out = derive(df)
    np.testing.assert_allclose(out["Charging Energy / Wh"],    [0.0, 1.0, 2.0])
    np.testing.assert_allclose(out["Discharging Energy / Wh"], [0.0, 0.0, 0.0])
    np.testing.assert_allclose(out["Cumulative Energy / Wh"],  [0.0, 1.0, 2.0])
    np.testing.assert_allclose(out["Net Energy / Wh"],         [0.0, 1.0, 2.0])


# ---------------------------------------------------------------------------
# Step quantities (require Step ID)
# ---------------------------------------------------------------------------

def test_step_capacity_resets_at_transition():
    """
    Two steps, each with constant 1 A for 2 × 1 s intervals.

    step_id = [1, 1, 1, 2, 2, 2]
    t       = [0, 1, 2, 3, 4, 5]
    I       = [1, 1, 1, -1, -1, -1]  (|I| = 1 everywhere)

    Within step 1 (rows 0-2):
      step_changes = [T, F, F, ...]
      contrib: 0, 0.5*(1+1)*1=1, 0.5*(1+1)*1=1  → cumsum [0, 1, 2] A·s
      → [0, 1/3600, 2/3600] Ah

    Within step 2 (rows 3-5):
      step_changes[3] = True  → contrib[3] = 0 (cross-boundary excluded)
      contrib: 0, 0.5*(1+1)*1=1, 0.5*(1+1)*1=1  → cumsum [0, 1, 2] A·s
      → [0, 1/3600, 2/3600] Ah

    Full result: [0, 1/3600, 2/3600, 0, 1/3600, 2/3600] Ah
    """
    df = _df(
        t=[0, 1, 2, 3, 4, 5],
        v=[3.7]*6,
        i=[1.0, 1.0, 1.0, -1.0, -1.0, -1.0],
        step_id=[1, 1, 1, 2, 2, 2],
    )
    out = derive(df)
    expected = np.array([0, 1, 2, 0, 1, 2]) / 3600.0
    np.testing.assert_allclose(out["Step Capacity / Ah"], expected, atol=1e-12)


def test_step_capacity_is_unsigned():
    """Step capacity must be non-negative even during discharge."""
    df = _df(
        t=[0, 1800, 3600],
        v=[3.7, 3.7, 3.7],
        i=[-2.0, -2.0, -2.0],
        step_id=[1, 1, 1],
    )
    out = derive(df)
    assert (out["Step Capacity / Ah"] >= -1e-12).all()


def test_step_time_resets_at_transition():
    """Step Time / s resets to 0 at each step transition."""
    df = _df(
        t=[0, 10, 20, 30, 40],
        v=[3.7]*5,
        i=[1.0]*5,
        step_id=[1, 1, 1, 2, 2],
    )
    out = derive(df)
    # Within step 1: [0, 10, 20]; cross-boundary interval excluded
    # Within step 2: [0, 10]
    np.testing.assert_allclose(
        out["Step Time / s"], [0.0, 10.0, 20.0, 0.0, 10.0], atol=1e-10
    )


def test_step_columns_absent_without_step_id():
    """No Step ID → step columns must not be added."""
    df = _df(t=[0, 1, 2], v=[3.7]*3, i=[1.0]*3)
    out = derive(df)
    for col in DERIVABLE_STEP_COLUMNS:
        assert col not in out.columns, f"'{col}' should not be present without Step ID"


# ---------------------------------------------------------------------------
# fill_missing behaviour
# ---------------------------------------------------------------------------

def test_fill_missing_false_adds_nothing():
    """fill_missing=False must return the original columns unchanged."""
    df = _df(t=[0, 1], v=[3.7, 3.7], i=[1.0, 1.0])
    out = derive(df, fill_missing=False)
    assert set(out.columns) == set(df.columns)


def test_fill_missing_true_populates_all_derivable():
    """fill_missing=True adds all derivable columns when none are present."""
    df = _df(t=[0, 1800, 3600], v=[3.7]*3, i=[1.0]*3)
    out = derive(df, fill_missing=True)
    for col in DERIVABLE_COLUMNS:
        assert col in out.columns, f"'{col}' missing after derive(fill_missing=True)"


def test_derive_does_not_mutate_input():
    """derive() must not modify the original DataFrame."""
    df = _df(t=[0, 1], v=[3.7, 3.7], i=[1.0, 1.0])
    original_cols = list(df.columns)
    _ = derive(df)
    assert list(df.columns) == original_cols


# ---------------------------------------------------------------------------
# validate behaviour
# ---------------------------------------------------------------------------

def test_validate_warns_on_discrepancy():
    """validate=True warns when existing column disagrees with derived value."""
    bad_power = np.array([999.0, 999.0, 999.0])
    df = _df(t=[0, 1, 2], v=[3.0]*3, i=[1.0]*3, **{"Power / W": bad_power})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        derive(df, fill_missing=False, validate=True, rtol=0.01)
    msgs = [str(w.message) for w in caught if issubclass(w.category, UserWarning)]
    assert any("Power / W" in m for m in msgs), (
        "Expected a UserWarning about 'Power / W' discrepancy"
    )


def test_validate_no_warn_when_values_match():
    """validate=True must not warn when existing column equals derived value."""
    v = np.array([3.0, 4.0, 2.0])
    i = np.array([1.0, 2.0, -1.0])
    exact_power = v * i
    df = _df(t=[0, 1, 2], v=v, i=i, **{"Power / W": exact_power})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        derive(df, fill_missing=False, validate=True, rtol=0.01)
    msgs = [str(w.message) for w in caught if issubclass(w.category, UserWarning)]
    assert not any("Power / W" in m for m in msgs), (
        "Unexpected warning for exact-match 'Power / W'"
    )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_missing_required_column_raises():
    """derive() must raise ValueError if a required column is absent."""
    df = pd.DataFrame({"Test Time / s": [0, 1], "Voltage / V": [3.7, 3.7]})
    with pytest.raises(ValueError, match="Current / A"):
        derive(df)


def test_single_row_returns_zeros():
    """A single-row DataFrame has no time interval: all integrals must be 0."""
    df = _df(t=[0], v=[3.7], i=[1.0])
    out = derive(df)
    assert out["Charging Capacity / Ah"].iloc[0] == pytest.approx(0.0)
    assert out["Power / W"].iloc[0] == pytest.approx(3.7)


def test_non_monotonic_time_does_not_go_negative():
    """Negative dt intervals are clipped to zero: capacity never decreases."""
    # Row 2 has a time that goes backward — should not produce negative Ah
    df = _df(t=[0, 1800, 1000, 3600], v=[3.7]*4, i=[1.0]*4)
    out = derive(df)
    cap = out["Charging Capacity / Ah"].to_numpy()
    assert (np.diff(cap) >= -1e-12).all(), \
        "Capacity should never decrease, even with non-monotonic time"
