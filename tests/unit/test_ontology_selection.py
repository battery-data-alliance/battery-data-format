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
from bdf.spec import COLUMN_ONTOLOGY, ColumnOntology
from bdf.table_normalizers import Syn, TableNormalizer

BUNDLED = COLUMN_ONTOLOGY.ontology_version
TEMPERATURE = COLUMN_ONTOLOGY.ambient_temperature_celsius.formatted_label

# A release like the bundle, without ambient temperature.
OLDER = ColumnOntology(
    {name: q.model_copy(deep=True) for name, q in COLUMN_ONTOLOGY if name != "ambient_temperature_celsius"},
    "1.2.9",
)


class FakeReleases:
    """Stands in for ColumnOntology.from_version: serves OLDER, or fails when down."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.down = False

    def __call__(self, version: str, **_: object) -> ColumnOntology:
        self.asked.append(version)
        if self.down:
            raise BDFDefinitionError(f"bdf-ontology {version} could not be fetched")
        return OLDER


@pytest.fixture
def releases(monkeypatch: pytest.MonkeyPatch) -> FakeReleases:
    fake = FakeReleases()
    monkeypatch.setattr(ColumnOntology, "from_version", fake)
    monkeypatch.setattr(io, "_UNLOADABLE_RELEASES", set())
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


def test_a_stated_release_that_cannot_load_falls_back_once(tmp_path: Path, releases: FakeReleases) -> None:
    path = saved(tmp_path, OLDER)
    releases.down = True
    for _ in range(2):
        with pytest.warns(BDFDefinitionWarning, match="1.2.9.*bundled release"):
            df, metadata = io.read(path)
    assert releases.asked == ["1.2.9"]
    assert metadata.bdf.ontology_version == BUNDLED
    assert TEMPERATURE in df.columns


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
