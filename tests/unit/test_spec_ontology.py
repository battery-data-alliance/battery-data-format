from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import polars as pl
import pytest
from pydantic import ValidationError

from bdf import spec
from bdf.spec import (
    COLUMN_ONTOLOGY,
    ColumnOntology,
    Quantity,
    get_unit_conversion,
    parse_label,
    unit_from_label,
)

_MINI_TTL = """\
@prefix : <https://w3id.org/battery-data-alliance/ontology/battery-data-format#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix schema: <https://schema.org/> .

:test_time_second rdf:type owl:Class ;
    skos:prefLabel "Test Time / ms"@en ;
    skos:altLabel "elapsed_ms"@en ;
    schema:unitCode "ms" .
"""

_UCUM_TTL = """\
@prefix : <https://w3id.org/battery-data-alliance/ontology/battery-data-format#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix schema: <https://schema.org/> .

:ambient_temperature_celsius rdf:type owl:Class ;
    skos:prefLabel "Ambient Temperature / Cel"@en ;
    schema:unitCode "Cel" .

:charge_capacity_amp_hour rdf:type owl:Class ;
    skos:prefLabel "Charge Capacity / A.h"@en ;
    schema:unitCode "A.h" .

:energy_watt_hour rdf:type owl:Class ;
    skos:prefLabel "Energy / W.h"@en ;
    schema:unitCode "W.h" .

:internal_resistance_ohm rdf:type owl:Class ;
    skos:prefLabel "Internal Resistance / Ohm"@en ;
    schema:unitCode "Ohm" .

:step_label rdf:type owl:Class ;
    skos:prefLabel "Step Label"@en ;
    schema:description "A free-text string identifier for the step."@en .
"""


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Voltage / V", ("Voltage", "V")),
        ("Test Time / s", ("Test Time", "s")),
        ("Ambient Temperature / celsius", ("Ambient Temperature", "degC")),
        ("Ambient Temperature / ℃", ("Ambient Temperature", "degC")),
        ("  Padded  /  V  ", ("Padded", "V")),
        ("no slash here", None),
        ("", None),
        ("Voltage /", None),
        ("/ V", None),
    ],
)
def test_parse_label(label: str, expected: tuple[str, str] | None) -> None:
    assert parse_label(label) == expected


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Voltage / V", "V"),
        ("Test Time / s", "s"),
        ("Temperature / celsius", "degC"),
        ("no slash", None),
        ("", None),
    ],
)
def test_unit_from_label(label: str, expected: str | None) -> None:
    assert unit_from_label(label) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ohm", "ohm"),
        ("Cel", "degC"),
        ("A.h", "Ah"),
        ("W.h", "Wh"),
    ],
)
def test_normalize_unit_ucum_codes_are_case_sensitive(raw: str, expected: str) -> None:
    """schema:unitCode UCUM codes normalize to BDF's canonical pint-valid unit string.

    Lookup is case-sensitive: "Ohm" maps to lowercase "ohm" (pint doesn't
    recognize capitalized "Ohm"), while "Cel"/"A.h"/"W.h" map to their
    spelled-out canonical forms.
    """
    assert spec._normalize_unit(raw) == expected


@pytest.mark.parametrize(
    ("alias", "canonical", "expected"),
    [
        # Identical unit string
        ("V", "V", True),
        # UCUM dotted form pint already parses as a product of two units,
        # equal in value to the canonical spelling
        ("A.h", "Ah", True),
        # Already a built-in pint alias for degree_Celsius
        ("celsius", "degC", True),
        # Same dimensionality as degC (both temperature) but not the same
        # value at a given magnitude, since kelvin has no offset
        ("kelvin", "degC", False),
        # Not a unit pint knows at all
        ("nonexistentunit123", "V", False),
    ],
)
def test_pint_understands(alias: str, canonical: str, expected: bool) -> None:
    """_pint_understands compares actual conversion values at 0 and 1, not just dimensionality."""
    assert spec._pint_understands(alias, canonical) is expected


def test_pint_understands_never_propagates_on_pathological_alias() -> None:
    """U+2103 (℃) must yield a bool, never raise -- so module import can't crash.

    pint's parser handles this character inconsistently across platforms: on
    some it parses ℃ natively as degree_Celsius (returning True), on others it
    raises a bare AssertionError rather than UndefinedUnitError. The truth value
    is therefore platform-dependent; what matters is that _pint_understands
    swallows the failure and returns a bool instead of letting it escape.
    """
    assert isinstance(spec._pint_understands("℃", "degC"), bool)


