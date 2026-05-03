"""Tests for bdf._explore axis-label helpers."""
import pytest

from bdf._explore import _label_with_unit


class TestLabelWithUnit:
    """_label_with_unit should humanise canonical BDF column names."""

    def test_canonical_voltage(self):
        assert _label_with_unit("voltage_volt", "V") == "Voltage / V"

    def test_canonical_voltage_unit_conversion(self):
        # When the user requests millivolts the base should still be "Voltage"
        assert _label_with_unit("voltage_volt", "mV") == "Voltage / mV"

    def test_canonical_current(self):
        assert _label_with_unit("current_ampere", "A") == "Current / A"

    def test_canonical_current_unit_conversion(self):
        assert _label_with_unit("current_ampere", "mA") == "Current / mA"

    def test_canonical_time(self):
        assert _label_with_unit("test_time_second", "s") == "Test Time / s"

    def test_canonical_time_unit_conversion(self):
        assert _label_with_unit("test_time_second", "h") == "Test Time / h"

    def test_canonical_temperature(self):
        assert _label_with_unit("ambient_temperature_celsius", "°C") == "Ambient Temperature / °C"

    def test_already_humanized_no_double_unit(self):
        # If the column name is already "Voltage / V", applying a new unit
        # should strip the old unit and append the new one cleanly.
        assert _label_with_unit("Voltage / V", "mV") == "Voltage / mV"

    def test_already_humanized_same_unit(self):
        assert _label_with_unit("Voltage / V", "V") == "Voltage / V"

    def test_unknown_column_passthrough(self):
        # Unrecognised columns fall back to the previous behaviour.
        assert _label_with_unit("custom_signal", "A") == "custom_signal / A"

    def test_unknown_column_with_slash(self):
        assert _label_with_unit("Custom Signal / raw", "mA") == "Custom Signal / mA"
