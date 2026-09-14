"""Acceptance checks for optional, described measurements alongside BDF cycling data."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from bdf import BDFMetadataError, Metadata, io
from bdf.battinfo.generated.dataset_schema import VariableMeasured
from bdf.metadata_parsers import BdfSidecarParser

_THICKNESS_DESCRIPTION = "Measured every 100 cycles with a parallel plate height gauge at 100% SOC."


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BDF_CACHE_DIR", str(tmp_path / "cache"))


def _cycling_data() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "Test Time / s": [0.0, 1.0, 2.0, 3.0],
            "Voltage / V": [4.2, 4.1, 4.2, 4.2],
            "Current / A": [0.1, 0.1, 0.1, 0.1],
            "Thickness / mm": [7.1, None, 0.0, 7.3],
        }
    )


def _metadata(*variables: VariableMeasured) -> Metadata:
    metadata = Metadata()
    metadata.battinfo_dataset.dataset.variable_measured = list(variables)
    return metadata


def _phil_metadata() -> Metadata:
    return _metadata(VariableMeasured(name="Thickness", unit_text="mm", description=_THICKNESS_DESCRIPTION))


def _csv_expected(frame: pl.DataFrame) -> pl.DataFrame:
    """The CSV parser preserves custom/unknown cell text; no datatype is inferred from a unit."""
    return frame.with_columns(pl.col(name).cast(pl.String) for name in frame.columns[3:])


def _write_input(path: Path, frame: pl.DataFrame, metadata: Metadata | None = None) -> None:
    """Write external input directly, so reader checks do not depend on the BDF writer."""
    frame.write_csv(path)
    if metadata is not None:
        BdfSidecarParser().sidecar_path(path).write_text(json.dumps(metadata.to_dict()), encoding="utf-8")


@pytest.mark.parametrize("extension", ["csv", "parquet", "csv.gz"])
@pytest.mark.parametrize("labels", ["preferred", "machine"])
def test_phils_sparse_measurements_roundtrip_by_default(tmp_path: Path, extension: str, labels: str) -> None:
    """Phil's description needs no ontology IRI, and a zero is distinct from a missing reading."""
    frame = _cycling_data()
    expected = frame if extension == "parquet" else _csv_expected(frame)
    metadata = _phil_metadata()
    path = tmp_path / f"thickness.bdf.{extension}"

    io.save(frame.lazy(), path, metadata=metadata, labels=labels)

    for reader in (io.read, io.scan):
        restored, restored_metadata = reader(path)
        if reader is io.scan:
            assert isinstance(restored, pl.LazyFrame)
            restored = restored.collect()
        assert_frame_equal(restored, expected)
        assert restored["Thickness / mm"].to_list() == (
            [7.1, None, 0.0, 7.3] if extension == "parquet" else ["7.1", None, "0.0", "7.3"]
        )
        assert restored_metadata.battinfo_dataset.dataset.variable_measured == (
            metadata.battinfo_dataset.dataset.variable_measured
        )

        second_path = tmp_path / f"resaved-{reader.__name__}.bdf.{extension}"
        io.save(restored, second_path, metadata=restored_metadata, labels=labels)
        second_frame, second_metadata = io.read(second_path)
        assert_frame_equal(second_frame, expected)
        assert second_metadata.battinfo_dataset.dataset.variable_measured == (
            metadata.battinfo_dataset.dataset.variable_measured
        )


@pytest.mark.parametrize("header", ["Thickness", "Thickness / mm"])
def test_exact_header_binding_preserves_the_custom_name(tmp_path: Path, header: str) -> None:
    frame = _cycling_data().rename({"Thickness / mm": header})
    metadata = _metadata(VariableMeasured(name=header, unit_text="mm"))
    path = tmp_path / "exact.bdf.csv"
    _write_input(path, frame, metadata)

    restored, _ = io.read(path)

    assert_frame_equal(restored, _csv_expected(frame))


