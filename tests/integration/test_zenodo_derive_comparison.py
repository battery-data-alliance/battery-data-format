# tests/integration/test_zenodo_derive_comparison.py
"""
Cross-validate bdf.derive() against vendor-native derived columns for two
representative files from the BDF Zenodo validation record.

Strategy
--------
For each file, load via bdf.read() (which runs the plugin's fixup()), then
call bdf.derive() on a DataFrame stripped down to the three required base
columns plus Step ID.  The derived arrays are compared against the values
the plugin produced from the vendor file.

Files and what is compared
--------------------------
BioLogic GITT  — Power/W (V×I, instantaneous; should agree to machine precision).
                 Charging Capacity compared segment-by-segment (EC-Lab resets
                 Q_charge at every technique boundary; within each segment the
                 trapezoidal integral should agree to < 2 %).
                 The validate=True warning mechanism is also exercised to confirm
                 it correctly flags the full-column divergence caused by resets.

                 NOT compared: Step Capacity.  BioLogic stores the FINAL dQ value
                 for a technique in every row of that technique rather than a
                 running accumulator; the per-row comparison is therefore
                 meaningless and has been excluded.

Neware NDA     — Charging/Discharging Capacity and Energy; these reset per cycle
                 so segment-by-segment comparison is used.

Files not yet compared
----------------------
Digatron HPPC  — Set aside pending resolution of two issues:
                 (1) 'Program Duration#s' carries ms values despite the '#s' label;
                     fixup() auto-corrects this but the correction needs independent
                     verification on more Digatron files.
                 (2) Step 26 (a long rest) accumulates 133 µAh of sub-resolution
                     leakage current in the vendor hardware integrator, which the
                     logged I = 0 cannot reproduce.  A higher ATOL threshold is
                     required but cannot be set confidently from a single file.

Tolerances
----------
  RTOL_INSTANT  = 0.001  (0.1%) — instantaneous quantities (Power = V×I)
  RTOL_INTEGRAL = 0.02   (2%)   — cumulative integrals; accounts for the
                                  difference between hardware high-frequency
                                  integration and logged-data trapezoidal rule

Run with:
    pytest -m zenodo tests/integration/test_zenodo_derive_comparison.py -v

Files must already be cached by a prior run of test_zenodo_validation_suite.py.
"""
from __future__ import annotations

import hashlib
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
RECORD_API = "https://zenodo.org/api/records/16994937"
CACHE_DIR  = Path(".pytest_cache/bdf_registry").resolve()

RTOL_INSTANT  = 0.001   # Power / W  (V × I at each logged point)
RTOL_INTEGRAL = 0.02    # cumulative capacity / energy (2 %)

# Absolute floor: segments whose vendor maximum is below this threshold are
# near-zero (rest periods or leakage-only accumulation) and are excluded.
ATOL_SEGMENT  = 1e-4    # 100 µAh / 100 µWh

pytestmark = pytest.mark.zenodo

# ---------------------------------------------------------------------------
# File identifiers
# ---------------------------------------------------------------------------
BIOLOGIC_KEY    = "SINTEF__NaCR32140-MP10-04__2025-08-25__GITT_0p05C_25degC__BioLogic.mpt"
BIOLOGIC_PLUGIN = "biologic-mpt"

NEWARE_KEY      = "SINTEF__G20M7-202512-Gru6mV__20251228__C30__25degC__Neware.nda"
NEWARE_PLUGIN   = "neware-nda"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _file_url(key: str) -> str:
    record_h = hashlib.sha256(RECORD_API.encode()).hexdigest()[:16]
    record_cache = CACHE_DIR / f"{record_h}__record.json"
    if not record_cache.exists():
        pytest.skip("Zenodo record.json not cached — run test_zenodo_validation_suite.py first")
    files = json.loads(record_cache.read_text())
    entry = next((f for f in files if f["key"] == key), None)
    if entry is None:
        pytest.skip(f"'{key}' not found in record.json")
    return entry["url"]


def _cached_path(url: str, key: str) -> Path:
    h = hashlib.sha256(url.encode()).hexdigest()[:16]
    p = CACHE_DIR / f"{h}__{key}"
    if not p.exists():
        pytest.skip(
            f"'{key}' not in cache — run: "
            "pytest -m zenodo tests/integration/test_zenodo_validation_suite.py"
        )
    return p


def _load(key: str, plugin: str, bdf_mod) -> pd.DataFrame:
    url  = _file_url(key)
    path = _cached_path(url, key)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return bdf_mod.read(path, plugin=plugin)


def _derive_from_base(df: pd.DataFrame, bdf_mod) -> pd.DataFrame:
    """Re-derive using only the three required columns + Step ID."""
    keep = [c for c in ["Test Time / s", "Voltage / V", "Current / A", "Step ID"]
            if c in df.columns]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return bdf_mod.derive(df[keep].copy(), fill_missing=True)


def _to_float(s: pd.Series) -> np.ndarray:
    return pd.to_numeric(s, errors="coerce").to_numpy(dtype=float)


