from __future__ import annotations

from pathlib import Path

import pytest

from bdf import spec


def test_spec_uses_ontology_labels_units_and_aliases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ontology = tmp_path / "bdf-mini.ttl"
    ontology.write_text(
        """@prefix : <https://w3id.org/battery-data-alliance/ontology/battery-data-format#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .

:test_time_second rdf:type owl:Class ;
    skos:prefLabel "Test Time / ms"@en ;
    skos:altLabel "elapsed_ms"@en .
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("BDF_ONTOLOGY_PATH", str(ontology))
    spec.refresh_columns()

    assert spec.unit_for("test_time_second") == "ms"
    assert spec._label_for("test_time_second") == "Test Time / ms"
    assert spec.base_synonym_index()["elapsed-ms"] == "test_time_second"


def test_spec_falls_back_when_ontology_cannot_be_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BDF_ONTOLOGY_PATH", "does-not-exist.ttl")
    with pytest.warns(UserWarning):
        spec.refresh_columns()
    assert spec.unit_for("test_time_second") == "s"
