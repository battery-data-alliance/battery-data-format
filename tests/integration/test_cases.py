from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

import pytest

from bdf.plugins import PLUGINS
from bdf.spec import COLUMN_ONTOLOGY

_ALL_DELIM_IDS: frozenset[str] = frozenset(
    {
        "arbin_csv",
        "basytec_txt",
        "bdf_csv",
        "biologic_mpt",
        "digatron_csv",
        "landt_csv",
        "landt_txt",
        "maccor_csv",
        "neware_csv",
        "novonix_csv",
    }
)

_ZENODO_BASE = "https://zenodo.org/api/records/18986774/files"
_ZENODO_BASYTEC_URL = f"{_ZENODO_BASE}/DLR__LiLNMOHydra0b__20221130__GITT__25degC__Basytec.txt/content"
_ZENODO_BIOLOGIC_URL = f"{_ZENODO_BASE}/SINTEF__NaCR32140-MP10-04__2025-08-25__GITT_0p05C_25degC__BioLogic.mpt/content"
_ZENODO_LANDT_CSV_URL = f"{_ZENODO_BASE}/SINTEF__LiGrR2032__2024-04-30__25degC__Landt.csv/content"
_ZENODO_LANDT_TXT_URL = f"{_ZENODO_BASE}/SINTEF__LiGrR2032__2024-04-30__25degC__Landt.txt/content"
_ZENODO_DIGATRON_URL = f"{_ZENODO_BASE}/FZJ__INR21700__20250606__HPPC__25degC__Digatron.csv/content"
_ZENODO_NOVONIX_URL = f"{_ZENODO_BASE}/SINTEF__SLPBA842124HV-06__20241011__DCIR__0p1C__25degC__Novonix.csv/content"
_ZENODO_NEWARE_TIME_BUG_URL = (
    f"{_ZENODO_BASE}/SINTEF__SLPBA842124HV__2024-10-23__Rate_25degC__Neware__Time_Bug.csv/content"
)
_ZENODO_MACCOR_URL = f"{_ZENODO_BASE}/faraday__lg-INR21700M50-2019-002__2019-06-02__rate__25degC__maccor.csv/content"
_ZENODO_ARBIN_URL = f"{_ZENODO_BASE}/shandong__nacr32140-mp10__2023-10-10__pulse__25degC__arbin.CSV/content"
_ZENODO_XLSX_BASE = "https://zenodo.org/api/records/21337233/files"
_ZENODO_ARBIN_XLSX_OCV_URL = f"{_ZENODO_XLSX_BASE}/UCCS__ANR26650M1B-A002__2021__OCV-S4__-25degC__Arbin.xlsx/content"
_ZENODO_ARBIN_XLSX_CAP_URL = (
    f"{_ZENODO_XLSX_BASE}/Stanford__IFPR26650-YX05__20231012__Capacity__25degC__Arbin.xlsx/content"
)
_ZENODO_ARBIN_XLSX_EIS_URL = (
    f"{_ZENODO_XLSX_BASE}/Oxford__SLPBB142124-01__20240812__DynamicMBTF__25degC__Arbin__Wb1.xlsx/content"
)


class ColExpect(NamedTuple):
    """Per-column real-file evidence: the winning source header, applied scale, and datetime flag."""

    source_header: str
    scale: float = 1.0
    is_datetime: bool = False


