"""Contract test: field names BDF writes are contract-tested against the
bundled upstream BattINFO schemas (``src/bdf/data/battinfo/``).

Every field path declared on the ``bdf.battinfo.generated`` entity records must
resolve to a property in the corresponding bundled upstream schema, so a
generated package that drifted from the bundle it claims to render fails this
test with a one-line fix (refresh the bundle and the package with
``scripts/update_battinfo.py``).
"""

from __future__ import annotations

import ast
import inspect
import json
import sys
import types
import typing
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pydantic
import pytest

import bdf.metadata
from bdf.battinfo.generated.cell_instance_schema import BattinfoCellInstance
from bdf.battinfo.generated.channel_schema import BattinfoChannelInstance
from bdf.battinfo.generated.dataset_schema import BattinfoDatasetSchemaOrgAligned
from bdf.battinfo.generated.equipment_schema import BattinfoEquipmentInstance
from bdf.battinfo.generated.test_protocol_schema import BattinfoTestProtocol
from bdf.battinfo.generated.test_schema import BattinfoTest
from bdf.metadata import Metadata

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.update_battinfo import (  # noqa: E402
    DEFAULT_BRANCH,
    compare,
    fetch_schemas,
    load_managed,
    load_schema,
    load_version,
    main,
    normalize,
    resolve_ref,
    update,
    write_bundle,
)


def _load_schema(name: str) -> dict[str, Any]:
    """Load a bundled upstream schema by file name."""
    return load_schema(name)


def _resolve(doc: dict[str, Any], node: dict[str, Any]) -> dict[str, Any] | None:
    """Follow a single local ``$ref`` (``#/$defs/Name``) within a schema doc.

    Args:
        doc: The full schema document, providing the ``$defs`` lookup table.
        node: The property (or ``items``) mapping to resolve; returned
            unchanged if it carries no ``$ref``.

    Returns:
        The referenced ``$defs`` entry, ``node`` itself if unreferenced, or
        None for a reference into another schema file, which the walk does
        not follow: only local ``$defs`` targets are resolved here.
    """
    ref = node.get("$ref")
    if ref is None:
        return node
    prefix = "#/$defs/"
    if not ref.startswith(prefix):
        return None
    return doc["$defs"][ref[len(prefix) :]]


def _unwrap_optional(annotation: Any) -> Any:
    """Strip ``Optional[T]`` / ``T | None`` down to ``T``; pass through otherwise.

    Args:
        annotation: A pydantic field annotation, possibly a two-armed
            union with ``None``.

    Returns:
        The non-``None`` member of a two-armed union, or ``annotation``
        unchanged.
    """
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _list_item_type(annotation: Any) -> Any | None:
    """Return ``T`` for ``list[T]`` / ``Optional[list[T]]`` annotations, else None.

    Args:
        annotation: A pydantic field annotation to inspect.

    Returns:
        The list's item type, or ``None`` if ``annotation`` is not a list
        (optionally wrapped in ``Optional``) with exactly one type argument.
    """
    annotation = _unwrap_optional(annotation)
    if typing.get_origin(annotation) is list:
        args = typing.get_args(annotation)
        return args[0] if len(args) == 1 else None
    return None


def _is_model(annotation: Any) -> bool:
    """Return whether ``annotation`` is a pydantic model class.

    Args:
        annotation: A (possibly unwrapped) field annotation to test.

    Returns:
        ``True`` if ``annotation`` is a class carrying pydantic's
        ``model_fields``. A ``RootModel`` is a typed leaf rather than a
        section, so it returns ``False``: its single ``root`` field is
        pydantic bookkeeping and names no upstream property.
    """
    return (
        isinstance(annotation, type)
        and hasattr(annotation, "model_fields")
        and not issubclass(annotation, pydantic.RootModel)
    )


