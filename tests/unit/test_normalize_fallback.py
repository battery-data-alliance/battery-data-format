"""Robust fallback column mapping (applies to every plugin).

Covers the deterministic fallback layers added to the normalizer:
  - token canonicalization (abbreviation / spelling variants)
  - shared package aliases (e.g. surface_temp -> Surface Temperature T1)
  - unit-dimension guard (reject name matches with conflicting units)
  - keep-unmapped-and-warn (no silent column loss)
"""
from __future__ import annotations

import warnings

import pandas as pd
import pytest

from bdf.normalize import normalize_columns

BASE = {"Test Time / s": [0, 1, 2], "Voltage / V": [3.7, 3.6, 3.5], "Current / A": [1.0, 1.0, -1.0]}


def _norm(extra, **kw):
    df = pd.DataFrame({**BASE, **extra})
    kw.setdefault("strict", False)
    kw.setdefault("keep_unmapped", True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return normalize_columns(df, **kw)


def test_token_canonicalization_maps_abbreviated_headers():
    out = _norm({
        "Chg Cap (Ah)": [0.0, 0.1, 0.2],
        "Dchg Cap (Ah)": [0.0, 0.0, 0.1],
        "Surface Temp (degC)": [25.0, 25.1, 25.2],
    })
    assert "Charging Capacity / Ah" in out.columns
    assert "Discharging Capacity / Ah" in out.columns
    assert "Surface Temperature T1 / degC" in out.columns


def test_surface_temp_alias_maps_to_t1():
    out = _norm({"Surface_Temp(degC)": [25.0, 25.5, 26.0]})
    assert "Surface Temperature T1 / degC" in out.columns
    assert out["Surface Temperature T1 / degC"].tolist() == [25.0, 25.5, 26.0]


def test_exact_match_takes_precedence_over_canonicalization():
    # Canonicalization must not override an already-correct mapping.
    out = _norm({"Voltage (V)": [4.0, 4.1, 4.2]})
    # "Voltage / V" from BASE coalesces; the canonical column survives.
    assert "Voltage / V" in out.columns


def test_unit_dimension_guard_rejects_incompatible_match():
    # A column named like 'current' but carrying a temperature unit must NOT be
    # accepted as Current / A — it stays unmapped.
    out = _norm({"Current (degC)": [25.0, 26.0, 27.0]})
    assert "Current (degC)" in out.columns          # kept as-is
    # Current / A from BASE is real amps; the bogus column did not overwrite it
    assert out["Current / A"].tolist() == [1.0, 1.0, -1.0]


def test_keep_unmapped_preserves_and_warns():
    df = pd.DataFrame({**BASE, "Widget Count (1)": [1, 2, 3]})
    with pytest.warns(UserWarning, match="no BDF canonical mapping"):
        out = normalize_columns(df, strict=False, keep_unmapped=True)
    assert "Widget Count (1)" in out.columns


def test_ambiguous_columns_stay_unmapped():
    # Regression guard: 'cell temperature' and bare 'pressure' are ambiguous and
    # must never be auto-mapped by the fallback layers.
    out = _norm({"temperature_cell": [25.0, 25.0, 25.0], "pressure": [1e5, 1e5, 1e5]})
    assert "temperature_cell" in out.columns
    assert "pressure" in out.columns
    assert "Surface Temperature T1 / degC" not in out.columns


def test_canonical_columns_ordered_before_vendor_columns():
    out = _norm({"ACR (Ohm)": [0.01, 0.01, 0.01], "Cycle (1)": [1, 1, 1]})
    cols = list(out.columns)
    # required trio first; the unmapped vendor column lands after canonical ones
    assert cols[:3] == ["Test Time / s", "Voltage / V", "Current / A"]
    assert cols.index("Cycle Count / 1") < cols.index("ACR (Ohm)")