@dataclass
class SampleCase:
    """All per-file expectations for detection, sniffing, metadata, and column tests."""

    # File path or URL to test data.
    source: str
    # Whether source is a URL (requires requests module).
    is_url: bool = False
    # Plugin ID expected to read this file.
    plugin_id: str = ""
    # Plugin IDs that match by file extension alone.
    ext_ids: frozenset[str] = field(default_factory=frozenset)
    # Plugin IDs that successfully extract metadata.
    meta_ids: frozenset[str] = field(default_factory=frozenset)
    # Plugin ID defining the output column structure.
    cols_id: str | None = None
    # Plugin ID the detection pipeline should select.
    detect_id: str = ""
    # Detection stage where winning plugin is confirmed (ext/metadata/columns).
    deciding_stage: str = ""
    # Number of header rows to skip when parsing delimited text.
    skip: int | None = None
    # Field separator for delimited text (e.g., ",", "\t", " ").
    sep: str | None = None
    # Metadata fields and expected values from the plugin.
    expected_metadata: dict | None = None
    # BDF column names → ColExpect (source header, scale, datetime flag).
    expected_columns: dict[str, ColExpect] | None = None
    # BDF column labels that may be entirely null.
    null_ok_columns: frozenset[str] = field(default_factory=frozenset)
    # Upper bound on |Current / A| after unit scaling. Catches mA-read-as-A regressions.
    current_max_abs_amps: float | None = None
    # BDF column name → description of expected data issues (not test failures).
    known_validity_bugs: dict[str, str] = field(default_factory=dict)
    # Pytest marks (e.g., pytest.mark.network, pytest.mark.skipif).
    marks: tuple = ()


def get_sample_data_source(source: str, is_url: bool, data_dir: Path) -> str | Path:
    """Resolve a test data source to either a URL or local file path.

    Args:
        source: File path or URL string.
        is_url: Whether source is a URL (vs. a local file path).
        data_dir: Base directory for local test data files.

    Returns:
        URL string if is_url=True, or constructed Path object for local files.

    Raises:
        pytest.skip: If source is local file but not present, or requests not installed for URL source.
    """
    if is_url:
        pytest.importorskip("requests")
        return source
    p = data_dir / source
    if not p.exists():
        pytest.skip(f"sample data not present: {source}")
    return p


def expected_labels(case: SampleCase) -> frozenset[str]:
    """Return the set of BDF output labels implied by a case's ``expected_columns`` keys.

    Args:
        case: The sample case whose ``expected_columns`` mapping to read.

    Returns:
        Frozenset of formatted BDF labels (e.g. ``"Voltage / V"``).
    """
    assert case.expected_columns is not None
    return frozenset(getattr(COLUMN_ONTOLOGY, mr).formatted_label for mr in case.expected_columns)


