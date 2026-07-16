from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import polars as pl
import pytest

from bdf import io
from bdf.io import read
from bdf.plugins import Plugin
from bdf.table_normalizers import TableNormalizer
from bdf.table_parsers import DelimTxtParser


def test_detect_format_known_and_unknown(tmp_path: Path):
    assert io._detect_format(tmp_path / "file.bdf.csv") == "csv"
    assert io._detect_format(tmp_path / "file.bdf.parquet") == "parquet"
    assert io._detect_format(tmp_path / "file.bdf.json") == "json"


def test_save_and_load_roundtrip_csv_parquet_json(tmp_path: Path):
    df = pd.DataFrame(
        {
            "Test Time / s": [0, 1, 2],
            "Voltage / V": [3.7, 3.6, 3.5],
            "Current / A": [0.1, 0.1, 0.1],
        }
    )

    for fname in ("sample.bdf.csv", "sample.bdf.parquet", "sample.bdf.json"):
        path = tmp_path / fname
        io.save(df, path, index=False)
        loaded = io.load(path)
        pd.testing.assert_frame_equal(df, loaded)


def test_detect_format_unknown_raises(tmp_path: Path):
    bad = tmp_path / "file.unknown"
    bad.touch()
    with pytest.raises(ValueError):
        io._detect_format(bad)


def test_save_defaults_to_notation_and_human_opt_in(tmp_path: Path):
    df = pd.DataFrame(
        {
            "Test Time / s": [0, 1],
            "Voltage / V": [3.7, 3.6],
            "Current / A": [0.1, 0.1],
        }
    )

    machine_path = tmp_path / "machine.bdf.csv"
    io.save(df, machine_path, index=False)
    raw_machine = pd.read_csv(machine_path)
    assert "test_time_second" in raw_machine.columns
    assert "voltage_volt" in raw_machine.columns
    assert "current_ampere" in raw_machine.columns

    loaded = io.load(machine_path)
    assert "Test Time / s" in loaded.columns
    assert "Voltage / V" in loaded.columns
    assert "Current / A" in loaded.columns

    human_path = tmp_path / "human.bdf.csv"
    io.save(df, human_path, index=False, human=True)
    raw_human = pd.read_csv(human_path)
    assert "Test Time / s" in raw_human.columns
    assert "Voltage / V" in raw_human.columns
    assert "Current / A" in raw_human.columns


@pytest.mark.parametrize("fname", ["roundtrip.bdf.csv", "roundtrip.bdf.parquet"])
def test_save_default_artifact_read_validate_roundtrip(tmp_path: Path, fname: str) -> None:
    """save() default notation output is readable by read() with validation enabled.

    Args:
        tmp_path: Temporary directory for the artifact.
        fname: Artifact filename under test.
    """
    df = pd.DataFrame(
        {
            "Test Time / s": [0, 1],
            "Voltage / V": [3.7, 3.6],
            "Current / A": [0.1, 0.1],
        }
    )

    path = tmp_path / fname
    io.save(df, path, index=False)
    loaded, meta = io.read(path, lazy=False)

    assert meta["source"] in {"bdf_csv", "bdf_parquet"}
    assert isinstance(loaded, pl.DataFrame)
    assert loaded.columns == ["Test Time / s", "Voltage / V", "Current / A"]


# ---------------------------------------------------------------------------
# read() orchestration (collaborators mocked)
#
# read() is a thin orchestrator: it resolves a plugin, delegates the actual read
# to table_parser.read(), merges metadata_parser.parse() into the result, and
# returns the frame unchanged. The parsing/normalization/detection logic is
# covered by the per-module unit suites (test_table_parsers, test_table_normalizers,
# test_metadata_parsers, test_plugins); these tests pin only read()'s own wiring —
# which collaborator is called, with which arguments — by patching the three seams.
# ---------------------------------------------------------------------------


