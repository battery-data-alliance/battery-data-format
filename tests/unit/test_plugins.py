"""Unit tests for Plugin model, PLUGINS, and detection functions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from bdf.normalizers import NORMALIZERS, TableNormalizer
from bdf.plugins import (
    BIOLOGIC_MPT,
    MACCOR_CSV,
    NEWARE_CSV,
    PLUGINS,
    Plugin,
    detect,
    detect_from_columns,
    detect_from_ext,
    detect_from_metadata,
)
from bdf.table_parsers import DelimTxtParser


@pytest.mark.parametrize("ds", list(PLUGINS.values()), ids=list(PLUGINS))
def test_plugin_json_round_trip(ds: Plugin) -> None:
    """Every built-in Plugin survives model_dump_json → model_validate_json."""
    assert Plugin.model_validate_json(ds.model_dump_json()) == ds


def test_plugin_defaults_metadata_to_inert_parser() -> None:
    """A Plugin built from a table_parser alone gets an inert base MetadataParser."""
    p = Plugin(table_parser=DelimTxtParser(normalizer=NORMALIZERS["arbin"]))
    assert p.table_parser.normalizer is NORMALIZERS["arbin"]
    assert p.metadata_parser.kind == "base"


def test_plugin_legacy_fields_raise() -> None:
    """Passing legacy top-level reader/normalizer fields raises ValidationError."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Plugin(normalizer=TableNormalizer(), reader=DelimTxtParser())  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# TableParser.matches_ext
# ---------------------------------------------------------------------------


def test_matches_ext_unique_ext() -> None:
    assert DelimTxtParser(unique_exts=frozenset({".mpt"})).matches_ext(".mpt") is True


def test_matches_ext_case_insensitive() -> None:
    assert DelimTxtParser().matches_ext(".CSV") is True


# ---------------------------------------------------------------------------
# detect_from_ext
# ---------------------------------------------------------------------------


def test_detect_from_ext_distinctive_ext() -> None:
    result = detect_from_ext("data.mpt")
    assert len(result) == 1
    assert "biologic_mpt" in result
    assert result["biologic_mpt"].table_parser is BIOLOGIC_MPT.table_parser


def test_detect_from_ext_shared_ext() -> None:
    result = detect_from_ext("data.csv")
    assert len(result) >= 1
    assert all(p.table_parser.matches_ext(".csv") for p in result.values())


def test_detect_from_ext_unknown_raises() -> None:
    with pytest.raises(ValueError, match="no table parser registered"):
        detect_from_ext("data.xyz")


def test_detect_from_ext_with_cands() -> None:
    cands = {"biologic_mpt": BIOLOGIC_MPT, "neware_csv": NEWARE_CSV}
    result = detect_from_ext("data.mpt", cands)
    assert list(result) == ["biologic_mpt"]


def test_detect_from_ext_url() -> None:
    result = detect_from_ext("https://example.com/data.mpt")
    assert "biologic_mpt" in result


# ---------------------------------------------------------------------------
# detect_from_metadata
# ---------------------------------------------------------------------------


def test_detect_from_metadata_match_narrows(tmp_path: Path) -> None:
    """Only plugins whose metadata parser matches are returned."""
    p = tmp_path / "data.mpt"
    p.write_text("BT-Lab ASCII FILE\nsome biologic content\n")
    result = detect_from_metadata(p)
    assert all(plugin.metadata_parser.matches(p) for plugin in result.values())
    assert len(result) < len(PLUGINS)


def test_detect_from_metadata_no_match_returns_unchanged(tmp_path: Path) -> None:
    """When nothing matches, the candidate dict is returned unchanged."""
    p = tmp_path / "data.csv"
    p.write_text("totally generic content with no magic tokens\n")
    cands = dict(PLUGINS)
    result = detect_from_metadata(p, cands)
    assert result is cands


def test_detect_from_metadata_maccor_magic(tmp_path: Path) -> None:
    """detect_from_metadata recognises the Maccor preamble magic."""
    p = tmp_path / "data.csv"
    p.write_text("Date of Test:,2021-01-01\n")
    result = detect_from_metadata(p, {"maccor_csv": MACCOR_CSV})
    assert "maccor_csv" in result


# ---------------------------------------------------------------------------
# detect_from_columns
# ---------------------------------------------------------------------------


def test_detect_from_columns_clear_winner(tmp_path: Path) -> None:
    """detect_from_columns returns the plugin whose normalizer scores highest."""
    p = tmp_path / "data.csv"
    rows = "\n".join("0.1,3.5,1" for _ in range(6))
    p.write_text(f"time/s,Ewe/V,I/mA\n{rows}\n")
    plugin_id, plugin = detect_from_columns(p)
    assert plugin_id == "biologic_mpt"
    assert plugin.table_parser.normalizer is NORMALIZERS["biologic"]


