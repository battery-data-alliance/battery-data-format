"""Unit tests for Plugin model, PLUGINS, and detection functions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import ALL_CASES, SampleCase, resolve_source

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
# Parametrised: detection pipeline — all cases from conftest.ALL_CASES
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cid,case",
    [pytest.param(cid, c, id=cid, marks=c.marks) for cid, c in ALL_CASES],
)
def test_detect_from_ext_candidate_set(cid: str, case: SampleCase, data_dir: Path) -> None:
    assert set(detect_from_ext(resolve_source(case.source, case.is_url, data_dir))) == case.ext_ids


@pytest.mark.parametrize(
    "cid,case",
    [pytest.param(cid, c, id=cid, marks=c.marks) for cid, c in ALL_CASES],
)
def test_detect_from_metadata_candidate_set(cid: str, case: SampleCase, data_dir: Path) -> None:
    assert set(detect_from_metadata(resolve_source(case.source, case.is_url, data_dir))) == case.meta_ids


@pytest.mark.parametrize(
    "cid,case",
    [pytest.param(cid, c, id=cid, marks=c.marks) for cid, c in ALL_CASES],
)
def test_detect_from_columns_selects_winner(cid: str, case: SampleCase, data_dir: Path) -> None:
    assert case.cols_id is not None
    plugin_id, plugin = detect_from_columns(resolve_source(case.source, case.is_url, data_dir))
    assert plugin_id == case.cols_id
    assert plugin is PLUGINS[case.cols_id]


@pytest.mark.parametrize(
    "cid,case",
    [pytest.param(cid, c, id=cid, marks=c.marks) for cid, c in ALL_CASES],
)
def test_detect_pipeline_resolves_plugin(cid: str, case: SampleCase, data_dir: Path) -> None:
    import bdf.plugins as _mod

    with (
        patch.object(_mod, "detect_from_ext", wraps=_mod.detect_from_ext) as spy_ext,
        patch.object(_mod, "detect_from_metadata", wraps=_mod.detect_from_metadata) as spy_meta,
        patch.object(_mod, "detect_from_columns", wraps=_mod.detect_from_columns) as spy_cols,
    ):
        plugin_id, plugin = detect(resolve_source(case.source, case.is_url, data_dir))

    assert plugin_id == case.detect_id
    assert plugin is PLUGINS[case.detect_id]

    assert spy_ext.called, "ext stage not run"
    if case.deciding_stage == "ext":
        assert not spy_meta.called, "metadata stage ran — expected ext to be decisive"
        assert not spy_cols.called, "columns stage ran — expected ext to be decisive"
    elif case.deciding_stage == "metadata":
        assert spy_meta.called
        assert not spy_cols.called, "columns stage ran — expected metadata to be decisive"
    elif case.deciding_stage == "columns":
        assert spy_meta.called
        assert spy_cols.called