def _assert_direct(vendor_col: pd.Series, derived_col: pd.Series,
                   label: str, rtol: float) -> None:
    """Assert max relative error ≤ rtol, normalised by the column maximum."""
    v = _to_float(vendor_col)
    d = _to_float(derived_col)
    mask = np.isfinite(v) & np.isfinite(d)
    assert mask.any(), f"{label}: no valid values to compare"
    scale = np.abs(v[mask]).max()
    if scale < 1e-9:
        return
    max_err = np.abs(v[mask] - d[mask]).max() / scale
    assert max_err <= rtol, (
        f"{label}: max rel error {max_err:.2%} > tolerance {rtol:.2%}  "
        f"[vendor max={v[mask].max():.4g}  derived max={d[mask].max():.4g}]"
    )


def _assert_segments(vendor_col: pd.Series, derived_col: pd.Series,
                     label: str, rtol: float) -> None:
    """
    Compare derived vs vendor within each monotonic segment of a resetting
    accumulator.

    A reset is detected when the vendor value drops by > 0.5 % of the column
    maximum.  Within each segment the derived array is offset-aligned to the
    vendor start value.  Segments whose vendor maximum is < ATOL_SEGMENT are
    skipped (near-zero rest / leakage rows that the logged current cannot
    reproduce).
    """
    v = _to_float(vendor_col)
    d = _to_float(derived_col)

    max_val = np.abs(v).max()
    if max_val < 1e-9:
        return

    drops     = np.diff(v)
    reset_idx = np.where(drops < -max_val * 0.005)[0] + 1
    bounds    = np.concatenate([[0], reset_idx, [len(v)]])

    worst = 0.0
    for start, end in zip(bounds[:-1], bounds[1:]):
        if end - start < 2:
            continue
        v_seg = v[start:end]
        d_seg = d[start:end]

        scale = np.abs(v_seg).max()
        if scale < ATOL_SEGMENT:
            continue  # near-zero / leakage-only segment

        offset    = d_seg[0] - v_seg[0]
        d_aligned = d_seg - offset
        rel_err   = np.abs(v_seg - d_aligned).max() / scale
        worst     = max(worst, rel_err)

    assert worst <= rtol, (
        f"{label}: worst segment rel error {worst:.2%} > {rtol:.2%} "
        f"({len(bounds) - 1} segments, {len(v)} rows)"
    )


# ---------------------------------------------------------------------------
# Session fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def _bdf():
    try:
        import bdf
        return bdf
    except ImportError:
        pytest.skip("bdf package not importable")


@pytest.fixture(scope="session")
def biologic(_bdf):
    df = _load(BIOLOGIC_KEY, BIOLOGIC_PLUGIN, _bdf)
    return df, _derive_from_base(df, _bdf)


@pytest.fixture(scope="session")
def neware(_bdf):
    df = _load(NEWARE_KEY, NEWARE_PLUGIN, _bdf)
    return df, _derive_from_base(df, _bdf)


# ---------------------------------------------------------------------------
# BioLogic GITT
# ---------------------------------------------------------------------------

def test_biologic_power_matches_vi(biologic):
    """Power/W reported by EC-Lab must equal V × I at each logged point."""
    df, der = biologic
    _assert_direct(df["Power / W"], der["Power / W"],
                   f"{BIOLOGIC_KEY} | Power", RTOL_INSTANT)


def test_biologic_charging_capacity_validate_warns(biologic, _bdf):
    """
    validate=True must warn for Charging Capacity because EC-Lab resets the
    Q_charge accumulator at each technique boundary.  This confirms the
    warning mechanism fires correctly for a known divergence case.
    """
    df, _ = biologic
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _bdf.derive(df, fill_missing=False, validate=True, rtol=0.02)

    user_warnings = [str(w.message) for w in caught if issubclass(w.category, UserWarning)]
    assert any("Charging Capacity" in m for m in user_warnings), (
        "Expected a UserWarning about 'Charging Capacity / Ah' divergence "
        "(EC-Lab resets accumulators at technique boundaries) but none was raised. "
        f"Warnings seen: {user_warnings}"
    )


def test_biologic_charging_capacity_per_segment(biologic):
    """
    Within each monotonic EC-Lab technique segment, our integral must agree
    with Q_charge to within 2 %.
    """
    df, der = biologic
    _assert_segments(df["Charging Capacity / Ah"], der["Charging Capacity / Ah"],
                     f"{BIOLOGIC_KEY} | Charging Capacity (per segment)", RTOL_INTEGRAL)


# ---------------------------------------------------------------------------
# Neware NDA — per-cycle resetting accumulators
# ---------------------------------------------------------------------------

def test_neware_charging_capacity(neware):
    df, der = neware
    _assert_segments(df["Charging Capacity / Ah"], der["Charging Capacity / Ah"],
                     f"{NEWARE_KEY} | Charging Capacity", RTOL_INTEGRAL)


def test_neware_discharging_capacity(neware):
    df, der = neware
    _assert_segments(df["Discharging Capacity / Ah"], der["Discharging Capacity / Ah"],
                     f"{NEWARE_KEY} | Discharging Capacity", RTOL_INTEGRAL)


def test_neware_charging_energy(neware):
    df, der = neware
    _assert_segments(df["Charging Energy / Wh"], der["Charging Energy / Wh"],
                     f"{NEWARE_KEY} | Charging Energy", RTOL_INTEGRAL)


def test_neware_discharging_energy(neware):
    df, der = neware
    _assert_segments(df["Discharging Energy / Wh"], der["Discharging Energy / Wh"],
                     f"{NEWARE_KEY} | Discharging Energy", RTOL_INTEGRAL)