@pytest.mark.parametrize(
    ("src", "dst", "expected"),
    [
        # Identity
        ("V", "V", (1.0, 0.0)),
        ("v", "V", (1.0, 0.0)),  # case-insensitive identity short-circuit
        # Scale-only
        ("V", "mV", (1000.0, 0.0)),
        ("kV", "V", (1000.0, 0.0)),
        ("s", "ms", (1000.0, 0.0)),
        ("Ah", "mAh", (1000.0, 0.0)),
        ("mWh", "Wh", (0.001, 0.0)),
        # Scale + offset (temperature)
        ("degC", "K", (1.0, 273.15)),
        ("K", "degC", (1.0, -273.15)),
        # Incompatible dimensions
        ("V", "A", None),
        ("s", "V", None),
        # Dimensionless / None handling
        (None, "1", (1.0, 0.0)),
        (None, "", (1.0, 0.0)),
        ("", "1", (1.0, 0.0)),
        ("1", "1", (1.0, 0.0)),
        (None, "V", None),
        ("V", "1", None),
        # Bare "C"/"c" disambiguated as Celsius only against a temperature dst
        ("C", "degC", (1.0, 0.0)),
        ("c", "degC", (1.0, 0.0)),
        ("C", "K", (1.0, 273.15)),
        ("C", "degF", (Fraction(9, 5), 32)),
        # Bare "C" against a non-temperature dst is interpreted as coulombs
        ("C", "A", None),
        # Bare "C" as the dst is not special-cased (only src is); falls through
        # to pint, which treats it as coulombs, an incompatible dimension here
        ("degC", "C", None),
        # degC alt-spelling aliases registered with pint via "@alias" must
        # preserve the +273.15 offset, not just the dimensionality
        ("degc", "K", (1.0, 273.15)),
        ("degreec", "K", (1.0, 273.15)),
        ("\xf8c", "K", (1.0, 273.15)),
        ("\xb0c", "K", (1.0, 273.15)),
        # "Ohm" (UCUM unitCode casing) is registered as a real pint alias, so
        # conversions to a *different* resistance unit work too -- not just
        # the case-insensitive identity short-circuit above
        ("Ohm", "kohm", (0.001, 0.0)),
        ("Ohm", "milliohm", (1000.0, 0.0)),
        # "Cel" (UCUM unitCode casing) converts with the correct offset
        ("Cel", "K", (1.0, 273.15)),
        ("Cel", "degC", (1.0, 0.0)),
    ],
)
def test_get_unit_conversion(src: str | None, dst: str, expected: tuple[float, float] | None) -> None:
    result = get_unit_conversion(src, dst)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx((float(expected[0]), float(expected[1])))


# ---------------------------------------------------------------------------
# Quantity model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label_in", "unit", "expected_label"),
    [
        ("Voltage / {unit}", "V", "Voltage / {unit}"),
        ("Test Time / s", "s", "Test Time / {unit}"),
        ("Cycle Count / {unit}", "1", "Cycle Count / 1"),
    ],
)
def test_quantity_label_resolution(label_in: str, unit: str, expected_label: str) -> None:
    q = Quantity(unit=unit, label_template=label_in, mr_name="x", iri="", synonyms=[])
    assert q.label_template == expected_label


@pytest.mark.parametrize(
    ("unit", "expected_dtype"),
    [("1", "int"), ("V", "float"), ("s", "float"), ("degC", "float")],
)
def test_quantity_dtype_inferred_from_unit(unit: str, expected_dtype: str) -> None:
    q = Quantity(unit=unit, label_template="X / {unit}", mr_name="x", iri="", synonyms=[])
    assert q.dtype == expected_dtype


def test_quantity_dtype_explicit_overrides_inference() -> None:
    q = Quantity(unit="1", label_template="X / {unit}", dtype="float", mr_name="x", iri="", synonyms=[])
    assert q.dtype == "float"


def test_quantity_unit_none_dtype_defaults_to_int() -> None:
    q = Quantity(unit=None, label_template="X", mr_name="x", iri="", synonyms=[])
    assert q.dtype == "int"


def test_quantity_unit_none_dtype_str_when_description_mentions_string() -> None:
    q = Quantity(
        unit=None, label_template="X", mr_name="x", iri="", synonyms=[], description="A free-text string value."
    )
    assert q.dtype == "str"


def test_quantity_unit_none_dtype_explicit_overrides_inference() -> None:
    q = Quantity(unit=None, label_template="X", dtype="str", mr_name="x", iri="", synonyms=[])
    assert q.dtype == "str"


@pytest.mark.parametrize("bad_dtype", ["double", "", "Int"])
def test_quantity_invalid_dtype_raises(bad_dtype: str) -> None:
    with pytest.raises(ValidationError):
        Quantity(unit="V", label_template="V / {unit}", dtype=bad_dtype, mr_name="x", iri="", synonyms=[])


def test_quantity_invalid_field_type_raises() -> None:
    with pytest.raises(ValidationError):
        Quantity(
            unit="V",
            label_template="Voltage / {unit}",
            deprecated="not-a-bool-or-coercible",  # type: ignore[arg-type]
            mr_name="voltage_volt",
            iri="",
            synonyms=[],
        )


def test_quantity_defaults() -> None:
    q = Quantity(unit="V", label_template="V / {unit}", mr_name="v", iri="", synonyms=[])
    assert q.required is False
    assert q.deprecated is False
    assert q.notation == ""


