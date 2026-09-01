"""Gate: reference example data must satisfy the format's own rules.

The artifacts under ``docs/examples/reference/`` are the canonical BDF
examples, each generated from a raw vendor export on Zenodo (see
``datasets.json`` and ``scripts/generate_zenodo_reference_bdf.py``). Two
checks gate every artifact, CSV and parquet alike:

  1. ``bdf.read(path, validate=True)`` must succeed -- the artifact must
     read back under full validation, which includes the elapsed-time /
     wall-clock cross-check.
  2. ``validate_df`` must report no derived-column issues (mislabeled step
     ids, swapped cumulative/net columns, non-monotonic accumulators, ...).

``KNOWN_NONCOMPLIANT`` lists the files that currently fail a check, each
with the issue that tracks why. Both directions are enforced: a file NOT
listed must pass both checks, and a listed file must still fail at least
one -- so when the underlying issue is fixed, this test demands the entry
be removed.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import polars as pl
import pytest

import bdf

REFERENCE_DIR = Path(__file__).resolve().parents[2] / "docs" / "examples" / "reference"

# filename -> tracking issue. See #52 for the original audit.
KNOWN_NONCOMPLIANT: dict[str, str] = {
    # Deliberate known-bad sample: real instrument voltage outliers, kept to
    # demonstrate bdf.clean. Format-compliant, fails the derived checks.
    "SINTEF__NaCR32140-MP10-04__2025-08-25__CCCV_0p02C_25degC__BioLogic__Outlier_Bug.bdf.csv": "#55",
    # Checker limitation: minute-resolution wall clock against 10-30 s
    # sampling misreads the time-scale ratio as ~0.5x.
    "faraday__lg-INR21700M50-2019-002__2019-06-02__rate__25degC__maccor.bdf.csv": "#98",
    # Checker limitation: 1-second wall clock against ~0.15 s sampling
    # misreads the time-scale ratio as ~0.15x.
    "SINTEF__SLPBA842124HV-06__20241011__DCIR__0p1C__25degC__Novonix.bdf.parquet": "#98",
}


def _reference_files() -> list[Path]:
    return sorted([*REFERENCE_DIR.glob("*.bdf.csv"), *REFERENCE_DIR.glob("*.bdf.parquet")])


def _failures(path: Path) -> list[str]:
    """Return every gate failure for ``path``: read-back errors and derived issues."""
    failures: list[str] = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            bdf.read(path, validate=True)
        except Exception as exc:  # noqa: BLE001 - any read-back failure gates
            failures.append(f"read-back with validate=True failed: {exc}")
        frame = pl.read_parquet(path) if path.suffix == ".parquet" else pl.read_csv(path)
        report = bdf.validate_df(frame, report=False, raise_on_error=False)
    failures.extend(f"derived: {issue}" for issue in report["derived"]["issues"])
    return failures


def _manifest() -> dict:
    import json

    return json.loads((REFERENCE_DIR / "datasets.json").read_text(encoding="utf-8"))


@pytest.mark.skipif(not _reference_files(), reason="no reference data present")
def test_manifest_matches_the_files_on_disk() -> None:
    """Every artifact datasets.json names exists with exactly the sha256 it states,
    and every artifact on disk has a manifest entry. The shas hash stored bytes
    (LF), which .gitattributes pins; a mismatch on a fresh clone is real, while a
    mismatch in a pre-existing checkout usually means stale line endings - run
    `git checkout -- docs/examples/reference` and retry."""
    import hashlib

    entries = {e["bdf_file"]: e["bdf_sha256"] for e in _manifest()["datasets"]}
    on_disk = {p.name for p in _reference_files()}
    assert set(entries) == on_disk, f"manifest/disk mismatch: {set(entries) ^ on_disk}"
    bad = [
        name for name, sha in entries.items() if hashlib.sha256((REFERENCE_DIR / name).read_bytes()).hexdigest() != sha
    ]
    assert not bad, (
        f"sha256 mismatch for {bad}; if this is a pre-existing checkout, refresh line endings "
        "with `git checkout -- docs/examples/reference` and retry."
    )


@pytest.mark.skipif(not _reference_files(), reason="no reference data present")
@pytest.mark.parametrize("path", _reference_files(), ids=lambda p: p.name)
def test_reference_file_compliance(path: Path) -> None:
    failures = _failures(path)
    if path.name in KNOWN_NONCOMPLIANT:
        # Known-bad: must still fail. When the tracked issue is fixed this
        # assertion fires -> remove the file from KNOWN_NONCOMPLIANT.
        assert failures, (
            f"{path.name} now passes every check; remove it from KNOWN_NONCOMPLIANT"
            f" (tracked in {KNOWN_NONCOMPLIANT[path.name]})."
        )
    else:
        assert not failures, f"{path.name} fails the reference gates:\n  - " + "\n  - ".join(failures)
