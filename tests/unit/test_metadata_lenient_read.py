"""Lenient metadata validation tests.

``Metadata.from_dict`` restores a document a newer bdf (or newer BattINFO
bundle) wrote: what this version does not understand is kept where it was and
dumps back unchanged. Everywhere else (e.g. assigning values later) an unknown
key or invalid value raises.
"""

from __future__ import annotations

import copy
import warnings
from typing import Any

import pytest
from pydantic import ValidationError

from bdf import BDFMetadataWarning, Metadata
from bdf.battinfo.generated.test_protocol_schema import Step, Termination
from bdf.battinfo.generated.test_schema import Test

VALID: dict[str, Any] = {
    "battinfo_test": {
        "test": {"instrument_name": "Neware BTS4000", "started_at": 1718600000, "status": "completed"},
        "contributor": [{"name": "Ada", "url": "https://example.org/ada"}],
    },
    "battinfo_test_protocol": {
        "method": [
            {
                "mode": "cc",
                "direction": "discharge",
                "termination": [{"direction": "below", "quantity": "voltage", "unit": "V", "value": 2.5}],
            }
        ]
    },
    "bdf": {"bdf_version": "0.2.0rc3"},
}


def _future() -> dict[str, Any]:
    """A future Metadata record may have new keys, records, enum values, and constraints."""
    doc = copy.deepcopy(VALID)
    doc["bdf"] = {"bdf_version": "123.456.789", "new_stamp": "abc"}
    doc["battinfo_future_record"] = {"x": 1}
    test = doc["battinfo_test"]["test"]
    test["status"] = "paused"
    test["handle"] = "some_invalid_test"
    test["future_term"] = {"nested": [1, 2]}
    doc["battinfo_test"]["contributor"][0]["favourite_color"] = "green"
    step = doc["battinfo_test_protocol"]["method"][0]
    step["mode"] = "pulse"
    step["termination"].append({"direction": "below", "quantity": "pressure", "unit": "Pa", "value": 10000})
    return doc


def _read(doc: dict[str, Any]) -> Metadata:
    with pytest.warns(BDFMetadataWarning):
        return Metadata.from_dict(doc, source="x.metadata.json")


def test_valid_document_restores_strictly_without_a_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        meta = Metadata.from_dict(copy.deepcopy(VALID))

    assert meta.read_issues == []
    assert meta.to_dict() == VALID


def test_newer_document_restores_and_dumps_back_unchanged() -> None:
    future = _future()
    meta = _read(copy.deepcopy(future))

    assert meta.to_dict() == future
    assert meta.model_dump(mode="json", exclude_defaults=True) == future


def test_kept_values_read_back_in_place() -> None:
    meta = _read(_future())

    assert meta.battinfo_test.test.status == "paused"  # type: ignore[union-attr]
    assert meta.battinfo_test.test.model_extra == {"future_term": {"nested": [1, 2]}}  # type: ignore[union-attr]
    assert meta.model_extra == {"battinfo_future_record": {"x": 1}}
    step = meta.battinfo_test_protocol.method[0]  # type: ignore[index]
    assert step.mode == "pulse"
    assert all(isinstance(t, Termination) for t in step.termination)  # type: ignore[union-attr]
    assert [t.quantity for t in step.termination] == ["voltage", "pressure"]  # type: ignore[union-attr]


def test_warning_names_the_writer_and_every_path() -> None:
    with pytest.warns(BDFMetadataWarning) as record:
        meta = Metadata.from_dict(_future(), source="x.metadata.json")

    message = str(record[0].message)
    assert message.startswith("x.metadata.json was written by bdf 123.456.789 and holds 8 value(s)")
    assert {(i["path"], i["kind"]) for i in meta.read_issues} == {
        ("battinfo_future_record", "extra_forbidden"),
        ("battinfo_test.contributor.0.favourite_color", "extra_forbidden"),
        ("battinfo_test.test.future_term", "extra_forbidden"),
        ("battinfo_test.test.handle", "string_pattern_mismatch"),
        ("battinfo_test.test.status", "literal_error"),
        ("battinfo_test_protocol.method.0.mode", "literal_error"),
        ("battinfo_test_protocol.method.0.termination.1.quantity", "literal_error"),
        ("bdf.new_stamp", "extra_forbidden"),
    }
    for issue in meta.read_issues:
        assert issue["path"] in message


