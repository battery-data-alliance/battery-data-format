"""Unit tests for the read-metadata handoff (``bdf.metadata.Metadata``).

Cover the five-entity-record shape of :class:`Metadata`, strict
validation on the BattINFO-generated entity records it carries, the
refusal of unconverted datetime text, and lossless ``to_dict()``
round-tripping.
"""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import BaseModel, ValidationError

from bdf.battinfo.generated.cell_instance_schema import BattinfoCellInstance, CellInstance
from bdf.battinfo.generated.channel_schema import BattinfoChannelInstance, Channel
from bdf.battinfo.generated.equipment_schema import BattinfoEquipmentInstance, Equipment
from bdf.battinfo.generated.test_protocol_schema import BattinfoTestProtocol, TestSpec
from bdf.battinfo.generated.test_schema import BattinfoTest, Test
from bdf.metadata import BattinfoDataset, BdfReadInfo, Metadata

VALID_CELL_IRI = "https://w3id.org/battinfo/cell/abcd-efgh-jkmn-pqrs"

# An epoch with no significance beyond being a value the record accepts.
_PLACEHOLDER_EPOCH = 1700000000


def _iri_field_names(model: type[BaseModel]) -> list[str]:
    """Return the declared field names a BattINFO IRI pattern constrains on ``model``.

    Args:
        model: A generated record or section model.

    Returns:
        The name of every field whose schema carries a ``w3id.org/battinfo``
        pattern constraint, in declaration order.
    """
    schema = model.model_json_schema()
    names = []
    for name, prop in schema["properties"].items():
        variants = prop.get("anyOf", [prop])
        for variant in variants:
            if isinstance(variant, dict) and r"w3id\.org/battinfo" in variant.get("pattern", ""):
                names.append(name)
                break
    return names


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_read_metadata_declares_the_six_entity_records_bdf_raw_and_extras_only() -> None:
    """Metadata's field set is the six BattINFO entity records, bdf, raw, and extras."""
    assert set(Metadata.model_fields) == {
        "battinfo_test",
        "battinfo_cell",
        "battinfo_channel",
        "battinfo_equipment",
        "battinfo_test_protocol",
        "battinfo_dataset",
        "bdf",
        "raw",
        "extras",
    }


def test_fresh_read_metadata_auto_constructs_every_entity_record() -> None:
    """A freshly constructed Metadata carries a default instance of each entity record."""
    meta = Metadata()

    assert isinstance(meta.battinfo_test, BattinfoTest)
    assert isinstance(meta.battinfo_cell, BattinfoCellInstance)
    assert isinstance(meta.battinfo_channel, BattinfoChannelInstance)
    assert isinstance(meta.battinfo_dataset, BattinfoDataset)
    assert isinstance(meta.battinfo_equipment, BattinfoEquipmentInstance)
    assert isinstance(meta.battinfo_test_protocol, BattinfoTestProtocol)
    assert isinstance(meta.bdf, BdfReadInfo)
    assert meta.raw is None
    assert meta.extras is None


def test_nested_assignment_on_a_fresh_read_metadata_reaches_the_leaf() -> None:
    """meta.battinfo_test.test.started_at = ... works with no None guard on a fresh Metadata()."""
    meta = Metadata()
    test_section = cast(Test, meta.battinfo_test.test)
    test_section.started_at = _PLACEHOLDER_EPOCH

    assert test_section.started_at == _PLACEHOLDER_EPOCH
    assert meta.to_dict() == {"battinfo_test": {"test": {"started_at": _PLACEHOLDER_EPOCH}}}


# ---------------------------------------------------------------------------
# Strictness
# ---------------------------------------------------------------------------


def test_assignment_is_type_checked() -> None:
    """Assigning a non-integer to battinfo_test.test.started_at raises a validation error."""
    meta = Metadata()
    test_section = cast(Test, meta.battinfo_test.test)

    with pytest.raises(ValidationError):
        test_section.started_at = "not-a-timestamp"  # type: ignore[assignment]


def test_bad_enum_value_raises() -> None:
    """A staged test.status value outside the declared literal set fails the read."""
    with pytest.raises(ValidationError):
        Metadata.model_validate({"battinfo_test": {"test": {"status": "not-a-real-status"}}})


def test_a_schema_constraint_enforces() -> None:
    """A staged cell_instance.short_id value that violates the schema's pattern fails the read."""
    with pytest.raises(ValidationError):
        Metadata.model_validate({"battinfo_cell": {"cell_instance": {"short_id": "not valid!"}}})


def test_a_non_conforming_id_fails_the_read() -> None:
    """A staged cell_id value of 'CELL001' does not conform to the IRI pattern and fails the read."""
    with pytest.raises(ValidationError):
        Metadata.model_validate({"battinfo_test": {"test": {"cell_id": "CELL001"}}})