@pytest.fixture
def read_mocks(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Patch read()'s three collaborators with mocks and return them.

    A MagicMock installed as a class attribute is not a descriptor, so it does not
    bind ``self``; the recorded call args are exactly what read() passed.

    Args:
        monkeypatch: pytest fixture used to install the patched attributes.

    Returns:
        Namespace with ``plugin`` (a real Plugin whose seams are mocked),
        ``table_read``, ``meta_parse``, and ``detect`` mocks.
    """
    plugin = Plugin(table_parser=DelimTxtParser(normalizer=TableNormalizer()))
    table_read = MagicMock(return_value=pl.DataFrame({"x": [1]}).lazy())
    meta_parse = MagicMock(return_value={})
    detect = MagicMock(return_value=("detected_id", plugin))
    monkeypatch.setattr("bdf.table_parsers.TableParser.read", table_read)
    monkeypatch.setattr("bdf.metadata_parsers.MetadataParser.parse", meta_parse)
    monkeypatch.setattr("bdf.io.detect", detect)
    return SimpleNamespace(plugin=plugin, table_read=table_read, meta_parse=meta_parse, detect=detect)


def test_read_plugin_none_delegates_to_detect(read_mocks: SimpleNamespace, tmp_path: Path) -> None:
    """read(plugin=None) calls detect(path) and takes its plugin id as the source."""
    p = tmp_path / "f.csv"
    _, meta = read(p)
    read_mocks.detect.assert_called_once_with(p)
    assert meta["source"] == "detected_id"


def test_read_plugin_str_uses_registry_not_detect(
    read_mocks: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """read(plugin='vend') resolves via PLUGINS and never calls detect()."""
    monkeypatch.setattr("bdf.io.PLUGINS", {"vend": read_mocks.plugin})
    p = tmp_path / "f.csv"
    _, meta = read(p, plugin="vend")
    assert meta["source"] == "vend"
    read_mocks.detect.assert_not_called()


def test_read_plugin_instance_is_custom_and_skips_detect(read_mocks: SimpleNamespace, tmp_path: Path) -> None:
    """read(plugin=<Plugin>) uses it directly, sets source='custom', never calls detect()."""
    p = tmp_path / "f.csv"
    _, meta = read(p, plugin=read_mocks.plugin)
    assert meta["source"] == "custom"
    read_mocks.detect.assert_not_called()


def test_read_plugin_invalid_type_raises(tmp_path: Path) -> None:
    """read(plugin=42) raises ValueError for an unsupported plugin argument type."""
    p = tmp_path / "f.csv"
    with pytest.raises(ValueError, match="invalid plugin argument"):
        read(p, plugin=42)  # type: ignore[arg-type]


def test_read_forwards_all_read_kwargs_to_table_parser(read_mocks: SimpleNamespace, tmp_path: Path) -> None:
    """read() forwards path + the six read-shaping kwargs (incl. lazy, tz) verbatim, nothing else."""
    p = tmp_path / "f.csv"
    read(
        p,
        plugin=read_mocks.plugin,
        normalize=False,
        validate=False,
        include_optional=False,
        extra_columns={"a": "b"},
        lazy=False,
        tz="America/New_York",
    )
    read_mocks.table_read.assert_called_once_with(
        p,
        normalize=False,
        validate=False,
        include_optional=False,
        extra_columns={"a": "b"},
        lazy=False,
        tz="America/New_York",
    )


def test_read_merges_metadata_parser_output(read_mocks: SimpleNamespace, tmp_path: Path) -> None:
    """read() calls metadata_parser.parse(path) and merges its keys alongside source."""
    read_mocks.meta_parse.return_value = {"start_time": "2024-01-15", "channel": "3"}
    p = tmp_path / "f.csv"
    _, meta = read(p, plugin=read_mocks.plugin)
    read_mocks.meta_parse.assert_called_once_with(p)
    assert meta == {"source": "custom", "start_time": "2024-01-15", "channel": "3"}


def test_read_returns_table_parser_frame_unchanged(read_mocks: SimpleNamespace, tmp_path: Path) -> None:
    """read() returns the exact frame from table_parser.read (collection is the parser's job)."""
    sentinel = pl.DataFrame({"x": [1, 2]})
    read_mocks.table_read.return_value = sentinel
    p = tmp_path / "f.csv"
    result, _ = read(p, plugin=read_mocks.plugin, lazy=False)
    assert result is sentinel


class TestDeprecatedLabelRedirects:
    """Load and save paths route deprecated labels via dcterms:isReplacedBy."""

    def test_canonicalize_renames_to_replacement(self):
        """Regression: step_capacity_ah was renamed to its own deprecated pref label
        instead of the replacement's."""
        df = pd.DataFrame({"step_capacity_ah": [0.5], "test_time_second": [0.0]})
        out, legacy = io.canonicalize_legacy_labels(df)
        assert "Step Cumulative Capacity / Ah" in out.columns
        assert "Step Capacity / Ah" not in out.columns
        assert "step_capacity_ah" in legacy

    def test_label_maps_route_deprecated_notation_to_replacement_pref(self):
        """The save-path label maps must also honor the replacement link."""
        _, machine_to_pref = io._label_maps()
        assert machine_to_pref["step_capacity_ah"] == "Step Cumulative Capacity / Ah"
        assert machine_to_pref["step_energy_wh"] == "Step Cumulative Energy / Wh"
        assert machine_to_pref["test_time_millisecond"] == "Test Time / s"
