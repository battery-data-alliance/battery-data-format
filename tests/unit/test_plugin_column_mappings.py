# tests/unit/test_plugin_column_mappings.py
"""
Column-mapping smoke tests for each delimited-text plugin.

Each test:
  1. Loads a minimal fixture file through the full bdf.read() pipeline.
  2. Asserts required BDF columns are present with correct dtype.
  3. Checks basic physical plausibility (voltage range, current sign,
     time monotonicity, capacity monotonicity where applicable).

Fixture files live in tests/data/ and are committed to the repo.
They are synthetic but structurally valid — each row is physically
plausible for a Li-ion cell.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import bdf

HERE = Path(__file__).parent
DATA = HERE.parent / "data"

REQUIRED = {"Test Time / s", "Voltage / V", "Current / A"}
VOLTAGE_RANGE = (2.0, 4.5)   # physically plausible for Li-ion


def _load(path: Path) -> pd.DataFrame:
    """Load via bdf.read() and return the first (only) DataFrame."""
    result = bdf.read(path)
    if isinstance(result, pd.DataFrame):
        return result
    if isinstance(result, dict):
        frames = list(result.values())
        assert frames, f"bdf.read() returned empty dict for {path}"
        return frames[0]
    pytest.fail(f"Unexpected return type from bdf.read(): {type(result)}")


def _check_required(df: pd.DataFrame, path: Path) -> None:
    missing = REQUIRED - set(df.columns)
    assert not missing, f"{path.name}: missing required columns {sorted(missing)}"
    for col in REQUIRED:
        coerced = pd.to_numeric(df[col], errors="coerce")
        nan_count = coerced.isna().sum()
        assert nan_count == 0, \
            f"{path.name}: column '{col}' has {nan_count} non-numeric values (dtype={df[col].dtype})"


def _check_time_monotonic(df: pd.DataFrame, path: Path) -> None:
    t = pd.to_numeric(df["Test Time / s"], errors="coerce").dropna()
    assert (t.diff().dropna() >= 0).all(), \
        f"{path.name}: 'Test Time / s' is not monotonically non-decreasing"


def _check_voltage_range(df: pd.DataFrame, path: Path) -> None:
    v = pd.to_numeric(df["Voltage / V"], errors="coerce").dropna()
    lo, hi = VOLTAGE_RANGE
    assert v.between(lo, hi).all(), \
        f"{path.name}: voltage outside [{lo}, {hi}] V — got min={v.min():.3f}, max={v.max():.3f}"


def _check_capacity_monotonic(df: pd.DataFrame, path: Path, col: str) -> None:
    if col not in df.columns:
        return
    c = pd.to_numeric(df[col], errors="coerce").dropna()
    assert (c.diff().dropna() >= -1e-9).all(), \
        f"{path.name}: '{col}' is not monotonically non-decreasing"


def _check_capacity_scale(df: pd.DataFrame, path: Path) -> None:
    """Capacity columns should be in Ah (< 1000), not mAh."""
    for col in ("Charging Capacity / Ah", "Discharging Capacity / Ah"):
        if col not in df.columns:
            continue
        c = pd.to_numeric(df[col], errors="coerce").dropna()
        assert c.max() < 1000, \
            f"{path.name}: '{col}' max={c.max():.1f} — looks like mAh, not Ah"


# ---------------------------------------------------------------------------
# BioLogic MPT
# ---------------------------------------------------------------------------

def test_biologic_mpt_required_columns():
    path = DATA / "tiny_biologic.mpt"
    df = _load(path)
    _check_required(df, path)
    _check_time_monotonic(df, path)
    _check_voltage_range(df, path)


def test_biologic_mpt_capacity_in_ah():
    path = DATA / "tiny_biologic.mpt"
    df = _load(path)
    _check_capacity_scale(df, path)
    _check_capacity_monotonic(df, path, "Charging Capacity / Ah")
    _check_capacity_monotonic(df, path, "Discharging Capacity / Ah")


def test_biologic_mpt_has_step_id():
    path = DATA / "tiny_biologic.mpt"
    df = _load(path)
    assert "Step ID" in df.columns, \
        f"tiny_biologic.mpt: 'Step ID' (from Ns column) not found in {list(df.columns)}"


# ---------------------------------------------------------------------------
# Neware CSV
# ---------------------------------------------------------------------------

def test_neware_csv_required_columns():
    path = DATA / "tiny_neware.csv"
    df = _load(path)
    _check_required(df, path)
    _check_time_monotonic(df, path)
    _check_voltage_range(df, path)


def test_neware_csv_capacity_scaled_to_ah():
    """Neware exports mAh; fixup() must scale to Ah."""
    path = DATA / "tiny_neware.csv"
    df = _load(path)
    _check_capacity_scale(df, path)
    _check_capacity_monotonic(df, path, "Charging Capacity / Ah")
    _check_capacity_monotonic(df, path, "Discharging Capacity / Ah")


def test_neware_csv_step_id_present():
    path = DATA / "tiny_neware.csv"
    df = _load(path)
    assert "Step ID" in df.columns, \
        f"tiny_neware.csv: 'Step ID' not found in {list(df.columns)}"


# ---------------------------------------------------------------------------
# LANDT CSV
# ---------------------------------------------------------------------------

def test_landt_csv_required_columns():
    path = DATA / "tiny_landt.csv"
    df = _load(path)
    _check_required(df, path)
    _check_time_monotonic(df, path)
    _check_voltage_range(df, path)


def test_landt_csv_step_id_present():
    path = DATA / "tiny_landt.csv"
    df = _load(path)
    assert "Step ID" in df.columns, \
        f"tiny_landt.csv: 'Step ID' not found in {list(df.columns)}"


# ---------------------------------------------------------------------------
# Basytec TXT
# ---------------------------------------------------------------------------

def test_basytec_txt_required_columns():
    path = DATA / "tiny_basytec.txt"
    df = _load(path)
    _check_required(df, path)
    _check_time_monotonic(df, path)
    _check_voltage_range(df, path)


def test_basytec_txt_both_current_directions():
    path = DATA / "tiny_basytec.txt"
    df = _load(path)
    I = df["Current / A"]
    assert (I > 0).any(), "tiny_basytec.txt: no positive (charging) current"
    assert (I < 0).any(), "tiny_basytec.txt: no negative (discharging) current"


# ---------------------------------------------------------------------------
# Digatron CSV
# ---------------------------------------------------------------------------

def test_digatron_csv_required_columns():
    path = DATA / "tiny_digatron.csv"
    df = _load(path)
    _check_required(df, path)
    _check_time_monotonic(df, path)
    _check_voltage_range(df, path)


def test_digatron_csv_cumulative_capacity_non_decreasing():
    """fixup() must produce a monotonically non-decreasing cumulative capacity."""
    path = DATA / "tiny_digatron.csv"
    df = _load(path)
    _check_capacity_monotonic(df, path, "Cumulative Capacity / Ah")


def test_digatron_csv_net_capacity_within_bounds():
    """Net capacity must never exceed cumulative capacity in magnitude."""
    path = DATA / "tiny_digatron.csv"
    df = _load(path)
    if "Net Capacity / Ah" not in df.columns or "Cumulative Capacity / Ah" not in df.columns:
        pytest.skip("Net or Cumulative Capacity column absent")
    net = pd.to_numeric(df["Net Capacity / Ah"], errors="coerce").abs()
    cum = pd.to_numeric(df["Cumulative Capacity / Ah"], errors="coerce")
    assert (net <= cum + 1e-9).all(), \
        "tiny_digatron.csv: |Net Capacity| exceeds Cumulative Capacity"


def test_digatron_csv_step_capacity_non_negative():
    """step_capacity_ah is unsigned — must be >= 0."""
    path = DATA / "tiny_digatron.csv"
    df = _load(path)
    if "Step Capacity / Ah" not in df.columns:
        pytest.skip("Step Capacity column absent")
    step = pd.to_numeric(df["Step Capacity / Ah"], errors="coerce").dropna()
    assert (step >= -1e-9).all(), \
        f"tiny_digatron.csv: 'Step Capacity / Ah' has negative values — should be unsigned"


# ---------------------------------------------------------------------------
# Arbin (Format A — CSV, and Format B — XLSX)
# ---------------------------------------------------------------------------

def test_arbin_csv_required_columns():
    path = DATA / "tiny_arbin.csv"
    df = _load(path)
    _check_required(df, path)
    _check_time_monotonic(df, path)
    _check_voltage_range(df, path)


def test_arbin_csv_capacity_in_ah():
    path = DATA / "tiny_arbin.csv"
    df = _load(path)
    _check_capacity_scale(df, path)
    _check_capacity_monotonic(df, path, "Charging Capacity / Ah")
    _check_capacity_monotonic(df, path, "Discharging Capacity / Ah")


def test_arbin_csv_step_index_maps_to_step_id():
    """Arbin 'Step Index' is the schedule step id -> 'Step ID', not 'Step Index / 1'."""
    path = DATA / "tiny_arbin.csv"
    df = _load(path)
    assert "Step ID" in df.columns, \
        f"tiny_arbin.csv: 'Step ID' not found in {list(df.columns)}"


def test_arbin_xlsx_required_columns():
    path = DATA / "tiny_arbin.xlsx"
    df = _load(path)
    _check_required(df, path)
    _check_time_monotonic(df, path)
    _check_voltage_range(df, path)


def test_arbin_xlsx_degree_symbol_aux_temperatures():
    """Format B 'Aux_Temperature(°)_N' headers must map to Surface Temperature TN."""
    path = DATA / "tiny_arbin.xlsx"
    df = _load(path)
    for col in ("Surface Temperature T1 / degC", "Surface Temperature T2 / degC"):
        assert col in df.columns, \
            f"tiny_arbin.xlsx: '{col}' not found in {list(df.columns)}"