def _assert_fields_resolve(
    model_cls: Any,
    doc: dict[str, Any],
    properties: dict[str, Any],
    path: str,
    seen: frozenset[type] = frozenset(),
) -> set[str]:
    """Recursively assert every field declared on ``model_cls`` has a matching
    upstream property, following nested sections and list-of-model fields into
    the corresponding nested/`items` property tree.

    Args:
        model_cls: The pydantic model class whose declared fields are walked.
        doc: The full schema document, providing the ``$defs`` lookup table
            used to resolve ``$ref`` properties.
        properties: The upstream schema's property mapping at this nesting
            level.
        path: The dotted field path walked so far; empty at the root.
        seen: The model classes already open on this branch. A recursive
            definition (a protocol step that holds its own sub-steps) stops
            the walk at its second visit, rather than descend forever.

    Returns:
        The set of full field paths walked, including every nested section
        and list-item path reached. A model declaring no fields walks
        nothing, so callers must check the returned set against an expected
        minimum rather than treat a clean pass alone as proof of coverage.
    """
    if model_cls in seen:
        return set()
    seen = seen | {model_cls}

    walked: set[str] = set()
    for field_name, field_info in model_cls.model_fields.items():
        full_path = f"{path}.{field_name}" if path else field_name
        assert field_name in properties, f"{full_path} has no matching upstream property"
        walked.add(full_path)

        annotation = _unwrap_optional(field_info.annotation)
        item_type = _list_item_type(field_info.annotation)
        if _is_model(annotation):
            sub = _resolve(doc, properties[field_name])
            if sub is not None:
                walked |= _assert_fields_resolve(annotation, doc, sub.get("properties", {}), full_path, seen)
        elif item_type is not None and _is_model(item_type):
            sub = _resolve(doc, properties[field_name])
            items = _resolve(doc, sub.get("items", {})) if sub is not None else None
            if items is not None:
                walked |= _assert_fields_resolve(item_type, doc, items.get("properties", {}), f"{full_path}[]", seen)
    return walked


def test_test_record_fields_resolve_upstream() -> None:
    """Every field path the test record declares (schema_version, test.*,
    provenance and its nested contents) is a valid location in the pinned
    test.schema.json, and the walk covers at least the test facts a data file
    states."""

    doc = _load_schema("test.schema.json")

    walked = _assert_fields_resolve(BattinfoTest, doc, doc["properties"], "")

    expected = {
        "schema_version",
        "test.name",
        "test.instrument_name",
        "test.protocol_name",
        "test.started_at",
        "test.ended_at",
        "test.conditions",
        "test.conformance",
        "provenance.source_file",
        "provenance.source_type",
    }
    assert expected <= walked


def test_cell_record_fields_resolve_upstream() -> None:
    """Every field path the cell record declares is a valid location in the
    pinned cell-instance.schema.json, and the walk covers at least the cell
    naming facts a data file states."""

    doc = _load_schema("cell-instance.schema.json")

    walked = _assert_fields_resolve(BattinfoCellInstance, doc, doc["properties"], "")

    expected = {
        "cell_instance.name",
        "cell_instance.serial_number",
        "cell_instance.batch_id",
        "provenance.source_file",
    }
    assert expected <= walked


def test_channel_record_fields_resolve_upstream() -> None:
    """Every field path the channel record declares is a valid location in the
    pinned channel.schema.json, and the walk covers at least the vendor channel
    number and its label."""

    doc = _load_schema("channel.schema.json")

    walked = _assert_fields_resolve(BattinfoChannelInstance, doc, doc["properties"], "")

    expected = {"channel.index", "channel.label", "provenance.source_file"}
    assert expected <= walked


def test_equipment_record_fields_resolve_upstream() -> None:
    """Every field path the equipment record declares is a valid location in
    the pinned equipment.schema.json, and the walk covers at least the
    equipment naming facts a data file states."""

    doc = _load_schema("equipment.schema.json")

    walked = _assert_fields_resolve(BattinfoEquipmentInstance, doc, doc["properties"], "")

    expected = {
        "equipment.name",
        "equipment.serial_number",
        "equipment.location",
        "provenance.source_file",
    }
    assert expected <= walked


