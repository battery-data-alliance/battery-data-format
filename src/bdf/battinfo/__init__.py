"""The BattINFO schema, and the check of metadata against it.

On disk, a BattINFO schema is a directory with a ``VERSION`` file and the JSON
Schema documents under ``schemas/``. bdf bundles one version
(:data:`BATTINFO_SCHEMA`) and loads other versions through
:data:`BATTINFO_SOURCE`, from the cache or from the BattINFO repository. A
``BattinfoSchema`` never changes after it is built, and each way to load one
returns a new object.
"""

from __future__ import annotations

import json
import posixpath
import re
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import jsonschema
from referencing import Registry
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

from bdf._definitions import DefinitionSource
from bdf._errors import BDFDefinitionError
from bdf.data import BATTINFO_BUNDLE

if TYPE_CHECKING:
    from bdf.metadata import Metadata

FIELD_SCHEMAS: dict[str, str] = {
    "battinfo_test": "test.schema.json",
    "battinfo_cell": "cell-instance.schema.json",
    "battinfo_channel": "channel.schema.json",
    "battinfo_equipment": "equipment.schema.json",
    "battinfo_test_protocol": "test-protocol.schema.json",
    "battinfo_dataset": "dataset.schema.json",
}
"""Each ``battinfo_*`` field of ``Metadata``, and the path under ``schemas/`` of the schema that checks it."""

_RAW_URL = "https://raw.githubusercontent.com/BIG-MAP/BattINFO/{tag}/assets/schemas/{path}"


def _required(
    validator: Any, required: Any, instance: Any, schema: Mapping[str, Any]
) -> Iterator[jsonschema.ValidationError]:
    """Check ``required`` except where it lists the fields of a record, so a partial record is valid."""
    if "properties" in schema:
        return
    yield from jsonschema.Draft202012Validator.VALIDATORS["required"](validator, required, instance, schema)


_Validator = jsonschema.validators.extend(jsonschema.Draft202012Validator, {"required": _required})


class BattinfoSchema:
    """The documents of one BattINFO schema. A set never changes after it is built."""

    version: str
    docs: dict[str, dict[str, Any]]

    def __init__(self, docs: Mapping[str, dict[str, Any]], version: str) -> None:
        """Hold the documents of one BattINFO schema.

        Args:
            docs: Each document's path under ``schemas/`` (``/`` separators) and its parsed JSON.
                The set keeps each document without its ``$schema``, so that a ``$ref``
                into another document keeps bdf's validator.
            version: The BattINFO version of the documents, such as ``"0.8.0rc2"``.

        Raises:
            ValueError: If a document has no string ``$id``.
        """
        self.version = version
        self.docs = {path: {k: v for k, v in doc.items() if k != "$schema"} for path, doc in docs.items()}
        for path, doc in self.docs.items():
            if not isinstance(doc.get("$id"), str):
                raise ValueError(f"BattINFO document {path} has no string $id")
        self._registry: Registry = Registry().with_resources(
            (doc["$id"], DRAFT202012.create_resource(doc)) for doc in self.docs.values()
        )
        self._validators: dict[str, Any] = {}

    def __repr__(self) -> str:
        return f"BattinfoSchema({self.version!r})"

    @classmethod
    def build(cls) -> BattinfoSchema:
        """Load the bundled BattINFO schema, with no network access.

        Raises:
            BDFDefinitionError: If the bundled BattINFO schema is missing or unreadable.
        """
        return _bundled_schema(BATTINFO_SOURCE.get)

    @classmethod
    def from_version(cls, version: str, *, refresh: bool = False) -> BattinfoSchema:
        """Load BattINFO schema ``version`` from the bundle, the cache, or upstream, as a new set.

        Args:
            version: The BattINFO version, such as ``"0.8.0rc2"``.
            refresh: If True, fetch the schemas again.

        Raises:
            BDFDefinitionError: If that BattINFO schema cannot be loaded.
        """
        return BATTINFO_SOURCE.get(version, refresh=refresh)

    def check(self, field: str, data: Mapping[str, Any]) -> list[str]:
        """Return one line for each value in ``data`` that the schema for ``field`` refuses.

        Args:
            field: A ``battinfo_*`` field of ``Metadata``, such as ``"battinfo_test"``.
            data: The value of that field.

        Raises:
            KeyError: If ``field`` is not a ``battinfo_*`` field.
            BDFDefinitionError: If ``data`` is not empty and the set cannot check it: it has
                no schema for ``field``, a ``$ref`` names no document, or a ``pattern`` does
                not compile in Python's ``re``. A set that bdf downloads or bundles has none
                of these faults; one built in code, or put in the cache by hand, can.
            RecursionError: If references loop.
        """
        path = FIELD_SCHEMAS[field]
        if not data:
            return []
        validator = self._validators.get(field)
        if validator is None:
            if path not in self.docs:
                raise BDFDefinitionError(f"BattINFO {self.version} has no document {path} for {field}")
            validator = self._validators[field] = _Validator(self.docs[path], registry=self._registry)
        try:
            # Sorted by path; a key is put with its type, as a mapping built in code can mix int and str keys.
            errors = sorted(
                validator.iter_errors(data), key=lambda e: [(isinstance(k, str), k) for k in e.absolute_path]
            )
        except (re.error, Unresolvable) as exc:
            raise BDFDefinitionError(f"BattINFO {self.version} cannot check {field}: {exc}") from exc
        lines = []
        for error in errors:
            # A combinator's own message names no key: name its most relevant branch error.
            while error.context:
                error = min(error.context, key=jsonschema.exceptions.relevance)
            where = ".".join(str(p) for p in (field, *error.absolute_path))
            lines.append(f"BattINFO {self.version} refuses {where}: {error.message}")
        return lines


