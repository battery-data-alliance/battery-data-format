"""Metadata source parsers: ``MetadataParser`` base class and concrete sources.

A metadata parser combines a *source* (where the metadata lives) with a
*rules* mapping (how to pull staged ``Metadata`` fields out of it, and
how to convert each matched value to its canonical form). Identification is
:meth:`MetadataParser.matches`, extraction is :meth:`MetadataParser.parse`;
each subclass owns all of its own file I/O.

Sources are fully orthogonal to readers: a delimited-text file may carry its
metadata in a preamble (:class:`TxtPreambleParser`) while any file may have an
adjacent JSON sidecar (:class:`JsonSidecarParser`). To keep that orthogonality at
the import level too, **this module MUST NOT import from** :mod:`bdf.readers`; it
reads the bytes it needs through :func:`read_head` from :mod:`bdf.file_utils`.

A rule pairs one target (:mod:`bdf.metadata_targets`, built through the
``METADATA`` namespace) with its extraction (:class:`RegexRule` or
:class:`JsonRule`) and a normalization (:mod:`bdf.normalization`). The author-facing ``rules`` value is a dict;
construction canonicalises it to a sorted tuple, so parser instances stay
frozen and hashable and ``PLUGINS.metadata_parsers`` can be a ``frozenset``.
"""

from __future__ import annotations

import json
import re
import warnings
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .file_utils import read_head
from .metadata import Metadata
from .metadata_targets import ExtrasTarget, MetadataTarget
from .normalization import (
    AbsoluteTimeNormalization,
    ElapsedTimeNormalization,
    IdentityNormalization,
    LinearNormalization,
    Normalization,
    RelativeTimeNormalization,
)

# Trailing UTC offset or Zulu marker on raw extracted timestamp text: a
# value carrying one states its own zone, so `tz` never applies to it and it
# never triggers the naive-timestamp warning.
_TZ_MARKER_RE = re.compile(r"(?:[+-]\d{2}:?\d{2}(?::\d{2})?|Z)$")

# A rule's normalization, typed as this discriminated union rather than the
# bare Normalization base, so a JSON round trip (model_dump_json then
# model_validate_json) reconstructs the declared kind instead of the
# fieldless base class. Mirrors bdf.table_normalizers._NormalizationField.
_NormalizationField = Annotated[
    IdentityNormalization
    | LinearNormalization
    | AbsoluteTimeNormalization
    | RelativeTimeNormalization
    | ElapsedTimeNormalization,
    Field(discriminator="kind"),
]


# A rule's target: either a canonical path or an extras key, discriminated
# by `kind` so a JSON round trip reconstructs the right one even though both
# models otherwise share the same `path` shape.
_TargetUnion = Annotated[MetadataTarget | ExtrasTarget, Field(discriminator="kind")]


class RegexRule(BaseModel):
    """Extracts one value from the first line a compiled regex matches.

    ``group(1)`` of the first matching line is the extracted value, and
    ``normalization`` then converts it before staging. To take two values off
    one line, declare two rules.
    """

    model_config = ConfigDict(frozen=True)

    pattern: re.Pattern[str]
    normalization: _NormalizationField = Field(default_factory=IdentityNormalization)

    @model_validator(mode="after")
    def _check_capturing_group(self) -> RegexRule:
        """Reject a pattern that declares no capturing group.

        Returns:
            ``self``, once ``pattern`` declares a capturing group.

        Raises:
            ValueError: ``pattern`` declares no capturing group.
        """
        if self.pattern.groups < 1:
            raise ValueError(f"pattern {self.pattern.pattern!r} declares no capturing group")
        return self

    def extract(self, lines: list[str]) -> str | None:
        """Return ``group(1)``, stripped, of the first line this rule's pattern matches.

        Args:
            lines: The decoded head text, split into lines.

        Returns:
            The stripped captured text, or None when no line matched or the
            capture was empty.
        """
        for line in lines:
            match = self.pattern.search(line)
            if match:
                # group(0) is the whole matched text, label included, and one
                # rule stages one target, so a later group has nowhere to go.
                captured = match.group(1).strip()
                return captured or None
        return None


