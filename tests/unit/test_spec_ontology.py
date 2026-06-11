from __future__ import annotations

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

:test_time_second rdf:type owl:Class ;
    skos:prefLabel "Test Time / ms"@en ;
    skos:altLabel "elapsed_ms"@en .
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
    assert q.unit_conversion(dst_unit) == expected


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
def test_quantity_convert_from(
    quantity_unit: str, src_unit: str | None, expected: tuple[float, float] | None
) -> None:
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
        unit="V", label_template="Old / {unit}", obligation="required", mr_name="old_volt", iri="", synonyms=[], deprecated=True
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


def test_load_version_missing_raises_value_error(tmp_path: Path) -> None:
    """load_version with no cached file raises ValueError listing available versions."""
    onto = ColumnOntology.build()
    with (
        patch("bdf.spec._ontology_cache_dir", return_value=tmp_path),
        pytest.raises(ValueError, match="not found in cache"),
    ):
        onto.load_version("9.9.9")


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


@pytest.mark.network
def test_bundled_snapshot_is_up_to_date(tmp_path: Path) -> None:
    """Bundled snapshot matches live ontology. Run `bdf-update-snapshot` if this fails."""
    fresh_path = ColumnOntology.get_snapshot(dest=tmp_path / "fresh.ttl")

    fresh = ColumnOntology({})
    fresh.load_ttl(fresh_path)

    bundled = ColumnOntology.build()

    fresh_quantities = {name: (q.unit, q.label_template) for name, q in fresh}
    bundled_quantities = {name: (q.unit, q.label_template) for name, q in bundled}

    assert fresh_quantities == bundled_quantities, (
        "Bundled snapshot is stale. Run `bdf-update-snapshot` to regenerate."
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
    return pl.DataFrame({
        "Test Time / s": [0.0, 1.0],
        "Voltage / V": [3.7, 3.6],
        "Current / A": [0.1, 0.1],
    })


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
    pdf = pd.DataFrame({
        "Test Time / s": [0.0],
        "Voltage / V": [3.7],
        "Current / A": [0.1],
        "Unknown Column": [99],
    })
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