def test_test_protocol_record_fields_resolve_upstream() -> None:
    """Every field path the test-protocol record declares is a valid location
    in the pinned test-protocol.schema.json, and the walk covers at least the
    spec naming and the artifact links."""

    doc = _load_schema("test-protocol.schema.json")

    walked = _assert_fields_resolve(BattinfoTestProtocol, doc, doc["properties"], "")

    expected = {
        "test_spec.name",
        "test_spec.identifier",
        "artifacts",
        "provenance.source_file",
    }
    assert expected <= walked


def test_dataset_record_fields_resolve_upstream() -> None:
    """Every field path the dataset record declares is a valid location in the
    pinned dataset.schema.json, and the walk covers at least the identity and
    the file-distribution facts the record exists to carry (#92)."""

    doc = _load_schema("dataset.schema.json")

    walked = _assert_fields_resolve(BattinfoDatasetSchemaOrgAligned, doc, doc["properties"], "")

    expected = {
        "schema_version",
        "dataset.name",
        "dataset.access_url",
        "provenance.source_type",
    }
    assert expected <= walked


def test_no_runtime_battinfo_import() -> None:
    """BDF must never import battinfo at runtime, keeping the
    battinfo[processing] -> batterydf dependency direction acyclic: the
    module source carries no import naming it, and a fresh record's
    ``to_dict()`` serialisation carries no trace of it either."""
    tree = ast.parse(inspect.getsource(bdf.metadata))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] != "battinfo" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module is None or node.module.split(".")[0] != "battinfo"

    dumped = Metadata().to_dict()
    assert isinstance(dumped, dict)
    assert "battinfo" not in json.dumps(dumped)


def test_read_metadata_exposes_no_importer_adapter() -> None:
    """The serialised ``Metadata`` is the handoff contract itself: the
    only public method BDF adds is ``to_dict()`` — no ``to_battinfo`` or
    other adapter producing importer keyword arguments."""

    def _public_methods(cls: type) -> set[str]:
        return {name for name in dir(cls) if not name.startswith("_") and callable(getattr(cls, name))}

    own = _public_methods(Metadata) - _public_methods(pydantic.BaseModel)
    assert own == {"to_dict"}


# ---------------------------------------------------------------------------
# Bundled schema snapshot (package data), mirroring the ontology snapshot
# (``bdf.spec.ColumnOntology.build`` / ``.get_snapshot``)
# ---------------------------------------------------------------------------

# The eight files bdf.battinfo.generated is rendered from, keyed by their
# bundle-relative path, mirroring scripts.update_battinfo.MANAGED_SCHEMA_PATHS.
_MOCK_MANAGED_SCHEMA_CONTENT: dict[str, bytes] = {
    "test.schema.json": b'{"title": "test schema", "properties": {}}',
    "cell-instance.schema.json": b'{"title": "cell-instance schema", "properties": {}}',
    "cell-canonical.schema.json": b'{"title": "cell-canonical schema", "properties": {}}',
    "channel.schema.json": b'{"title": "channel schema", "properties": {}}',
    "equipment.schema.json": b'{"title": "equipment schema", "properties": {}}',
    "test-protocol.schema.json": b'{"title": "test-protocol schema", "properties": {}}',
    "dataset.schema.json": b'{"title": "dataset schema", "properties": {}}',
    "modules/common/quantitative-properties.schema.json": b'{"title": "quantitative-properties schema"}',
    "modules/common/quantity.schema.json": b'{"title": "quantity schema", "properties": {}}',
}


def test_bundle_loads_the_managed_schemas_and_version_stamp() -> None:
    """The package-data loader reads the bundle under ``bdf/data/battinfo/``
    and exposes every managed schema file that ``bdf.battinfo.generated``
    is rendered from, and a ``VERSION`` stamp carrying a commit ref."""
    managed = load_managed()

    assert set(managed) == set(_MOCK_MANAGED_SCHEMA_CONTENT)
    for rel_name, schema in managed.items():
        assert isinstance(schema, dict) and schema, f"bundled schema {rel_name} is empty"
    assert load_version()["ref"], "VERSION stamp carries no commit ref"


