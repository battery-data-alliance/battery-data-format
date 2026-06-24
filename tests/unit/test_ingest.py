from __future__ import annotations

from pathlib import Path

import bdf


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_ingest_converts_raw_and_validates_bdf(tmp_path: Path) -> None:
    raw_csv = tmp_path / "raw.csv"
    _write_text(
        raw_csv,
        "Voltage(V),Current(A),Time(s),Total Time(s)\n4.0,0.1,1,1\n4.1,0.1,2,2\n",
    )

    bdf_csv = tmp_path / "sample.bdf.csv"
    _write_text(
        bdf_csv,
        "Test Time / s,Voltage / V,Current / A\n1,4.0,0.1\n2,4.1,0.1\n",
    )

    out_dir = tmp_path / "out"
    summary = bdf.ingest(
        tmp_path,
        out_dir=out_dir,
        format="csv",
        recursive=False,
        validate_existing=True,
        validate_converted=True,
        data_dir=None,
        raw_dir=None,
    )

    converted = [c["path"] for c in summary.get("converted", [])]
    validated = [v["path"] for v in summary.get("validated", [])]

    assert str(raw_csv) in converted
    assert str(out_dir / "sample.bdf.csv") in validated
    assert (out_dir / "raw.bdf.csv").exists()


def test_ingest_existing_bdf_does_not_delete_source(tmp_path: Path) -> None:
    root = tmp_path / "collection"
    root.mkdir()

    # minimal metadata inputs
    (root / "contribution.json").write_text(
        '{"title": "Test Contribution", "description": "Test", "keywords": ["test"]}',
        encoding="utf-8",
    )
    (root / "person.json").write_text(
        '{"p1": {"name": "Test Person"}}',
        encoding="utf-8",
    )
    (root / "battery.json").write_text(
        '{"spec": {"manufacturer": "Test", "model": "X", "batch": "1"}, "ids": ["cell1"]}',
        encoding="utf-8",
    )

    # source BDF file in root
    src_bdf = root / "cell1.bdf.csv"
    _write_text(
        src_bdf,
        "Test Time / s,Voltage / V,Current / A\n1,4.0,0.1\n2,4.1,0.1\n",
    )

    # existing output in data/ to force conflict
    data_dir = root / "data"
    data_dir.mkdir()
    out_bdf = data_dir / "cell1.bdf.csv"
    _write_text(
        out_bdf,
        "Test Time / s,Voltage / V,Current / A\n1,4.0,0.1\n",
    )

    summary = bdf.ingest(
        root,
        layout="nested",
        format="csv",
        recursive=False,
        validate_existing=True,
        validate_converted=True,
        data_dir=None,
        raw_dir=None,
        cell_metadata_dir=None,
    )

    assert src_bdf.exists(), "Source BDF should not be deleted when output exists."
    assert out_bdf.exists(), "Existing output BDF should be preserved."
    assert (root / "metadata.jsonld").exists(), "Collection metadata should be generated."
    assert (root / "test-x-1-cell1" / "metadata.jsonld").exists(), "Cell metadata should be generated."
    assert any(item.get("reason") == "output_exists" for item in summary.get("skipped", []))


def test_ingest_accepts_minimal_contribution_metadata(tmp_path: Path) -> None:
    root = tmp_path / "contribution"
    raw_dir = root / "timeseries" / "raw"
    raw_dir.mkdir(parents=True)
    _write_text(
        raw_dir / "sample.csv",
        "Voltage(V),Current(A),Total Time(s)\n4.0,0.1,1\n4.1,0.1,2\n",
    )
    (root / "contribution.json").write_text(
        '{"dataset_doi": "10.1234/example.dataset", "license": "CC-BY-4.0"}',
        encoding="utf-8",
    )

    summary = bdf.ingest(root, format="csv", recursive=False, doi_enrich=False)

    assert (root / "timeseries" / "sample.bdf.csv").exists()
    assert (root / "metadata.jsonld").exists()
    assert not summary.get("metadata_failed")


def test_ingest_per_file_metadata_does_not_require_battery(tmp_path: Path) -> None:
    root = tmp_path / "flat"
    root.mkdir()
    _write_text(
        root / "raw.csv",
        "Voltage(V),Current(A),Total Time(s)\n4.0,0.1,1\n4.1,0.1,2\n",
    )
    (root / "dataset.json").write_text(
        '{"dataset_doi": "10.1234/per-file.dataset", "license": "CC-BY-4.0"}',
        encoding="utf-8",
    )

    summary = bdf.ingest(
        root,
        format="csv",
        recursive=False,
        data_dir=None,
        raw_dir=None,
        doi_enrich=False,
    )

    assert (root / "raw.bdf.csv").exists()
    assert (root / "raw.jsonld").exists()
    assert not summary.get("metadata_failed")


def test_ingest_nested_supports_v1_battery_cells(tmp_path: Path) -> None:
    root = tmp_path / "nested"
    root.mkdir()
    (root / "contribution.json").write_text(
        '{"dataset_doi": "10.1234/nested.dataset", "license": "CC-BY-4.0"}',
        encoding="utf-8",
    )
    (root / "battery.json").write_text(
        (
            "{"
            '"battery_model": "https://example.org/battery/anr26650m1-b",'
            '"spec": {'
            '"manufacturer": {"@id": "https://ror.org/02y7qqd86", "name": "A123"},'
            '"productID": "ANR26650M1-B"'
            "},"
            '"cells": ['
            '{"name": "lfp_k1"},'
            '{"name": "lfp_k2", "cell_id": "lfp_k2_cell"}'
            "]"
            "}"
        ),
        encoding="utf-8",
    )
    _write_text(
        root / "Inst__lfp_k1__20240101__CC__25C.bdf.csv",
        "Test Time / s,Voltage / V,Current / A\n1,4.0,0.1\n2,4.1,0.1\n",
    )

    summary = bdf.ingest(
        root,
        layout="nested",
        format="csv",
        recursive=False,
        data_dir=None,
        raw_dir=None,
        cell_metadata_dir=None,
        doi_enrich=False,
    )

    assert (root / "data" / "Inst__lfp_k1__20240101__CC__25C.bdf.csv").exists()
    assert (root / "metadata.jsonld").exists()
    assert (root / "lfp_k1" / "metadata.jsonld").exists()
    assert not summary.get("metadata_failed")
