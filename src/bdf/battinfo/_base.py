"""The base class the generated BattINFO record models inherit.

:class:`_RecordModel` carries the shared configuration and serialisation
every generated model needs. It validates canonical values alone: a metadata
parser owns the interpretation of its own source format and converts a
matched value to its canonical form before validation ever sees it. A
wrong-typed value for any declared field raises out of validation.

Validation is strict by default: unknown fields or fields with unknown values
raise. A read that passes `context={"lenient": True}` keeps unknown fields
and invalid values, so a file with newer BattINFO schemas still loads and dumps
back unchanged.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, ValidationInfo, model_validator


def _validate_strict(cls: type[BaseModel], data: dict[str, Any], handler: Any) -> Any:
    """Strict ``data`` validation, raise on unknown fields, or known fields with bad values.

    Mimic Pydantic's "extras": "forbid" behaviour (models have "allow" set).
    `handler(data)` raises for known fields with bad values
    """
    known = set(cls.model_fields) | {f.alias for f in cls.model_fields.values() if f.alias}
    line_errors: list[Any] = [
        {"type": "extra_forbidden", "loc": (key,), "input": value} for key, value in data.items() if key not in known
    ]
    if not line_errors:
        return handler(data)
    try:
        handler(data)
    except ValidationError as exc:
        line_errors += [
            {"type": e["type"], "loc": e["loc"], "input": e["input"], **({"ctx": e["ctx"]} if "ctx" in e else {})}
            for e in exc.errors()
        ]
    raise ValidationError.from_exception_data(cls.__name__, line_errors)


def _validate_lenient(cls: type[BaseModel], data: dict[str, Any], handler: Any) -> Any:
    """Lenient ``data`` validation, allow unknown fields, and known fields with bad values.

    "extras": "allow" is default behaviour
    If `handler(data)` raises for known fields with bad values, remove those fields, validate again,
    and if it passes, restore the fields with bad values to the model.
    """
    try:
        return handler(data)  # Try normal validation (extra=allow)
    except ValidationError as exc:
        known_fields_bad_values = {e["loc"][0] for e in exc.errors() if e["loc"] and e["loc"][0] in cls.model_fields}
        if not known_fields_bad_values:
            raise  # If error is beyond that, give up
    # Try validating again without the bad fields
    instance = handler({key: value for key, value in data.items() if key not in known_fields_bad_values})
    # Restore the bad fields as-is, so a dump writes it back unchanged
    for key in known_fields_bad_values:
        instance.__dict__[key] = data[key]  # Restore field
        instance.__pydantic_fields_set__.add(key)  # Mark as set so it is included in dump
    return instance


# The (model class, attribute) a `__setattr__` is assigning, consumed by the first validator call.
_ASSIGNING: ContextVar[tuple[type, str] | None] = ContextVar("_ASSIGNING", default=None)


class _RecordModel(BaseModel):
    """Shared configuration and serialisation for every BattINFO record model."""

    model_config = ConfigDict(extra="allow", validate_assignment=True)

    # This gets called every time pydantic validates (model constructed, assignment, on model_validate)
    # We use _ASSIGNING like a global, which tells the validation we're trying to assign a new value
    @model_validator(mode="wrap")
    @classmethod
    def _strict_unless_lenient(cls, data: Any, handler: Any, info: ValidationInfo) -> Any:
        """Reject unknown keys, or with `"lenient": True` keep them and any invalid value."""
        assigning = _ASSIGNING.get()
        # If this function was called for an assignment, just do normal pydantic validation
        # __setattr__ blocks unknown fields, so does not matter that extras has allow
        if assigning is not None:
            _ASSIGNING.set(None)
            if assigning == (cls, info.field_name):
                return handler(data)
        if not isinstance(data, dict):
            return handler(data)  # Already a model, or junk, just do normal pydantic validation
        if not (info.context or {}).get("lenient"):
            # Strict validation (mimic extra=forbid, keep bad keys failing)
            return _validate_strict(cls, data, handler)
        # Lenient validation (keep extra=allow, let bad keys through)
        return _validate_lenient(cls, data, handler)

    def __setattr__(self, name: str, value: Any) -> None:
        """Assign a declared field or a kept extra key; any other public name raises ``AttributeError``."""
        if name.startswith("_"):
            super().__setattr__(name, value)
            return
        if name not in type(self).model_fields and name not in (self.__pydantic_extra__ or {}):
            raise AttributeError(f"{type(self).__name__} has no field {name!r}")
        token = _ASSIGNING.set((type(self), name))
        try:
            super().__setattr__(name, value)
        finally:
            _ASSIGNING.reset(token)

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

            Keys and values a lenient read kept are written back where they
            were read from, without further serializer warnings.
        """
        return self.model_dump(mode="json", exclude_defaults=True, warnings=False)
