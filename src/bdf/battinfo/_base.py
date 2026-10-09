"""The base class the generated BattINFO record models inherit.

:class:`_RecordModel` carries the shared configuration and serialisation
every generated model needs. It validates canonical values alone: a metadata
parser owns the interpretation of its own source format and converts a
matched value to its canonical form before validation ever sees it. A
wrong-typed value for any declared field raises out of validation.
"""

from __future__ import annotations

from typing import Any, Iterator

from pydantic import BaseModel, ConfigDict


class _RecordModel(BaseModel):
    """Shared configuration and serialisation for every BattINFO record model."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    def __repr_args__(self) -> Iterator[tuple[str | None, Any]]:
        """Yield only the fields that differ from their default, for ``repr`` and ``str``."""
        for name, field in type(self).model_fields.items():
            value = getattr(self, name)
            if value != field.get_default(call_default_factory=True):
                yield name, value

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a plain dict in BattINFO shape.

        Returns:
            The model dumped in JSON mode with ``exclude_defaults=True``. JSON
            mode gives a URL value its normalised string form, so the result
            is a JSON document already. Every declared field's default is
            ``None`` or a factory that builds an empty model, so a field left
            untouched, and a section nothing filled, are both absent from the
            result rather than present as ``None`` or ``{}``.

            One exception applies to every field whose default comes from a
            factory rather than a literal ``None``. An explicit ``None``
            differs from what the factory builds, so such a field serialises
            as a stated ``null`` instead of vanishing. That covers each
            nested-model field, the wrapper-typed leaves ``expires_at``,
            ``manufactured_at``, ``commissioned_at``, and ``Quantity.value``
            among them.
        """
        return self.model_dump(mode="json", exclude_defaults=True)
