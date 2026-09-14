"""Offline, advisory term discovery and explicit metadata linking.

Suggestions use a bundled subset of BattINFO mappings, never a live ontology.
No function in this module reads or changes measurement values.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping
from functools import lru_cache
from importlib.resources import files
from tokenize import TokenError
from typing import Any

from pydantic import AnyUrl

from bdf._custom_measurements import bind_custom_measurements, custom_measurements
from bdf._errors import BDFMetadataError
from bdf.battinfo.generated.dataset_schema import VariableMeasured
from bdf.metadata import Metadata
from bdf.spec import COLUMN_ONTOLOGY, _slugify, get_unit_conversion

REFERENCE_URL = "https://big-map.github.io/BattINFO/dev/pages/property-reference.html"
PROPOSE_URL = "https://bda.discourse.group/t/adding-optionality-for-custom-measurement-integration/56"


@lru_cache(maxsize=1)
def _index() -> dict[str, Any]:
    """Load the packaged index only when term discovery is requested."""
    return json.loads(files("bdf.data").joinpath("ontology-terms.json").read_text(encoding="utf-8"))


def _name_key(name: str) -> str:
    # Treat spaces, underscores and CamelCase as spelling variants, not fuzzy
    # semantic matches. Preserve acronym boundaries (ACInternalResistance).
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return " ".join(re.sub(r"[_\W]+", " ", name.casefold()).split())


def _variables(columns: Collection[str], metadata: Metadata) -> dict[str, VariableMeasured]:
    bound = bind_custom_measurements(metadata, columns)
    variables = custom_measurements(metadata)
    return dict(zip(bound, variables, strict=True))


def _additional_columns(columns: Collection[str]) -> list[str]:
    synonyms = COLUMN_ONTOLOGY.base_synonym_index()
    return [
        c
        for c in columns
        if COLUMN_ONTOLOGY.quantity_from_label(c) is None
        and _slugify(c) not in synonyms
        and _slugify(c.partition(" / ")[0]) not in synonyms
    ]


def _unit_status(unit: str | None, reference_unit: str | None) -> str:
    if not unit or not reference_unit:
        return "unchecked"
    from pint.errors import PintError

    from bdf.spec import ureg

    try:
        return (
            "compatible"
            if ureg.Unit(unit).dimensionality == ureg.Unit(reference_unit).dimensionality
            else "incompatible"
        )
    except (PintError, ValueError, TypeError, AssertionError, SyntaxError, TokenError):
        return "unchecked"


def suggest_terms(columns: Collection[str], metadata: Metadata | None = None) -> dict[str, Any]:
    """Report candidates for unlinked additional columns; never apply a match.

    Names and curated aliases must match after spelling normalization. Known
    incompatible dimensions exclude a candidate; unknown units are explicitly
    unchecked. Empty results mean no match in this index, not in every ontology.
    """
    metadata = metadata if metadata is not None else Metadata()
    variables = _variables(columns, metadata)
    unlinked = []
    for column in _additional_columns(columns):
        variable = variables.get(column)
        if variable and variable.same_as:
            continue
        base, separator, header_unit = column.partition(" / ")
        unit = variable.unit_text if variable else (header_unit.strip() if separator else None)
        name = variable.name.partition(" / ")[0] if variable and variable.name else base
        unlinked.append((column, name, unit))

    # Ordinary, fully linked tables do not even load the suggestion resource.
    if not unlinked:
        return {"index": None, "unlinked": [], "search_url": REFERENCE_URL, "propose_url": PROPOSE_URL}
    index = _index()
    findings = []
    for column, name, unit in unlinked:
        candidates = []
        for term in index["terms"]:
            matching = [alias for alias in term["names"] if _name_key(alias) == _name_key(name)]
            if not matching:
                continue
            unit_status = _unit_status(unit, term.get("reference_unit"))
            if unit_status == "incompatible":
                continue
            candidates.append(
                {
                    "label": term["label"],
                    "iri": term["iri"],
                    "definition": term.get("definition"),
                    "matched_names": matching,
                    "unit_check": unit_status,
                }
            )
        findings.append(
            {
                "column": column,
                "unit_text": unit,
                "candidates": candidates,
                "message": "Review suggested terms before applying."
                if candidates
                else "No matching term found in this index.",
            }
        )
    return {"index": index["source"], "unlinked": findings, "search_url": REFERENCE_URL, "propose_url": PROPOSE_URL}


def apply_term_mappings(
    columns: Collection[str], metadata: Metadata, mappings: Collection[Mapping[str, str]]
) -> Metadata:
    """Return copied metadata with explicitly selected mappings applied atomically.

    Each mapping contains an exact ``column``, ``unit_text`` and ``same_as``.
    Previously accepted mappings can name an arbitrary column, but must retain
    its exact header and equivalent unit and refer to a term in this index.
    Existing links cannot be overwritten. No IRI is fetched or value converted.
    """
    updated = metadata.model_copy(deep=True)
    variables = _variables(columns, updated)
    additional = set(_additional_columns(columns))
    terms = {t["iri"]: t for t in _index()["terms"]}
    seen: set[str] = set()
    for mapping in mappings:
        column, unit, iri = (mapping.get(k, "") for k in ("column", "unit_text", "same_as"))
        if not all(isinstance(v, str) and v.strip() for v in (column, unit, iri)):
            raise BDFMetadataError("Each term mapping requires a nonblank column, unit_text and same_as.")
        if column in seen:
            raise BDFMetadataError(f"Duplicate term mapping for {column!r}.")
        seen.add(column)
        if column not in additional:
            raise BDFMetadataError(f"{column!r} is not an additional column in this table.")
        if iri not in terms:
            raise BDFMetadataError(f"{iri!r} is not in the bundled term index; review the mapping before using it.")
        variable = variables.get(column)
        actual_unit = variable.unit_text if variable else column.partition(" / ")[2]
        if actual_unit and actual_unit != unit and get_unit_conversion(actual_unit, unit) != (1.0, 0.0):
            raise BDFMetadataError(f"Mapping unit {unit!r} conflicts with {column!r} ({actual_unit!r}).")
        if _unit_status(unit, terms[iri].get("reference_unit")) == "incompatible":
            raise BDFMetadataError(f"Mapping for {column!r} has units incompatible with {terms[iri]['label']}.")
        if variable and variable.same_as and str(variable.same_as) != iri:
            raise BDFMetadataError(f"{column!r} already has a different ontology link; it was not overwritten.")
        if variable is None:
            variable = VariableMeasured(name=column, unit_text=actual_unit or unit)
            dataset = updated.battinfo_dataset.dataset
            if dataset is None:
                raise BDFMetadataError("Dataset metadata must be an object before adding term mappings.")
            dataset.variable_measured = [*(dataset.variable_measured or []), variable]
            variables[column] = variable
        variable.same_as = AnyUrl(iri)
    bind_custom_measurements(updated, columns)
    return updated


def format_term_report(report: dict[str, Any]) -> str:
    """Render one grouped, actionable advisory for CLI or notebook use."""
    if not report["unlinked"]:
        return ""
    lines = ["Unlinked measurement columns (advisory):"]
    for finding in report["unlinked"]:
        lines.append(f"  {finding['column']}: {finding['message']}")
        for candidate in finding["candidates"]:
            lines.append(f"    Suggested: {candidate['label']} — {candidate['iri']} (units {candidate['unit_check']})")
            if candidate["definition"]:
                lines.append(f"      {candidate['definition']}")
        if finding.get("retained") is False:
            lines.append("    Excluded from output; use --include-unknown or describe this column before converting.")
    lines.extend(
        [
            "Review an IRI's definition, then use bdf terms FILE --accept 'COLUMN=IRI' to save the link.",
            f"Search properties: {report['search_url']}",
            f"Discuss missing terms: {report['propose_url']}",
            "Coverage is limited to the bundled index; a missing match does not mean the ontology lacks a term.",
        ]
    )
    return "\n".join(lines)
