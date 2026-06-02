from __future__ import annotations

from pathlib import Path

import numpy as np

import bdf
from bdf.data_sources.landt_ccs import LandtCCS


_SAMPLE = Path("data/INT-37_0.95_3el_14022023_SNEL_10_5.ccs")


def test_landt_ccs_sniff_and_parse_sample() -> None:
    plugin = LandtCCS()
    head = _SAMPLE.read_bytes()[:8192]
    sniff = plugin.sniff(_SAMPLE, head)

    assert sniff.id == "landt-ccs"
    assert sniff.confidence >= 0.65

    df = plugin.parse(_SAMPLE)
    assert len(df) > 1000

    required = {"ccs_test_time_s", "ccs_voltage_v", "ccs_current_a"}
    assert required.issubset(df.columns)

    dt = np.diff(df["ccs_test_time_s"].to_numpy(dtype=float))
    assert np.all(dt >= 0.0)
    assert float(df["ccs_test_time_s"].iloc[0]) == 0.0

    assert float(df["ccs_voltage_v"].min()) > 2.5
    assert float(df["ccs_voltage_v"].max()) < 4.3

    assert (df["ccs_current_a"] > 0).any()
    assert (df["ccs_current_a"] < 0).any()

    assert np.all(np.diff(df["ccs_charging_capacity_ah"].to_numpy(dtype=float)) >= 0.0)
    assert np.all(np.diff(df["ccs_discharging_capacity_ah"].to_numpy(dtype=float)) >= 0.0)


def test_bdf_read_with_landt_ccs_plugin() -> None:
    df = bdf.read(_SAMPLE, plugin="landt-ccs", validate=True)
    assert len(df) > 1000
    assert {"Test Time / s", "Voltage / V", "Current / A"}.issubset(df.columns)
