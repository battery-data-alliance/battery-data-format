"""Guards for the canonical reference datasets in ``docs/examples/reference/``.

``datasets.json`` is the provenance manifest pairing every committed BDF artifact
with the raw vendor export (on Zenodo) it was generated from; the Reference
Datasets docs page is rendered from it. These tests keep the three views —
manifest, files on disk, and BDF validation — in agreement, entirely offline:

* every manifest entry's artifact exists, reads with ``validate=True``, and
  matches the recorded row count and on-disk column keys;
* every on-disk column key is current (non-deprecated) ontology notation;
* every committed artifact is listed in the manifest (no orphans);
* deliberate-bug artifacts are exactly the ``__*_Bug``-suffixed ones.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import polars as pl
import pytest

import bdf
from bdf.spec import COLUMN_ONTOLOGY

REPO_ROOT = Path(__file__).resolve().parents[2]
REF_DIR = REPO_ROOT / "docs" / "examples" / "reference"
MANIFEST_PATH = REF_DIR / "datasets.json"

MANIFEST: dict = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
DATASETS: list[dict] = MANIFEST["datasets"]

_ARTIFACT_SUFFIXES = (".bdf.csv", ".bdf.parquet")


def _disk_columns(path: Path) -> list[str]:
    if path.name.endswith(".bdf.parquet"):
        return pl.scan_parquet(path).collect_schema().names()
    with path.open(encoding="utf-8") as f:
        return f.readline().strip().split(",")


@pytest.mark.parametrize("entry", DATASETS, ids=lambda e: e["bdf_file"])
def test_manifest_entry_reads_and_validates(entry: dict) -> None:
    """Each manifest artifact exists, validates on read, and matches its recorded shape."""
    path = REF_DIR / entry["bdf_file"]
    assert path.is_file(), f"manifest lists {entry['bdf_file']} but it is not on disk"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df, meta = bdf.read(path, validate=True, lazy=False)

    assert meta["source"] in {"bdf_csv", "bdf_parquet"}
    assert df.height == entry["rows"], "row count drifted from datasets.json"
    assert _disk_columns(path) == entry["columns"], "on-disk column keys drifted from datasets.json"


@pytest.mark.parametrize("entry", DATASETS, ids=lambda e: e["bdf_file"])
def test_manifest_entry_uses_current_notation_keys(entry: dict) -> None:
    """Canonical artifacts carry only current (non-deprecated) ontology notation keys."""
    current = {q.effective_notation for _, q in COLUMN_ONTOLOGY if not q.deprecated}
    stale = set(entry["columns"]) - current
    assert not stale, f"{entry['bdf_file']} carries non-current column keys: {sorted(stale)}"


def test_every_artifact_on_disk_is_in_manifest() -> None:
    """No orphan reference artifacts: disk contents and datasets.json list the same files."""
    on_disk = {p.name for p in REF_DIR.iterdir() if p.name.endswith(_ARTIFACT_SUFFIXES)}
    in_manifest = {e["bdf_file"] for e in DATASETS}
    assert on_disk == in_manifest


def test_bug_flag_matches_filename_convention() -> None:
    """deliberate_data_bugs is set exactly for the __*_Bug-suffixed artifacts."""
    for entry in DATASETS:
        flagged = entry["deliberate_data_bugs"] is not None
        named = "_Bug" in entry["bdf_file"]
        assert flagged == named, entry["bdf_file"]


def test_manifest_provenance_fields_complete() -> None:
    """Every entry carries full provenance: plugin, provider, and checksummed raw source."""
    for entry in DATASETS:
        assert entry["plugin"], entry["bdf_file"]
        assert entry["provider"], entry["bdf_file"]
        assert entry["bdf_sha256"] and len(entry["bdf_sha256"]) == 64, entry["bdf_file"]
        assert entry["raw_files"], entry["bdf_file"]
        for raw in entry["raw_files"]:
            assert raw["url"].startswith("https://zenodo.org/"), entry["bdf_file"]
            assert raw["checksum"], entry["bdf_file"]


@pytest.mark.network
@pytest.mark.parametrize("entry", DATASETS, ids=lambda e: e["bdf_file"])
def test_published_artifact_fetches_and_validates(entry: dict) -> None:
    """Fetch-based guard for the published artifact record (artifacts-on-Zenodo model).

    Activates per entry once ``prepare_artifact_record.py --record-id`` has
    back-filled the ``artifact`` block; skips (with a reason) until then. When
    the committed copies leave git, this test is the artifact coverage.
    """
    art = entry.get("artifact") or {}
    if not art.get("url"):
        pytest.skip("artifact not yet published to the Zenodo artifact record")
    import hashlib

    from bdf.fetch import fetch_url

    local = fetch_url(art["url"], filename=art["filename"])
    h = hashlib.sha256()
    with open(local, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    assert h.hexdigest() == art["sha256"], f"checksum mismatch for {art['filename']}"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df, _meta = bdf.read(local, validate=True, lazy=False)
    assert df.height == entry["rows"], f"row count differs from manifest for {art['filename']}"