def test_load_schema_reads_one_managed_file_and_rejects_any_other() -> None:
    """The by-name loader serves a managed file and refuses a name the bundle
    does not manage, so a typo fails at the call rather than at the read."""
    assert load_schema("test.schema.json")["title"]

    with pytest.raises(KeyError):
        load_schema("not-a-managed-file.schema.json")


def _mock_fetch_responses(overrides: dict[str, bytes] | None = None) -> list[Mock]:
    """Return one mocked HTTP response per managed schema file, in fetch order.

    Args:
        overrides: Content to serve instead of the default for the named files.
            Use it to stand in for an upstream edit.
    """
    overrides = overrides or {}
    responses = []
    for rel_name, content in _MOCK_MANAGED_SCHEMA_CONTENT.items():
        response = Mock()
        response.content = overrides.get(rel_name, content)
        response.raise_for_status = Mock()
        responses.append(response)
    return responses


def _bundle_the_mock_schemas(tmp_path: Path) -> Path:
    """Write the mock schema content as a bundle and return its schema directory."""
    return write_bundle(dict(_MOCK_MANAGED_SCHEMA_CONTENT), "0" * 40, tmp_path / "bundle") / "schemas"


def test_update_script_fetches_writes_and_stamps(tmp_path: Path) -> None:
    """The update script fetches the eight managed schema files at a given ref
    over a mocked transport (ontology-snapshot style), writes them atomically
    under ``schemas/``, stamps ``VERSION`` with that ref, and regenerates the
    model package (mocked here, covered on its own by
    ``tests/unit/test_battinfo_generated.py``)."""
    dest = tmp_path / "battinfo"
    ref = "deadbeef1234"
    with (
        patch("requests.get", side_effect=_mock_fetch_responses()) as mock_get,
        patch("scripts.update_battinfo.regenerate") as mock_regenerate,
    ):
        result = update(ref, dest=dest)
        assert mock_get.call_count == len(_MOCK_MANAGED_SCHEMA_CONTENT)

    for call in mock_get.call_args_list:
        url = call.args[0] if call.args else call.kwargs.get("url")
        assert url is not None and ref in url, f"fetch URL {url!r} does not carry the requested ref {ref!r}"

    assert result == dest
    for rel_name, content in _MOCK_MANAGED_SCHEMA_CONTENT.items():
        assert json.loads((dest / "schemas" / rel_name).read_bytes()) == json.loads(content)
    assert not list(dest.rglob("*.tmp")), "the update left a temporary file behind"

    version_text = (dest / "VERSION").read_text(encoding="utf-8")
    assert f"ref={ref}" in version_text

    mock_regenerate.assert_called_once_with()


def test_write_bundle_normalizes_what_it_writes(tmp_path: Path) -> None:
    """The bundle holds the upstream document and not the upstream byte layout.
    Every file lands with sorted keys, a two-space indent, and a trailing
    newline, so an upstream commit that only reorders keys produces no diff."""
    schemas = _bundle_the_mock_schemas(tmp_path)

    for rel_name, content in _MOCK_MANAGED_SCHEMA_CONTENT.items():
        written = (schemas / rel_name).read_bytes()
        assert written == normalize(content)
        assert json.loads(written) == json.loads(content)
        assert written.endswith(b"\n")

    text = (schemas / "test.schema.json").read_text(encoding="utf-8")
    assert text.index('"properties"') < text.index('"title"'), "write_bundle did not sort the keys"
    assert '\n  "properties"' in text, "write_bundle did not indent by two spaces"


def test_write_bundle_is_stable_across_an_upstream_reorder(tmp_path: Path) -> None:
    """Two upstream layouts of one document write the same bytes."""
    reordered = {
        rel_name: json.dumps(dict(reversed(list(json.loads(content).items()))), indent=8).encode()
        for rel_name, content in _MOCK_MANAGED_SCHEMA_CONTENT.items()
    }
    first = _bundle_the_mock_schemas(tmp_path / "first")
    second = write_bundle(reordered, "0" * 40, tmp_path / "second" / "bundle") / "schemas"

    for rel_name in _MOCK_MANAGED_SCHEMA_CONTENT:
        assert (first / rel_name).read_bytes() == (second / rel_name).read_bytes()


