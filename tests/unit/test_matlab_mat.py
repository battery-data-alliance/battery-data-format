from __future__ import annotations

from bdf.data_sources import matlab_mat


def test_normalize_mapping_inverts_when_values_are_bdf() -> None:
    mapping = {"time": "Test Time / s", "voltage": "Voltage / V"}
    normalized = matlab_mat._normalize_mapping(mapping)
    assert "Test Time / s" in normalized
    assert normalized["Test Time / s"] == "time"


def test_datetime_config_from_units() -> None:
    variables = {"Unix Time / s": "TimeStamp"}
    units = {"Unix Time / s": "MM/DD/YYYY HH:MM:SS AM"}
    cfg = matlab_mat._datetime_config({"variables": variables}, variables, units)
    assert cfg is not None
    assert cfg["source"] == "TimeStamp"
    assert cfg["format"] == "MM/DD/YYYY HH:MM:SS AM"
