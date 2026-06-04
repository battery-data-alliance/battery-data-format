from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import bdf

# NOTE: test_legacy_labels_normalized_from_ontology was removed here because it
# reads a legacy parquet fixture (data/empa__ccid000001.bdf.parquet) that is not
# committed to the repo (it is *.parquet-gitignored), so it fails on a clean CI
# checkout with FileNotFoundError. It is reintroduced — with the fixture committed
# under tests/data/bdf/ and the path un-ignored — in the parquet-fixture PR (#28).


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