@pytest.mark.parametrize("header", ["dQ/dV", "dQ/dV / Ah/V"])
def test_slashes_in_measurement_names_and_units_do_not_conflict(tmp_path: Path, header: str) -> None:
    frame = _cycling_data().rename({"Thickness / mm": header})
    metadata = _metadata(VariableMeasured(name="dQ/dV", unit_text="Ah/V"))
    path = tmp_path / "differential.bdf.csv"
    _write_input(path, frame, metadata)

    restored, restored_metadata = io.read(path)

    assert_frame_equal(restored, _csv_expected(frame))
    assert restored_metadata.battinfo_dataset.dataset.variable_measured == (
        metadata.battinfo_dataset.dataset.variable_measured
    )


def test_optional_ontology_iri_is_preserved_without_a_network_request(tmp_path: Path) -> None:
    """The suite blocks sockets: descriptive IRIs must never trigger an ontology download."""
    variable = VariableMeasured(name="Thickness", unit_text="mm", same_as="https://example.org/ontology/thickness")
    path = tmp_path / "iri.bdf.parquet"
    io.save(_cycling_data(), path, metadata=_metadata(variable))

    restored, metadata = io.read(path)

    assert "Thickness / mm" in restored.columns
    assert metadata.battinfo_dataset.dataset.variable_measured == [variable]


@pytest.mark.parametrize("unit", ["m / s", "s"])
def test_exact_header_checks_the_complete_compound_unit(tmp_path: Path, unit: str) -> None:
    header = "Rate / m / s"
    frame = _cycling_data().rename({"Thickness / mm": header})
    metadata = _metadata(VariableMeasured(name=header, unit_text=unit))
    path = tmp_path / "rate.bdf.csv"
    _write_input(path, frame, metadata)

    if unit == "s":
        with pytest.raises(BDFMetadataError, match="conflicts"):
            io.read(path)
    else:
        restored, _ = io.read(path)
        assert_frame_equal(restored, _csv_expected(frame))


@pytest.mark.parametrize("reader", [io.read, io.scan])
@pytest.mark.parametrize("declared", [False, True])
def test_unknown_opt_in_still_controls_undeclared_columns(tmp_path: Path, reader, declared: bool) -> None:
    frame = _cycling_data().with_columns(pl.lit(3.0).alias("Undescribed sensor / arbitrary"))
    path = tmp_path / "extras.bdf.csv"
    _write_input(path, frame, _phil_metadata() if declared else None)

    restored, _ = reader(path)
    columns = restored.collect_schema().names() if isinstance(restored, pl.LazyFrame) else restored.columns
    assert ("Thickness / mm" in columns) is declared
    assert "Undescribed sensor / arbitrary" not in columns

    all_columns, _ = reader(path, include_unknown=True)
    if isinstance(all_columns, pl.LazyFrame):
        all_columns = all_columns.collect()
    assert_frame_equal(all_columns, _csv_expected(frame))


@pytest.mark.parametrize("reader", [io.read, io.scan])
def test_standard_variable_metadata_does_not_disable_bdf_unit_conversion(tmp_path: Path, reader) -> None:
    frame = _cycling_data().rename({"Current / A": "Current / mA"})
    frame = frame.with_columns(pl.lit(100.0).alias("Current / mA"))
    metadata = _phil_metadata()
    metadata.battinfo_dataset.dataset.variable_measured.append(VariableMeasured(name="Current", unit_text="mA"))
    path = tmp_path / "standard.bdf.csv"
    _write_input(path, frame, metadata)

    restored, restored_metadata = reader(path)
    if isinstance(restored, pl.LazyFrame):
        restored = restored.collect()

    assert "Current / mA" not in restored.columns
    assert restored["Current / A"].to_list() == pytest.approx([0.1] * 4)
    assert restored["Thickness / mm"].to_list() == ["7.1", None, "0.0", "7.3"]
    assert restored_metadata.battinfo_dataset.dataset.variable_measured == (
        metadata.battinfo_dataset.dataset.variable_measured
    )


def _invalid_bindings(case: str) -> tuple[pl.DataFrame, Metadata]:
    frame = _cycling_data()
    variable = VariableMeasured(name="Thickness", unit_text="mm")
    if case == "missing-column":
        variable.name = "Unrecorded sensor"
    elif case == "duplicate":
        return frame, _metadata(variable, variable.model_copy())
    elif case == "duplicate-target":
        return frame, _metadata(variable, VariableMeasured(name="Thickness / mm", unit_text="mm"))
    elif case == "ambiguous":
        frame = frame.with_columns(pl.col("Thickness / mm").alias("Thickness"))
    elif case == "unit-conflict":
        variable.name = "Thickness / mm"
        variable.unit_text = "cm"
    elif case == "blank-name":
        variable.name = " "
    elif case == "missing-name":
        variable.name = None
    elif case == "blank-unit":
        variable.unit_text = " "
    elif case == "missing-unit":
        variable.unit_text = None
    else:
        raise AssertionError(f"Unknown test case: {case}")
    return frame, _metadata(variable)