@pytest.mark.parametrize(
    ("src_unit", "dst_unit", "expected"),
    [
        ("V", "mV", (1000.0, 0.0)),
        ("V", "V", (1.0, 0.0)),
        ("V", "A", None),
        ("degC", "K", (1.0, 273.15)),
    ],
)
def test_quantity_unit_conversion(src_unit: str, dst_unit: str, expected: tuple[float, float] | None) -> None:
    q = Quantity(unit=src_unit, label_template=f"X / {src_unit}", mr_name="x", iri="", synonyms=[])
    assert q.convert_to(dst_unit) == expected


@pytest.mark.parametrize(
    ("quantity_unit", "src_unit", "expected"),
    [
        # Compatible units
        ("V", "mV", (0.001, 0.0)),
        # Same unit
        ("V", "V", (1.0, 0.0)),
        # Incompatible units
        ("V", "second", None),
        # None src on dimensionless quantity
        ("1", None, (1.0, 0.0)),
        # None src on non-dimensionless quantity
        ("V", None, None),
    ],
)
def test_quantity_convert_from(quantity_unit: str, src_unit: str | None, expected: tuple[float, float] | None) -> None:
    q = Quantity(unit=quantity_unit, label_template=f"X / {quantity_unit}", mr_name="x", iri="", synonyms=[])
    assert q.convert_from(src_unit) == expected


@pytest.mark.parametrize(
    ("notation", "mr_name", "expected"),
    [
        ("preferred", "fallback_name", "preferred"),
        ("", "fallback_name", "fallback_name"),
        ("   ", "fallback_name", "fallback_name"),
        ("  preferred  ", "fallback_name", "preferred"),
    ],
)
def test_quantity_effective_notation(notation: str, mr_name: str, expected: str) -> None:
    q = Quantity(unit="V", label_template="X / V", mr_name=mr_name, iri="", synonyms=[], notation=notation)
    assert q.effective_notation == expected


# ---------------------------------------------------------------------------
# ColumnOntology
# ---------------------------------------------------------------------------


def test_columns_getattr_returns_quantity() -> None:
    q = spec.COLUMN_ONTOLOGY.voltage_volt
    assert isinstance(q, Quantity)
    assert q.unit == "V"
    assert q.label_template == "Voltage / {unit}"
    assert q.formatted_label == "Voltage / V"


def test_internal_resistance_ohm_unit_is_lowercase_ohm() -> None:
    """internal_resistance_ohm's schema:unitCode 'Ohm' resolves to lowercase, pint-valid 'ohm'."""
    q = spec.COLUMN_ONTOLOGY.internal_resistance_ohm
    assert q.unit == "ohm"
    assert q.formatted_label == "Internal Resistance / ohm"


def test_step_id_unit_none_dtype_int_label() -> None:
    """step_id loaded with unit=None, dtype='int', formatted_label='Step ID'."""
    q = spec.COLUMN_ONTOLOGY.step_id
    assert q.unit is None
    assert q.dtype == "int"
    assert q.formatted_label == "Step ID"


def test_step_type_unit_none_dtype_str_label() -> None:
    """step_type loaded with unit=None, dtype='str', formatted_label='Step Type'."""
    q = spec.COLUMN_ONTOLOGY.step_type
    assert q.unit is None
    assert q.dtype == "str"
    assert q.formatted_label == "Step Type"


def test_get_unit_conversion_none_dst_none_src() -> None:
    """get_unit_conversion(None, None) returns (1.0, 0.0)."""
    assert get_unit_conversion(None, None) == (1.0, 0.0)


def test_get_unit_conversion_physical_src_none_dst() -> None:
    """get_unit_conversion('V', None) returns None."""
    assert get_unit_conversion("V", None) is None


def test_columns_iteration_yields_mr_quantity_pairs() -> None:
    pairs = list(spec.COLUMN_ONTOLOGY)
    assert pairs, "expected at least one quantity"
    for key, val in pairs:
        assert isinstance(key, str)
        assert isinstance(val, Quantity)
        assert val.mr_name == key


def test_required_labels_match_required_flag() -> None:
    labels = spec.COLUMN_ONTOLOGY.required_labels()
    expected = {q.formatted_label for _, q in spec.COLUMN_ONTOLOGY if q.required and not q.deprecated}
    assert set(labels) == expected


def test_required_labels_excludes_deprecated() -> None:
    q_dep = Quantity(
        unit="V",
        label_template="Old / {unit}",
        obligation="required",
        mr_name="old_volt",
        iri="",
        synonyms=[],
        deprecated=True,
    )
    onto = ColumnOntology({"old_volt": q_dep})
    assert "Old / V" not in onto.required_labels()


def test_optional_labels_excludes_required_and_deprecated() -> None:
    labels = spec.COLUMN_ONTOLOGY.optional_labels()
    for label in labels:
        mr = spec.COLUMN_ONTOLOGY.mr_name_from_label(label)
        assert mr is not None
        q = getattr(spec.COLUMN_ONTOLOGY, mr)
        assert not q.required
        assert not q.deprecated


def test_base_synonym_index_excludes_deprecated() -> None:
    q_dep = Quantity(
        unit="V",
        label_template="Old / {unit}",
        mr_name="old_volt",
        iri="",
        synonyms=["old-voltage"],
        deprecated=True,
    )
    onto = ColumnOntology({"old_volt": q_dep})
    assert "old-voltage" not in onto.base_synonym_index()


