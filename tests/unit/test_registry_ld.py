"""Tests for datastore-style source support in registry_ld."""
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from bdf.registry_ld import (
    DatasetResult,
    ResolvedSource,
    _SOURCE_ALIASES,
    _extract_datastore_rows,
    _iter_battery_json_files,
    _iter_datastore_jsonld_files,
    _load_user_sources,
    _resolve_source,
    _save_user_sources,
    _sources_config_path,
    add_registry,
    build_registry,
    remove_registry,
    search,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cell(root: Path, contributor: str, cell_name: str, spec: dict, ids: list[str],
               timeseries_files: list[str] | None = None, eis_files: list[str] | None = None):
    """Create a datastore cell directory with battery.json and optional data files."""
    cell_dir = root / contributor / cell_name
    cell_dir.mkdir(parents=True, exist_ok=True)
    battery = {"spec": spec, "ids": ids}
    (cell_dir / "battery.json").write_text(json.dumps(battery), encoding="utf-8")

    for subdir, files in [("timeseries/processed", timeseries_files), ("eis/processed", eis_files)]:
        if files:
            d = cell_dir / subdir
            d.mkdir(parents=True, exist_ok=True)
            for fname in files:
                (d / fname).write_text("time_s,voltage_v,current_a\n0,3.7,1.0\n", encoding="utf-8")

    return cell_dir


def _make_test_jsonld(cell_dir: Path, data_type: str, name: str, content: dict | None = None):
    """Create a JSON-LD test metadata file in a data-type directory."""
    dt_dir = cell_dir / data_type
    dt_dir.mkdir(parents=True, exist_ok=True)
    if content is None:
        content = {
            "@context": {"@vocab": "https://schema.org/"},
            "@type": "BatteryTest",
            "name": name,
            "description": f"Test: {name}",
            "keywords": ["cycling", "li-ion"],
        }
    (dt_dir / f"test_{name}.json").write_text(json.dumps(content), encoding="utf-8")


GOOGLE_SPEC = {
    "manufacturer": "google",
    "model": "g20m7",
    "batch": "202512",
    "chemistry": "li-ion",
    "form_factor": "pouch",
    "nominal_voltage_v": 3.9,
    "rated_capacity_ah": 4.835,
    "rated_energy_wh": 18.86,
    "mass_g": 55.0,
    "volume_l": 0.024,
    "pe_materials": ["nmc"],
    "ne_materials": ["graphite", "silicon"],
}


# ---------------------------------------------------------------------------
# Tests: _iter_battery_json_files
# ---------------------------------------------------------------------------

class TestIterBatteryJsonFiles:
    def test_finds_at_depth_2(self, tmp_path: Path):
        _make_cell(tmp_path, "SINTEF", "google-g20m7-001", GOOGLE_SPEC, ["001"])
        _make_cell(tmp_path, "Microsoft", "mfg1-cell-002", {"manufacturer": "mfg1"}, ["002"])

        found = sorted(_iter_battery_json_files(tmp_path))
        assert len(found) == 2
        assert all(p.name == "battery.json" for p in found)

    def test_ignores_wrong_depth(self, tmp_path: Path):
        # depth 1: root/battery.json
        (tmp_path / "battery.json").write_text("{}", encoding="utf-8")

        # depth 3: root/a/b/c/battery.json
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "battery.json").write_text("{}", encoding="utf-8")

        found = list(_iter_battery_json_files(tmp_path))
        assert len(found) == 0


# ---------------------------------------------------------------------------
# Tests: _iter_datastore_jsonld_files
# ---------------------------------------------------------------------------

class TestIterDatastoreJsonldFiles:
    def test_finds_jsonld_test_files(self, tmp_path: Path):
        cell = _make_cell(tmp_path, "SINTEF", "cell-001", GOOGLE_SPEC, ["001"])
        _make_test_jsonld(cell, "timeseries", "c30_25degC")
        _make_test_jsonld(cell, "eis", "eis_25degC")

        found = list(_iter_datastore_jsonld_files(tmp_path))
        assert len(found) == 2

    def test_ignores_non_jsonld_json(self, tmp_path: Path):
        cell = _make_cell(tmp_path, "SINTEF", "cell-001", GOOGLE_SPEC, ["001"])
        # A plain JSON file without @type should be ignored
        ts_dir = cell / "timeseries"
        ts_dir.mkdir(parents=True, exist_ok=True)
        (ts_dir / "notes.json").write_text('{"notes": "some notes"}', encoding="utf-8")

        found = list(_iter_datastore_jsonld_files(tmp_path))
        assert len(found) == 0


# ---------------------------------------------------------------------------
# Tests: _extract_datastore_rows
# ---------------------------------------------------------------------------

