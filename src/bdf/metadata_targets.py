"""Where a metadata rule stages its value: the target types and the ``METADATA`` namespace.

A target is a frozen reference to one path in the document a read returns
(:class:`bdf.metadata.Metadata`). :data:`METADATA` mirrors that document as an
attribute path: ``METADATA.battinfo_test.test.started_at`` is a reference to
that field, and ``METADATA.extras["rig_bay"]`` is a reference to an ``extras``
key. Attribute access reads ``model_fields`` on the generated BattINFO models
themselves, so the models stay the single source of truth and no second
representation of the same tree can go stale.

An open map (e.g. ``test.conditions``) accepts a subscript instead of a
further attribute. A misspelled attribute raises ``AttributeError`` before any
parser is built. There is no target for an array-typed field, nor for ``bdf``,
which a read stamps itself.
"""

from __future__ import annotations

import typing
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, RootModel

from .battinfo.generated.cell_instance_schema import BattinfoCellInstance
from .battinfo.generated.channel_schema import BattinfoChannelInstance
from .battinfo.generated.equipment_schema import BattinfoEquipmentInstance
from .battinfo.generated.test_protocol_schema import BattinfoTestProtocol
from .battinfo.generated.test_schema import BattinfoTest


class MetadataTarget(BaseModel):
    """A frozen reference to one canonical path in the read metadata document.

    Constructed by the ``METADATA`` namespace, never directly; a rule
    pairs one target with its extraction and normalization, and the staging
    engine calls :meth:`stage` to place the rule's normalized value.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["metadata"] = "metadata"
    path: tuple[str, ...]

    def stage(self, document: dict, value: Any) -> None:
        """Set ``value`` at ``self.path`` in ``document``, creating intermediate dicts.

        Args:
            document: The staged metadata document under construction.
            value: The rule's normalized value to place at ``self.path``.
        """
        node = document
        for segment in self.path[:-1]:
            node = node.setdefault(segment, {})
        node[self.path[-1]] = value


class ExtrasTarget(BaseModel):
    """A frozen reference to one key under the staged document's ``extras`` mapping.

    Constructed by the ``METADATA`` namespace's ``extras`` subscript,
    never directly; lets a rule stage a vendor value under a chosen ``extras``
    key instead of a canonical field.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["extras"] = "extras"
    path: tuple[str, ...]

    def stage(self, document: dict, value: Any) -> None:
        """Set ``value`` at ``self.path`` under ``document["extras"]``.

        Args:
            document: The staged metadata document under construction.
            value: The rule's normalized value to place under ``extras``.
        """
        node = document.setdefault("extras", {})
        for segment in self.path[:-1]:
            node = node.setdefault(segment, {})
        node[self.path[-1]] = value


# The wrapper key each entity's root model answers to, matching the record
# fields of the read metadata. `bdf`, `raw`, and `extras` are not entity
# records and hold no target: only an explicit `extras` subscript reaches
# outside an entity.
_ROOTS: dict[str, type[BaseModel]] = {
    "battinfo_test": BattinfoTest,
    "battinfo_cell": BattinfoCellInstance,
    "battinfo_channel": BattinfoChannelInstance,
    "battinfo_equipment": BattinfoEquipmentInstance,
    "battinfo_test_protocol": BattinfoTestProtocol,
}

_ARRAY_ORIGINS = (list, tuple, set, frozenset)


def _sole_type(annotation: Any) -> Any:
    """Return the one type ``annotation`` states, or ``annotation`` itself.

    Args:
        annotation: A generated model field's type annotation.

    Returns:
        The single non-``None`` member of an optional or union annotation,
        or ``annotation`` unchanged where it states no union or states more
        than one member.
    """
    members = [member for member in typing.get_args(annotation) if member is not type(None)]
    return members[0] if len(members) == 1 else annotation


class _OpenMapTarget:
    """A generated attribute node that accepts a subscript, not a further attribute."""

    __slots__ = ("_path",)

    def __init__(self, path: tuple[str, ...]) -> None:
        self._path = path

    def __getitem__(self, key: str) -> MetadataTarget:
        """Return the target for ``key`` under this open map.

        Args:
            key: The map key to target.

        Returns:
            A frozen :class:`MetadataTarget` for ``self._path + (key,)``.
        """
        return MetadataTarget(path=(*self._path, key))


class _ExtrasNamespace:
    """``METADATA.extras``: a subscript onto any key of the staged document's ``extras`` mapping."""

    __slots__ = ()

    def __getitem__(self, key: str) -> ExtrasTarget:
        """Return the extras target for ``key``.

        Args:
            key: The ``extras`` key to target.

        Returns:
            A frozen :class:`ExtrasTarget` for ``(key,)``.
        """
        return ExtrasTarget(path=(key,))


class _MetadataNode:
    """One node of the metadata target namespace, backed by a generated model."""

    __slots__ = ("_path", "_model")

    def __init__(self, path: tuple[str, ...], model: type[BaseModel]) -> None:
        self._path = path
        self._model = model

    def __getattr__(self, name: str) -> Any:
        """Resolve ``name`` against this node's model fields.

        Args:
            name: The attribute segment to resolve.

        Returns:
            A :class:`MetadataTarget`, an :class:`_OpenMapTarget`, or a
            nested :class:`_MetadataNode`.

        Raises:
            AttributeError: ``name`` names no field of this node's model, or
                names an array-typed field, which is never a rule target.
        """
        field = self._model.model_fields.get(name)
        path = (*self._path, name)
        if field is None:
            raise AttributeError(f"{'.'.join(path)!r} is not a declared BattINFO metadata path")

        # One segment per access, so a self-referencing schema costs nothing
        # until a caller walks into it.
        annotation = _sole_type(field.annotation)
        origin = typing.get_origin(annotation)
        if origin is dict:
            return _OpenMapTarget(path)
        if origin in _ARRAY_ORIGINS:
            raise AttributeError(f"{'.'.join(path)!r} is an array field, which states no metadata target")
        if isinstance(annotation, type) and issubclass(annotation, BaseModel) and not issubclass(annotation, RootModel):
            return _MetadataNode(path, annotation)
        return MetadataTarget(path=path)


class _MetadataRoot:
    """The top-level ``METADATA`` namespace: the five entity records, plus ``extras``."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        """Resolve ``name`` against the entity records and ``extras``.

        Args:
            name: The attribute segment to resolve.

        Returns:
            An :class:`_ExtrasNamespace` for ``"extras"``, otherwise the
            :class:`_MetadataNode` for that entity record.

        Raises:
            AttributeError: ``name`` names no entity record.
        """
        if name == "extras":
            return _ExtrasNamespace()
        model = _ROOTS.get(name)
        if model is None:
            raise AttributeError(f"{name!r} is not a declared BattINFO metadata record")
        return _MetadataNode((name,), model)


METADATA = _MetadataRoot()