def test_base_synonym_index_includes_label_notation_and_synonyms() -> None:
    q = Quantity(
        unit="V",
        label_template="Custom Label / {unit}",
        mr_name="custom_q",
        iri="",
        synonyms=["alias-one", "alias-two"],
        notation="custom-notation",
    )
    onto = ColumnOntology({"custom_q": q})
    idx = onto.base_synonym_index()
    assert idx["custom-label"] == "custom_q"
    assert idx["custom-notation"] == "custom_q"
    assert idx["alias-one"] == "custom_q"
    assert idx["alias-two"] == "custom_q"


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Voltage / V", "voltage_volt"),
        ("Voltage / mV", "voltage_volt"),  # unit irrelevant, base name matches
        ("voltage / V", "voltage_volt"),  # case-insensitive on base
        ("Test Time / s", "test_time_second"),
        ("Nonexistent / X", None),
        ("malformed-no-slash", None),
    ],
)
def test_mr_name_from_label(label: str, expected: str | None) -> None:
    assert spec.COLUMN_ONTOLOGY.mr_name_from_label(label) == expected


def test_quantity_from_label_valid_with_unit() -> None:
    result = spec.COLUMN_ONTOLOGY.quantity_from_label("Voltage / mV")
    assert result is not None
    quantity, unit = result
    assert quantity.mr_name == "voltage_volt"
    assert unit == "mV"


def test_quantity_from_label_dimensionless() -> None:
    result = spec.COLUMN_ONTOLOGY.quantity_from_label("Cycle Count / 1")
    assert result is not None
    quantity, unit = result
    assert quantity.unit == "1"
    assert unit == "1"


def test_quantity_from_label_unparseable_returns_none() -> None:
    assert spec.COLUMN_ONTOLOGY.quantity_from_label("not_a_label") is None


def test_quantity_from_label_no_slash_matches_unitless_quantity() -> None:
    """Mirrors mr_name_from_label's fallback for unit=None terms like 'Step ID'."""
    result = spec.COLUMN_ONTOLOGY.quantity_from_label("Step ID")
    assert result is not None
    quantity, unit = result
    assert quantity.mr_name == "step_id"
    assert unit is None


def test_mr_name_from_label_and_quantity_from_label_agree_on_no_slash_label() -> None:
    mr_name = spec.COLUMN_ONTOLOGY.mr_name_from_label("Step ID")
    result = spec.COLUMN_ONTOLOGY.quantity_from_label("Step ID")
    assert result is not None
    assert mr_name == result[0].mr_name


def test_mr_name_from_label_no_slash_prefers_non_deprecated() -> None:
    q_dep = Quantity(
        unit=None, label_template="Old Step ID", mr_name="old_step_id", iri="", synonyms=[], deprecated=True
    )
    q_pref = Quantity(
        unit=None, label_template="Old Step ID", mr_name="step_id_v2", iri="", synonyms=[], deprecated=False
    )
    onto = ColumnOntology({"old_step_id": q_dep, "step_id_v2": q_pref})
    assert onto.mr_name_from_label("Old Step ID") == "step_id_v2"


def test_quantity_from_label_no_slash_prefers_non_deprecated() -> None:
    q_dep = Quantity(
        unit=None, label_template="Old Step ID", mr_name="old_step_id", iri="", synonyms=[], deprecated=True
    )
    q_pref = Quantity(
        unit=None, label_template="Old Step ID", mr_name="step_id_v2", iri="", synonyms=[], deprecated=False
    )
    onto = ColumnOntology({"old_step_id": q_dep, "step_id_v2": q_pref})
    result = onto.quantity_from_label("Old Step ID")
    assert result is not None
    assert result[0].mr_name == "step_id_v2"


def test_quantity_from_label_unknown_base_returns_none() -> None:
    assert spec.COLUMN_ONTOLOGY.quantity_from_label("Unknown Quantity / kg") is None


def test_quantity_from_label_prefers_non_deprecated() -> None:
    q_dep = Quantity(
        unit="mV",
        label_template="Voltage / {unit}",
        mr_name="voltage_millivolt",
        iri="",
        synonyms=[],
        deprecated=True,
    )
    q_pref = Quantity(
        unit="V",
        label_template="Voltage / {unit}",
        mr_name="voltage_volt",
        iri="",
        synonyms=[],
        deprecated=False,
    )
    onto = ColumnOntology({"voltage_millivolt": q_dep, "voltage_volt": q_pref})
    result = onto.quantity_from_label("Voltage / mV")
    assert result is not None
    quantity, _ = result
    assert quantity.mr_name == "voltage_volt"
    assert not quantity.deprecated


# ---------------------------------------------------------------------------
# ColumnOntology.build()
# ---------------------------------------------------------------------------


def test_build_returns_instance_with_core_quantities() -> None:
    """build() returns instance with voltage_volt, current_ampere, test_time_second present."""
    onto = ColumnOntology.build()
    assert "voltage_volt" in onto
    assert "current_ampere" in onto
    assert "test_time_second" in onto
    assert isinstance(onto.voltage_volt, Quantity)
    assert onto.voltage_volt.unit == "V"