ALL_CASES: list[tuple[str, SampleCase]] = [
    (
        "basytec/zenodo_18986774",
        SampleCase(
            source=_ZENODO_BASYTEC_URL,
            is_url=True,
            plugin_id="basytec_txt",
            ext_ids=_ALL_DELIM_IDS,
            meta_ids=frozenset({"basytec_txt"}),
            cols_id="basytec_txt",
            detect_id="basytec_txt",
            deciding_stage="metadata",
            skip=12,
            sep=" ",
            expected_metadata={"start_time": "30.11.2022 15:00:21"},
            expected_columns={
                "test_time_second": ColExpect("~Time[h]", 3600.0),
                "record_index": ColExpect("DataSet", 1.0),
                "step_id": ColExpect("Line", 1.0),
                "voltage_volt": ColExpect("U[V]", 1.0),
                "current_ampere": ColExpect("I[A]", 1.0),
                "temperature_t1_celsius": ColExpect("T1[°C]", 1.0),
            },
            marks=(pytest.mark.network,),
        ),
    ),
    (
        "biologic/zenodo_18986774",
        SampleCase(
            source=_ZENODO_BIOLOGIC_URL,
            is_url=True,
            plugin_id="biologic_mpt",
            ext_ids=frozenset({"biologic_mpt"}),
            meta_ids=frozenset({"biologic_mpt"}),
            cols_id="biologic_mpt",
            detect_id="biologic_mpt",
            deciding_stage="ext",
            skip=112,
            sep="\t",
            expected_columns={
                "test_time_second": ColExpect("time/s", 1.0),
                "voltage_volt": ColExpect("Ecell/V", 1.0),
                "current_ampere": ColExpect("I/mA", 0.001),
                "cycle_count": ColExpect("cycle number", 1.0),
                "step_id": ColExpect("Ns", 1.0),
                "step_time_second": ColExpect("step time/s", 1.0),
                "net_capacity_ah": ColExpect("(Q-Qo)/mA.h", 0.001),
                "net_energy_wh": ColExpect("Energy/W.h", 1.0),
                "charging_energy_wh": ColExpect("Energy charge/W.h", 1.0),
                "discharging_energy_wh": ColExpect("Energy discharge/W.h", 1.0),
                "power_watt": ColExpect("P/W", 1.0),
                "internal_resistance_ohm": ColExpect("R/Ohm", 1.0),
            },
            marks=(pytest.mark.network,),
        ),
    ),
    (
        "landt_csv/zenodo_18986774",
        SampleCase(
            source=_ZENODO_LANDT_CSV_URL,
            is_url=True,
            plugin_id="landt_csv",
            ext_ids=_ALL_DELIM_IDS,
            meta_ids=frozenset(PLUGINS),
            cols_id="landt_csv",
            detect_id="landt_csv",
            deciding_stage="columns",
            skip=6,
            sep=",",
            expected_columns={
                "test_time_second": ColExpect("test_time_s", 1.0),
                "voltage_volt": ColExpect("voltage_V", 1.0),
                "current_ampere": ColExpect("current_A", 1.0),
                "cycle_count": ColExpect("cycle_index", 1.0),
                "step_id": ColExpect("step_index", 1.0),
                "step_time_second": ColExpect("step_time_s", 1.0),
                "record_index": ColExpect("channel_index", 1.0),
                "unix_time_second": ColExpect("date_time_iso_string", 1.0, is_datetime=True),
                "step_charging_capacity_ah": ColExpect("charge_capacity_Ah", 1.0),
                "step_discharging_capacity_ah": ColExpect("discharge_capacity_Ah", 1.0),
                "step_charging_energy_wh": ColExpect("charge_energy_Wh", 1.0),
                "step_discharging_energy_wh": ColExpect("discharge_energy_Wh", 1.0),
                "temperature_t1_celsius": ColExpect("temperature_1_C", 1.0),
                "temperature_t2_celsius": ColExpect("temperature_2_C", 1.0),
                "temperature_t3_celsius": ColExpect("temperature_3_C", 1.0),
                "step_type": ColExpect("step_name", 1.0),
            },
            marks=(pytest.mark.network,),
        ),
    ),
    (
        "landt_txt/zenodo_18986774",
        SampleCase(
            source=_ZENODO_LANDT_TXT_URL,
            is_url=True,
            plugin_id="landt_txt",
            ext_ids=_ALL_DELIM_IDS,
            meta_ids=frozenset(PLUGINS),
            cols_id="landt_txt",
            detect_id="landt_txt",
            deciding_stage="columns",
            skip=1,
            sep="\t",
            expected_columns={
                "test_time_second": ColExpect("Test(Sec)", 1.0),
                "voltage_volt": ColExpect("Volts", 1.0),
                "current_ampere": ColExpect("Amps", 1.0),
                "unix_time_second": ColExpect("DPt-Time", 1.0, is_datetime=True),
                "cycle_count": ColExpect("Cyc#", 1.0),
                "step_id": ColExpect("Step", 1.0),
                "record_index": ColExpect("Rec#", 1.0),
                "step_time_second": ColExpect("Step(Sec)", 1.0),
                "step_cumulative_capacity_ah": ColExpect("Amp-hr", 1.0),
                "step_cumulative_energy_wh": ColExpect("Watt-hr", 1.0),
            },
            marks=(pytest.mark.network,),
        ),
    ),
    (
        "digatron/zenodo_18986774",
        SampleCase(
            source=_ZENODO_DIGATRON_URL,
            is_url=True,
            plugin_id="digatron_csv",
            ext_ids=_ALL_DELIM_IDS,
            meta_ids=frozenset(PLUGINS),
            cols_id="digatron_csv",
            detect_id="digatron_csv",
            deciding_stage="columns",
            skip=0,
            sep=",",
            expected_columns={
                "test_time_second": ColExpect("Program Duration#s", 1.0),
                "voltage_volt": ColExpect("Voltage#V", 1.0),
                "current_ampere": ColExpect("Current#A", 1.0),
                "unix_time_second": ColExpect("Timestamp", 1.0, is_datetime=True),
                "step_id": ColExpect("Step", 1.0),
                "step_time_second": ColExpect("Step Duration#s", 1.0),
                "step_type": ColExpect("Status", 1.0),
                "charging_capacity_ah": ColExpect("AhCha#AH", 1.0),
                "discharging_capacity_ah": ColExpect("AhDch#Ah", 1.0),
                "step_cumulative_capacity_ah": ColExpect("AhStep#Ah", 1.0),
                "net_capacity_ah": ColExpect("AhAccu#Ah", 1.0),
                "charging_energy_wh": ColExpect("WhCha#Wh", 1.0),
                "discharging_energy_wh": ColExpect("WhDch#Wh", 1.0),
                "step_cumulative_energy_wh": ColExpect("WhStep#Wh", 1.0),
                "net_energy_wh": ColExpect("WhAccu#Wh", 1.0),
                "temperature_t1_celsius": ColExpect("T1#degC", 1.0),
                "ambient_temperature_celsius": ColExpect("Tenv#degC", 1.0),
            },
            marks=(pytest.mark.network,),
        ),
    ),
    (
        "novonix/zenodo_18986774",
        SampleCase(
            source=_ZENODO_NOVONIX_URL,
            is_url=True,
            plugin_id="novonix_csv",
            ext_ids=_ALL_DELIM_IDS,
            meta_ids=frozenset({"novonix_csv"}),
            cols_id="novonix_csv",
            detect_id="novonix_csv",
            deciding_stage="metadata",
            skip=20,
            sep=",",
            expected_columns={
                "test_time_second": ColExpect("Run Time (h)", 3600.0),
                "voltage_volt": ColExpect("Potential (V)", 1.0),
                "current_ampere": ColExpect("Current (A)", 1.0),
                "unix_time_second": ColExpect("Date and Time", 1.0, is_datetime=True),
                "cycle_count": ColExpect("Cycle Number", 1.0),
                "step_count": ColExpect("Step Number", 1.0),
                "temperature_t1_celsius": ColExpect("Temperature (°C)", 1.0),
                "temperature_t2_celsius": ColExpect("Circuit Temperature (°C)", 1.0),
                "step_id": ColExpect("Step position", 1.0),
                "step_time_second": ColExpect("Step Time (h)", 3600.0),
                "net_capacity_ah": ColExpect("Capacity (Ah)", 1.0),
                "step_net_energy_wh": ColExpect("Energy (Wh)", 1.0),
                "power_watt": ColExpect("Power(W)", 1.0),
                "step_type": ColExpect("Step Type", 1.0),
            },
            known_validity_bugs={
                "temperature_t1_celsius": "Novonix writes -9999 sentinel when no valid temperature is available",
            },
            marks=(pytest.mark.network,),
        ),
    ),
    (
        "bdf_csv",
        SampleCase(
            source="bdf/sample.bdf.csv",
            plugin_id="bdf_csv",
            ext_ids=frozenset({"bdf_csv"}),
            meta_ids=frozenset(PLUGINS),
            cols_id="bdf_csv",
            detect_id="bdf_csv",
            deciding_stage="ext",
            expected_columns={
                "test_time_second": ColExpect("Test Time / s", 1.0),
                "voltage_volt": ColExpect("Voltage / V", 1.0),
                "current_ampere": ColExpect("Current / A", 1.0),
            },
        ),
    ),
    (
        "neware_nda/zenodo_18986774",
        SampleCase(
            source=f"{_ZENODO_BASE}/SINTEF__G20M7-202512-Gru6mV__20251228__C30__25degC__Neware.nda/content",
            is_url=True,
            plugin_id="neware_nda",
            ext_ids=frozenset({"neware_nda"}),
            meta_ids=frozenset(PLUGINS),
            cols_id="neware_nda",
            detect_id="neware_nda",
            deciding_stage="ext",
            expected_columns={
                "test_time_second": ColExpect("total_time_s", 1.0),
                "voltage_volt": ColExpect("voltage_V", 1.0),
                "current_ampere": ColExpect("current_mA", 0.001),
                "unix_time_second": ColExpect("unix_time_s", 1.0),
                "cycle_count": ColExpect("cycle_count", 1.0),
                "step_count": ColExpect("step_count", 1.0),
                "step_id": ColExpect("step_index", 1.0),
                "step_type": ColExpect("step_type", 1.0),
                "record_index": ColExpect("index", 1.0),
                "step_time_second": ColExpect("step_time_s", 1.0),
                "step_net_capacity_ah": ColExpect("capacity_mAh", 0.001),
                "step_net_energy_wh": ColExpect("energy_mWh", 0.001),
            },
            marks=(
                pytest.mark.network,
                pytest.mark.skipif(
                    pytest.importorskip("fastnda", reason="fastnda not installed") is None,
                    reason="fastnda not installed",
                ),
            ),
        ),
    ),
    (
        "neware_csv/zenodo_18986774_time_bug",
        SampleCase(
            source=_ZENODO_NEWARE_TIME_BUG_URL,
            is_url=True,
            plugin_id="neware_csv",
            ext_ids=_ALL_DELIM_IDS,
            meta_ids=frozenset(PLUGINS),
            cols_id="neware_csv",
            detect_id="neware_csv",
            deciding_stage="columns",
            skip=0,
            sep=",",
            expected_columns={
                "test_time_second": ColExpect("Total Time(s)", 1.0),
                "voltage_volt": ColExpect("Voltage(V)", 1.0),
                "current_ampere": ColExpect("Current(A)", 1.0),
                "unix_time_second": ColExpect("Date", 1.0, is_datetime=True),
                "cycle_count": ColExpect("Cycle Index", 1.0),
                "step_id": ColExpect("Step Index", 1.0),
                "step_time_second": ColExpect("Time(s)", 1.0),
                "step_charging_capacity_ah": ColExpect("Chg. Cap.(Ah)", 1.0),
                "step_discharging_capacity_ah": ColExpect("DChg. Cap.(Ah)", 1.0),
                "step_cumulative_capacity_ah": ColExpect("Capacity(Ah)", 1.0),
                "step_charging_energy_wh": ColExpect("Chg. Energy(Wh)", 1.0),
                "step_discharging_energy_wh": ColExpect("DChg. Energy(Wh)", 1.0),
            },
            known_validity_bugs={
                "test_time_second": (
                    "known Neware export bug: 'Total Time(s)' resets at step boundaries "
                    "instead of accumulating across the whole test; see bdf.repair.fix_time"
                ),
            },
            marks=(pytest.mark.network,),
        ),
    ),
    (
        "maccor_csv/zenodo_18986774",
        SampleCase(
            source=_ZENODO_MACCOR_URL,
            is_url=True,
            plugin_id="maccor_csv",
            ext_ids=_ALL_DELIM_IDS,
            meta_ids=frozenset(PLUGINS),
            cols_id="maccor_csv",
            detect_id="maccor_csv",
            deciding_stage="columns",
            skip=13,
            sep=",",
            expected_columns={
                "test_time_second": ColExpect("Test Time [s]", 1.0),
                "voltage_volt": ColExpect("Voltage [V]", 1.0),
                "current_ampere": ColExpect("Current [A]", 1.0),
                "unix_time_second": ColExpect("DPT Time", 1.0, is_datetime=True),
                "cycle_count": ColExpect("Cycle C", 1.0),
                "step_count": ColExpect("Step", 1.0),
                "record_index": ColExpect("Rec", 1.0),
                "step_time_second": ColExpect("Step Time [s]", 1.0),
                "step_cumulative_capacity_ah": ColExpect("Capacity [Ah]", 1.0),
                "step_cumulative_energy_wh": ColExpect("Energy [Wh]", 1.0),
                "temperature_t1_celsius": ColExpect("Temperature Cell [degC]", 1.0),
                "ambient_temperature_celsius": ColExpect("Temperature Chamber [degC]", 1.0),
            },
            marks=(pytest.mark.network,),
        ),
    ),
    (
        "arbin_csv/zenodo_18986774",
        SampleCase(
            source=_ZENODO_ARBIN_URL,
            is_url=True,
            plugin_id="arbin_csv",
            ext_ids=_ALL_DELIM_IDS,
            meta_ids=frozenset(PLUGINS),
            cols_id="arbin_csv",
            detect_id="arbin_csv",
            deciding_stage="columns",
            skip=0,
            sep=",",
            expected_columns={
                "test_time_second": ColExpect("Test Time (s)", 1.0),
                "voltage_volt": ColExpect("Voltage (V)", 1.0),
                "current_ampere": ColExpect("Current (A)", 1.0),
                "unix_time_second": ColExpect("Date Time", 1.0, is_datetime=True),
                "cycle_count": ColExpect("Cycle Index", 1.0),
                "step_id": ColExpect("Step Index", 1.0),
                "record_index": ColExpect("Data Point", 1.0),
                "step_time_second": ColExpect("Step Time (s)", 1.0),
                "charging_capacity_ah": ColExpect("Charge Capacity (Ah)", 1.0),
                "discharging_capacity_ah": ColExpect("Discharge Capacity (Ah)", 1.0),
                "charging_energy_wh": ColExpect("Charge Energy (Wh)", 1.0),
                "discharging_energy_wh": ColExpect("Discharge Energy (Wh)", 1.0),
                "power_watt": ColExpect("Power (W)", 1.0),
                "dc_internal_resistance_ohm": ColExpect("Internal Resistance (Ohm)", 1.0),
                "ac_internal_resistance_ohm": ColExpect("ACR (Ohm)", 1.0),
                "temperature_t1_celsius": ColExpect("Aux_Temperature_1 (C)", 1.0),
            },
            null_ok_columns=frozenset({"DC Internal Resistance / ohm", "AC Internal Resistance / ohm"}),
            known_validity_bugs={
                "ac_internal_resistance_ohm": "ACR (Ohm) is entirely empty in this pulse-test export",
                "charging_capacity_ah": (
                    "Charge Capacity resets exactly once, at the step-9 boundary marking the "
                    "end of initial conditioning and the start of the repeated pulse loop; "
                    "globally cumulative everywhere else, so this is a single scripted "
                    "counter reset in the test plan, not a per-step reset"
                ),
                "discharging_capacity_ah": (
                    "Discharge Capacity resets at two specific steps (12 and 74) on every "
                    "pass through the repeated pulse loop, not at the other ~60 step "
                    "boundaries per loop; scripted resets at those two steps in the test "
                    "plan, not a per-step reset"
                ),
                "charging_energy_wh": (
                    "Charge Energy mirrors Charge Capacity: resets once at the step-9 "
                    "boundary, globally cumulative elsewhere"
                ),
                "discharging_energy_wh": (
                    "Discharge Energy mirrors Discharge Capacity: resets at specific steps "
                    "within the repeated pulse loop, not at every step boundary"
                ),
            },
            marks=(pytest.mark.network,),
        ),
    ),
    (
        "arbin_xlsx/zenodo_21337233_ocv_micro",
        SampleCase(
            source=_ZENODO_ARBIN_XLSX_OCV_URL,
            is_url=True,
            plugin_id="arbin_xlsx",
            ext_ids=frozenset({"neware_xlsx", "arbin_xlsx"}),
            meta_ids=frozenset(PLUGINS),
            cols_id="arbin_xlsx",
            detect_id="arbin_xlsx",
            deciding_stage="columns",
            expected_columns={
                # underscore header dialect (older MITS Excel); 39-row micro-fixture
                "test_time_second": ColExpect("Test_Time(s)", 1.0),
                "voltage_volt": ColExpect("Voltage(V)", 1.0),
                "current_ampere": ColExpect("Current(A)", 1.0),
                "unix_time_second": ColExpect("Date_Time", 1.0, is_datetime=True),
                "cycle_count": ColExpect("Cycle_Index", 1.0),
                "step_id": ColExpect("Step_Index", 1.0),
                "record_index": ColExpect("Data_Point", 1.0),
                "step_time_second": ColExpect("Step_Time(s)", 1.0),
            },
            marks=(
                pytest.mark.network,
                pytest.mark.skipif(
                    pytest.importorskip("fastexcel", reason="fastexcel not installed") is None,
                    reason="fastexcel not installed",
                ),
            ),
        ),
    ),
    (
        "arbin_xlsx/zenodo_21337233_capacity",
        SampleCase(
            source=_ZENODO_ARBIN_XLSX_CAP_URL,
            is_url=True,
            plugin_id="arbin_xlsx",
            ext_ids=frozenset({"neware_xlsx", "arbin_xlsx"}),
            meta_ids=frozenset(PLUGINS),
            cols_id="arbin_xlsx",
            detect_id="arbin_xlsx",
            deciding_stage="columns",
            expected_columns={
                # underscore dialect without Data_Point (lithiumwerks-style export)
                "test_time_second": ColExpect("Test_Time(s)", 1.0),
                "voltage_volt": ColExpect("Voltage(V)", 1.0),
                "current_ampere": ColExpect("Current(A)", 1.0),
                "unix_time_second": ColExpect("Date_Time", 1.0, is_datetime=True),
                "cycle_count": ColExpect("Cycle_Index", 1.0),
                "step_id": ColExpect("Step_Index", 1.0),
                "step_time_second": ColExpect("Step_Time(s)", 1.0),
            },
            marks=(
                pytest.mark.network,
                pytest.mark.skipif(
                    pytest.importorskip("fastexcel", reason="fastexcel not installed") is None,
                    reason="fastexcel not installed",
                ),
            ),
        ),
    ),
    (
        "arbin_xlsx/zenodo_21337233_dynamic_eis",
        SampleCase(
            source=_ZENODO_ARBIN_XLSX_EIS_URL,
            is_url=True,
            plugin_id="arbin_xlsx",
            ext_ids=frozenset({"neware_xlsx", "arbin_xlsx"}),
            meta_ids=frozenset(PLUGINS),
            cols_id="arbin_xlsx",
            detect_id="arbin_xlsx",
            deciding_stage="columns",
            expected_columns={
                # space header dialect (newer MITS Excel); multi-sheet workbook whose
                # ACIM_chan (EIS) and Statistics sheets must be ignored by the parser
                "test_time_second": ColExpect("Test Time (s)", 1.0),
                "voltage_volt": ColExpect("Voltage (V)", 1.0),
                "current_ampere": ColExpect("Current (A)", 1.0),
                "unix_time_second": ColExpect("Date Time", 1.0, is_datetime=True),
                "cycle_count": ColExpect("Cycle Index", 1.0),
                "step_id": ColExpect("Step Index", 1.0),
                "record_index": ColExpect("Data Point", 1.0),
                "step_time_second": ColExpect("Step Time (s)", 1.0),
                "power_watt": ColExpect("Power (W)", 1.0),
                "temperature_t1_celsius": ColExpect("Aux_Temperature_1 (C)", 1.0),
                "dc_internal_resistance_ohm": ColExpect("Internal Resistance (Ohm)", 1.0),
                "ac_internal_resistance_ohm": ColExpect("ACR (Ohm)", 1.0),
                # NOTE: the four vendor accumulator expectations below disappear when the
                # accumulator-unmap change (fix/derived-tolerance-and-arbin-accumulators)
                # merges; drop them during that rebase.
                "charging_capacity_ah": ColExpect("Charge Capacity (Ah)", 1.0),
                "discharging_capacity_ah": ColExpect("Discharge Capacity (Ah)", 1.0),
                "charging_energy_wh": ColExpect("Charge Energy (Wh)", 1.0),
                "discharging_energy_wh": ColExpect("Discharge Energy (Wh)", 1.0),
            },
            null_ok_columns=frozenset({"DC Internal Resistance / ohm", "AC Internal Resistance / ohm"}),
            known_validity_bugs={
                "ac_internal_resistance_ohm": "ACR (Ohm) is entirely empty in this dynamic-load export",
                # Schedule-authored accumulator resets (79 discharge / 1 charge, at scripted
                # steps); removed along with the accumulator ColExpects at the unmap rebase.
                "charging_capacity_ah": "Charge Capacity resets at schedule-defined steps",
                "discharging_capacity_ah": "Discharge Capacity resets at schedule-defined steps",
                "charging_energy_wh": "Charge Energy resets at schedule-defined steps",
                "discharging_energy_wh": "Discharge Energy resets at schedule-defined steps",
            },
            marks=(
                pytest.mark.network,
                pytest.mark.skipif(
                    pytest.importorskip("fastexcel", reason="fastexcel not installed") is None,
                    reason="fastexcel not installed",
                ),
            ),
        ),
    ),
]
