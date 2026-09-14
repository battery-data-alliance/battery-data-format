"""User-facing term discovery, acceptance and reuse, with networking disabled."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import polars as pl
import pytest
from pydantic import AnyUrl
from typer.testing import CliRunner

from bdf import BDFMetadataError, Metadata, io, validate
from bdf.battinfo.generated.dataset_schema import VariableMeasured
from bdf.cli import app
from bdf.metadata_parsers import BdfSidecarParser
from bdf.ontology_terms import _index, apply_term_mappings, suggest_terms

IRI = "https://w3id.org/emmo#EMMO_43003c86_9d15_433b_9789_ee2940920656"
COLUMN = "Thickness / mm"
runner = CliRunner()


def frame() -> pl.DataFrame:
    return pl.DataFrame(
        {"Test Time / s": [0.0, 1.0, 2.0], "Voltage / V": [4.2] * 3, "Current / A": [0.1] * 3, COLUMN: [5.1, None, 0.0]}
    )


def metadata() -> Metadata:
    value = Metadata()
    value.extras = {"keep": {"nested": [1, None]}}
    assert value.battinfo_dataset.dataset is not None
    value.battinfo_dataset.dataset.variable_measured = [
        VariableMeasured(name="Thickness", unit_text="mm", description="Height gauge at 100% SOC")
    ]
    return value


def measurement(meta: Metadata) -> VariableMeasured:
    dataset = meta.battinfo_dataset.dataset
    assert dataset is not None and dataset.variable_measured
    return dataset.variable_measured[0]


def write_source(tmp_path: Path, *, described: bool = True, name: str = "sample") -> Path:
    path = tmp_path / f"{name}.bdf.csv"
    frame().write_csv(path)
    if described:
        BdfSidecarParser().sidecar_path(path).write_text(metadata().model_dump_json(exclude_defaults=True))
    return path


def mapping(column: str = COLUMN, unit: str = "mm", iri: str = IRI) -> dict:
    return {"column": column, "unit_text": unit, "same_as": iri}


@pytest.mark.parametrize(
    "column", [COLUMN, "thickness / mm", "foil_thickness / mm", "FoilThickness / mm", "wall-thickness / mm"]
)
def test_curated_names_and_aliases_suggest_existing_thickness_offline(column):
    report = suggest_terms([column])
    candidate = report["unlinked"][0]["candidates"][0]
    assert candidate["iri"] == IRI
    assert candidate["unit_check"] == "compatible"
    assert len(report["index"]["ref"]) == 40
    assert report["index"]["mapping_version"] == "0.4.0"


@pytest.mark.parametrize("column", ["Thickness / s", "Thickness / kg", "Unpublished metric / mm", "Thicknes / mm"])
def test_no_fuzzy_or_unit_only_guesses(column):
    finding = suggest_terms([column])["unlinked"][0]
    assert finding["candidates"] == []
    assert finding["message"] == "No matching term found in this index."


@pytest.mark.parametrize("column", ["Thickness", "Thickness / instrument_units", "Thickness / m(", "Thickness / m^"])
def test_unchecked_units_are_explicit(column):
    finding = suggest_terms([column])["unlinked"][0]
    assert finding["candidates"][0]["unit_check"] == "unchecked"


def test_standard_and_linked_columns_skip_index_loading(monkeypatch):
    import bdf.ontology_terms as module

    meta = metadata()
    measurement(meta).same_as = AnyUrl("https://example.org/external-term")
    monkeypatch.setattr(module, "_index", lambda: pytest.fail("No unlinked column should load the index"))
    assert suggest_terms(frame().columns, meta)["unlinked"] == []
    assert suggest_terms(["current_ampere", "Voltage / mV", "Test Time / s"])["unlinked"] == []


def test_ambiguous_candidates_remain_choices(monkeypatch):
    import bdf.ontology_terms as module

    index = json.loads(json.dumps(_index()))
    term = next(t for t in index["terms"] if t["iri"] == IRI)
    index["terms"].append({**term, "iri": "https://example.org/other-thickness"})
    monkeypatch.setattr(module, "_index", lambda: index)
    meta = metadata()
    assert len(suggest_terms(frame().columns, meta)["unlinked"][0]["candidates"]) == 2
    assert measurement(meta).same_as is None


def test_explicit_mapping_copies_metadata_and_preserves_description():
    original = metadata()
    updated = apply_term_mappings(frame().columns, original, [mapping()])
    assert measurement(original).same_as is None
    variable = measurement(updated)
    assert str(variable.same_as) == IRI
    assert variable.description == "Height gauge at 100% SOC"
    assert updated.extras == original.extras


def test_profile_accepts_equivalent_unit_spelling_without_relabeling():
    result = apply_term_mappings([COLUMN], Metadata(), [mapping(unit="millimeter")])
    variable = measurement(result)
    assert variable.unit_text == "mm"
    assert str(variable.same_as) == IRI


def test_bare_column_accepts_units_explicitly_supplied_in_profile():
    result = apply_term_mappings(["Thickness"], Metadata(), [mapping(column="Thickness")])
    variable = measurement(result)
    assert variable.unit_text == "mm"
    assert str(variable.same_as) == IRI


@pytest.mark.parametrize(
    "selections",
    [
        [mapping(), mapping()],
        [mapping(column="Absent / mm")],
        [mapping(unit="cm")],
        [mapping(iri="https://example.org/not-in-index")],
        [mapping(), mapping(column="Absent / mm")],
        [{"column": COLUMN, "unit_text": None, "same_as": IRI}],
    ],
)
def test_invalid_selection_never_partially_mutates_metadata(selections):
    original = metadata()
    before = original.model_dump_json()
    with pytest.raises(BDFMetadataError):
        apply_term_mappings(frame().columns, original, selections)
    assert original.model_dump_json() == before


def test_mapping_cannot_override_existing_link_or_unit_dimensions():
    meta = metadata()
    measurement(meta).same_as = AnyUrl("https://example.org/chosen")
    with pytest.raises(BDFMetadataError, match="different ontology link"):
        apply_term_mappings(frame().columns, meta, [mapping()])
    with pytest.raises(BDFMetadataError, match="incompatible"):
        apply_term_mappings(["Custom / s"], Metadata(), [mapping(column="Custom / s", unit="s")])


def test_cli_review_apply_reuse_and_default_read(tmp_path):
    source = write_source(tmp_path)
    sidecar = BdfSidecarParser().sidecar_path(source)
    original_data, original_sidecar = source.read_bytes(), sidecar.read_bytes()
    result = runner.invoke(app, ["terms", str(source), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["unlinked"][0]["candidates"][0]["iri"] == IRI
    assert source.read_bytes() == original_data
    assert sidecar.read_bytes() == original_sidecar

    profile = tmp_path / "setup-mappings.json"
    result = runner.invoke(app, ["terms", str(source), "--accept", f"{COLUMN}={IRI}", "--save-mappings", str(profile)])
    assert result.exit_code == 0, result.output
    assert source.read_bytes() == original_data
    restored, meta = io.read(source)
    assert restored[COLUMN].to_list() == ["5.1", None, "0.0"]
    assert str(measurement(meta).same_as) == IRI
    assert measurement(meta).description == "Height gauge at 100% SOC"
    assert meta.extras == metadata().extras
    assert validate(source)["ontology_terms"]["unlinked"] == []

    second = write_source(tmp_path, described=False, name="second")
    second_before = second.read_bytes()
    result = runner.invoke(app, ["terms", str(second), "--mappings", str(profile)])
    assert result.exit_code == 0, result.output
    assert second.read_bytes() == second_before
    _, second_meta = io.read(second)
    assert str(measurement(second_meta).same_as) == IRI
    # Applying a saved profile again is idempotent.
    result = runner.invoke(app, ["terms", str(second), "--mappings", str(profile)])
    assert result.exit_code == 0, result.output


def test_undeclared_column_gets_suggestions_during_validation(tmp_path):
    source = write_source(tmp_path, described=False)
    result = runner.invoke(app, ["validate", str(source), "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["extras"] == [COLUMN]
    assert report["ontology_terms"]["unlinked"][0]["candidates"][0]["iri"] == IRI
    assert not BdfSidecarParser().sidecar_path(source).exists()
    human = runner.invoke(app, ["validate", str(source)])
    assert human.exit_code == 0
    assert human.stdout.count("Unlinked measurement columns") == 1


@pytest.mark.parametrize("described", [False, True])
def test_conversion_advises_and_preserves_described_metadata(tmp_path, described):
    source = write_source(tmp_path, described=described)
    output = tmp_path / "converted.bdf.parquet"
    result = runner.invoke(app, ["convert", str(source), "--to", str(output)])
    assert result.exit_code == 0, result.output
    assert "Suggested: Thickness" in result.stderr
    assert "Unlinked measurement" not in result.stdout
    table, meta = io.read(output)
    assert (COLUMN in table.columns) is described
    assert ("Excluded from output" in result.stderr) is not described
    if described:
        assert measurement(meta).description == "Height gauge at 100% SOC"
        assert measurement(meta).same_as is None


def test_conversion_stdout_is_only_data(tmp_path):
    source = write_source(tmp_path)
    result = runner.invoke(app, ["convert", str(source), "--to", "-"])
    assert result.exit_code == 0, result.output
    assert "Suggested: Thickness" in result.stderr
    assert "Suggested" not in result.stdout
    assert COLUMN in result.stdout.splitlines()[0]


def test_no_validate_conversion_still_allows_invalid_custom_binding(tmp_path):
    source = write_source(tmp_path)
    bad = metadata()
    measurement(bad).name = "Missing"
    BdfSidecarParser().sidecar_path(source).write_text(bad.model_dump_json(exclude_defaults=True))
    output = tmp_path / "unchecked.bdf.csv"
    result = runner.invoke(app, ["convert", str(source), "--to", str(output), "--no-validate", "--include-unknown"])
    assert result.exit_code == 0, result.output
    assert "Ontology suggestions skipped" in result.stderr
    assert COLUMN in pl.read_csv(output).columns


@pytest.mark.parametrize(
    "profile",
    [
        [],
        {"format_version": 2, "mappings": []},
        {"format_version": 1, "mappings": [None]},
        {"format_version": 1, "mappings": [mapping(), mapping(column="Missing / mm")]},
    ],
)
def test_invalid_profile_leaves_files_unchanged(tmp_path, profile):
    source = write_source(tmp_path)
    sidecar = BdfSidecarParser().sidecar_path(source)
    before = source.read_bytes(), sidecar.read_bytes()
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(profile))
    result = runner.invoke(app, ["terms", str(source), "--mappings", str(path)])
    assert result.exit_code == 2, result.output
    assert (source.read_bytes(), sidecar.read_bytes()) == before


def test_profile_cannot_overwrite_input_or_sidecar(tmp_path):
    source = write_source(tmp_path)
    sidecar = BdfSidecarParser().sidecar_path(source)
    before = source.read_bytes(), sidecar.read_bytes()
    for target in (source, sidecar):
        result = runner.invoke(
            app, ["terms", str(source), "--accept", f"{COLUMN}={IRI}", "--save-mappings", str(target)]
        )
        assert result.exit_code == 2, result.output
        assert (source.read_bytes(), sidecar.read_bytes()) == before


@pytest.mark.parametrize("failure", ["sidecar", "profile"])
def test_write_failure_preserves_original_and_removes_new_profile(tmp_path, monkeypatch, failure):
    import bdf._terms_cli as module

    source = write_source(tmp_path)
    sidecar = BdfSidecarParser().sidecar_path(source)
    before = sidecar.read_bytes()
    profile = tmp_path / "profile.json"

    def fail(*args):
        raise OSError("Simulated disk error")

    if failure == "sidecar":
        monkeypatch.setattr(module, "_write_sidecar", fail)
    else:
        original_open = Path.open

        @contextmanager
        def failing_open(path, *args, **kwargs):
            with original_open(path, *args, **kwargs) as handle:
                if path == profile:

                    class FailedWrite:
                        def write(self, data):
                            handle.write(data[:8])
                            raise OSError("Simulated partial profile write")

                    yield FailedWrite()
                else:
                    yield handle

        monkeypatch.setattr(Path, "open", failing_open)
    result = runner.invoke(app, ["terms", str(source), "--accept", f"{COLUMN}={IRI}", "--save-mappings", str(profile)])
    assert result.exit_code == 2
    assert sidecar.read_bytes() == before
    assert not profile.exists()
    assert not list(tmp_path.glob(f".{sidecar.name}.*"))
