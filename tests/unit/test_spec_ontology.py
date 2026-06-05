from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

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

:test_time_second rdf:type owl:Class ;
    skos:prefLabel "Test Time / ms"@en ;
    skos:altLabel "elapsed_ms"@en .
"""

_DEPRECATED_TTL = """\
@prefix : <https://w3id.org/battery-data-alliance/ontology/battery-data-format#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .

:voltage_volt rdf:type owl:Class ;
    skos:prefLabel "Old Voltage / mV"@en ;
    owl:deprecated "true"^^<http://www.w3.org/2001/XMLSchema#boolean> .
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
    ],
)
def test_get_unit_conversion(src: str | None, dst: str, expected: tuple[float, float] | None) -> None:
    assert get_unit_conversion(src, dst) == expected


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


@pytest.mark.parametrize("bad_dtype", ["str", "double", "", "Int"])
def test_quantity_invalid_dtype_raises(bad_dtype: str) -> None:
    with pytest.raises(ValidationError):
        Quantity(unit="V", label_template="V / {unit}", dtype=bad_dtype, mr_name="x", iri="", synonyms=[])


def test_quantity_invalid_field_type_raises() -> None:
    with pytest.raises(ValidationError):
        Quantity(
            unit="V",
            label_template="Voltage / {unit}",
            required="not-a-bool-or-coercible",  # type: ignore[arg-type]
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
    assert q.unit_conversion(dst_unit) == expected


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
        unit="V", label_template="Old / {unit}", required=True, mr_name="old_volt", iri="", synonyms=[], deprecated=True
    )
    onto = ColumnOntology(old_volt=q_dep)  # type: ignore[call-arg]
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
        required=False,
        mr_name="old_volt",
        iri="",
        synonyms=["old-voltage"],
        deprecated=True,
    )
    onto = ColumnOntology(old_volt=q_dep)  # type: ignore[call-arg]
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
    onto = ColumnOntology(custom_q=q)  # type: ignore[call-arg]
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


# ---------------------------------------------------------------------------
# Ontology loading & build()
# ---------------------------------------------------------------------------


def test_build_loads_ontology_from_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Env var TTL is parsed; live URL and snapshot loaders are not invoked."""
    ttl = tmp_path / "mini.ttl"
    ttl.write_text(_MINI_TTL, encoding="utf-8")
    monkeypatch.setenv("BDF_ONTOLOGY_PATH", str(ttl))

    with patch("bdf.spec._requests") as mock_requests, patch("bdf.spec._graph_from_bytes") as snapshot_loader:
        onto = ColumnOntology.build()
        mock_requests.get.assert_not_called()
        snapshot_loader.assert_not_called()

    assert onto.test_time_second.unit == "ms"
    assert onto.test_time_second.label_template == "Test Time / {unit}"
    assert onto.test_time_second.formatted_label == "Test Time / ms"
    assert onto.base_synonym_index()["elapsed-ms"] == "test_time_second"


def test_build_warns_when_env_var_path_cannot_be_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failed env-var load warns and stops — does not fall through to live URL or snapshot."""
    monkeypatch.setattr(spec, "_WARNED_ONTOLOGY_SOURCES", set())
    monkeypatch.setenv("BDF_ONTOLOGY_PATH", "does-not-exist.ttl")

    with (
        patch("bdf.spec._requests") as mock_requests,
        patch("bdf.spec._graph_from_bytes") as snapshot_loader,
        pytest.warns(UserWarning),
    ):
        onto = ColumnOntology.build()
        mock_requests.get.assert_not_called()
        snapshot_loader.assert_not_called()

    assert isinstance(onto, ColumnOntology)


def test_build_uses_live_url_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live URL succeeds → its data is used; snapshot loader not reached."""
    monkeypatch.delenv("BDF_ONTOLOGY_PATH", raising=False)
    monkeypatch.delenv("BDF_ONTOLOGY", raising=False)

    response = Mock()
    response.content = _MINI_TTL.encode("utf-8")
    response.raise_for_status = Mock()

    with (
        patch("bdf.spec._requests") as mock_requests,
        patch("bdf.spec._graph_from_bytes", wraps=spec._graph_from_bytes) as graph_loader,
    ):
        mock_requests.get.return_value = response
        onto = ColumnOntology.build()
        mock_requests.get.assert_called_once_with(spec._BDF_LIVE_URL, timeout=5)
        # Step 2 (live) and step 3 (snapshot) both go through _graph_from_bytes; if live
        # succeeds, snapshot is never reached, so exactly one call.
        assert graph_loader.call_count == 1

    # Live overrode the static default
    assert onto.test_time_second.unit == "ms"


def test_build_falls_back_to_snapshot_when_network_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env var + no network → snapshot loader is invoked."""
    monkeypatch.delenv("BDF_ONTOLOGY_PATH", raising=False)
    monkeypatch.delenv("BDF_ONTOLOGY", raising=False)

    with (
        patch("bdf.spec._requests", None),
        patch("bdf.spec._graph_from_bytes", wraps=spec._graph_from_bytes) as graph_loader,
    ):
        onto = ColumnOntology.build()
        # _requests is None so step 2 is skipped entirely; only the snapshot path
        # (step 3) calls _graph_from_bytes.
        graph_loader.assert_called_once()

    assert isinstance(onto, ColumnOntology)