class JsonRule(BaseModel):
    """Extracts one value from the first candidate path present in a JSON document.

    ``candidates`` is an ordered tuple of dict-traversal paths, each path a
    tuple of one key per level. The first path present in the document wins,
    and ``normalization`` then converts the extracted value before staging.
    """

    model_config = ConfigDict(frozen=True)

    candidates: tuple[tuple[str, ...], ...] = Field(min_length=1)
    normalization: _NormalizationField = Field(default_factory=IdentityNormalization)

    def extract(self, data: dict) -> Any:
        """Return the value of the first candidate path present in ``data``.

        Args:
            data: The loaded sidecar JSON document.

        Returns:
            The value at the first candidate path fully present in ``data``,
            or ``None`` when no candidate resolves.
        """
        for candidate in self.candidates:
            node: Any = data
            found = True
            for segment in candidate:
                if isinstance(node, dict) and segment in node:
                    node = node[segment]
                else:
                    found = False
                    break
            if found:
                return node
        return None


def _canonicalise_rules(value: Any) -> tuple[tuple[Any, Any], ...]:
    """Canonicalise a ``rules`` value (author dict or already-built pairs) into a sorted tuple.

    Args:
        value: Either the author-facing ``{target: rule}`` dict, or a
            sequence of ``(target, rule)`` pairs (e.g. from a JSON round
            trip).

    Returns:
        A tuple of ``(target, rule)`` pairs sorted by target kind and path,
        so two equal declarations built in different orders compare equal.

    Raises:
        ValueError: Two pairs share the same target kind and path.
    """
    items = list(value.items()) if isinstance(value, dict) else [tuple(item) for item in value]
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for target, _rule in items:
        key = _target_key(target)
        if key in seen:
            raise ValueError(f"duplicate metadata target: {target!r}")
        seen.add(key)
    items.sort(key=lambda pair: _target_key(pair[0]))
    return tuple(items)


def _target_key(target: Any) -> tuple[str, tuple[str, ...]]:
    """Return ``(kind, path)`` for a target, whether it is a model instance or its raw dict form.

    Args:
        target: A ``MetadataTarget``/``ExtrasTarget`` instance, or the raw
            dict a JSON round trip hands the "before" validator ahead of
            pydantic's own field coercion.

    Returns:
        A hashable, orderable key identifying the target.
    """
    if isinstance(target, dict):
        return (target.get("kind", "metadata"), tuple(target.get("path", ())))
    return (target.kind, tuple(target.path))


def _maybe_warn_naive(value: Any, normalization: Normalization, *, tz: str) -> None:
    """Warn once when an absolute-time value states no zone and ``tz`` is the default.

    Args:
        value: The raw extracted value, before normalization.
        normalization: The rule's normalization.
        tz: The IANA timezone the caller passed to ``parse()``.
    """
    # Tested by value, as the table path tests it, so a metadata timestamp and
    # a table timestamp warn under the same condition.
    if tz != "UTC" or not isinstance(normalization, AbsoluteTimeNormalization):
        return
    if _TZ_MARKER_RE.search(str(value).strip()):
        return
    warnings.warn(
        "naive timestamp and tz defaulted to UTC; pass tz=... if the data was recorded in a different timezone",
        UserWarning,
        stacklevel=3,
    )


def _normalize_or_raise(
    field_path: tuple[str, ...],
    value: Any,
    normalization: Normalization,
    tz: str,
) -> Any:
    """Apply ``normalization.scalar`` to ``value``, naming the target on failure.

    Args:
        field_path: The rule's target path, for the error message.
        value: The raw extracted value.
        normalization: The rule's normalization.
        tz: IANA timezone applied where the normalization reads it.

    Returns:
        The normalized value, truncated to whole seconds for an absolute
        time normalization.

    Raises:
        ValueError: ``normalization.scalar`` raised; the message is
            prefixed with the dotted target path.
    """
    try:
        result = normalization.scalar(value, tz=tz)
    except ValueError as exc:
        raise ValueError(f"{'.'.join(field_path)}: {exc}") from exc
    # An absolute time normalization returns sub-second epoch seconds, which
    # the table path needs, and the BattINFO schema types a datetime field as
    # whole seconds. Truncate here rather than lose that precision upstream.
    if isinstance(normalization, AbsoluteTimeNormalization) and isinstance(result, float):
        result = int(result)
    return result