def test_detect_from_columns_zero_score_raises(tmp_path: Path) -> None:
    """detect_from_columns raises when no candidate scores above zero."""
    p = tmp_path / "data.csv"
    rows = "\n".join("1,2,3" for _ in range(6))
    p.write_text(f"unknown_a,unknown_b,unknown_c\n{rows}\n")
    with pytest.raises(ValueError, match="no candidate scored"):
        detect_from_columns(p)


def test_detect_from_columns_tied_raises(tmp_path: Path) -> None:
    """detect_from_columns raises when the top score is tied."""
    from bdf.normalizers import Syn

    p = tmp_path / "tied.csv"
    rows = "\n".join("1,2" for _ in range(6))
    p.write_text(f"col_a,col_b\n{rows}\n")
    cands = {
        "a": Plugin(
            table_parser=DelimTxtParser(normalizer=TableNormalizer(voltage_volt=(Syn("col_a"),)), separator=",")
        ),
        "b": Plugin(
            table_parser=DelimTxtParser(normalizer=TableNormalizer(current_ampere=(Syn("col_a"),)), separator=",")
        ),
    }
    with pytest.raises(ValueError, match="ambiguous"):
        detect_from_columns(p, cands)


# ---------------------------------------------------------------------------
# detect() integration
# ---------------------------------------------------------------------------


def test_neware_csv_detects_by_extension_and_headers(tmp_path: Path) -> None:
    """A neware-style CSV resolves to neware_csv via the shared normalizer scoring."""
    p = tmp_path / "neware.csv"
    rows = "\n".join(f"{i},{i},{i},{i},{i}" for i in range(6))
    p.write_text(f"date,total time,cycle,step,record\n{rows}\n")
    plugin_id, plugin = detect(p)
    assert plugin_id == "neware_csv"
    assert plugin.table_parser.normalizer is NORMALIZERS["neware"]


def test_neware_xlsx_detects_by_extension(data_dir: Path) -> None:
    """The neware xlsx sample resolves to neware_xlsx, sharing the neware normalizer (R1)."""
    pytest.importorskip("fastexcel")
    p = data_dir / "neware/sample_data_neware.xlsx"
    if not p.exists():
        pytest.skip("neware xlsx sample not present")
    plugin_id, plugin = detect(p)
    assert plugin_id == "neware_xlsx"
    assert plugin.table_parser.normalizer is NORMALIZERS["neware"]


# ---------------------------------------------------------------------------
# Zenodo URLs (record 18214281) — same record as test_metadata_parsers
# ---------------------------------------------------------------------------

_ZENODO_BASE = "https://zenodo.org/api/records/18214281/files"
_ZENODO_BASYTEC_URL = f"{_ZENODO_BASE}/DLR__LiLNMOHydra0b__20221130__GITT__25degC__Basytec.txt/content"
_ZENODO_BIOLOGIC_URL = f"{_ZENODO_BASE}/SINTEF__NaCR32140-MP10-04__2025-08-25__GITT_0p05C_25degC__BioLogic.mpt/content"

# ---------------------------------------------------------------------------
# Parameterised: detection pipeline — shared cases for all four stages
#
# Columns: source, is_url, ext_ids, meta_ids, cols_id, detect_id
#   ext_ids   — frozenset expected from detect_from_ext
#   meta_ids  — frozenset expected from detect_from_metadata;
#               frozenset(PLUGINS) means no magic token matched (cands unchanged)
#   cols_id   — plugin ID expected from detect_from_columns;
#               None = preamble prevents header scan, column stage skipped
#   detect_id — plugin ID expected from detect()
# ---------------------------------------------------------------------------