def test_compare_reports_nothing_when_upstream_matches_the_bundle(tmp_path: Path) -> None:
    """The sync trigger reads content, not the pinned commit. An upstream commit
    that leaves the eight managed files alone reports no difference, so an
    unrelated change upstream opens no pull request."""
    source = _bundle_the_mock_schemas(tmp_path)

    with patch("requests.get", side_effect=_mock_fetch_responses()):
        assert compare("main", source=source) == {}


def test_compare_reports_only_the_files_whose_content_changed(tmp_path: Path) -> None:
    """A comparison names the managed files that moved and no others, so the
    sync pull request states what actually changed upstream."""
    source = _bundle_the_mock_schemas(tmp_path)
    edited = b'{"title": "test schema", "properties": {"schema_version": {"type": "string"}}}'

    with patch("requests.get", side_effect=_mock_fetch_responses({"test.schema.json": edited})):
        differing = compare("main", source=source)

    assert set(differing) == {"test.schema.json"}
    assert differing["test.schema.json"] == edited


def test_compare_ignores_a_reformat_that_leaves_the_content_equal(tmp_path: Path) -> None:
    """The comparison parses before it compares. An upstream commit that only
    reindents a managed file reports no difference."""
    source = _bundle_the_mock_schemas(tmp_path)
    reformatted = b'{\n  "title": "test schema",\n  "properties": {}\n}\n'

    with patch("requests.get", side_effect=_mock_fetch_responses({"test.schema.json": reformatted})):
        assert compare("main", source=source) == {}


def test_resolve_ref_returns_the_commit_a_branch_names() -> None:
    """The stamp records a commit and never a branch, because a branch moves.
    The resolution turns the branch the sync job reads into that commit."""
    sha = "a" * 40
    response = Mock(raise_for_status=Mock())
    response.json.return_value = {"sha": sha}

    with patch("requests.get", return_value=response) as mock_get:
        assert resolve_ref("main") == sha

    url = mock_get.call_args.args[0]
    assert url.endswith("/commits/main"), f"resolution URL {url!r} does not name the requested ref"


def test_resolve_ref_raises_runtime_error_when_the_response_carries_no_sha() -> None:
    """A resolution that returns no commit fails at the call. A stamp written
    from a missing SHA would name a ref that no test can refetch."""
    response = Mock(raise_for_status=Mock())
    response.json.return_value = {}

    with patch("requests.get", return_value=response), pytest.raises(RuntimeError, match="no commit SHA"):
        resolve_ref("main")