class MetadataParser(BaseModel):
    """Base / null metadata parser: never matches, extracts nothing.

    Subclasses override :meth:`matches` and :meth:`parse` and own all file I/O
    for their source type. Frozen so instances are hashable.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["base"] = "base"
    rules: tuple[tuple[_TargetUnion, RegexRule | JsonRule], ...] = Field(
        default=(),
        description="Target -> rule pairs. Empty for the base and BDF sidecar parsers.",
    )

    @model_validator(mode="before")
    @classmethod
    def _canonicalise(cls, data: Any) -> Any:
        """Canonicalise a ``rules`` dict into a sorted tuple before field validation.

        Args:
            data: The raw constructor input.

        Returns:
            ``data`` with ``rules`` canonicalised, when present.
        """
        if isinstance(data, dict) and "rules" in data:
            data = {**data, "rules": _canonicalise_rules(data["rules"])}
        return data

    def matches(self, path: str | Path) -> bool:
        """Return whether this parser recognises ``path`` as its source. Base: never.

        Args:
            path: Local file path or URL to check.

        Returns:
            False for base class (override in subclasses).
        """
        return False

    def parse(self, path: str | Path, tz: str = "UTC") -> Metadata:
        """Extract staged metadata from ``path``. Base: nothing.

        Args:
            path: Local file path or URL to parse.
            tz: IANA timezone; unused by the base parser.

        Returns:
            An empty ``Metadata`` for the base class (override in subclasses).
        """
        return Metadata()


class TxtPreambleParser(MetadataParser):
    """Reads metadata from the head bytes of the data file itself.

    ``magic`` tokens identify the format; ``encoding`` decodes the head bytes;
    ``rules`` maps a target to a :class:`RegexRule` whose ``group(1)`` is the
    extracted value. :meth:`parse` applies each rule over the decoded head
    lines (no separator / skip-rows sniffing), converts the matched text with
    the rule's own normalization, and stages it at the rule's target. The
    whole decoded head text is captured into ``raw``, verbatim.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["txt_preamble"] = "txt_preamble"  # type: ignore[assignment]
    magic: tuple[str | bytes, ...] = Field(
        default=(),
        description=(
            "Tokens that identify this format: str tokens are matched case-insensitively "
            "against decoded head text; bytes tokens are matched as raw byte substrings."
        ),
    )
    encoding: str = Field(default="utf-8", description="Codec used to decode head bytes before regex matching.")
    rules: tuple[tuple[_TargetUnion, RegexRule], ...] = Field(
        default=(),
        description="Target -> RegexRule pairs; each pattern's group(1) is normalized and staged at its target.",
    )

    def matches(self, path: str | Path) -> bool:
        """Return True when any magic token is found in the file's head bytes.

        Args:
            path: Local file path or URL to check.

        Returns:
            True if any magic token appears in the file head.
        """
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

    def parse(self, path: str | Path, tz: str = "UTC") -> Metadata:
        """Decode the head with ``encoding`` and apply each rule; first match per target.

        Args:
            path: Local file path or URL to parse.
            tz: IANA timezone applied to a naive absolute-time match. At its
                ``"UTC"`` default, a naive match warns once.

        Returns:
            A ``Metadata`` staged with every rule's normalized match,
            plus ``raw`` carrying the whole decoded head text.
        """
        head = read_head(path)
        text = head.decode(self.encoding, errors="replace")
        lines = text.splitlines()

        document: dict[str, Any] = {"raw": text}
        for target, rule in self.rules:
            matched = rule.extract(lines)
            if matched is None:
                continue
            _maybe_warn_naive(matched, rule.normalization, tz=tz)
            value = _normalize_or_raise(target.path, matched, rule.normalization, tz)
            target.stage(document, value)
        return Metadata.model_validate(document)