_ALL_DELIM_IDS = frozenset(
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

_CASES = [
    pytest.param(
        "biologic/Sample_data_biologic_no_header.mpt",
        False,
        frozenset({"biologic_mpt"}),  # unique .mpt extension
        frozenset(PLUGINS),  # no preamble magic in this file
        "biologic_mpt",  # columns recognisable
        "biologic_mpt",  # exits at ext stage
        "ext",
        id="biologic/mpt",
    ),
    pytest.param(
        "biologic/Sample_data_biologic_01_MB_CA1.txt",
        False,
        _ALL_DELIM_IDS,  # shared .txt extension
        frozenset({"biologic_mpt"}),  # BT-Lab magic narrows
        "biologic_mpt",
        "biologic_mpt",  # exits at metadata stage
        "metadata",
        id="biologic/txt",
    ),
    pytest.param(
        "basytec/sample_data_basytec.txt",
        False,
        _ALL_DELIM_IDS,
        frozenset({"basytec_txt"}),  # Basytec magic narrows
        "basytec_txt",
        "basytec_txt",
        "metadata",
        id="basytec/local",
    ),
    pytest.param(
        "maccor/sample_data_maccor.csv",
        False,
        _ALL_DELIM_IDS,
        frozenset({"maccor_csv"}),  # Date-of-Test magic narrows
        "maccor_csv",
        "maccor_csv",
        "metadata",
        id="maccor/local",
    ),
    pytest.param(
        "novonix/sample_data_novonix.csv",
        False,
        _ALL_DELIM_IDS,
        frozenset({"novonix_csv"}),  # [Summary] magic narrows
        "novonix_csv",
        "novonix_csv",
        "metadata",
        id="novonix/local",
    ),
    pytest.param(
        "arbin/sample_data_arbin.csv",
        False,
        _ALL_DELIM_IDS,
        frozenset(PLUGINS),  # no preamble magic
        "arbin_csv",  # columns decide
        "arbin_csv",  # exits at columns stage
        "columns",
        id="arbin/local",
    ),
    pytest.param(
        _ZENODO_BASYTEC_URL,
        True,
        _ALL_DELIM_IDS,  # .txt extension
        frozenset({"basytec_txt"}),  # magic present
        "basytec_txt",  # space-delimited; space is a detected candidate
        "basytec_txt",  # exits at metadata stage
        "metadata",
        id="basytec/url",
        marks=pytest.mark.network,
    ),
    pytest.param(
        _ZENODO_BIOLOGIC_URL,
        True,
        frozenset({"biologic_mpt"}),  # unique .mpt extension
        frozenset({"biologic_mpt"}),  # BT-Lab magic also present
        "biologic_mpt",
        "biologic_mpt",  # exits at ext stage
        "ext",
        id="biologic/url",
        marks=pytest.mark.network,
    ),
]


def _resolve(source: str, is_url: bool, data_dir: Path) -> str | Path:
    if is_url:
        pytest.importorskip("requests")
        return source
    p = data_dir / source
    if not p.exists():
        pytest.skip(f"sample data not present: {source}")
    return p


@pytest.mark.parametrize("source,is_url,ext_ids,meta_ids,cols_id,detect_id,deciding_stage", _CASES)
def test_detect_from_ext_candidate_set(
    source: str,
    is_url: bool,
    ext_ids: frozenset[str],
    meta_ids: frozenset[str],
    cols_id: str | None,
    detect_id: str,
    deciding_stage: str,
    data_dir: Path,
) -> None:
    assert set(detect_from_ext(_resolve(source, is_url, data_dir))) == ext_ids


@pytest.mark.parametrize("source,is_url,ext_ids,meta_ids,cols_id,detect_id,deciding_stage", _CASES)
def test_detect_from_metadata_candidate_set(
    source: str,
    is_url: bool,
    ext_ids: frozenset[str],
    meta_ids: frozenset[str],
    cols_id: str | None,
    detect_id: str,
    deciding_stage: str,
    data_dir: Path,
) -> None:
    assert set(detect_from_metadata(_resolve(source, is_url, data_dir))) == meta_ids


@pytest.mark.parametrize("source,is_url,ext_ids,meta_ids,cols_id,detect_id,deciding_stage", _CASES)
def test_detect_from_columns_selects_winner(
    source: str,
    is_url: bool,
    ext_ids: frozenset[str],
    meta_ids: frozenset[str],
    cols_id: str | None,
    detect_id: str,
    deciding_stage: str,
    data_dir: Path,
) -> None:
    assert cols_id is not None
    plugin_id, plugin = detect_from_columns(_resolve(source, is_url, data_dir))
    assert plugin_id == cols_id
    assert plugin is PLUGINS[cols_id]


@pytest.mark.parametrize("source,is_url,ext_ids,meta_ids,cols_id,detect_id,deciding_stage", _CASES)
def test_detect_pipeline_resolves_plugin(
    source: str,
    is_url: bool,
    ext_ids: frozenset[str],
    meta_ids: frozenset[str],
    cols_id: str | None,
    detect_id: str,
    deciding_stage: str,
    data_dir: Path,
) -> None:
    import bdf.plugins as _mod

    with (
        patch.object(_mod, "detect_from_ext", wraps=_mod.detect_from_ext) as spy_ext,
        patch.object(_mod, "detect_from_metadata", wraps=_mod.detect_from_metadata) as spy_meta,
        patch.object(_mod, "detect_from_columns", wraps=_mod.detect_from_columns) as spy_cols,
    ):
        plugin_id, plugin = detect(_resolve(source, is_url, data_dir))

    assert plugin_id == detect_id
    assert plugin is PLUGINS[detect_id]

    assert spy_ext.called, "ext stage not run"
    if deciding_stage == "ext":
        assert not spy_meta.called, "metadata stage ran — expected ext to be decisive"
        assert not spy_cols.called, "columns stage ran — expected ext to be decisive"
    elif deciding_stage == "metadata":
        assert spy_meta.called
        assert not spy_cols.called, "columns stage ran — expected metadata to be decisive"
    elif deciding_stage == "columns":
        assert spy_meta.called
        assert spy_cols.called