def test_build_falls_back_to_static_when_snapshot_also_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env, no network, broken snapshot → static baseline still produces a valid ontology."""
    monkeypatch.delenv("BDF_ONTOLOGY_PATH", raising=False)
    monkeypatch.delenv("BDF_ONTOLOGY", raising=False)

    with (
        patch("bdf.spec._requests", None),
        patch("bdf.spec._graph_from_bytes", return_value=None),
    ):
        onto = ColumnOntology.build()

    # Static baseline always provides the three required quantities
    for required in spec.ColumnOntology().required_labels():
        assert required in onto.required_labels()


def test_ontology_merge_deprecated_entry_preserves_canonical_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ttl = tmp_path / "dep.ttl"
    ttl.write_text(_DEPRECATED_TTL, encoding="utf-8")
    monkeypatch.setenv("BDF_ONTOLOGY_PATH", str(ttl))

    onto = ColumnOntology.build()

    assert onto.voltage_volt.unit == "V"  # canonical preserved
    assert onto.voltage_volt.deprecated is True  # flag still picked up


# ---------------------------------------------------------------------------
# ColumnOntology.validate()
# ---------------------------------------------------------------------------

_REQUIRED_COLS = {
    "Test Time / s": [0.0, 1.0],
    "Voltage / V": [3.7, 3.6],
    "Current / A": [0.1, 0.1],
}


def _required_df() -> pl.DataFrame:
    return pl.DataFrame(_REQUIRED_COLS)


def test_validate_passes_with_required_columns() -> None:
    spec.COLUMN_ONTOLOGY.validate(_required_df())


def test_validate_passes_with_lazyframe() -> None:
    spec.COLUMN_ONTOLOGY.validate(_required_df().lazy())


def test_validate_raises_when_required_column_missing() -> None:
    from bdf.validate import BDFValidationError

    df = _required_df().drop("Voltage / V")
    with pytest.raises(BDFValidationError, match="Voltage / V"):
        spec.COLUMN_ONTOLOGY.validate(df)


def test_validate_raises_listing_all_missing_required_columns() -> None:
    from bdf.validate import BDFValidationError

    df = pl.DataFrame({"Test Time / s": [0.0]})
    with pytest.raises(BDFValidationError) as exc_info:
        spec.COLUMN_ONTOLOGY.validate(df)
    msg = str(exc_info.value)
    assert "Voltage / V" in msg
    assert "Current / A" in msg


def test_validate_warns_on_extra_non_bdf_columns() -> None:
    df = _required_df().with_columns(pl.lit(0).alias("Unknown Column"))
    with pytest.warns(UserWarning, match="Unknown Column"):
        spec.COLUMN_ONTOLOGY.validate(df)


def test_validate_no_warning_with_only_canonical_columns(recwarn: pytest.WarningsChecker) -> None:
    spec.COLUMN_ONTOLOGY.validate(_required_df())
    user_warnings = [w for w in recwarn.list if issubclass(w.category, UserWarning)]
    assert len(user_warnings) == 0


def test_validate_deprecated_quantity_not_counted_as_required() -> None:
    q_dep = Quantity(
        unit="V",
        label_template="Old Voltage / V",
        required=True,
        mr_name="old_voltage_volt",
        iri="",
        synonyms=[],
        deprecated=True,
    )
    onto = ColumnOntology(old_voltage_volt=q_dep)  # type: ignore[call-arg]
    # Should not raise even though old_voltage_volt is absent from df
    onto.validate(_required_df())


def test_validate_extra_canonical_columns_do_not_warn(recwarn: pytest.WarningsChecker) -> None:
    df = _required_df().with_columns(pl.lit(25.0).alias("Ambient Temperature / degC"))
    spec.COLUMN_ONTOLOGY.validate(df)
    user_warnings = [w for w in recwarn.list if issubclass(w.category, UserWarning)]
    assert len(user_warnings) == 0


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
