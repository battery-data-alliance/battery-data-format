"""File operations for explicit ontology-link acceptance in the CLI."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from bdf._errors import BDFMetadataError
from bdf.io import _write_sidecar, scan
from bdf.metadata_parsers import BdfSidecarParser
from bdf.ontology_terms import _index, apply_term_mappings, suggest_terms


def _replace_sidecar(path: Path, metadata) -> None:
    # A failed write must leave an existing sidecar readable and untouched.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        _write_sidecar(temporary, metadata)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_terms(
    path: Path,
    *,
    accept: list[str],
    mappings_path: Path | None = None,
    save_mappings: Path | None = None,
) -> dict[str, Any]:
    """Inspect a local file, then apply only explicit selections or a saved profile.

    A profile binds exact headers and units, not guessed similar measurements.
    Validate every mapping before writing either output. Never rewrite data.
    """
    if not path.is_file():
        raise BDFMetadataError(f"Not a local data file: {path}")
    sidecar = BdfSidecarParser().sidecar_path(path)
    protected = {path.resolve(), sidecar.resolve()}
    if mappings_path:
        protected.add(mappings_path.resolve())
    if save_mappings:
        if save_mappings.resolve() in protected:
            raise BDFMetadataError("Save mappings to a separate new file, not the data, sidecar or input profile.")
        if save_mappings.exists():
            raise FileExistsError(f"Mapping profile already exists: {save_mappings}")
        if not save_mappings.parent.is_dir():
            raise FileNotFoundError(f"Mapping profile directory does not exist: {save_mappings.parent}")
        if not accept and mappings_path is None:
            raise BDFMetadataError("--save-mappings requires --accept or --mappings.")

    frame, metadata = scan(path, normalize=False, validate=False, include_unknown=True)
    columns = frame.collect_schema().names()
    before = suggest_terms(columns, metadata)
    selections = []
    if mappings_path:
        payload = json.loads(mappings_path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("format_version") != 1
            or not isinstance(payload.get("mappings"), list)
        ):
            raise BDFMetadataError("Expected a version 1 mapping profile containing a mappings list.")
        for mapping in payload["mappings"]:
            if not isinstance(mapping, dict) or set(mapping) != {"column", "unit_text", "same_as"}:
                raise BDFMetadataError("Each saved mapping must contain column, unit_text and same_as only.")
            selections.append(mapping)
    findings = {f["column"]: f for f in before["unlinked"]}
    for selection in accept:
        column, separator, iri = selection.partition("=")
        if not separator or not column or not iri:
            raise BDFMetadataError("Use --accept 'COLUMN=IRI'.")
        finding = findings.get(column)
        if finding is None:
            raise BDFMetadataError(f"{column!r} is not an unlinked additional column in this table.")
        unit = finding["unit_text"]
        if not unit:
            raise BDFMetadataError(
                f"Describe the units of {column!r} in metadata or a mapping profile before linking it."
            )
        selections.append({"column": column, "unit_text": unit, "same_as": iri})

    if not selections:
        if save_mappings or mappings_path:
            raise BDFMetadataError("No mappings were selected.")
        return {**before, "applied": [], "sidecar": None}
    updated = apply_term_mappings(columns, metadata, selections)
    # Compute the report before any write as well; malformed remaining metadata
    # must not leave a partially successful mapping operation.
    after = suggest_terms(columns, updated)
    if save_mappings:
        profile = {"format_version": 1, "index": _index()["source"], "mappings": selections}
        created = False
        try:
            with save_mappings.open("x", encoding="utf-8") as handle:
                created = True
                handle.write(json.dumps(profile, indent=2, ensure_ascii=False) + "\n")
        except Exception:
            # Close the file before removing it (also required on Windows).
            # An exclusive-open failure must never remove someone else's file.
            if created:
                save_mappings.unlink(missing_ok=True)
            raise
    try:
        _replace_sidecar(sidecar, updated)
    except Exception:
        if save_mappings:
            save_mappings.unlink(missing_ok=True)
        raise
    return {**after, "index": _index()["source"], "applied": selections, "sidecar": str(sidecar)}
