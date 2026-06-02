from __future__ import annotations

import pandas as pd

from bdf.normalize import normalize_columns


def test_package_ingest_aliases_normalize_custom_fields() -> None:
    df = pd.DataFrame(
        {
            "test_time": [0, 1, 2],
            "potential": [3.7, 3.6, 3.5],
            "current": [0.1, 0.1, 0.1],
            "epoch_time_utc": [1700000000, 1700000001, 1700000002],
            "cycle_num": [1, 1, 1],
            "step_num": [1, 2, 3],
            "test_cumulated_charge_capacity": [0.0, 0.1, 0.2],
            "test_cumulated_discharge_capacity": [0.0, 0.0, 0.0],
            "test_net_capacity": [0.0, 0.1, 0.2],
            "test_cumulated_charge_energy": [0.0, 0.3, 0.6],
            "test_cumulated_discharge_energy": [0.0, 0.0, 0.0],
            "test_net_energy": [0.0, 0.3, 0.6],
            "temperature_chamber": [25.0, 25.0, 25.0],
            # Ambiguous fields should stay unmapped when keep_unmapped=True.
            "temperature_cell": [25.1, 25.2, 25.3],
            "pressure": [101325.0, 101325.0, 101325.0],
        }
    )

    out = normalize_columns(df, strict=False, include_optional=True, keep_unmapped=True)

    assert "Test Time / s" in out.columns
    assert "Voltage / V" in out.columns
    assert "Current / A" in out.columns
    assert "Unix Time / s" in out.columns
    assert "Cycle Count / 1" in out.columns
    assert "Step Count / 1" in out.columns
    assert "Charging Capacity / Ah" in out.columns
    assert "Discharging Capacity / Ah" in out.columns
    assert "Net Capacity / Ah" in out.columns
    assert "Charging Energy / Wh" in out.columns
    assert "Discharging Energy / Wh" in out.columns
    assert "Net Energy / Wh" in out.columns
    assert "Ambient Temperature / degC" in out.columns

    assert out["Unix Time / s"].tolist() == [1700000000, 1700000001, 1700000002]
    assert "temperature_cell" in out.columns
    assert "pressure" in out.columns

