from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import bdf
from bdf.io import canonicalize_legacy_labels


def test_legacy_labels_normalized_from_ontology(data_dir: Path) -> None:
    legacy_path = data_dir / "bdf" / "legacy.bdf.parquet"
    df = pd.read_parquet(legacy_path)
    assert "test_time_millisecond" in df.columns

    with pytest.warns(UserWarning):
        normalized = bdf.load(legacy_path)

    assert "Test Time / s" in normalized.columns
    assert "Voltage / V" in normalized.columns
    assert "Current / A" in normalized.columns
    assert "Cycle Count / 1" in normalized.columns
    assert "Ambient Temperature / degC" in normalized.columns

    raw_ms = df["test_time_millisecond"].iloc[0]
    conv = normalized["Test Time / s"].iloc[0]
    assert abs(conv - (raw_ms / 1000.0)) < 1e-6


def test_hidden_label_is_normalized_to_preferred_label() -> None:
    df = pd.DataFrame(
        {
            "Test Time / s": [0.0, 1.0],
            "Voltage / V": [3.7, 3.6],
            "Current / A": [0.1, 0.1],
            "Internal Resistance / Ohm": [0.045, 0.046],
        }
    )

    normalized, _legacy = canonicalize_legacy_labels(df)

    assert "Internal Resistance / ohm" in normalized.columns
    assert "Internal Resistance / Ohm" not in normalized.columns
