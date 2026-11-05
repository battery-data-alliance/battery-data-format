from __future__ import annotations

import json
import warnings
from pathlib import Path

import polars as pl
import pytest

from bdf import io
from bdf._errors import BDFDefinitionError, BDFDefinitionWarning
from bdf.metadata import BdfReadInfo, Metadata
from bdf.metadata_parsers import BdfSidecarParser
from bdf.spec import COLUMN_ONTOLOGY, ONTOLOGY_SOURCE, ColumnOntology
from bdf.table_normalizers import BDF_NORMALIZER, Syn, TableNormalizer
from bdf.table_parsers import DelimTxtParser

BUNDLED = COLUMN_ONTOLOGY.ontology_version
TEMPERATURE = COLUMN_ONTOLOGY.ambient_temperature_celsius.formatted_label

# A release like the bundle, without ambient temperature.
OLDER = ColumnOntology(
    {name: q.model_copy(deep=True) for name, q in COLUMN_ONTOLOGY if name != "ambient_temperature_celsius"},
    "1.2.9",
)

# A release like the bundle in which Step Index is current, as in 1.1.0. The bundled release
# deprecates it, so TableNormalizer has no field for it.
STEP_INDEX = ColumnOntology(
    {
        **{name: q.model_copy(deep=True) for name, q in COLUMN_ONTOLOGY if name != "step_record_index"},
        "step_index": COLUMN_ONTOLOGY.step_index.model_copy(update={"deprecated": False, "replaced_by": ""}),
    },
    "1.2.9",
)
STEP_INDEX_LABEL = "Step Index / 1"