def test_generated_records_reject_an_undeclared_field() -> None:
    """A generated entity record no longer absorbs an undeclared key: it neither
    accepts nor silently drops it, so raw is left as the only place it can survive."""
    with pytest.raises(ValidationError):
        Metadata.model_validate({"battinfo_test": {"test": {"rig_bay": "B7"}}})


def test_a_nested_undeclared_key_neither_absorbs_nor_passes() -> None:
    """An undeclared key nested inside a record section fails validation rather
    than being absorbed, mirroring the top-level case."""
    with pytest.raises(ValidationError):
        Metadata.model_validate({"battinfo_test": {"test": {"conditions": {"note": "fine"}, "extra_key": 1}}})


def test_a_valid_iri_passes_through() -> None:
    """A conforming cell_id IRI appears unchanged in the staged record."""
    meta = Metadata.model_validate({"battinfo_test": {"test": {"cell_id": VALID_CELL_IRI}}})

    assert meta.battinfo_test.test.cell_id == VALID_CELL_IRI


def test_bdf_never_mints_an_iri() -> None:
    """A freshly constructed Metadata, with no IRI stated by any source, leaves every IRI field unset."""
    meta = Metadata()

    sections = [
        (meta.battinfo_test.test, Test),
        (meta.battinfo_cell.cell_instance, CellInstance),
        (meta.battinfo_channel.channel, Channel),
        (meta.battinfo_equipment.equipment, Equipment),
        (meta.battinfo_test_protocol.test_spec, TestSpec),
    ]
    for section, model in sections:
        for field_name in _iri_field_names(model):
            assert getattr(section, field_name) is None


# ---------------------------------------------------------------------------
# Datetime
# ---------------------------------------------------------------------------


def test_the_record_takes_integer_epoch_seconds() -> None:
    """A parser converts before it validates, so the record takes epoch seconds."""
    meta = Metadata.model_validate({"battinfo_test": {"test": {"started_at": _PLACEHOLDER_EPOCH}}})

    assert meta.battinfo_test.test.started_at == _PLACEHOLDER_EPOCH


def test_a_digit_string_reads_as_epoch_seconds() -> None:
    """A digits-only string is an epoch second count, which the declared int field accepts."""
    meta = Metadata.model_validate({"battinfo_test": {"test": {"started_at": str(_PLACEHOLDER_EPOCH)}}})

    assert meta.battinfo_test.test.started_at == _PLACEHOLDER_EPOCH


def test_datetime_text_fails_validation() -> None:
    """The record model reads no datetime text: interpretation belongs to the parser."""
    with pytest.raises(ValidationError, match="started_at"):
        Metadata.model_validate({"battinfo_test": {"test": {"started_at": "2024-01-01T00:00:00"}}})


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_to_dict_prunes_empty_sections_and_treats_a_stated_null_as_unset() -> None:
    """A stated null is unset (omitted), and a section left entirely unset vanishes rather than appear as {}."""
    meta = Metadata.model_validate(
        {
            "battinfo_test": {
                "test": {"name": "Hydra.0b_C_GITTOCV_002b", "started_at": _PLACEHOLDER_EPOCH, "ended_at": None}
            },
            "battinfo_channel": {},
        }
    )

    assert meta.to_dict() == {
        "battinfo_test": {"test": {"name": "Hydra.0b_C_GITTOCV_002b", "started_at": _PLACEHOLDER_EPOCH}}
    }


def test_an_unset_commissioned_at_reads_as_an_empty_wrapper_and_to_dict_omits_it() -> None:
    """An unset wrapper-typed leaf reads as an empty wrapper in memory, and to_dict() still omits it (D13)."""
    from bdf.battinfo.generated.equipment_schema import Equipment, UnixTime

    equipment = Equipment()

    assert isinstance(equipment.commissioned_at, UnixTime)
    assert equipment.commissioned_at.root is None
    assert "commissioned_at" not in equipment.to_dict()


def test_curated_document_round_trips_with_a_uri_value_in_its_normalised_form() -> None:
    """A curated document with only schema-legal keys round-trips, a URI value excepted.

    ``https://example.com`` serialises as ``https://example.com/``: pydantic's
    URL normalisation, not a byte-faithful copy.
    """
    curated = {
        "cell_instance": {"name": "Cell 42", "batch_id": "batch-7"},
        "provenance": {"source_url": "https://example.com"},
    }

    record = BattinfoCellInstance.model_validate(curated)

    assert record.to_dict() == {
        "cell_instance": {"name": "Cell 42", "batch_id": "batch-7"},
        "provenance": {"source_url": "https://example.com/"},
    }
