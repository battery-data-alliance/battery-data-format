from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import bdf


def test_legacy_labels_normalized_from_ontology(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ontology = Path("tests/fixtures/ontology_labels.ttl").resolve()
    monkeypatch.setenv("BDF_ONTOLOGY_PATH", str(ontology))

    df = pd.DataFrame(
        {
            "test_time_millisecond": [0.0, 1000.0],
            "voltage_volt": [3.7, 3.8],
            "current_ampere": [0.1, 0.1],
            "cycle_count": [1, 1],
            "ambient_temperature_celsius": [25.0, 25.0],
        }
    )
    legacy_path = tmp_path / "legacy.bdf.parquet"
    df.to_parquet(legacy_path, index=False)

    with pytest.warns(UserWarning):
        normalized = bdf.read(legacy_path, validate=True)

    assert "Test Time / s" in normalized.columns
    assert "Voltage / V" in normalized.columns
    assert "Current / A" in normalized.columns
    assert "Cycle Count / 1" in normalized.columns
    assert "Ambient Temperature / degC" in normalized.columns

    raw_ms = df["test_time_millisecond"].iloc[0]
    conv = normalized["Test Time / s"].iloc[0]
    assert abs(conv - (raw_ms / 1000.0)) < 1e-6


def test_hidden_label_is_normalized_to_preferred_label(monkeypatch: pytest.MonkeyPatch) -> None:
    ontology = Path("tests/fixtures/ontology_labels.ttl").resolve()
    monkeypatch.setenv("BDF_ONTOLOGY_PATH", str(ontology))

    df = pd.DataFrame(
        {
            "Test Time / s": [0.0, 1.0],
            "Voltage / V": [3.7, 3.6],
            "Current / A": [0.1, 0.1],
            "Internal Resistance / Ohm": [0.045, 0.046],
        }
    )

    with pytest.warns(UserWarning):
        normalized = bdf.normalize(df)

    assert "Internal Resistance / ohm" in normalized.columns
    assert "Internal Resistance / Ohm" not in normalized.columns
