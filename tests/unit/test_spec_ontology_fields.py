"""Ontology-sourced metadata on Quantity: obligation, docs fields, derivations.

The bundled snapshot (ontology >= 1.1.0) is the source of truth for these
fields; the tests below pin both the extraction logic and the behavioural
contract that obligation drives requiredness.
"""

from __future__ import annotations

from rdflib import Graph

from bdf.spec import COLUMN_ONTOLOGY, ColumnOntology

# Behavioural contract: changing :obligation in an ontology release changes
# validate_df() behaviour. This set must only be updated deliberately, in
# the same PR that adopts the new ontology snapshot.
EXPECTED_REQUIRED = {"test_time_second", "voltage_volt", "current_ampere"}
EXPECTED_RECOMMENDED = {
    "unix_time_second",
    "step_count",
    "cycle_count",
    "ambient_temperature_celsius",
}


def test_required_set_matches_ontology_obligations() -> None:
    actual = {name for name, q in COLUMN_ONTOLOGY if q.required and not q.deprecated}
    assert actual == EXPECTED_REQUIRED


def test_recommended_set_matches_ontology_obligations() -> None:
    actual = {
        name
        for name, q in COLUMN_ONTOLOGY
        if q.obligation == "recommended" and not q.deprecated
    }
    assert actual == EXPECTED_RECOMMENDED


def test_every_active_quantity_has_an_obligation() -> None:
    missing = [
        name for name, q in COLUMN_ONTOLOGY if not q.deprecated and not q.obligation
    ]
    assert missing == []


def test_obligation_values_are_known() -> None:
    levels = {q.obligation for _, q in COLUMN_ONTOLOGY if q.obligation}
    assert levels <= {"required", "recommended", "optional"}


def test_deprecated_terms_carry_no_obligation_and_are_never_required() -> None:
    deprecated = [(name, q) for name, q in COLUMN_ONTOLOGY if q.deprecated]
    assert deprecated, "snapshot should contain deprecated tombstones"
    for name, q in deprecated:
        assert q.obligation == "", name
        assert not q.required, name


def test_description_and_definition_extracted() -> None:
    q = COLUMN_ONTOLOGY["current_ampere"]
    assert q.description == "Instantaneous current recorded in ampere."
    assert q.definition
    # description is the short, table-friendly text
    assert len(q.description) <= len(q.definition)


def test_latex_symbol_and_formula_extracted() -> None:
    assert COLUMN_ONTOLOGY["current_ampere"].latex_symbol == "I"
    cum = COLUMN_ONTOLOGY["step_cumulative_capacity_ah"]
    assert cum.latex_symbol
    assert "\\int" in cum.latex_formula


def test_derived_from_resolves_to_mr_names() -> None:
    cum = COLUMN_ONTOLOGY["step_cumulative_capacity_ah"]
    assert cum.derived_from == ("current_ampere", "step_time_second")
    net = COLUMN_ONTOLOGY["net_capacity_ah"]
    assert net.derived_from == ("charging_capacity_ah", "discharging_capacity_ah")
    for name in cum.derived_from + net.derived_from:
        assert name in COLUMN_ONTOLOGY


_MINIMAL_PRE_OBLIGATION_TTL = """
@prefix : <https://w3id.org/battery-data-alliance/ontology/battery-data-format#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .

:test_time_second a owl:Class ;
    skos:prefLabel "Test Time / s"@en .

:power_watt a owl:Class ;
    skos:prefLabel "Power / W"@en .
"""


def test_required_falls_back_to_default_without_obligations() -> None:
    g = Graph()
    g.parse(data=_MINIMAL_PRE_OBLIGATION_TTL, format="turtle")
    onto = ColumnOntology.from_graph(g)
    # Without :obligation annotations the level is synthesized from the
    # static fallback set; `required` is derived from it.
    assert onto["test_time_second"].obligation == "required"
    assert onto["test_time_second"].required
    assert onto["power_watt"].obligation == "optional"
    assert not onto["power_watt"].required
