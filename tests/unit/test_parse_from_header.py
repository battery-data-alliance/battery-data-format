from __future__ import annotations

import pytest

from bdf.units.core import has_pint, parse_from_header


# Quantity names that happen to collide with real Pint units. A bare column header
# is a quantity *name*, not a unit, so these must NOT be parsed as carrying a unit.
# "cycle" is the dangerous one: Pint defines it as the angular unit (1 cycle = 2*pi
# rad), so reading "Cycle" as a unit silently scaled dimensionless cycle counts by
# 2*pi during conversion. See the header parser in bdf/units/core.py.
@pytest.mark.parametrize(
    "header",
    ["Cycle", "cycle", "Turn", "Revolution", "Step", "Index"],
)
def test_bare_quantity_name_is_not_parsed_as_a_unit(header: str) -> None:
    base, unit, source = parse_from_header(header)
    assert unit is None, f"{header!r} should have no unit, got {unit!r}"
    assert source == "none"
    assert base == header


@pytest.mark.parametrize(
    "header",
    [
        "Cycle Count",
        "Cycle Count / 1",
        "Step Count",
        "Step Index / 1",
        "z cycle",  # Biologic cycle-count synonym: unit word follows a prefix
        "cell turn",
        "3 revolutions",
    ],
)
def test_count_headers_never_acquire_an_angular_unit(header: str) -> None:
    # Whatever the parser does with the base/unit split, it must never claim an
    # angular unit (cycle/turn/revolution/radian) for a count column -- otherwise the
    # count would be scaled by 2*pi during conversion.
    _base, unit, _source = parse_from_header(header)
    if unit is not None:
        assert not any(tok in unit.lower() for tok in ("cycle", "turn", "revolution", "rad")), (
            f"{header!r} acquired an angular unit {unit!r}"
        )


def test_explicit_units_still_parse() -> None:
    assert parse_from_header("Voltage#V") == ("Voltage", "V", "hash")
    assert parse_from_header("Current (A)") == ("Current", "A", "paren")


@pytest.mark.skipif(not has_pint, reason="snake-suffix unit inference requires Pint")
def test_snake_suffix_unit_still_parses_with_a_real_base() -> None:
    # A genuine "name + trailing unit" snake header must still resolve, and must keep
    # a non-empty base (the guard only rejects whole-header-as-unit matches).
    base, unit, source = parse_from_header("voltage_V")
    assert base == "voltage"
    assert unit == "V"
    assert source == "snake"

    base, unit, source = parse_from_header("specific_energy_watt_hour_per_kilogram")
    assert base, "base must not be empty"
    assert unit is not None and "h" in unit.lower()
    assert source == "snake"