def test_check_mode_fails_and_writes_nothing_when_the_bundle_is_stale(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--check`` reports the files that moved, exits non-zero so the pull
    request check fails, and never touches the bundle. The exit code matches
    ``--check`` on ``datamodel-codegen`` and on ``scripts/generate_docs.py``."""
    sha = "b" * 40
    with (
        patch("scripts.update_battinfo.resolve_ref", return_value=sha),
        patch("scripts.update_battinfo.compare", return_value={"test.schema.json": b"{}"}),
        patch("scripts.update_battinfo.write_bundle") as mock_write,
        pytest.raises(SystemExit) as excinfo,
    ):
        main(["--check"])

    assert excinfo.value.code != 0
    assert sha in str(excinfo.value), "the failure does not name the ref to refresh from"
    assert capsys.readouterr().out.splitlines() == [
        "changed=true",
        f"ref={sha}",
        "files=test.schema.json",
    ]
    mock_write.assert_not_called()


def test_check_mode_reports_no_change_when_the_bundle_is_current(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A current bundle reports ``changed=false`` with an empty file list and
    exits zero, so the pull request check passes."""
    sha = "c" * 40
    with (
        patch("scripts.update_battinfo.resolve_ref", return_value=sha),
        patch("scripts.update_battinfo.compare", return_value={}),
    ):
        main(["--check"])

    assert capsys.readouterr().out.splitlines() == ["changed=false", f"ref={sha}", "files="]


def test_update_mode_stamps_the_commit_a_branch_resolves_to() -> None:
    """A caller that names a branch still stamps a commit, because the entry
    point resolves the ref before it writes."""
    sha = "d" * 40
    with (
        patch("scripts.update_battinfo.resolve_ref", return_value=sha) as mock_resolve,
        patch("scripts.update_battinfo.update") as mock_update,
    ):
        main(["main"])

    mock_resolve.assert_called_once_with("main")
    mock_update.assert_called_once_with(sha)


@pytest.mark.network
@pytest.mark.live_network
@pytest.mark.freshness
def test_bundled_snapshot_matches_its_declared_release(tmp_path: Path) -> None:
    """The stamp never lies about the bundle: fetching the eight managed
    schema files at the ref the bundled ``VERSION`` stamp declares reproduces
    the bundled files exactly."""
    ref = load_version()["ref"]
    fresh_dir = write_bundle(fetch_schemas(ref), ref, tmp_path / "fresh")

    assert load_managed(fresh_dir / "schemas") == load_managed()


@pytest.mark.network
@pytest.mark.live_network
@pytest.mark.freshness
def test_bundle_matches_the_upstream_default_branch() -> None:
    """The bundle never ages silently. The stamp alone cannot catch that: it
    pins a commit, and a refetch at a commit reproduces the bundle however far
    upstream moves. This test reads upstream ``main`` instead.

    The comparison reads parsed content, so an upstream commit that reformats
    one of the eight files, or that leaves ``assets/schemas/`` alone, keeps
    this green. Only a real schema change fails it."""
    ref = resolve_ref(DEFAULT_BRANCH)
    differing = compare(ref)

    assert not differing, (
        f"the bundled BattINFO schemas differ from {DEFAULT_BRANCH} at {ref}: "
        f"{', '.join(sorted(differing))}. Run scripts/update_battinfo.py {ref} to refresh the bundle."
    )


def test_missing_bundle_raises_runtime_error_naming_the_update_script(tmp_path: Path) -> None:
    """A missing schema directory raises ``RuntimeError`` naming the update
    script, as the ontology snapshot does for its own bundle."""
    with pytest.raises(RuntimeError, match="update_battinfo"):
        load_managed(tmp_path / "does-not-exist")


def test_unparseable_bundle_raises_runtime_error_naming_the_update_script(tmp_path: Path) -> None:
    """A schema directory whose file is not valid JSON raises ``RuntimeError``
    naming the update script."""
    schemas = tmp_path / "schemas"
    (schemas / "modules" / "common").mkdir(parents=True)
    (schemas / "test.schema.json").write_text("not valid json", encoding="utf-8")
    for rel_name in _MOCK_MANAGED_SCHEMA_CONTENT:
        if rel_name != "test.schema.json":
            (schemas / rel_name).write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="update_battinfo"):
        load_managed(schemas)

    with pytest.raises(RuntimeError, match="update_battinfo"):
        load_version(tmp_path)


def test_fetch_raises_http_error_on_fetch_failure() -> None:
    """The fetch propagates ``requests.HTTPError`` when a schema fetch fails."""
    import requests

    response = Mock()
    response.raise_for_status = Mock(side_effect=requests.HTTPError("fetch failed"))

    with patch("requests.get", return_value=response), pytest.raises(requests.HTTPError):
        fetch_schemas("deadbeef1234")


def test_fetch_raises_runtime_error_on_invalid_json() -> None:
    """The fetch raises ``RuntimeError`` when fetched content is not valid JSON."""
    response = Mock()
    response.content = b"not valid json"
    response.raise_for_status = Mock()

    with patch("requests.get", return_value=response), pytest.raises(RuntimeError):
        fetch_schemas("deadbeef1234")
