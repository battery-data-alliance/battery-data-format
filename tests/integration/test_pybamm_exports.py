"""Optional integration tests against real PyBaMM solution exports."""

from __future__ import annotations

import polars as pl
import pytest

from bdf.table_normalizers import NORMALIZERS

pybamm = pytest.importorskip("pybamm", reason="pybamm not installed")


@pytest.mark.parametrize(
    "temperature_name",
    [
        pytest.param("X-averaged cell temperature [C]", id="celsius"),
        pytest.param("X-averaged cell temperature [K]", id="kelvin"),
    ],
)
def test_pybamm_solution_export_normalizes_temperature_units(temperature_name: str) -> None:
    """Normalize a real ``pybamm.Solution`` export using either Celsius or Kelvin temperature columns."""
    model = pybamm.lithium_ion.DFN(options={"thermal": "lumped"})
    parameter_values = pybamm.ParameterValues("Chen2020")
    simulation = pybamm.Simulation(model, parameter_values=parameter_values)
    solution = simulation.solve([0, 10])

    export_names = [
        "Time [s]",
        "Current [A]",
        "Voltage [V]",
        "Discharge capacity [A.h]",
        temperature_name,
    ]
    exported = pl.DataFrame(solution.get_data_dict(export_names))
    expected_celsius = exported[temperature_name].to_list()
    if temperature_name.endswith("[K]"):
        expected_celsius = [value - 273.15 for value in expected_celsius]

    out = NORMALIZERS["pybamm"].normalize(exported, validate=False)

    assert out["Test Time / s"].to_list() == pytest.approx(exported["Time [s]"].to_list())
    assert out["Voltage / V"].to_list() == pytest.approx(exported["Voltage [V]"].to_list())
    assert out["Current / A"].to_list() == pytest.approx((-exported["Current [A]"]).to_list())
    assert out["Net Capacity / Ah"].to_list() == pytest.approx((-exported["Discharge capacity [A.h]"]).to_list())
    assert out["Temperature T1 / degC"].to_list() == pytest.approx(expected_celsius)
