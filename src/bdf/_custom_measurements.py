"""Bind proposed custom measurement descriptions to columns without interpreting values."""

from __future__ import annotations

import warnings
from collections.abc import Collection

from bdf._errors import BDFMetadataError
from bdf.battinfo.generated.dataset_schema import VariableMeasured
from bdf.metadata import Metadata
from bdf.spec import COLUMN_ONTOLOGY, unit_from_label


def custom_measurements(metadata: Metadata) -> list[VariableMeasured]:
    """Return non-core variable descriptions from the dataset metadata.

    Descriptions of standard BDF quantities remain descriptive metadata; they
    cannot opt a standard column out of its existing normalization or validation.
    """
    dataset = metadata.battinfo_dataset.dataset
    if dataset is None:
        return []
    standard_names = {
        name.casefold()
        for mr_name, quantity in COLUMN_ONTOLOGY
        for name in (
            mr_name,
            quantity.effective_notation,
            quantity.formatted_label,
            quantity.label_template.split(" / ")[0],
        )
    }
    return [
        variable
        for variable in dataset.variable_measured or []
        if not (
            variable.name
            and (
                variable.name.casefold() in standard_names
                or COLUMN_ONTOLOGY.quantity_from_label(variable.name) is not None
            )
        )
    ]


def bind_custom_measurements(
    metadata: Metadata,
    columns: Collection[str],
    *,
    validate: bool = True,
    normalized_sources: Collection[str] = (),
) -> tuple[str, ...]:
    """Resolve each custom description to exactly one column.

    A name can be the exact header or its quantity portion, with ``unit_text``
    supplying the suffix (``Thickness`` + ``mm`` → ``Thickness / mm``).
    Missing, duplicate, ambiguous, or contradictory descriptions fail before
    data is written or selected. ``validate=False`` warns and skips invalid
    bindings, without guessing a column or changing a value.

    Args:
        metadata: Metadata whose dataset describes this table's variables.
        columns: Actual column headers, before any normalization.
        validate: Raise on invalid descriptions; otherwise warn.
        normalized_sources: Headers already claimed by a vendor normalizer.

    Returns:
        Custom headers to preserve, in description order.

    Raises:
        BDFMetadataError: A description cannot unambiguously identify a custom column.
    """
    headers = set(columns)
    bound: list[str] = []
    seen: set[str] = set()
    problems: list[str] = []
    for variable in custom_measurements(metadata):
        name, unit = variable.name, variable.unit_text
        if not name or not name.strip() or not unit or not unit.strip():
            problems.append("Custom measurements require a nonblank name and unit_text (use '1' for dimensionless).")
            continue

        candidates = {name, f"{name} / {unit}"} & headers
        if not candidates:
            problems.append(f"Custom measurement {name!r} has no matching column (unit_text={unit!r}).")
            continue
        if len(candidates) != 1:
            problems.append(f"Custom measurement {name!r} is ambiguous between columns {sorted(candidates)!r}.")
            continue

        column = next(iter(candidates))
        if column in seen:
            problems.append(f"Duplicate custom measurement descriptions for column {column!r}.")
            # A conflicting duplicate must not leave the first definition active.
            if column in bound:
                bound.remove(column)
            continue
        seen.add(column)
        if column in normalized_sources:
            problems.append(f"Custom column {column!r} is already mapped to a standard BDF quantity by the parser.")
            continue
        # A slash inside a quantity (dQ/dV) or unit (Ah/V) is not the
        # explicit " / " separator. A composed match already agrees by
        # construction. For an exact header, inspect only an explicit suffix.
        header_unit = column.partition(" / ")[2] if column == name and " / " in column else None
        declared_unit = unit_from_label(f"Measurement / {unit}")
        if header_unit is not None and unit_from_label(f"Measurement / {header_unit}") != declared_unit:
            problems.append(
                f"Custom column {column!r} conflicts with declared unit_text={unit!r}; no conversion is assumed."
            )
            continue
        bound.append(column)

    if problems:
        message = "Invalid custom measurement metadata: " + " ".join(problems)
        if validate:
            raise BDFMetadataError(message)
        warnings.warn(message, UserWarning, stacklevel=2)
    return tuple(bound)
