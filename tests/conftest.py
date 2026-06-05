# tests/conftest.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from bdf.plugins import PLUGINS

HERE = Path(__file__).parent
DATA = HERE / "data"


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA


@pytest.fixture(scope="session")
def tiny_files(data_dir: Path):
    """Minimal valid tiny files for each plugin (keep <1KB)."""
    return {
        "biologic": data_dir / "tiny_biologic.mpt",
        "neware": data_dir / "tiny_neware.csv",
        "landt_csv": data_dir / "tiny_landt.csv",
        "basytec": data_dir / "tiny_basytec.txt",
        "digatron": data_dir / "tiny_digatron.csv",
    }


@pytest.fixture(scope="session")
def minimal_registry(data_dir: Path) -> dict:
    with open(data_dir / "minimal_registry.jsonld", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def offline_registry_url() -> str:
    """Fallback to the minimal local registry (used by some unit tests)."""
    return "file://minimal"


@pytest.fixture(scope="session")
def pint_ureg():
    try:
        import pint

        return pint.UnitRegistry()
    except Exception:
        pytest.skip("pint not available")


# ---------------------------------------------------------------------------
# Shared detection-pipeline constants
# ---------------------------------------------------------------------------

_ALL_DELIM_IDS: frozenset[str] = frozenset(
    {
        "arbin_csv",
        "basytec_txt",
        "biologic_mpt",
        "digatron_csv",
        "landt_csv",
        "landt_txt",
        "maccor_csv",
        "neware_csv",
        "novonix_csv",
    }
)

_ZENODO_BASE = "https://zenodo.org/api/records/18214281/files"
_ZENODO_BASYTEC_URL = f"{_ZENODO_BASE}/DLR__LiLNMOHydra0b__20221130__GITT__25degC__Basytec.txt/content"
_ZENODO_BIOLOGIC_URL = f"{_ZENODO_BASE}/SINTEF__NaCR32140-MP10-04__2025-08-25__GITT_0p05C_25degC__BioLogic.mpt/content"
_ZENODO_LANDT_CSV_URL = f"{_ZENODO_BASE}/SINTEF__LiGrR2032__2024-04-30__25degC__Landt.csv/content"
_ZENODO_LANDT_TXT_URL = f"{_ZENODO_BASE}/SINTEF__LiGrR2032__2024-04-30__25degC__Landt.txt/content"
_ZENODO_DIGATRON_URL = f"{_ZENODO_BASE}/FZJ__INR21700__20250606__HPPC__25degC__Digatron.csv/content"


# ---------------------------------------------------------------------------
# SampleCase dataclass
# ---------------------------------------------------------------------------


@dataclass
class SampleCase:
    """All per-file expectations for detection, sniffing, metadata, and column tests."""

    source: str
    is_url: bool = False
    plugin_id: str = ""
    ext_ids: frozenset[str] = field(default_factory=frozenset)
    meta_ids: frozenset[str] = field(default_factory=frozenset)
    cols_id: str | None = None
    detect_id: str = ""
    deciding_stage: str = ""
    skip: int | None = None
    sep: str | None = None
    expected_metadata: dict | None = None
    expected_columns: frozenset[str] | None = None
    null_ok_columns: frozenset[str] = field(default_factory=frozenset)
    current_max_abs: float | None = None
    marks: tuple = ()


def resolve_source(source: str, is_url: bool, data_dir: Path) -> str | Path:
    if is_url:
        pytest.importorskip("requests")
        return source
    p = data_dir / source
    if not p.exists():
        pytest.skip(f"sample data not present: {source}")
    return p


# ---------------------------------------------------------------------------
# ALL_CASES
# ---------------------------------------------------------------------------

ALL_CASES: list[tuple[str, SampleCase]] = [
    (
        "biologic/mpt",
        SampleCase(
            source="biologic/Sample_data_biologic_no_header.mpt",
            plugin_id="biologic_mpt",
            ext_ids=frozenset({"biologic_mpt"}),
            meta_ids=frozenset(PLUGINS),
            cols_id="biologic_mpt",
            detect_id="biologic_mpt",
            deciding_stage="ext",
            skip=0,
            sep="\t",
            expected_columns=frozenset(
                {
                    "Test Time / s",
                    "Voltage / V",
                    "Current / A",
                    "Cycle Count / 1",
                    "Step Index / 1",
                    "Charging Capacity / Ah",
                    "Discharging Capacity / Ah",
                    "Step Capacity / Ah",
                    "Cumulative Capacity / Ah",
                    "Charging Energy / Wh",
                    "Discharging Energy / Wh",
                    "Cumulative Energy / Wh",
                    "Power / W",
                    "Internal Resistance / ohm",
                }
            ),
        ),
    ),
    (
        "biologic/txt",
        SampleCase(
            source="biologic/Sample_data_biologic_01_MB_CA1.txt",
            plugin_id="biologic_mpt",
            ext_ids=_ALL_DELIM_IDS,
            meta_ids=frozenset({"biologic_mpt"}),
            cols_id="biologic_mpt",
            detect_id="biologic_mpt",
            deciding_stage="metadata",
            skip=102,
            sep="\t",
            expected_metadata={"start_time": "05/13/2024 11:19:51.602"},
            current_max_abs=5.0,
            expected_columns=frozenset(
                {
                    "Test Time / s",
                    "Voltage / V",
                    "Current / A",
                    "Cycle Count / 1",
                    "Step Index / 1",
                    "Step Time / s",
                    "Charging Capacity / Ah",
                    "Discharging Capacity / Ah",
                    "Cumulative Capacity / Ah",
                    "Charging Energy / Wh",
                    "Discharging Energy / Wh",
                    "Power / W",
                    "Internal Resistance / ohm",
                }
            ),
        ),
    ),
    (
        "biologic/txt/ca1",
        SampleCase(
            source="biologic/Sample_data_biologic_CA1.txt",
            plugin_id="biologic_mpt",
            ext_ids=_ALL_DELIM_IDS,
            meta_ids=frozenset({"biologic_mpt"}),
            cols_id="biologic_mpt",
            detect_id="biologic_mpt",
            deciding_stage="metadata",
            skip=102,
            sep="\t",
            expected_metadata={"start_time": "05/13/2024 11:19:51.602"},
            current_max_abs=5.0,
            expected_columns=frozenset(
                {
                    "Test Time / s",
                    "Voltage / V",
                    "Current / A",
                    "Cycle Count / 1",
                    "Step Index / 1",
                    "Step Time / s",
                    "Charging Capacity / Ah",
                    "Discharging Capacity / Ah",
                    "Cumulative Capacity / Ah",
                    "Charging Energy / Wh",
                    "Discharging Energy / Wh",
                    "Power / W",
                    "Internal Resistance / ohm",
                }
            ),
        ),
    ),
    (
        "basytec/local",
        SampleCase(
            source="basytec/sample_data_basytec.txt",
            plugin_id="basytec_txt",
            ext_ids=_ALL_DELIM_IDS,
            meta_ids=frozenset({"basytec_txt"}),
            cols_id="basytec_txt",
            detect_id="basytec_txt",
            deciding_stage="metadata",
            skip=12,
            sep="\t",
            expected_metadata={"start_time": "19.06.2023 17:56:53"},
            expected_columns=frozenset(
                {
                    "Test Time / s",
                    "Voltage / V",
                    "Current / A",
                    "Step Index / 1",
                    "Net Capacity / Ah",
                }
            ),
        ),
    ),
    (
        "maccor/local",
        SampleCase(
            source="maccor/sample_data_maccor.csv",
            plugin_id="maccor_csv",
            ext_ids=_ALL_DELIM_IDS,
            meta_ids=frozenset({"maccor_csv"}),
            cols_id="maccor_csv",
            detect_id="maccor_csv",
            deciding_stage="metadata",
            skip=2,
            sep=",",
            expected_columns=frozenset(
                {
                    "Test Time / s",
                    "Voltage / V",
                    "Current / A",
                    "Unix Time / s",
                    "Cycle Count / 1",
                    "Step Count / 1",
                    "Ambient Temperature / degC",
                    "Step Time / s",
                    "Net Capacity / Ah",
                    "Net Energy / Wh",
                }
            ),
        ),
    ),
    (
        "novonix/local",
        SampleCase(
            source="novonix/sample_data_novonix.csv",
            plugin_id="novonix_csv",
            ext_ids=_ALL_DELIM_IDS,
            meta_ids=frozenset({"novonix_csv"}),
            cols_id="novonix_csv",
            detect_id="novonix_csv",
            deciding_stage="metadata",
            skip=20,
            sep=",",
            expected_columns=frozenset(
                {
                    "Test Time / s",
                    "Voltage / V",
                    "Current / A",
                    "Unix Time / s",
                    "Cycle Count / 1",
                    "Step Count / 1",
                    "Ambient Temperature / degC",
                    "Step Index / 1",
                    "Step Time / s",
                    "Net Capacity / Ah",
                    "Net Energy / Wh",
                    "Power / W",
                    "Surface Temperature T1 / degC",
                }
            ),
        ),
    ),
    (
        "arbin/local",
        SampleCase(
            source="arbin/sample_data_arbin.csv",
            plugin_id="arbin_csv",
            ext_ids=_ALL_DELIM_IDS,
            meta_ids=frozenset(PLUGINS),
            cols_id="arbin_csv",
            detect_id="arbin_csv",
            deciding_stage="columns",
            skip=0,
            sep=",",
            expected_columns=frozenset(
                {
                    "Test Time / s",
                    "Voltage / V",
                    "Current / A",
                    "Unix Time / s",
                    "Cycle Count / 1",
                    "Step Count / 1",
                    "Step Index / 1",
                    "Step Time / s",
                    "Charging Capacity / Ah",
                    "Discharging Capacity / Ah",
                    "Charging Energy / Wh",
                    "Discharging Energy / Wh",
                    "Power / W",
                    "Internal Resistance / ohm",
                }
            ),
            null_ok_columns=frozenset({"Internal Resistance / ohm"}),
        ),
    ),
    (
        "basytec/url",
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
            expected_columns=frozenset(
                {
                    "Test Time / s",
                    "Voltage / V",
                    "Current / A",
                    "Ambient Temperature / degC",
                    "Step Index / 1",
                }
            ),
            marks=(pytest.mark.network,),
        ),
    ),
    (
        "biologic/url",
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
            expected_columns=frozenset(
                {
                    "Test Time / s",
                    "Voltage / V",
                    "Current / A",
                    "Cycle Count / 1",
                    "Step Index / 1",
                    "Step Time / s",
                    "Charging Capacity / Ah",
                    "Discharging Capacity / Ah",
                    "Step Capacity / Ah",
                    "Cumulative Capacity / Ah",
                    "Charging Energy / Wh",
                    "Discharging Energy / Wh",
                    "Internal Resistance / ohm",
                    "Power / W",
                }
            ),
            marks=(pytest.mark.network,),
        ),
    ),
    (
        "landt_csv/url",
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
            expected_columns=frozenset(
                {
                    "Test Time / s",
                    "Voltage / V",
                    "Current / A",
                    "Cycle Count / 1",
                    "Step Count / 1",
                    "Step Time / s",
                }
            ),
            marks=(pytest.mark.network,),
        ),
    ),
    (
        "landt_txt/url",
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
            expected_columns=frozenset(
                {
                    "Test Time / s",
                    "Voltage / V",
                    "Current / A",
                    "Unix Time / s",
                    "Step Count / 1",
                    "Step Index / 1",
                    "Step Time / s",
                }
            ),
            marks=(pytest.mark.network,),
        ),
    ),
    (
        "neware/xlsx",
        SampleCase(
            source="neware/sample_data_neware.xlsx",
            plugin_id="neware_xlsx",
            ext_ids=frozenset({"neware_xlsx"}),
            meta_ids=frozenset(PLUGINS),
            cols_id="neware_xlsx",
            detect_id="neware_xlsx",
            deciding_stage="ext",
            current_max_abs=0.1,
            expected_columns=frozenset(
                {
                    "Test Time / s",
                    "Voltage / V",
                    "Current / A",
                    "Unix Time / s",
                }
            ),
        ),
    ),
    (
        "digatron/url",
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
            expected_columns=frozenset(
                {
                    "Test Time / s",
                    "Voltage / V",
                    "Current / A",
                    "Unix Time / s",
                    "Cycle Count / 1",
                    "Step Index / 1",
                    "Charging Capacity / Ah",
                    "Discharging Capacity / Ah",
                    "Step Capacity / Ah",
                    "Net Capacity / Ah",
                    "Cumulative Capacity / Ah",
                    "Charging Energy / Wh",
                    "Discharging Energy / Wh",
                    "Step Energy / Wh",
                    "Cumulative Energy / Wh",
                    "Surface Temperature T1 / degC",
                }
            ),
            marks=(pytest.mark.network,),
        ),
    ),
]