class FakeReleases:
    """Stands in for the ontology source's open hook: opens the cached release 1.2.9 as ``release``, or fails when down."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.down = False
        self.release = OLDER

    def __call__(self, path: Path) -> ColumnOntology:
        version = (path / "VERSION").read_text().strip()
        self.asked.append(version)
        if self.down:
            raise OSError(f"bdf-ontology {version} cannot be read")
        return self.release


@pytest.fixture
def releases(monkeypatch: pytest.MonkeyPatch, ontology_cache: Path) -> FakeReleases:
    """Put release 1.2.9 in an empty cache, opened by a FakeReleases; the bundled release opens as before."""
    (ontology_cache / "bdf-ontology-v1.2.9").mkdir(parents=True)
    (ontology_cache / "bdf-ontology-v1.2.9" / "VERSION").write_text("1.2.9\n")
    fake = FakeReleases()
    monkeypatch.setattr(ONTOLOGY_SOURCE, "open", fake)
    return fake


def saved(tmp_path: Path, ontology: ColumnOntology | None = None) -> Path:
    """Save a small BDF table with ambient temperature and a sidecar; return its path."""
    path = tmp_path / "cell.bdf.parquet"
    df = pl.DataFrame({"Test Time / s": [0.0, 1.0], "Voltage / V": [3.7, 3.8], "Current / A": [0.1, 0.1]})
    df = df.with_columns(pl.lit(25.0).alias(TEMPERATURE))
    io.save(df, path, metadata=Metadata(bdf=BdfReadInfo(source="test")), ontology=ontology)
    return path


def stated(path: Path) -> str:
    return json.loads(BdfSidecarParser().sidecar_path(path).read_text())["bdf"]["ontology_version"]


def test_a_read_uses_the_release_the_sidecar_states(tmp_path: Path, releases: FakeReleases) -> None:
    path = saved(tmp_path, OLDER)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # the file's own release matches its own labels
        df, metadata = io.read(path)
    assert releases.asked == ["1.2.9"]
    assert metadata.bdf.ontology_version == "1.2.9"
    assert TEMPERATURE not in df.columns  # 1.2.9 does not define it


def test_an_explicit_ontology_wins_over_the_sidecar(tmp_path: Path, releases: FakeReleases) -> None:
    path = saved(tmp_path, OLDER)
    df, metadata = io.read(path, ontology=COLUMN_ONTOLOGY)
    assert releases.asked == []
    assert metadata.bdf.ontology_version == BUNDLED
    assert TEMPERATURE in df.columns


def test_the_bundled_stated_release_loads_nothing(tmp_path: Path, releases: FakeReleases) -> None:
    path = saved(tmp_path)
    assert stated(path) == BUNDLED
    io.read(path)
    assert releases.asked == []


def test_a_stated_ontology_that_cannot_load_raises_or_reads_with_the_bundled_one(
    tmp_path: Path, releases: FakeReleases
) -> None:
    path = saved(tmp_path, OLDER)
    releases.down = True
    # With strict on, a read needs the stated ontology.
    with pytest.raises(BDFDefinitionError, match="strict=False"):
        io.read(path)
    # With strict off, the bundled ontology gives the column labels, the columns are not checked,
    # and the metadata keeps the stated ontology.
    with pytest.warns(BDFDefinitionWarning, match="1.2.9.*bundled ontology.*without a column check"):
        df, metadata = io.read(path, strict=False)
    assert metadata.bdf.ontology_version == "1.2.9"
    assert TEMPERATURE in df.columns
    # validate=False also needs an ontology for the labels, so it reads with the bundled one too.
    with pytest.warns(BDFDefinitionWarning, match="1.2.9.*bundled ontology"):
        io.read(path, validate=False)


def test_a_raw_read_loads_no_ontology(tmp_path: Path, releases: FakeReleases) -> None:
    path = saved(tmp_path, OLDER)
    releases.down = True
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # nothing to load, so nothing that cannot be loaded
        _, metadata = io.read(path, normalize=False)
    assert releases.asked == []
    assert metadata.bdf.ontology_version == "1.2.9"


def test_a_save_needs_the_stated_release_unless_strict_is_off(tmp_path: Path, releases: FakeReleases) -> None:
    """A save never writes a file it could not check against the ontology the file states."""
    # ontology= reads the file without loading 1.2.9, and the metadata still states 1.2.9.
    df, metadata = io.read(saved(tmp_path, OLDER), ontology=OLDER)
    releases.down = True
    target = tmp_path / "copy.bdf.parquet"
    with pytest.raises(BDFDefinitionError, match="strict=False"):
        io.save(df, target, metadata=metadata)
    assert not target.exists()
    with pytest.warns(BDFDefinitionWarning, match="1.2.9"):
        io.save(df, target, metadata=metadata, strict=False)
    assert stated(target) == "1.2.9"


@pytest.mark.usefixtures("releases")
def test_a_round_trip_keeps_the_release_unless_told_otherwise(tmp_path: Path) -> None:
    df, metadata = io.read(saved(tmp_path, OLDER))
    io.save(df, tmp_path / "kept.bdf.parquet", metadata=metadata)
    io.save(df, tmp_path / "moved.bdf.parquet", metadata=metadata, ontology=COLUMN_ONTOLOGY)
    assert stated(tmp_path / "kept.bdf.parquet") == "1.2.9"
    assert stated(tmp_path / "moved.bdf.parquet") == BUNDLED


def test_a_quantity_the_release_lacks_is_an_unknown_column() -> None:
    normalizer = TableNormalizer(
        test_time_second=(Syn(hdr="t", source_unit="s"),),
        voltage_volt=(Syn(hdr="U", source_unit="V"),),
        current_ampere=(Syn(hdr="I", source_unit="A"),),
        ambient_temperature_celsius=(Syn(hdr="Temp", source_unit="degC"),),
    )
    df = pl.DataFrame({"t": [0.0], "U": [3.7], "I": [0.1], "Temp": [25.0]})
    with pytest.warns(UserWarning, match="'Temp' maps to ambient_temperature_celsius.*1.2.9"):
        assert "Temp" not in normalizer.normalize(df, ontology=OLDER).columns
    with pytest.warns(UserWarning, match="'Temp'"):
        assert "Temp" in normalizer.normalize(df, ontology=OLDER, include_unknown=True).columns


def test_a_header_that_a_defined_quantity_takes_is_not_called_outside_the_spec() -> None:
    # "X" matches ambient temperature, which OLDER lacks, and step time, declared after it.
    normalizer = TableNormalizer(
        test_time_second=(Syn(hdr="t", source_unit="s"),),
        voltage_volt=(Syn(hdr="U", source_unit="V"),),
        current_ampere=(Syn(hdr="I", source_unit="A"),),
        ambient_temperature_celsius=(Syn(hdr="X", source_unit="degC"),),
        step_time_second=(Syn(hdr="X", source_unit="s"),),
    )
    df = pl.DataFrame({"t": [0.0], "U": [3.7], "I": [0.1], "X": [1.0]})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = normalizer.normalize(df, ontology=OLDER)
    assert OLDER.step_time_second.formatted_label in out.columns


def test_a_read_keeps_a_column_that_the_stated_release_defines_and_the_bundled_one_deprecates(
    tmp_path: Path, releases: FakeReleases
) -> None:
    releases.release = STEP_INDEX
    path = tmp_path / "cell.bdf.parquet"
    df = pl.DataFrame(
        {"Test Time / s": [0.0, 1.0], "Voltage / V": [3.7, 3.8], "Current / A": [0.1, 0.1], STEP_INDEX_LABEL: [1, 2]}
    )
    io.save(df, path, metadata=Metadata(bdf=BdfReadInfo(source="test")), ontology=STEP_INDEX)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # the file's own release maps the column, so it is not a legacy one
        read_df, metadata = io.read(path)
    assert STEP_INDEX_LABEL in read_df.columns
    # A save of what was read keeps it too.
    io.save(read_df, tmp_path / "again.bdf.parquet", metadata=metadata)
    assert STEP_INDEX_LABEL in io.read(tmp_path / "again.bdf.parquet")[0].columns


def test_an_extended_bdf_normalizer_maps_another_release_after_a_json_round_trip() -> None:
    parser = DelimTxtParser(normalizer=BDF_NORMALIZER.extend(voltage_volt=Syn(hdr="U", source_unit="V")))
    loaded = DelimTxtParser.model_validate_json(parser.model_dump_json())
    assert loaded == parser
    df = pl.DataFrame({"Test Time / s": [0.0], "U": [3.7], "Current / A": [0.1], STEP_INDEX_LABEL: [1]})
    out = loaded.normalizer.normalize(df, ontology=STEP_INDEX)
    # The release's own label, and the synonym the extension added.
    assert {"Voltage / V", STEP_INDEX_LABEL} <= set(out.columns)


def test_the_labels_of_a_release_are_built_once() -> None:
    assert BDF_NORMALIZER.for_ontology(OLDER) is BDF_NORMALIZER.for_ontology(OLDER)
    assert BDF_NORMALIZER.for_ontology(OLDER) is not BDF_NORMALIZER.for_ontology(COLUMN_ONTOLOGY)
