"""Where a metadata rule stages its value: the target types and the ``METADATA`` namespace.

A target is a frozen reference to one path in the document a read returns
(:class:`bdf.metadata.Metadata`). :data:`METADATA` spells that path as
attributes: ``METADATA.battinfo_test.test.started_at`` is a reference to that
field, ``METADATA.battinfo_test.test.conditions["temperature_c"]`` reaches a
map key, and ``METADATA.extras["rig_bay"]`` is a reference to an ``extras``
key.

The first segment must name a ``battinfo_*`` field of ``Metadata`` (or
``extras``); a wrong name raises ``AttributeError``. Any name extends the path
below that, so a misspelled key there is found when the read checks the staged
value against BattINFO: it raises ``BDFMetadataError``, or warns with
``strict=False``.
There is no target for ``bdf``, which a read stamps itself.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .battinfo import FIELD_SCHEMAS


class MetadataTarget(BaseModel):
    """A frozen reference to one canonical path in the read metadata document.

    Built from a ``METADATA`` path when a rule set is built, never directly; a rule
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


class _TargetPath:
    """A path below a ``battinfo_*`` field, spelled with attributes and subscripts.

    It holds only its path in a private slot, so any field name (``kind``,
    ``path``, ``copy`` among them) extends the path. A rule set turns it into a
    :class:`MetadataTarget` when it is built.
    """

    __slots__ = ("_path",)

    def __init__(self, path: tuple[str, ...]) -> None:
        self._path = path

    def __getattr__(self, name: str) -> _TargetPath:
        """Return the path extended by ``name``.

        Raises:
            AttributeError: ``name`` starts with ``_``, so copy, pickle and
                dunder lookups behave as for a plain object.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        return _TargetPath((*self._path, name))

    def __getitem__(self, key: str) -> _TargetPath:
        """Return the path extended by the map key ``key``.

        Raises:
            TypeError: ``key`` is not a str.
        """
        if not isinstance(key, str):
            raise TypeError(f"a metadata target key must be a str, not {type(key).__name__}")
        return _TargetPath((*self._path, key))

    # With __getitem__ alone, iter() and `in` would count up from 0 forever.
    __iter__ = None

    def __repr__(self) -> str:
        return f"METADATA.{'.'.join(self._path)}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _TargetPath):
            return NotImplemented
        return self._path == other._path

    def __hash__(self) -> int:
        return hash(self._path)


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


class _MetadataRoot:
    """The top-level ``METADATA`` namespace: the six ``battinfo_*`` fields, plus ``extras``."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        """Resolve ``name`` against the ``battinfo_*`` fields and ``extras``.

        Args:
            name: The attribute segment to resolve.

        Returns:
            An :class:`_ExtrasNamespace` for ``"extras"``, otherwise the
            :class:`_TargetPath` of that ``battinfo_*`` field.

        Raises:
            AttributeError: ``name`` is not a ``battinfo_*`` field or ``extras``.
        """
        if name == "extras":
            return _ExtrasNamespace()
        if name in FIELD_SCHEMAS:
            return _TargetPath((name,))
        raise AttributeError(f"{name!r} is not a declared BattINFO metadata record")


METADATA = _MetadataRoot()