def _open_battinfo(path: Path) -> BattinfoSchema:
    """Open a BattINFO schema directory: the source's open hook.

    The documents are not checked against the metaschema here: the download checks each
    one before it is written, and a unit test checks the bundle.

    Raises:
        referencing.exceptions.Unresolvable: If a ``$ref`` names nothing in the schema.
    """
    root = path / "schemas"
    docs = {
        p.relative_to(root).as_posix(): json.loads(p.read_text(encoding="utf-8")) for p in root.rglob("*.schema.json")
    }
    schema = BattinfoSchema(docs, (path / "VERSION").read_text(encoding="utf-8").strip())
    # Resolve every $ref now, so a schema with a broken one fails to load rather than fail in a check.
    for doc in schema.docs.values():
        resolver = schema._registry.resolver(base_uri=doc["$id"])
        for ref in _refs(doc):
            resolver.lookup(ref)
    return schema


def _refs(node: Any) -> Iterator[str]:
    """Yield every ``$ref`` string in a document."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from _refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _refs(item)


def _download_battinfo(tag: str, dest: Path, timeout: float) -> None:
    """Fetch the six schemas in ``FIELD_SCHEMAS`` at ``tag``, and every document that their ``$ref`` reach.

    Raises:
        jsonschema.exceptions.SchemaError: If a document breaks the Draft 2020-12 metaschema.
        ValueError: If a ``$ref`` is a URL, is absolute, or leaves ``assets/schemas/``.
        TimeoutError: If ``timeout`` seconds pass before the last request.
    """
    import requests

    deadline = time.monotonic() + timeout
    pending = list(FIELD_SCHEMAS.values())
    seen = set(pending)
    # One session, so that every document comes over the same connection.
    with requests.Session() as session:
        while pending:
            rel = pending.pop(0)
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"BattINFO {tag} did not arrive within {timeout} s")
            response = session.get(_RAW_URL.format(tag=tag, path=rel), timeout=left)
            response.raise_for_status()
            doc = json.loads(response.content)
            _Validator.check_schema(doc)
            out = dest / "schemas" / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
            for ref in _refs(doc):
                target = ref.partition("#")[0]
                if not target:
                    continue
                joined = posixpath.normpath(posixpath.join(posixpath.dirname(rel), target))
                if urlsplit(target).scheme or target.startswith("/") or joined.startswith(".."):
                    raise ValueError(f"{rel} refers to {ref!r}, outside the BattINFO schema")
                if joined not in seen:
                    seen.add(joined)
                    pending.append(joined)


BATTINFO_SOURCE: DefinitionSource[BattinfoSchema] = DefinitionSource(
    "battinfo",
    bundled=BATTINFO_BUNDLE,
    tag_prefix="v",
    download=_download_battinfo,
    open=_open_battinfo,
    # BattINFO publishes only pre-releases, so it has no latest version: a version is always named.
)


def _bundled_schema(get: Callable[..., BattinfoSchema]) -> BattinfoSchema:
    """Open the bundled BattINFO schema with ``get`` (``BATTINFO_SOURCE.get`` or ``.get_opened``), offline.

    Raises:
        BDFDefinitionError: If the bundled BattINFO schema is missing or unreadable.
    """
    try:
        return get(BATTINFO_SOURCE.get_bundled_version() or "", fetch=False)
    except BDFDefinitionError as exc:
        raise BDFDefinitionError(
            f"The bundled BattINFO schema at {BATTINFO_SOURCE.bundled} is missing or unreadable: {exc}. "
            "Restore it from git or reinstall bdf; bdf-update-battinfo VERSION writes a BattINFO schema there."
        ) from exc


BATTINFO_SCHEMA: BattinfoSchema = _bundled_schema(BATTINFO_SOURCE.get_opened)
"""The bundled BattINFO schema: the source's opened bundle. Nothing in bdf rebinds or changes it after import."""


def _battinfo_version(metadata: Metadata) -> str | None:
    """Return the BattINFO schema version that ``metadata``'s ``battinfo_*`` fields are checked against.

    That is the bundled one; None where every ``battinfo_*`` field is empty.
    """
    if not any(getattr(metadata, field) for field in FIELD_SCHEMAS):
        return None
    return BATTINFO_SCHEMA.version


def _update_snapshot_cli() -> None:
    """Run ``bdf-update-battinfo VERSION``, which writes a BattINFO schema into the bundle."""
    BATTINFO_SOURCE.run_cli("bdf-update-battinfo")