class JsonSidecarParser(MetadataParser):
    """Reads metadata from a JSON file adjacent to the data file (``path.with_suffix(".json")``).

    ``rules`` maps a target to a :class:`JsonRule`; :meth:`parse` resolves
    the first present candidate per rule, converts it with the rule's own
    normalization, and stages it at the rule's target. The whole loaded
    document is captured into ``raw``, verbatim; only an explicit
    ``ExtrasTarget`` rule can stage a value into ``extras``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["json_sidecar"] = "json_sidecar"  # type: ignore[assignment]
    rules: tuple[tuple[_TargetUnion, JsonRule], ...] = Field(
        default=(),
        description="Target -> JsonRule pairs; each rule's first present candidate is normalized and staged.",
    )

    def sidecar_path(self, path: str | Path) -> Path:
        """Return the sidecar JSON path for a data file.

        Args:
            path: Local file path to the data file.

        Returns:
            Path to the .json sidecar file (same name, .json suffix).
        """
        return Path(path).with_suffix(".json")

    def matches(self, path: str | Path) -> bool:
        """Return True when the ``.json`` sidecar file exists.

        Args:
            path: Local file path to the data file.

        Returns:
            True if the .json sidecar file exists.
        """
        return self.sidecar_path(path).exists()

    def parse(self, path: str | Path, tz: str = "UTC") -> Metadata:
        """Load the sidecar JSON, stage each rule's match, and capture the whole document into raw.

        Args:
            path: Local file path to the data file.
            tz: IANA timezone applied to a naive absolute-time match. At its
                ``"UTC"`` default, a naive match warns once.

        Returns:
            A ``Metadata`` staged with every rule's normalized match,
            plus ``raw`` carrying the whole loaded document.
        """
        sidecar = self.sidecar_path(path)
        if not sidecar.exists():
            return Metadata()
        with open(sidecar, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return Metadata()

        document: dict[str, Any] = {"raw": data}
        for target, rule in self.rules:
            matched = rule.extract(data)
            if matched is None:
                continue
            _maybe_warn_naive(matched, rule.normalization, tz=tz)
            value = _normalize_or_raise(target.path, matched, rule.normalization, tz)
            target.stage(document, value)

        return Metadata.model_validate(document)


class BdfSidecarParser(MetadataParser):
    """Restores the BDF-owned ``.metadata.json`` sidecar a prior ``save()`` wrote.

    Reads ``<stem>.metadata.json`` (``Path.with_suffix(".metadata.json")``) as
    the serialised ``Metadata`` structure. A ``save()`` sidecar already
    carries canonical values, so :meth:`parse` runs no normalization. ``raw``
    restores as an ordinary declared field, which keeps a repeated save and
    read from nesting a copy of the sidecar inside itself. A top-level key
    that ``Metadata`` does not declare raises out of ``model_validate``,
    naming that key, and a declared field with an invalid value raises the
    same way.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["bdf_sidecar"] = "bdf_sidecar"  # type: ignore[assignment]

    def sidecar_path(self, path: str | Path) -> Path:
        """Return the reserved BDF sidecar path for a data file.

        Args:
            path: Local file path to the data file.

        Returns:
            Path to the ``.metadata.json`` sidecar file.
        """
        return Path(path).with_suffix(".metadata.json")

    def matches(self, path: str | Path) -> bool:
        """Return True when the reserved ``.metadata.json`` sidecar exists.

        Args:
            path: Local file path to the data file.

        Returns:
            True if the ``.metadata.json`` sidecar file exists.
        """
        return self.sidecar_path(path).exists()

    def parse(self, path: str | Path, tz: str = "UTC") -> Metadata:
        """Restore the reserved sidecar verbatim, with no normalization.

        Args:
            path: Local file path to the data file.
            tz: Accepted for signature parity with the other parsers; unused.

        Returns:
            The restored ``Metadata``, or an empty one when no sidecar
            exists or it does not hold a JSON object.

        Raises:
            pydantic.ValidationError: The sidecar states a top-level key no
                ``Metadata`` field declares, or a recognised field an
                invalid value.
        """
        sidecar = self.sidecar_path(path)
        if not sidecar.exists():
            return Metadata()
        with open(sidecar, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return Metadata()

        return Metadata.model_validate(data)
