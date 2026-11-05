"""Tests of the BattINFO runtime schema set: its source, its download, and the check it gives."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import referencing.exceptions

import bdf.battinfo
from bdf._errors import BDFDefinitionError
from bdf.battinfo import BATTINFO_SOURCE, FIELD_SCHEMAS, BattinfoSchema, _download_battinfo, _update_snapshot_cli
from bdf.metadata import Metadata

from ._battinfo import (
    BUNDLED,
    CACHE_DIR,
    RAW_URL,
    FakeRawGitHub,
    bundled_docs,
    release_docs,
    serve_raw_github,
    write_release,
)

# Every test gets an empty cache in tmp_path, so no test sees a release that an earlier test fetched.
pytestmark = pytest.mark.usefixtures("battinfo_cache")

DRAFT = "https://json-schema.org/draft/2020-12/schema"
# A quantity the bundled release accepts: a value with its unit.
UNIT_TEXT = {"value": 25, "unit_text": "degC"}


# --- the source ---------------------------------------------------------------------------------


def test_the_bundled_set_is_the_release_the_version_file_names() -> None:
    # The set bdf loads at import is the packaged one, named by its VERSION file...
    schema = bdf.battinfo.BATTINFO_SCHEMA
    assert schema.version == (BUNDLED / "VERSION").read_text().strip()
    # ...and it holds a document for each of the six entities that Metadata carries.
    assert set(FIELD_SCHEMAS.values()) <= set(schema.docs)


def test_from_version_gives_a_new_set_and_caches_a_fetched_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fake GitHub serves release v0.9.0, which is neither bundled nor cached.
    serve_raw_github(monkeypatch, FakeRawGitHub(tag="v0.9.0"))
    bundled = bdf.battinfo.BATTINFO_SCHEMA

    fetched = BattinfoSchema.from_version("0.9.0")

    # The fetch gives that release and writes it into the cache, so a later call needs no request.
    assert fetched.version == "0.9.0"
    assert (tmp_path / "cache" / "battinfo-v0.9.0" / "VERSION").read_text() == "0.9.0\n"
    # Each call opens a new object, even for the bundled release: no caller can change another's set.
    assert BattinfoSchema.from_version(bundled.version) is not bundled
    # Loading another release does not replace the module's default set.
    assert bdf.battinfo.BATTINFO_SCHEMA is bundled


def test_a_release_that_breaks_the_metaschema_is_not_fetched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # "minimum" must be a number in JSON Schema, so this document is not a valid schema.
    docs = _fetchable_docs()
    docs["test.schema.json"]["minimum"] = "5"
    serve_raw_github(monkeypatch, FakeRawGitHub(docs, tag="v0.9.0"))

    # The download checks each document, so the fetch fails at once, with bdf's error naming the release...
    with pytest.raises(BDFDefinitionError, match="0.9.0") as info:
        BattinfoSchema.from_version("0.9.0")

    # ...and keeps the jsonschema error as the cause, so the user can see what is wrong in the document.
    assert isinstance(info.value.__cause__, jsonschema.exceptions.SchemaError)
    # Nothing is cached, so no later open meets the broken document.
    assert not (tmp_path / CACHE_DIR / "battinfo-v0.9.0").exists()


def test_the_bundled_documents_pass_the_metaschema() -> None:
    # The download checks each document against the metaschema; an open does not, so this test checks the bundle.
    for doc in bundled_docs().values():
        jsonschema.Draft202012Validator.check_schema(doc)


def test_the_update_command_writes_a_release_into_the_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A stand-in bundle at release 0.7.0, so the test does not change the real package data.
    bundle = write_release(tmp_path / "bundle", "0.7.0", release_docs("legacy_field"))
    monkeypatch.setattr(BATTINFO_SOURCE, "bundled", bundle)
    # The fake GitHub serves the real bundled documents under tag v0.8.0.
    serve_raw_github(monkeypatch, FakeRawGitHub(tag="v0.8.0"))
    monkeypatch.setattr(sys, "argv", ["bdf-update-battinfo", "0.8.0"])

    _update_snapshot_cli()

    # The bundle now holds exactly the documents of 0.8.0 (the 0.7.0 test document is gone)
    # and its VERSION file names 0.8.0.
    written = {p.relative_to(bundle / "schemas").as_posix() for p in (bundle / "schemas").rglob("*.json")}
    assert written == set(bundled_docs())
    assert (bundle / "VERSION").read_text() == "0.8.0\n"


def test_the_update_command_needs_a_version(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRawGitHub()
    serve_raw_github(monkeypatch, fake)
    # No version argument.
    monkeypatch.setattr(sys, "argv", ["bdf-update-battinfo"])

    # BattINFO publishes only pre-releases, so there is no latest BattINFO schema to default to: the
    # version is a required argument, and the command stops with a usage error...
    with pytest.raises(SystemExit) as info:
        _update_snapshot_cli()
    assert info.value.code == 2

    # ...before it makes any request.
    assert fake.urls == []


# --- the download -------------------------------------------------------------------------------


def _fetchable_docs() -> dict[str, dict[str, Any]]:
    """Six entity documents; two of them refer to one document, which refers up to another."""
    docs: dict[str, dict[str, Any]] = {path: {"$id": f"https://example.test/{path}"} for path in FIELD_SCHEMAS.values()}
    docs["test.schema.json"]["description"] = "µ"
    docs["test.schema.json"]["properties"] = {
        "a": {"$ref": "modules/common/quantity.schema.json"},
        "b": {"$ref": "#/$defs/B"},
    }
    docs["cell-instance.schema.json"]["properties"] = {"a": {"$ref": "modules/common/quantity.schema.json#/$defs/Q"}}
    docs["modules/common/quantity.schema.json"] = {"$id": "https://example.test/q", "$ref": "../units.schema.json"}
    docs["modules/units.schema.json"] = {"$id": "https://example.test/units"}
    return docs


def test_a_download_fetches_each_document_that_a_ref_reaches_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = _fetchable_docs()
    fake = FakeRawGitHub(docs)
    serve_raw_github(monkeypatch, fake)
    dest = tmp_path / "out"
    dest.mkdir()

    _download_battinfo("v0.9.0", dest, 30.0)

    # The download starts at the six entity documents and follows each $ref, so it also gets the
    # two documents that only a $ref names. quantity.schema.json is named twice but fetched once,
    # and the "#/$defs/B" ref stays inside its own document, so it fetches nothing.
    assert sorted(fake.urls) == sorted(RAW_URL.format(tag="v0.9.0", path=path) for path in docs)
    # Each document is written in one fixed form (sorted keys, ASCII only, so the "µ" is escaped),
    # so two downloads of one release give the same bytes.
    for path, doc in docs.items():
        text = (dest / "schemas" / path).read_text()
        assert text == json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


# A ref to another host, an absolute path, and a path above schemas/: each would make the download
# fetch or write outside the release's schemas folder.
@pytest.mark.parametrize("ref", ["https://example.com/q.schema.json", "/q.schema.json", "../q.schema.json"])
def test_a_download_refuses_a_ref_that_leaves_the_schemas(
    ref: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = _fetchable_docs()
    docs["test.schema.json"]["properties"] = {"a": {"$ref": ref}}
    serve_raw_github(monkeypatch, FakeRawGitHub(docs))

    with pytest.raises(ValueError):
        _download_battinfo("v0.9.0", tmp_path, 30.0)


def test_a_download_gives_each_request_the_time_that_is_left(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A clock that the test controls: each request "takes" 3 seconds.
    now = [0.0]
    fake = FakeRawGitHub(_fetchable_docs())

    def slow_get(url: str, **kwargs: Any) -> Any:
        try:
            return fake(url, **kwargs)
        finally:
            now[0] += 3.0

    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    serve_raw_github(monkeypatch, slow_get)

    # The timeout is a deadline for the whole download, not a limit for each request:
    # with 5 seconds and 3 seconds for each request, the third request would start after the deadline.
    with pytest.raises(TimeoutError):
        _download_battinfo("v0.9.0", tmp_path, 5.0)

    # The first request gets the full 5 seconds, the second the 2 seconds left, and no third request is made.
    assert fake.timeouts == [pytest.approx(5.0), pytest.approx(2.0)]


# --- the check ----------------------------------------------------------------------------------


def _subschemas(schema: Any, keywords: tuple[str, ...] = ()) -> Iterator[tuple[dict[str, Any], tuple[str, ...]]]:
    """Yield each subschema with the keywords on its path from the document, without following ``$ref``."""
    if not isinstance(schema, dict):
        return
    yield schema, keywords
    for key, value in schema.items():
        if key in ("properties", "patternProperties", "$defs", "dependentSchemas"):
            children = list(value.values())
        elif key in ("allOf", "anyOf", "oneOf", "prefixItems"):
            children = list(value)
        elif key in ("items", "additionalProperties", "not", "if", "then", "else", "contains", "propertyNames"):
            children = [value]
        else:
            continue
        for child in children:
            yield from _subschemas(child, (*keywords, key))


def test_the_bundle_keeps_the_rule_that_a_validator_may_ignore_a_records_required() -> None:
    # bdf checks partial records: its validator ignores "required" where it sits beside "properties"
    # (a record), and keeps it elsewhere. That is only correct while the schema uses "required" in
    # the ways listed below, so this test fails when a new release breaks one of them.
    for name, doc in bundled_docs().items():
        for sub, keywords in _subschemas(doc):
            where = f"{name} at {'/'.join(keywords)}"
            # A "required" with no "properties" picks a branch of a choice, so it must sit under
            # anyOf, oneOf or not, where the validator keeps it.
            if "required" in sub and "properties" not in sub:
                assert {"anyOf", "oneOf", "not"} & set(keywords), where
            # A record's "required" must not sit under a keyword whose result it would change if
            # ignored: an if, a not, a contains, or a dependentSchemas.
            if "required" in sub and "properties" in sub:
                assert not {"if", "not", "contains", "dependentSchemas"} & set(keywords), where
            # With "required" ignored, two record branches of one oneOf could both match a partial
            # record, and oneOf would then refuse it.
            assert sum("properties" in branch for branch in sub.get("oneOf", [])) < 2, where


def test_a_partial_record_is_valid_also_in_a_document_that_a_ref_reaches() -> None:
    # Two documents: the outer record requires "x", and "x" is a record in another document that requires "a".
    inner: dict[str, Any] = {"$id": "https://example.test/b.schema.json", "$schema": DRAFT, "required": ["a"]}
    inner["properties"] = {"a": {"type": "string"}}
    outer: dict[str, Any] = {"$id": "https://example.test/test.schema.json", "$schema": DRAFT, "required": ["x"]}
    outer["properties"] = {"x": {"$ref": "b.schema.json"}}
    schema = BattinfoSchema({"test.schema.json": outer, "b.schema.json": inner}, "0.1.0")

    # The inner record's "required" is ignored too, after the $ref: an empty "x" is accepted...
    assert schema.check("battinfo_test", {"x": {}}) == []
    # ...but the other keywords there still apply: "a" must be a string.
    assert len(schema.check("battinfo_test", {"x": {"a": 1}})) == 1


def test_a_quantity_with_a_value_and_no_unit_is_refused() -> None:
    # A quantity's "a value needs a unit" rule is a "required" inside a choice, not a record's
    # "required", so the partial-record rule must not switch it off.
    schema = bdf.battinfo.BATTINFO_SCHEMA
    test = {"test": {"conditions": {"ambient_temperature": {"value": 25}}}}

    assert any(
        "battinfo_test.test.conditions.ambient_temperature" in line for line in schema.check("battinfo_test", test)
    )


def test_a_map_keeps_the_keys_that_its_schema_declares() -> None:
    schema = bdf.battinfo.BATTINFO_SCHEMA

    # "conditions" is a map whose keys must match a pattern: a snake_case key is accepted...
    assert schema.check("battinfo_test", {"test": {"conditions": {"ambient_temperature": UNIT_TEXT}}}) == []
    # ...and a key with a space and capitals gives one line, which names the map and the bad key.
    (line,) = schema.check("battinfo_test", {"test": {"conditions": {"Bad Key": UNIT_TEXT}}})
    assert "battinfo_test.test.conditions" in line
    assert "Bad Key" in line


def test_a_choice_between_schemas_is_decided_as_a_whole() -> None:
    schema = bdf.battinfo.BATTINFO_SCHEMA

    # main_entity is either one object or a list of them; the list form is accepted.
    assert schema.check("battinfo_dataset", {"dataset": {"main_entity": [{"url": "data.csv"}]}}) == []
    # One object with an unknown key fails both branches. The line names the branch that came
    # closest (the object, with its unknown key), not "is not valid under any of the given schemas".
    (line,) = schema.check("battinfo_dataset", {"dataset": {"main_entity": {"url": "data.csv", "bogus": 1}}})
    assert "battinfo_dataset.dataset.main_entity" in line
    assert "'bogus' was unexpected" in line


def test_an_unknown_key_is_refused_where_the_schema_closes_the_object() -> None:
    # A misspelled field name: "test" does not allow other keys.
    (line,) = bdf.battinfo.BATTINFO_SCHEMA.check("battinfo_test", {"test": {"nmae": "cell-1"}})

    # The line names the object that refused the key, and the key.
    assert "battinfo_test.test:" in line
    assert "'nmae' was unexpected" in line


def test_an_empty_entity_needs_no_document_and_data_needs_one() -> None:
    # A set with no documents at all.
    schema = BattinfoSchema({}, "0.1.0")

    # An empty entity is not checked, so it needs no document...
    assert schema.check("battinfo_test", {}) == []
    # ...but an entity with data cannot be checked without its document, and the error names that document.
    with pytest.raises(BDFDefinitionError, match="test.schema.json"):
        schema.check("battinfo_test", {"test": {}})


def test_a_refused_value_can_be_assigned_and_check_names_it() -> None:
    # An entity is a plain dict, so assignment accepts a value the schema refuses.
    meta = Metadata()
    meta.battinfo_test = {"test": {"started_at": "yesterday"}}  # type: ignore[assignment]

    # check() is how the caller finds it: one line with the release, the path and the reason.
    release = bdf.battinfo.BATTINFO_SCHEMA.version
    assert meta.check() == [
        f"BattINFO {release} refuses battinfo_test.test.started_at: 'yesterday' is not of type 'integer'"
    ]


def test_a_partial_record_gives_no_lines() -> None:
    # A record with only a name is accepted, although the schema lists other fields as required.
    meta = Metadata()
    meta.battinfo_test = {"test": {"name": "cell-1"}}  # type: ignore[assignment]

    assert meta.check() == []


def test_a_set_that_cannot_be_used_raises() -> None:
    # The document refers to a document the set does not hold.
    doc = {"$id": "https://example.test/test.schema.json", "properties": {"test": {"$ref": "missing.schema.json"}}}
    meta = Metadata()
    meta.battinfo_test = {"test": {}}  # type: ignore[assignment]

    # A broken set is a fault of the set, not of the metadata: check() raises instead of giving a line.
    with pytest.raises(BDFDefinitionError, match="0.1.0 cannot check battinfo_test") as info:
        meta.check(BattinfoSchema({"test.schema.json": doc}, "0.1.0"))
    assert isinstance(info.value.__cause__, referencing.exceptions.Unresolvable)


def test_check_lists_refused_values_under_int_and_str_keys() -> None:
    # A mapping built in code, such as values by cycle number, can mix int and str keys.
    doc = {
        "$id": "https://example.test/test.schema.json",
        "properties": {"test": {"additionalProperties": {"type": "string"}}},
    }
    schema = BattinfoSchema({"test.schema.json": doc}, "0.1.0")
    lines = schema.check("battinfo_test", {"test": {1: 5, "x": 6}})
    assert [line.split(":")[0] for line in lines] == [
        "BattINFO 0.1.0 refuses battinfo_test.test.1",
        "BattINFO 0.1.0 refuses battinfo_test.test.x",
    ]


# Reaches GitHub, so it runs only when network tests are selected.
@pytest.mark.network
@pytest.mark.live_network
def test_a_fetched_bundled_release_is_the_bundle(tmp_path: Path) -> None:
    # Fetching the bundled release from upstream gives the same files, byte for byte, as the
    # package holds. This shows the bundle was made by the update command from that release.
    fetched = BATTINFO_SOURCE.write(tmp_path / "r", (BUNDLED / "VERSION").read_text().strip())

    def tree(root: Path) -> dict[str, bytes]:
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / "schemas").rglob("*") if p.is_file()}

    assert tree(fetched) == tree(BUNDLED)