def test_value_of_a_changed_type_is_kept_as_read() -> None:
    doc = copy.deepcopy(VALID)
    doc["battinfo_test"]["test"]["started_at"] = "2024-06-17T05:33:20Z"
    meta = _read(copy.deepcopy(doc))

    assert meta.battinfo_test.test.started_at == "2024-06-17T05:33:20Z"  # type: ignore[union-attr]
    assert meta.to_dict() == doc


def test_edits_after_a_lenient_read_keep_everything_else() -> None:
    meta = _read(_future())
    meta.battinfo_test.test.instrument_name = "Neware BTS9000"  # type: ignore[union-attr]
    meta.battinfo_test.test.future_term = {"nested": [3]}  # type: ignore[union-attr]
    meta.battinfo_test_protocol.method[0].termination[1].value = 5  # type: ignore[index]

    expected = _future()
    expected["battinfo_test"]["test"]["instrument_name"] = "Neware BTS9000"
    expected["battinfo_test"]["test"]["future_term"] = {"nested": [3]}
    expected["battinfo_test_protocol"]["method"][0]["termination"][1]["value"] = 5
    assert meta.to_dict() == expected


def test_model_validate_without_the_lenient_context_stays_strict() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Metadata.model_validate(_future())

    paths = {".".join(map(str, e["loc"])) for e in excinfo.value.errors()}
    assert "battinfo_test.test.future_term" in paths
    assert "battinfo_test_protocol.method.0.termination.1.quantity" in paths


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: Test(instrumnet_name="x"), id="constructor"),
        pytest.param(lambda: Metadata(battinfo_test={"test": {"instrumnet_name": "x"}}), id="nested-constructor"),
        pytest.param(lambda: Step(steps=[{"stepz": 1}]), id="model-nested-in-same-named-field"),
        pytest.param(lambda: Termination(quantity="pressure"), id="unknown-enum-value"),
    ],
)
def test_construction_from_code_rejects_unknown_keys_and_values(build: Any) -> None:
    with pytest.raises(ValidationError):
        build()


@pytest.mark.parametrize(
    ("target", "name", "value"),
    [
        pytest.param(lambda m: m.battinfo_test.test, "instrumnet_name", "x", id="attribute-typo"),
        pytest.param(lambda m: m.battinfo_test, "test", {"instrumnet_name": "x"}, id="nested-dict-typo"),
        pytest.param(
            lambda m: m.battinfo_test_protocol.method[0], "steps", [{"stepz": 1}], id="model-nested-in-same-named-field"
        ),
        pytest.param(lambda m: m.battinfo_test.test, "status", "paused2", id="unknown-enum-value"),
    ],
)
def test_assignment_from_code_rejects_unknown_keys_and_values(target: Any, name: str, value: Any) -> None:
    meta = _read(_future())

    with pytest.raises((ValidationError, AttributeError)):
        setattr(target(meta), name, value)


def test_assignment_of_valid_nested_values_still_works() -> None:
    meta = Metadata()
    meta.battinfo_test_protocol.method = [{"mode": "cc", "steps": [{"mode": "cv"}]}]  # type: ignore[list-item]
    meta.battinfo_test.test = {"instrument_name": "Neware"}  # type: ignore[assignment]

    assert meta.battinfo_test_protocol.method[0].steps[0].mode == "cv"  # type: ignore[index]
    assert meta.battinfo_test.test.instrument_name == "Neware"  # type: ignore[union-attr]


def test_non_object_bdf_section_restores_empty() -> None:
    meta = _read({"bdf": "not-an-object", "battinfo_test": {"test": {"instrument_name": "Neware"}}})

    meta.bdf.source = "custom"
    assert meta.to_dict() == {"battinfo_test": {"test": {"instrument_name": "Neware"}}, "bdf": {"source": "custom"}}