_INVALID_CASES = [
    "missing-column",
    "duplicate",
    "duplicate-target",
    "ambiguous",
    "unit-conflict",
    "blank-name",
    "missing-name",
    "blank-unit",
    "missing-unit",
]


@pytest.mark.parametrize("case", _INVALID_CASES)
def test_invalid_custom_metadata_is_rejected_before_save_creates_files(tmp_path: Path, case: str) -> None:
    frame, metadata = _invalid_bindings(case)
    path = tmp_path / "invalid.bdf.csv"

    with pytest.raises(BDFMetadataError):
        io.save(frame, path, metadata=metadata)

    assert not path.exists()
    assert not BdfSidecarParser().sidecar_path(path).exists()


@pytest.mark.parametrize("case", _INVALID_CASES)
@pytest.mark.parametrize("reader", [io.read, io.scan])
def test_invalid_custom_bindings_raise_or_warn_on_read(tmp_path: Path, case: str, reader) -> None:
    frame, metadata = _invalid_bindings(case)
    path = tmp_path / "invalid.bdf.csv"
    _write_input(path, frame, metadata)

    with pytest.raises(BDFMetadataError):
        reader(path)

    with pytest.warns(UserWarning):
        restored, restored_metadata = reader(path, validate=False, include_unknown=True)
        if isinstance(restored, pl.LazyFrame):
            restored = restored.collect()

    assert_frame_equal(restored, _csv_expected(frame))
    assert restored_metadata.battinfo_dataset.dataset.variable_measured == (
        metadata.battinfo_dataset.dataset.variable_measured
    )


def test_failed_metadata_validation_does_not_overwrite_an_existing_artifact(tmp_path: Path) -> None:
    path = tmp_path / "existing.bdf.csv"
    io.save(_cycling_data(), path, metadata=_phil_metadata())
    sidecar = BdfSidecarParser().sidecar_path(path)
    data_before, metadata_before = path.read_bytes(), sidecar.read_bytes()
    frame, metadata = _invalid_bindings("unit-conflict")

    with pytest.raises(BDFMetadataError):
        io.save(frame, path, metadata=metadata)

    assert path.read_bytes() == data_before
    assert sidecar.read_bytes() == metadata_before


def test_invalid_save_can_warn_without_discarding_explicit_metadata(tmp_path: Path) -> None:
    frame, metadata = _invalid_bindings("unit-conflict")
    path = tmp_path / "unchecked.bdf.csv"

    with pytest.warns(UserWarning):
        io.save(frame, path, metadata=metadata, validate=False)

    assert_frame_equal(pl.read_csv(path), frame)
    saved = json.loads(BdfSidecarParser().sidecar_path(path).read_text(encoding="utf-8"))
    assert saved["battinfo_dataset"]["dataset"]["variable_measured"] == [{"name": "Thickness / mm", "unit_text": "cm"}]


def test_scan_keeps_the_custom_measurement_plan_lazy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolving descriptions must inspect the schema, without materializing the whole table."""
    path = tmp_path / "lazy.bdf.parquet"
    io.save(_cycling_data(), path, metadata=_phil_metadata())

    def collect_is_unexpected(*args, **kwargs):
        pytest.fail("scan() materialized rows while binding custom measurements")

    with monkeypatch.context() as patch:
        patch.setattr(pl.LazyFrame, "collect", collect_is_unexpected)
        lazy_frame, metadata = io.scan(path, validate=False)

    assert isinstance(lazy_frame, pl.LazyFrame)
    assert_frame_equal(lazy_frame.collect(), _cycling_data())
    assert metadata.battinfo_dataset.dataset.variable_measured == (
        _phil_metadata().battinfo_dataset.dataset.variable_measured
    )
