"""Integration tests for bdf.plugins detection pipeline — real file and URL sources."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bdf import io
from bdf.file_utils import read_head
from bdf.plugins import (
    NEWARE_CSV,
    PLUGINS,
    detect,
    detect_from_columns,
    detect_from_ext_or_magic_bytes,
    detect_from_metadata,
    dump_plugins,
    load_plugins,
)
from integration.test_cases import (
    ALL_CASES,
    SampleCase,
    get_sample_data_source,
)


@pytest.mark.parametrize(
    "cid,case",
    [pytest.param(cid, c, id=cid, marks=c.marks) for cid, c in ALL_CASES],
)
def test_detect_from_ext_candidate_set(cid: str, case: SampleCase, data_dir: Path) -> None:
    """detect_from_ext_or_magic_bytes returns the expected candidate set per file extension."""
    assert (
        set(detect_from_ext_or_magic_bytes(get_sample_data_source(case.source, case.is_url, data_dir))) == case.ext_ids
    )


@pytest.mark.parametrize(
    "cid,case",
    [pytest.param(cid, c, id=cid, marks=c.marks) for cid, c in ALL_CASES],
)
def test_matches_magic_bytes_for_own_plugin(cid: str, case: SampleCase, data_dir: Path) -> None:
    """Each sample file's real head bytes pass its own plugin's matches_magic_bytes check."""
    head = read_head(get_sample_data_source(case.source, case.is_url, data_dir))
    parser = PLUGINS[case.plugin_id].table_parser
    assert parser.matches_magic_bytes(head) is True


@pytest.mark.parametrize(
    "cid,case",
    [
        pytest.param(cid, c, id=cid, marks=c.marks)
        for cid, c in ALL_CASES
        if not c.plugin_id.endswith(("_xlsx", "_parquet", "_nda"))
    ],
)
def test_text_format_passes_delim_txt_gate(cid: str, case: SampleCase, data_dir: Path) -> None:
    """Real text-vendor files pass DelimTxtParser's text-plausibility gate too (they're all is_text=True)."""
    head = read_head(get_sample_data_source(case.source, case.is_url, data_dir))
    assert PLUGINS["arbin_csv"].table_parser.matches_magic_bytes(head) is True


@pytest.mark.parametrize(
    "cid,case",
    [
        pytest.param(cid, c, id=cid, marks=c.marks)
        for cid, c in ALL_CASES
        if c.plugin_id.endswith(("_xlsx", "_parquet", "_nda"))
    ],
)
def test_binary_format_fails_delim_txt_gate(cid: str, case: SampleCase, data_dir: Path) -> None:
    """Real binary-vendor files are correctly rejected by DelimTxtParser's text-plausibility gate."""
    head = read_head(get_sample_data_source(case.source, case.is_url, data_dir))
    assert PLUGINS["arbin_csv"].table_parser.matches_magic_bytes(head) is False


@pytest.mark.parametrize(
    "cid,case",
    [pytest.param(cid, c, id=cid, marks=c.marks) for cid, c in ALL_CASES],
)
def test_detect_from_metadata_candidate_set(cid: str, case: SampleCase, data_dir: Path) -> None:
    """detect_from_metadata returns the expected candidate set per file magic."""
    assert set(detect_from_metadata(get_sample_data_source(case.source, case.is_url, data_dir))) == case.meta_ids


@pytest.mark.parametrize(
    "cid,case",
    [pytest.param(cid, c, id=cid, marks=c.marks) for cid, c in ALL_CASES],
)
def test_detect_from_columns_selects_winner(cid: str, case: SampleCase, data_dir: Path) -> None:
    """detect_from_columns selects the highest-scoring normalizer for each sample."""
    assert case.cols_id is not None
    plugin_id, plugin = detect_from_columns(get_sample_data_source(case.source, case.is_url, data_dir))
    assert plugin_id == case.cols_id
    assert plugin is PLUGINS[case.cols_id]


@pytest.mark.parametrize(
    "cid,case",
    [pytest.param(cid, c, id=cid, marks=c.marks) for cid, c in ALL_CASES],
)
def test_detect_pipeline_resolves_plugin(cid: str, case: SampleCase, data_dir: Path) -> None:
    """detect pipeline stages execute in order and stop at the deciding stage."""
    import bdf.plugins as _mod

    with (
        patch.object(_mod, "detect_from_ext_or_magic_bytes", wraps=_mod.detect_from_ext_or_magic_bytes) as spy_ext,
        patch.object(_mod, "detect_from_metadata", wraps=_mod.detect_from_metadata) as spy_meta,
        patch.object(_mod, "detect_from_columns", wraps=_mod.detect_from_columns) as spy_cols,
    ):
        plugin_id, plugin = detect(get_sample_data_source(case.source, case.is_url, data_dir))

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


def test_dump_edit_load_round_trip_used_by_io_read(tmp_path: Path) -> None:
    """dump_plugins -> hand-edit a synonym -> load_plugins -> io.read() honours the edit."""
    config_path = tmp_path / "plugins.json"
    dump_plugins({"neware_csv": NEWARE_CSV}, config_path)

    data = json.loads(config_path.read_text())
    data["neware_csv"]["table_parser"]["normalizer"]["voltage_volt"].append("MyVoltage(V)")
    config_path.write_text(json.dumps(data))

    loaded = load_plugins(config_path)

    csv_path = tmp_path / "lab_data.csv"
    csv_path.write_text("MyVoltage(V),Current(mA)\n3.7,500\n3.8,510\n")

    df, _metadata = io.read(csv_path, plugin=loaded["neware_csv"], validate=False)
    assert "Voltage / V" in df.columns
    assert df["Voltage / V"].to_list() == pytest.approx([3.7, 3.8])