class TestExtractDatastoreRows:
    def test_basic_extraction(self, tmp_path: Path):
        _make_cell(
            tmp_path, "SINTEF", "google-g20m7-001", GOOGLE_SPEC, ["001"],
            timeseries_files=["sintef__google-g20m7-001__20250101__c30__25degC.bdf.csv"],
        )
        battery_jsons = list(_iter_battery_json_files(tmp_path))
        rows = _extract_datastore_rows(battery_jsons, None, tmp_path)

        assert len(rows) == 1
        row = rows[0]
        assert row["dataset_id"] == "SINTEF/google-g20m7-001"
        assert row["manufacturer"] == "google"
        assert row["model"] == "g20m7"
        assert row["chemistry"] == "li-ion"
        assert row["form_factor"] == "pouch"
        assert row["nominal_voltage_v"] == 3.9
        assert row["rated_capacity_ah"] == 4.835
        assert json.loads(row["pe_materials"]) == ["nmc"]
        assert json.loads(row["ne_materials"]) == ["graphite", "silicon"]
        assert json.loads(row["battery_ids"]) == ["001"]
        # Single file → plain string URL
        assert row["url"].endswith(".bdf.csv")
        assert not row["url"].startswith("[")

    def test_multiple_files_json_array_url(self, tmp_path: Path):
        _make_cell(
            tmp_path, "SINTEF", "cell-001", GOOGLE_SPEC, ["001"],
            timeseries_files=["file1.bdf.csv", "file2.bdf.csv"],
        )
        battery_jsons = list(_iter_battery_json_files(tmp_path))
        rows = _extract_datastore_rows(battery_jsons, None, tmp_path)

        urls = json.loads(rows[0]["url"])
        assert len(urls) == 2

    def test_github_url_construction(self, tmp_path: Path):
        _make_cell(
            tmp_path, "SINTEF", "cell-001", GOOGLE_SPEC, ["001"],
            timeseries_files=["test.bdf.csv"],
        )
        battery_jsons = list(_iter_battery_json_files(tmp_path))
        github_info = ("battery-data-alliance", "bdf-datastore", "main", "")
        rows = _extract_datastore_rows(battery_jsons, github_info, tmp_path)

        url = rows[0]["url"]
        assert url.startswith("https://media.githubusercontent.com/media/battery-data-alliance/bdf-datastore/main/")
        assert url.endswith("test.bdf.csv")

    def test_no_data_files(self, tmp_path: Path):
        _make_cell(tmp_path, "SINTEF", "cell-001", GOOGLE_SPEC, ["001"])
        battery_jsons = list(_iter_battery_json_files(tmp_path))
        rows = _extract_datastore_rows(battery_jsons, None, tmp_path)

        assert len(rows) == 1
        assert rows[0]["url"] is None


# ---------------------------------------------------------------------------
# Tests: source alias resolution
# ---------------------------------------------------------------------------

class TestSourceAliases:
    def test_bdf_datastore_alias_exists(self):
        assert "bdf-datastore" in _SOURCE_ALIASES
        assert "bdf-datastore" in _SOURCE_ALIASES["bdf-datastore"]

    def test_resolve_alias_calls_github(self, tmp_path: Path):
        mock_resolved = ResolvedSource(local_path=tmp_path)
        with patch("bdf.registry_ld._download_github_repo", return_value=mock_resolved) as mock_dl:
            result = _resolve_source("bdf-datastore", tmp_path, refresh=False)
            mock_dl.assert_called_once()
            assert result.local_path == tmp_path


# ---------------------------------------------------------------------------
# Tests: build_registry with datastore source
# ---------------------------------------------------------------------------