# ---------------------------------------------------------------------------
# ColumnOntology.load_ttl()
# ---------------------------------------------------------------------------


def test_load_ttl_updates_quantities_in_place(tmp_path: Path) -> None:
    """load_ttl(path) parses file and updates _quantities in place."""
    ttl = tmp_path / "mini.ttl"
    ttl.write_text(_MINI_TTL, encoding="utf-8")

    onto = ColumnOntology.build()
    onto.load_ttl(ttl)

    assert onto.test_time_second.unit == "ms"
    assert onto.test_time_second.formatted_label == "Test Time / ms"


def test_load_ttl_invalid_file_raises(tmp_path: Path) -> None:
    """load_ttl with unparseable content raises (not silent)."""
    bad = tmp_path / "bad.ttl"
    bad.write_text("this is not valid turtle syntax !! @@@", encoding="utf-8")

    onto = ColumnOntology.build()
    with pytest.raises(ValueError):
        onto.load_ttl(bad)


# ---------------------------------------------------------------------------
# UCUM unit code aliases (schema:unitCode "Cel" / "A.h" / "W.h" / "Ohm")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("attr", "expected_unit", "expected_label"),
    [
        ("ambient_temperature_celsius", "degC", "Ambient Temperature / degC"),
        ("charge_capacity_amp_hour", "Ah", "Charge Capacity / Ah"),
        ("energy_watt_hour", "Wh", "Energy / Wh"),
        ("internal_resistance_ohm", "ohm", "Internal Resistance / ohm"),
    ],
)
def test_load_ttl_ucum_unit_code_aliases(tmp_path: Path, attr: str, expected_unit: str, expected_label: str) -> None:
    """schema:unitCode UCUM codes ('Cel'/'A.h'/'W.h'/'Ohm') normalize to BDF's
    canonical, pint-valid casing ('degC'/'Ah'/'Wh'/'ohm').
    """
    ttl = tmp_path / "ucum.ttl"
    ttl.write_text(_UCUM_TTL, encoding="utf-8")

    onto = ColumnOntology({})
    onto.load_ttl(ttl)

    q = getattr(onto, attr)
    assert q.unit == expected_unit
    assert q.formatted_label == expected_label


def test_load_ttl_no_unit_code_term_has_none_unit_and_str_dtype(tmp_path: Path) -> None:
    """Terms with no schema:unitCode load with unit=None; 'string' in description infers dtype='str'."""
    ttl = tmp_path / "ucum.ttl"
    ttl.write_text(_UCUM_TTL, encoding="utf-8")

    onto = ColumnOntology({})
    onto.load_ttl(ttl)

    q = onto.step_label
    assert q.unit is None
    assert q.dtype == "str"
    assert q.formatted_label == "Step Label"


# ---------------------------------------------------------------------------
# ColumnOntology.load_latest()
# ---------------------------------------------------------------------------


def test_load_latest_no_refresh_uses_cache(tmp_path: Path) -> None:
    """load_latest(refresh=False) loads from cache; no HTTP call made."""
    ttl = tmp_path / "bdf-ontology-v1.0.0.ttl"
    ttl.write_text(_MINI_TTL, encoding="utf-8")

    onto = ColumnOntology.build()
    with (
        patch("bdf.spec._ontology_cache_dir", return_value=tmp_path),
        patch("requests.get") as mock_get,
    ):
        onto.load_latest(refresh=False)
        mock_get.assert_not_called()

    assert onto.test_time_second.unit == "ms"


def test_load_latest_refresh_fetches_and_caches(tmp_path: Path) -> None:
    """load_latest(refresh=True) fetches from URL, caches result, updates quantities."""
    response = Mock()
    response.content = _MINI_TTL.encode("utf-8")
    response.raise_for_status = Mock()

    onto = ColumnOntology.build()
    with (
        patch("bdf.spec._ontology_cache_dir", return_value=tmp_path),
        patch("requests.get", return_value=response) as mock_get,
    ):
        onto.load_latest(refresh=True)
        mock_get.assert_called_once()

    assert onto.test_time_second.unit == "ms"
    cached = list(tmp_path.glob("bdf-ontology-v*.ttl"))
    assert cached, "expected a cached file to be written"


# ---------------------------------------------------------------------------
# ColumnOntology.load_version()
# ---------------------------------------------------------------------------


def test_load_version_uses_cached_file(tmp_path: Path) -> None:
    """load_version(version) loads versioned file from cache, no HTTP."""
    ttl = tmp_path / "bdf-ontology-v1.0.0.ttl"
    ttl.write_text(_MINI_TTL, encoding="utf-8")

    onto = ColumnOntology.build()
    with patch("bdf.spec._ontology_cache_dir", return_value=tmp_path):
        onto.load_version("1.0.0")

    assert onto.test_time_second.unit == "ms"


