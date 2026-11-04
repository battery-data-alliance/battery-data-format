from __future__ import annotations

import sys
from collections.abc import Iterator
from importlib.resources import files
from pathlib import Path

import pytest
from packaging.version import Version

from bdf._errors import BDFDefinitionError
from bdf.spec import ONTOLOGY_SOURCE, ColumnOntology, _update_snapshot_cli

from .conftest import FakeGitHub, ontology_ttl


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Use a bundled ontology 1.0.0 and an empty cache, both in tmp_path."""
    monkeypatch.setenv("BDF_CACHE_DIR", str(tmp_path / "cache"))
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "bdf-ontology.ttl").write_text(ontology_ttl("1.0.0"))
    (bundle / "VERSION").write_text("1.0.0\n")
    monkeypatch.setattr(ONTOLOGY_SOURCE, "bundled", bundle)
    ONTOLOGY_SOURCE.forget()
    yield bundle
    ONTOLOGY_SOURCE.forget()


def loaded_version(load: str, *args: object) -> str:
    onto = ColumnOntology({})
    getattr(onto, load)(*args)
    return onto.ontology_version


def test_the_packaged_version_file_matches_the_bundled_ontology() -> None:
    packaged = files("bdf.data") / "bdf-ontology"
    onto = ColumnOntology({})
    onto.load_ttl(Path(str(packaged / "bdf-ontology.ttl")))
    assert (packaged / "VERSION").read_text().strip() == str(Version(onto.ontology_version))


def test_load_version_refuses_an_ontology_that_states_another_version(
    bundle: Path, github: FakeGitHub, tmp_path: Path
) -> None:
    github.files["1.2.0"] = ontology_ttl("1.1.0")
    with pytest.raises(BDFDefinitionError, match="1.2.0") as info:
        loaded_version("load_version", "1.2.0")
    assert "1.1.0" in str(info.value.__cause__)
    assert not (tmp_path / "cache" / "bdf-ontology-v1.2.0").exists()


def test_load_latest_loads_the_release_that_the_latest_uri_states(bundle: Path, github: FakeGitHub) -> None:
    github.latest = "1.3.0"
    github.files["1.3.0"] = ontology_ttl("1.3.0")
    assert loaded_version("load_latest") == "1.3.0"


def test_get_snapshot_writes_a_named_version_to_dest(bundle: Path, github: FakeGitHub, tmp_path: Path) -> None:
    github.files["1.1.0"] = ontology_ttl("1.1.0")
    dest = ColumnOntology.get_snapshot(dest=tmp_path / "out.ttl", version="1.1.0")
    assert 'owl:versionInfo "1.1.0"' in dest.read_text()


def test_update_snapshot_replaces_the_bundle_and_prints_the_version(
    bundle: Path, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    github.files["1.1.0"] = ontology_ttl("1.1.0")
    monkeypatch.setattr(sys, "argv", ["bdf-update-snapshot", "1.1.0"])
    _update_snapshot_cli()
    assert (bundle / "VERSION").read_text() == "1.1.0\n"
    assert "1.1.0" in capsys.readouterr().out


def test_update_snapshot_help_makes_no_request(
    github: FakeGitHub, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["bdf-update-snapshot", "--help"])
    with pytest.raises(SystemExit):
        _update_snapshot_cli()
    assert "bdf-update-snapshot" in capsys.readouterr().out
    assert github.urls == []


@pytest.mark.network
@pytest.mark.live_network
@pytest.mark.freshness
def test_the_latest_release_can_be_fetched(tmp_path: Path) -> None:
    """GitHub's latest release has a tag that fetches an ontology stating that version."""
    ONTOLOGY_SOURCE.forget()
    version = ONTOLOGY_SOURCE.latest()
    ttl = ColumnOntology.get_snapshot(dest=tmp_path / "latest.ttl", version=version)
    onto = ColumnOntology({})
    onto.load_ttl(ttl)
    assert str(Version(onto.ontology_version)) == version