class TestBuildRegistryDatastore:
    def test_datastore_source(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        _make_cell(
            src, "SINTEF", "google-g20m7-001", GOOGLE_SPEC, ["001"],
            timeseries_files=["test.bdf.csv"],
        )

        reg_dir = tmp_path / "registry"
        result = build_registry(str(src), registry_dir=str(reg_dir))

        assert result["datasets"] == 1
        assert result["battery_json_files"] == 1
        assert (reg_dir / "registry.db").exists()

        # Verify the entry is searchable and returns DatasetResult
        hits = search("google", registry_dir=str(reg_dir))
        assert len(hits) >= 1
        hit = hits[0]
        assert isinstance(hit, DatasetResult)
        # Attribute access
        assert hit.manufacturer == "google"
        # Dict access still works
        assert hit["manufacturer"] == "google"

    def test_search_by_chemistry(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        _make_cell(src, "Lab1", "cell-a", {**GOOGLE_SPEC, "chemistry": "li-ion"}, ["a"])
        _make_cell(src, "Lab2", "cell-b", {**GOOGLE_SPEC, "chemistry": "na-ion", "manufacturer": "hina"}, ["b"])

        reg_dir = tmp_path / "registry"
        build_registry(str(src), registry_dir=str(reg_dir))

        hits = search("hina", registry_dir=str(reg_dir))
        assert len(hits) == 1
        assert hits[0]["dataset_id"] == "Lab2/cell-b"

    def test_search_numeric_filter(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        _make_cell(src, "Lab1", "small", {**GOOGLE_SPEC, "rated_capacity_ah": 1.0}, ["s"])
        _make_cell(src, "Lab1", "large", {**GOOGLE_SPEC, "rated_capacity_ah": 10.0}, ["l"])

        reg_dir = tmp_path / "registry"
        build_registry(str(src), registry_dir=str(reg_dir))

        hits = search(">=5ah", registry_dir=str(reg_dir))
        assert len(hits) == 1
        assert hits[0]["dataset_id"] == "Lab1/large"

    def test_mixed_sources(self, tmp_path: Path):
        """Datastore + metadata.json sources in a single build."""
        src = tmp_path / "src"
        src.mkdir()
        _make_cell(src, "Lab1", "cell-a", GOOGLE_SPEC, ["a"],
                   timeseries_files=["test.bdf.csv"])

        # Also create a metadata.jsonld source in a separate dir
        ld_src = tmp_path / "ld_src"
        ld_src.mkdir()
        # Minimal metadata that _iter_metadata_files will find
        (ld_src / "metadata.json").write_text(json.dumps({
            "@context": {"@vocab": "https://schema.org/"},
            "@type": "Dataset",
            "name": "LD Dataset",
            "identifier": "ld-001",
        }), encoding="utf-8")

        reg_dir = tmp_path / "registry"
        result = build_registry([str(src), str(ld_src)], registry_dir=str(reg_dir))

        # Should have at least the datastore entry (LD entry depends on rdflib parsing)
        assert result["datasets"] >= 1
        assert result["battery_json_files"] == 1

    def test_dataset_result_fields(self, tmp_path: Path):
        """DatasetResult parses JSON fields and provides .urls."""
        src = tmp_path / "src"
        src.mkdir()
        _make_cell(
            src, "SINTEF", "cell-001", GOOGLE_SPEC, ["001", "002"],
            timeseries_files=["file1.bdf.csv", "file2.bdf.csv"],
        )

        reg_dir = tmp_path / "registry"
        build_registry(str(src), registry_dir=str(reg_dir))

        hits = search("google", registry_dir=str(reg_dir))
        hit = hits[0]

        # JSON fields are parsed into real lists
        assert hit.pe_materials == ["nmc"]
        assert hit.ne_materials == ["graphite", "silicon"]
        assert hit.battery_ids == ["001", "002"]

        # urls is a parsed list
        assert len(hit.urls) == 2
        assert all(u.endswith(".bdf.csv") for u in hit.urls)

        # repr is readable
        r = repr(hit)
        assert "DatasetResult(" in r
        assert "google" in r
        assert "files=2" in r

        # to_dict returns plain dict with parsed url
        d = hit.to_dict()
        assert isinstance(d["url"], list)
        assert isinstance(d["pe_materials"], list)

    def test_empty_ttl_when_no_metadata(self, tmp_path: Path):
        """When only datastore sources, registry.ttl is written (empty) and doesn't crash."""
        src = tmp_path / "src"
        src.mkdir()
        _make_cell(src, "Lab1", "cell-a", GOOGLE_SPEC, ["a"])

        reg_dir = tmp_path / "registry"
        build_registry(str(src), registry_dir=str(reg_dir))

        ttl_path = reg_dir / "registry.ttl"
        assert ttl_path.exists()
        assert ttl_path.read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# Tests: add_registry / remove_registry
# ---------------------------------------------------------------------------

class TestUserSources:
    def test_add_and_remove(self, tmp_path: Path, monkeypatch):
        config = tmp_path / "sources.json"
        monkeypatch.setattr("bdf.registry_ld._sources_config_path", lambda: config)

        add_registry("my-lab", "https://github.com/my-org/our-cells")
        sources = _load_user_sources()
        assert sources["my-lab"] == "https://github.com/my-org/our-cells"

        remove_registry("my-lab")
        sources = _load_user_sources()
        assert "my-lab" not in sources

    def test_cannot_remove_builtin(self, tmp_path: Path, monkeypatch):
        config = tmp_path / "sources.json"
        monkeypatch.setattr("bdf.registry_ld._sources_config_path", lambda: config)

        with pytest.raises(ValueError, match="built-in"):
            remove_registry("bdf-datastore")

    def test_remove_nonexistent(self, tmp_path: Path, monkeypatch):
        config = tmp_path / "sources.json"
        monkeypatch.setattr("bdf.registry_ld._sources_config_path", lambda: config)

        with pytest.raises(KeyError, match="no-such"):
            remove_registry("no-such")