def test_load_version_fetches_when_not_cached(tmp_path: Path) -> None:
    """load_version fetches the release when no cached file exists, then caches it."""
    versioned_ttl = _MINI_TTL.replace(
        "@prefix schema: <https://schema.org/> .\n",
        "@prefix schema: <https://schema.org/> .\n\n"
        "<https://w3id.org/battery-data-alliance/ontology/battery-data-format> "
        'rdf:type owl:Ontology ; owl:versionInfo "1.0.0" .\n',
    )
    response = Mock()
    response.content = versioned_ttl.encode("utf-8")
    response.raise_for_status = Mock()

    onto = ColumnOntology.build()
    with (
        patch("bdf.spec._ontology_cache_dir", return_value=tmp_path),
        patch("requests.get", return_value=response) as mock_get,
    ):
        onto.load_version("1.0.0")

    mock_get.assert_called_once()
    assert onto.test_time_second.unit == "ms"
    assert (tmp_path / "bdf-ontology-v1.0.0.ttl").exists()


# ---------------------------------------------------------------------------
# ColumnOntology.get_snapshot()
# ---------------------------------------------------------------------------


def test_get_snapshot_writes_to_dest(tmp_path: Path) -> None:
    """get_snapshot(dest=...) fetches, serializes, writes to dest path."""
    response = Mock()
    response.content = _MINI_TTL.encode("utf-8")
    response.raise_for_status = Mock()

    dest = tmp_path / "snapshot.ttl"
    with patch("requests.get", return_value=response):
        result = ColumnOntology.get_snapshot(dest=dest)

    assert result == dest
    assert dest.exists()
    assert dest.stat().st_size > 0


def _bundled_snapshot_version() -> str:
    """Parse owl:versionInfo out of the bundled snapshot TTL."""
    import re
    from importlib.resources import files

    ttl = (files("bdf.data") / "bdf-ontology-snapshot.ttl").read_text(encoding="utf-8")
    m = re.search(r'owl:versionInfo\s+"([0-9.]+)"', ttl)
    assert m, "bundled snapshot carries no owl:versionInfo"
    return m.group(1)


@pytest.mark.network
@pytest.mark.live_network
def test_bundled_snapshot_matches_its_declared_release(tmp_path: Path) -> None:
    """Bundled snapshot is byte-equivalent (per-quantity) to the ontology release it declares.

    Deliberately pinned to the snapshot's own version rather than the live (latest
    deployed) ontology: ontology main legitimately runs ahead of the latest release
    during release-prep windows (version bumps are deferred to the release PR there),
    so bundled-vs-live is not a stable invariant. Run `bdf-update-snapshot` after a
    new ontology release to advance the snapshot.
    """
    version = _bundled_snapshot_version()
    fresh_path = ColumnOntology.get_snapshot(dest=tmp_path / "fresh.ttl", version=version)

    fresh = ColumnOntology({})
    fresh.load_ttl(fresh_path)

    bundled = ColumnOntology.build()

    fresh_quantities = {name: (q.unit, q.label_template) for name, q in fresh}
    bundled_quantities = {name: (q.unit, q.label_template) for name, q in bundled}

    assert fresh_quantities == bundled_quantities, (
        f"Bundled snapshot does not match ontology release {version}. Run `bdf-update-snapshot` to regenerate."
    )


@pytest.mark.network
@pytest.mark.live_network
def test_live_ontology_is_superset_of_bundled(tmp_path: Path) -> None:
    """Live ontology main never loses or alters a quantity the bundled release has.

    The live graph may carry ADDITIONAL terms (unreleased release-prep work upstream);
    that is expected and tolerated. What must never happen is an existing quantity
    disappearing or changing unit/label upstream without a coordinated release.
    """
    fresh_path = ColumnOntology.get_snapshot(dest=tmp_path / "fresh.ttl")

    fresh = ColumnOntology({})
    fresh.load_ttl(fresh_path)

    bundled = ColumnOntology.build()

    fresh_quantities = {name: (q.unit, q.label_template) for name, q in fresh}
    for name, q in bundled:
        assert name in fresh_quantities, f"quantity {name!r} vanished from the live ontology"
        assert fresh_quantities[name] == (q.unit, q.label_template), (
            f"quantity {name!r} changed upstream without a release: "
            f"live {fresh_quantities[name]} != bundled {(q.unit, q.label_template)}"
        )


# ---------------------------------------------------------------------------
# ColumnOntology container protocol
# ---------------------------------------------------------------------------


def test_get_nonexistent_returns_none() -> None:
    """ontology.get('nonexistent') returns None; 'nonexistent' not in ontology."""
    assert COLUMN_ONTOLOGY.get("nonexistent") is None
    assert "nonexistent" not in COLUMN_ONTOLOGY


def test_iteration_yields_str_quantity_pairs() -> None:
    """for name, q in ontology yields (str, Quantity) pairs."""
    for name, q in COLUMN_ONTOLOGY:
        assert isinstance(name, str)
        assert isinstance(q, Quantity)
        break  # one iteration is enough to confirm the pattern


# ---------------------------------------------------------------------------
# ColumnOntology.validate_df()
# ---------------------------------------------------------------------------


