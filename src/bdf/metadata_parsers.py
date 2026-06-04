"""Metadata source parsers: ``MetadataParser`` base class and concrete sources.

A metadata parser combines a *source* (where the metadata lives) with *extraction*
(how to pull BDF metadata fields out of it). Identification is :meth:`matches`,
extraction is :meth:`parse`; each subclass owns all of its own file I/O.

Sources are fully orthogonal to readers: a delimited-text file may carry its
metadata in a preamble (:class:`TxtPreambleParser`) while any file may have an
adjacent JSON sidecar (:class:`JsonSidecarParser`). To keep that orthogonality at
the import level too, **this module MUST NOT import from** :mod:`bdf.readers`; it
reads the bytes it needs through :func:`read_head` from :mod:`bdf.head_utils`.

:class:`MetadataSchema` is the single source of truth for BDF metadata field names
(symmetric with :class:`~bdf.normalizers.TableNormalizer`'s mr_name fields). Frozen +
scalar/tuple values ⇒ every parser instance is hashable, so ``PLUGINS.metadata_parsers``
can be a ``frozenset``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Generic, Iterator, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .head_utils import read_head

T = TypeVar("T")


class MetadataSchema(BaseModel, Generic[T]):
    """Generic frozen model declaring one field per supported BDF metadata field.

    ``T`` is the per-parser extraction-rule type (``str`` regex patterns for
    :class:`TxtPreambleParser`, ``tuple[str, ...]`` synonym keys for
    :class:`JsonSidecarParser`). The set of fields here is the single source of
    truth for BDF metadata field names. ``extra="forbid"`` rejects typos at
    construction; frozen + scalar/tuple values keep instances hashable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_time: T | None = None

    def __iter__(self) -> Iterator[tuple[str, T]]:  # type: ignore[override]
        """Yield ``(field_name, rule)`` for each set (non-None) field in declaration order."""
        for field_name in type(self).model_fields:
            val = getattr(self, field_name)
            if val is not None:
                yield field_name, val


class MetadataParser(BaseModel):
    """Base / null metadata parser: never matches, extracts nothing.

    Subclasses override :meth:`matches` and :meth:`parse` and own all file I/O
    for their source type. Frozen so instances are hashable.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["base"] = "base"

    def matches(self, path: str | Path) -> bool:
        """Return whether this parser recognises ``path`` as its source. Base: never."""
        return False

    def parse(self, path: str | Path) -> dict[str, str]:
        """Extract BDF metadata fields from ``path``. Base: nothing."""
        return {}


class TxtPreambleParser(MetadataParser):
    """Reads metadata from the head bytes of the data file itself.

    ``magic`` tokens identify the format; ``encoding`` decodes the head bytes;
    ``regex_patterns`` holds one regex per set field whose ``group(1)`` is the
    extracted value. :meth:`parse` applies each regex over the decoded head
    lines (no separator / skip-rows sniffing).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["txt_preamble"] = "txt_preamble"
    magic: tuple[str | bytes, ...] = Field(
        default=(),
        description=(
            "Tokens that identify this format: str tokens are matched case-insensitively "
            "against decoded head text; bytes tokens are matched as raw byte substrings."
        ),
    )
    encoding: str = Field(default="utf-8", description="Codec used to decode head bytes before regex matching.")
    regex_patterns: MetadataSchema[re.Pattern[str]] = Field(
        default_factory=lambda: MetadataSchema[re.Pattern[str]](),
        description="Per-field compiled regex patterns; each pattern's group(1) is the extracted value.",
    )

    def matches(self, path: str | Path) -> bool:
        """Return True when any magic token is found in the file's head bytes."""
        if not self.magic:
            return False
        head = read_head(path)
        text = head.decode("utf-8", errors="replace").lower()
        for m in self.magic:
            if isinstance(m, bytes):
                if m in head:
                    return True
            elif m.lower() in text:
                return True
        return False

    def parse(self, path: str | Path) -> dict[str, str]:
        """Decode the head with ``encoding`` and apply each regex; first match per field."""
        head = read_head(path)
        lines = head.decode(self.encoding, errors="replace").splitlines()
        result: dict[str, str] = {}
        for field_name, rx in self.regex_patterns:
            for line in lines:
                m = rx.search(line)
                if m:
                    result[field_name] = m.group(1).strip()
                    break
        return result


class JsonSidecarParser(MetadataParser):
    """Reads metadata from a JSON file adjacent to the data file (``path.with_suffix(".json")``).

    ``key_synonyms`` holds an ordered tuple of candidate JSON keys per set field;
    :meth:`parse` returns the value of the first synonym key present in the JSON.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["json_sidecar"] = "json_sidecar"
    key_synonyms: MetadataSchema[tuple[str, ...]] = Field(
        default_factory=lambda: MetadataSchema[tuple[str, ...]](),
        description="Per-field ordered tuples of candidate JSON keys.",
    )

    def _sidecar(self, path: str | Path) -> Path:
        return Path(path).with_suffix(".json")

    def matches(self, path: str | Path) -> bool:
        """Return True when the ``.json`` sidecar file exists."""
        return self._sidecar(path).exists()

    def parse(self, path: str | Path) -> dict[str, str]:
        """Load the sidecar JSON and resolve each set field's synonym keys (first match)."""
        sidecar = self._sidecar(path)
        if not sidecar.exists():
            return {}
        with open(sidecar, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {}
        result: dict[str, str] = {}
        for field_name, keys in self.key_synonyms:
            for key in keys:
                if key in data:
                    result[field_name] = str(data[key])
                    break
        return result


__all__ = [
    "MetadataSchema",
    "MetadataParser",
    "TxtPreambleParser",
    "JsonSidecarParser",
]