@pytest.fixture
def required_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "Test Time / s": [0.0, 1.0],
            "Voltage / V": [3.7, 3.6],
            "Current / A": [0.1, 0.1],
        }
    )


def test_validate_df_passes_with_required_columns(required_df: pl.DataFrame) -> None:
    spec.COLUMN_ONTOLOGY.validate_df(required_df)


def test_validate_df_passes_with_lazyframe(required_df: pl.DataFrame) -> None:
    spec.COLUMN_ONTOLOGY.validate_df(required_df.lazy())


def test_validate_df_raises_when_required_column_missing(required_df: pl.DataFrame) -> None:
    from bdf.validate import BDFValidationError

    df = required_df.drop("Voltage / V")
    with pytest.raises(BDFValidationError, match="Voltage / V"):
        spec.COLUMN_ONTOLOGY.validate_df(df)


def test_validate_df_raises_listing_all_missing_required_columns() -> None:
    from bdf.validate import BDFValidationError

    df = pl.DataFrame({"Test Time / s": [0.0]})
    with pytest.raises(BDFValidationError) as exc_info:
        spec.COLUMN_ONTOLOGY.validate_df(df)
    msg = str(exc_info.value)
    assert "Voltage / V" in msg
    assert "Current / A" in msg


def test_validate_df_warns_on_extra_non_bdf_columns(required_df: pl.DataFrame) -> None:
    df = required_df.with_columns(pl.lit(0).alias("Unknown Column"))
    with pytest.warns(UserWarning, match="Unknown Column"):
        spec.COLUMN_ONTOLOGY.validate_df(df)


def test_validate_df_no_warning_with_only_canonical_columns(required_df: pl.DataFrame, recwarn) -> None:
    spec.COLUMN_ONTOLOGY.validate_df(required_df)
    user_warnings = [w for w in recwarn.list if issubclass(w.category, UserWarning)]
    assert len(user_warnings) == 0


def test_validate_df_deprecated_quantity_not_counted_as_required(required_df: pl.DataFrame) -> None:
    q_dep = Quantity(
        unit="V",
        label_template="Old Voltage / V",
        obligation="required",
        mr_name="old_voltage_volt",
        iri="",
        synonyms=[],
        deprecated=True,
    )
    onto = ColumnOntology({"old_voltage_volt": q_dep})
    with pytest.warns(UserWarning, match="Non-BDF columns"):
        onto.validate_df(required_df)


def test_validate_df_extra_canonical_columns_do_not_warn(required_df: pl.DataFrame, recwarn) -> None:
    df = required_df.with_columns(pl.lit(25.0).alias("Ambient Temperature / degC"))
    spec.COLUMN_ONTOLOGY.validate_df(df)
    user_warnings = [w for w in recwarn.list if issubclass(w.category, UserWarning)]
    assert len(user_warnings) == 0


def test_validate_df_accepts_pandas_dataframe_and_returns_it(required_df: pl.DataFrame) -> None:
    pdf = required_df.to_pandas()
    result = spec.COLUMN_ONTOLOGY.validate_df(pdf)
    assert isinstance(result, pd.DataFrame)
    assert result.equals(pdf)


def test_validate_df_accepts_polars_dataframe_and_returns_it(required_df: pl.DataFrame) -> None:
    result = spec.COLUMN_ONTOLOGY.validate_df(required_df)
    assert isinstance(result, pl.DataFrame)
    assert result.equals(required_df)


def test_validate_df_accepts_lazyframe_and_returns_it(required_df: pl.DataFrame) -> None:
    lf = required_df.lazy()
    result = spec.COLUMN_ONTOLOGY.validate_df(lf)
    assert isinstance(result, pl.LazyFrame)
    assert result.collect().equals(lf.collect())


def test_validate_df_raises_on_missing_columns_with_pandas_input() -> None:
    from bdf.validate import BDFValidationError

    pdf = pd.DataFrame({"Test Time / s": [0.0]})
    with pytest.raises(BDFValidationError):
        spec.COLUMN_ONTOLOGY.validate_df(pdf)


def test_validate_df_warns_on_extra_columns_with_pandas_input() -> None:
    pdf = pd.DataFrame(
        {
            "Test Time / s": [0.0],
            "Voltage / V": [3.7],
            "Current / A": [0.1],
            "Unknown Column": [99],
        }
    )
    with pytest.warns(UserWarning, match="Unknown Column"):
        spec.COLUMN_ONTOLOGY.validate_df(pdf)


# ---------------------------------------------------------------------------
# Quantity label template validator
# ---------------------------------------------------------------------------


class TestQuantityModelValidator:
    def test_hard_coded_unit_auto_inserted(self) -> None:
        """Hard-coded units in label_template are replaced with {unit} placeholder."""
        q = Quantity(unit="V", label_template="Voltage / V", mr_name="x", iri="x", synonyms=[])
        assert q.label_template == "Voltage / {unit}"

    def test_hard_coded_unit_non_si(self) -> None:
        """Non-SI units in label_template are also replaced with {unit} placeholder."""
        q = Quantity(unit="mV", label_template="Voltage / mV", mr_name="x", iri="x", synonyms=[])
        assert q.label_template == "Voltage / {unit}"

    def test_dimensionless_already_correct_unchanged(self) -> None:
        """Dimensionless label_template with hardcoded 1 is left unchanged."""
        q = Quantity(unit="1", label_template="Cycle Count / 1", mr_name="x", iri="x", synonyms=[])
        assert q.label_template == "Cycle Count / 1"

    def test_label_without_slash_not_modified(self) -> None:
        """Labels without slash separator are not modified."""
        q = Quantity(unit="V", label_template="Voltage", mr_name="x", iri="x", synonyms=[])
        assert q.label_template == "Voltage"


# ---------------------------------------------------------------------------
# formatted_label property
# ---------------------------------------------------------------------------


class TestFormattedLabel:
    def test_template_quantity_returns_formatted(self) -> None:
        """formatted_label substitutes {unit} placeholder with actual unit."""
        q = Quantity(unit="V", label_template="Voltage / {unit}", mr_name="x", iri="x", synonyms=[])
        assert q.formatted_label == "Voltage / V"

    def test_dimensionless_returns_label_unchanged(self) -> None:
        """Dimensionless quantities (unit=1) return label_template unchanged."""
        q = Quantity(unit="1", label_template="Cycle Count / 1", mr_name="x", iri="x", synonyms=[])
        assert q.formatted_label == "Cycle Count / 1"

    def test_ontology_cycle_count(self) -> None:
        """COLUMN_ONTOLOGY.cycle_count produces correct formatted_label."""
        assert COLUMN_ONTOLOGY.cycle_count.formatted_label == "Cycle Count / 1"

    def test_ontology_test_time(self) -> None:
        """COLUMN_ONTOLOGY.test_time_second produces correct formatted_label."""
        assert COLUMN_ONTOLOGY.test_time_second.formatted_label == "Test Time / s"

    def test_ontology_current(self) -> None:
        """COLUMN_ONTOLOGY.current_ampere produces correct formatted_label."""
        assert COLUMN_ONTOLOGY.current_ampere.formatted_label == "Current / A"

    def test_unit_none_no_slash_returns_label_unchanged(self) -> None:
        """Unitless quantities (e.g. step_id) return label_template unchanged."""
        q = Quantity(unit=None, label_template="Step ID", mr_name="x", iri="x", synonyms=[])
        assert q.formatted_label == "Step ID"

    def test_unit_none_with_literal_placeholder_left_unsubstituted(self) -> None:
        """A literal {unit} placeholder is not substituted when unit is None."""
        q = Quantity(unit=None, label_template="X / {unit}", mr_name="x", iri="x", synonyms=[])
        assert q.formatted_label == "X / {unit}"


# ---------------------------------------------------------------------------
# dcterms:isReplacedBy -> Quantity.replaced_by
# ---------------------------------------------------------------------------

_REPLACED_BY_TTL = """\
@prefix : <https://w3id.org/battery-data-alliance/ontology/battery-data-format#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix schema: <https://schema.org/> .
@prefix dcterms: <http://purl.org/dc/terms/> .

:old_thing_ah rdf:type owl:Class ;
    owl:deprecated "true"^^<http://www.w3.org/2001/XMLSchema#boolean> ;
    dcterms:isReplacedBy :new_thing_ah ;
    skos:prefLabel "Old Thing / Ah"@en ;
    schema:unitCode "A.h" .

:orphan_ms rdf:type owl:Class ;
    owl:deprecated "true"^^<http://www.w3.org/2001/XMLSchema#boolean> ;
    skos:prefLabel "Orphan / ms"@en ;
    schema:unitCode "ms" .

:new_thing_ah rdf:type owl:Class ;
    skos:prefLabel "New Thing / Ah"@en ;
    schema:unitCode "A.h" .
"""


def test_replaced_by_extracted_from_isreplacedby_link() -> None:
    """A deprecated term's dcterms:isReplacedBy fragment lands in Quantity.replaced_by."""
    onto = ColumnOntology.from_graph(spec._graph_from_bytes(_REPLACED_BY_TTL.encode("utf-8"), format="turtle"))
    assert onto.old_thing_ah.replaced_by == "new_thing_ah"
    assert onto.new_thing_ah.replaced_by == ""


def test_replaced_by_empty_without_link() -> None:
    """A deprecated term without an isReplacedBy link keeps replaced_by empty (heuristic fallback)."""
    onto = ColumnOntology.from_graph(spec._graph_from_bytes(_REPLACED_BY_TTL.encode("utf-8"), format="turtle"))
    assert onto.orphan_ms.replaced_by == ""


def test_every_deprecated_quantity_has_replacement() -> None:
    """Bundled snapshot invariant: every deprecated term links a non-deprecated replacement."""
    for mr, q in COLUMN_ONTOLOGY:
        if not q.deprecated:
            continue
        assert q.replaced_by, f"{mr} is deprecated but carries no dcterms:isReplacedBy"
        target = COLUMN_ONTOLOGY.get(q.replaced_by)
        assert target is not None and not target.deprecated, f"{mr} -> {q.replaced_by}"
