from __future__ import annotations

import json
import re
import shutil
import warnings
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy
import polars as pl
import pytest
from polars.testing import assert_frame_equal, assert_series_equal
from pydantic import ValidationError

import bdf.battinfo
from bdf import (
    BDFMetadataError,
    BdfReadInfo,
    BDFValidationError,
    Metadata,
    io,
)
from bdf._errors import BDFDefinitionError, BDFDefinitionWarning, BDFMetadataWarning
from bdf.battinfo import FIELD_SCHEMAS, BattinfoSchema
from bdf.io import read, scan
from bdf.metadata_parsers import JsonRule, JsonSidecarParser, RegexRule, TxtPreambleParser
from bdf.normalization import AbsoluteTimeNormalization
from bdf.plugins import Plugin
from bdf.spec import COLUMN_ONTOLOGY
from bdf.table_normalizers import Syn, TableNormalizer
from bdf.table_parsers import DelimTxtParser

from ._battinfo import (
    FakeRawGitHub,
    bundled_docs,
    cache_release,
    point_bundle_at,
    release_docs,
    serve_raw_github,
)

# An epoch with no significance beyond being a value; the round-trip check
# below asserts it survives unchanged, not that it names any particular instant.
_PLACEHOLDER_EPOCH = 1700000000


def test_detect_format_known_and_unknown(tmp_path: Path):
    assert io._detect_format(tmp_path / "file.bdf.csv") == "csv"
    assert io._detect_format(tmp_path / "file.bdf.parquet") == "parquet"
    assert io._detect_format(tmp_path / "file.bdf.pq") == "parquet"
    assert io._detect_format(tmp_path / "file.bdf.json") == "json"
    assert io._detect_format(tmp_path / "file.bdf.ndjson") == "ndjson"
    assert io._detect_format(tmp_path / "file.bdf.feather") == "ipc"
    assert io._detect_format(tmp_path / "file.bdf.arrow") == "ipc"
    assert io._detect_format(tmp_path / "file.bdf.ipc") == "ipc"
    assert io._detect_format(tmp_path / "file.bdf.xlsx") == "xlsx"

    assert io._detect_format(tmp_path / "file.bdf.csv.gz") == "csv"
    assert io._detect_format(tmp_path / "file.bdf.csv.bz2") == "csv"
    assert io._detect_format(tmp_path / "file.bdf.csv.xz") == "csv"
    assert io._detect_format(tmp_path / "file.bdf.csv.zst") == "csv"


def test_save_and_load_roundtrips(tmp_path: Path):
    df = pl.DataFrame(
        {
            "Test Time / s": [0.0, 1.0, 2.0],
            "Voltage / V": [3.7, 3.6, 3.5],
            "Current / A": [0.1, 0.1, 0.1],
        }
    )

    exts = [".csv", ".parquet", ".json", ".ndjson", ".feather", ".arrow", ".ipc", ".xlsx"]
    comps = ["", ".gz", ".bz2", ".xz", ".zst"]

    for ext in exts:
        for comp in comps:
            path = tmp_path / ("data.bdf" + ext + comp)
            if ext == ".xlsx" and comp:
                with pytest.raises(ValueError, match="Compression is not supported for xlsx"):
                    io.save(df, path)
            else:
                io.save(df, path)
                loaded, _metadata = io.read(path)
                assert_frame_equal(df, loaded)


def test_compression_compresses(tmp_path: Path):
    df = pl.DataFrame(
        {  # Need more datapoints for compression to be able to do anything
            "Test Time / s": pl.linear_space(0, 1000, 1000, eager=True),
            "Voltage / V": pl.linear_space(3.5, 4.2, 1000, eager=True),
            "Current / A": pl.linear_space(1.0, 1.0, 1000, eager=True),
        }
    )
    path = tmp_path / "data.bdf.csv"
    io.save(df, path)
    uncompressed_size = path.stat().st_size

    comps = [".gz", ".bz2", ".xz", ".zst"]
    for comp in comps:
        path = tmp_path / ("data.bdf.csv" + comp)
        io.save(df, path)
        assert path.stat().st_size < uncompressed_size


def test_save_lazyframe_roundtrips(tmp_path: Path):
    df = pl.DataFrame(
        {
            "Test Time / s": [0.0, 1.0, 2.0],
            "Voltage / V": [3.7, 3.6, 3.5],
            "Current / A": [0.1, 0.1, 0.1],
        }
    )

    exts = [".csv", ".parquet", ".json", ".ndjson", ".feather", ".arrow", ".ipc", ".xlsx"]
    comps = ["", ".gz", ".bz2", ".xz", ".zst"]

    for ext in exts:
        for comp in comps:
            path = tmp_path / ("lazy.bdf" + ext + comp)
            if ext == ".xlsx" and comp:
                with pytest.raises(ValueError, match="Compression is not supported for xlsx"):
                    io.save(df.lazy(), path)
            else:
                io.save(df.lazy(), path)
                loaded, _metadata = io.read(path)
                assert_frame_equal(df, loaded)


@pytest.mark.parametrize(
    ("ext", "expected_writer"),
    [
        (".csv", "sink_csv"),
        (".csv.gz", "sink_csv"),
        (".parquet", "sink_parquet"),
        (".ipc", "sink_ipc"),
        (".ndjson", "sink_ndjson"),
        (".json", "write_json"),
        (".xlsx", "write_excel"),
    ],
)
def test_save_lazyframe_uses_the_streaming_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ext: str, expected_writer: str
):
    """save() streams a LazyFrame through sink_*, and collects only where no sink exists.

    Args:
        tmp_path: Temporary directory for the artifact.
        monkeypatch: Fixture used to record which polars writer save() calls.
        ext: Artifact extension under test.
        expected_writer: Name of the polars writer the format must use.
    """
    df = pl.DataFrame(
        {
            "Test Time / s": [0.0, 1.0],
            "Voltage / V": [3.7, 3.6],
            "Current / A": [0.1, 0.1],
        }
    )
    path = tmp_path / ("sink.bdf" + ext)

    called: list[str] = []
    watched = [(pl.LazyFrame, spec.sink) for spec in io._FORMATS.values() if spec.sink is not None]
    watched += [(pl.DataFrame, spec.write) for spec in io._FORMATS.values() if spec.sink is None]

    with monkeypatch.context() as m:
        for owner, name in watched:
            original = getattr(owner, name)

            def recording(self, *args, _name=name, _original=original, **kwargs):
                called.append(_name)
                return _original(self, *args, **kwargs)

            m.setattr(owner, name, recording)
        io.save(df.lazy(), path)

    assert called == [expected_writer]
    assert path.exists()


def test_save_xlsx_compression_writes_no_file(tmp_path: Path):
    """A compressed xlsx target raises before save() creates the file."""
    df = pl.DataFrame({"Voltage / V": [3.7]})
    path = tmp_path / "data.bdf.xlsx.gz"

    with pytest.raises(ValueError, match="Compression is not supported for xlsx"):
        io.save(df, path, validate=False)

    assert not path.exists()


def test_detect_format_unknown_raises(tmp_path: Path):
    bad = tmp_path / "file.unknown"
    bad.touch()
    with pytest.raises(ValueError):
        io._detect_format(bad)


def test_save_validation(tmp_path: Path):
    df_v = pl.DataFrame({"Voltage / V": [3.7, 3.6, 3.5]})
    path = tmp_path / "sample.bdf.csv"

    # With validate will fail
    with pytest.raises(BDFValidationError):
        io.save(df_v, path)

    # Without validation, it will save
    io.save(df_v, path, validate=False)

    # Reading with validation fails
    with pytest.raises(BDFValidationError):
        io.read(path)

    # Reading without validation works
    loaded, _metadata = io.read(path, validate=False)
    assert_frame_equal(df_v, loaded)

    # Non-standard column
    df_mv = pl.DataFrame({"Voltage / mV": [3.7, 3.6, 3.5]})

    # Validate will fail (missing cols)
    with pytest.raises(BDFValidationError):
        io.save(df_mv, path)

    # No validate saves as-is
    io.save(df_mv, path, validate=False)
    loaded, _metadata = io.read(path, validate=False, normalize=False)
    assert "Voltage / mV" in loaded.columns
    loaded = loaded.cast({"Voltage / mV": pl.Float64})
    assert_frame_equal(df_mv, loaded)

    # No validate will still normalize name/units reading back by default
    io.save(df_mv, path, validate=False)
    loaded, _metadata = io.read(path, validate=False)
    assert "Voltage / V" in loaded.columns
    loaded = loaded.with_columns((pl.col("Voltage / V") * 1000).alias("Voltage / mV"))
    assert_series_equal(df_mv["Voltage / mV"], loaded["Voltage / mV"])


def test_save_with_extra_cols(tmp_path: Path):
    """Save should keep additional columns by default."""
    df = pl.DataFrame(
        {
            "Test Time / s": [0.0, 1.0],
            "Voltage / V": [3.7, 3.6],
            "Current / A": [0.1, 0.1],
            "Thing I Just Calculated / %": [30.0, 40.0],
        }
    )
    path = tmp_path / "sample.bdf.parquet"
    with pytest.warns(UserWarning, match="Non-BDF columns present"):
        io.save(df, path)

    # Raw data contains extra column
    df2 = pl.read_parquet(path)
    assert_frame_equal(df, df2)


def test_save_legacy_warns(tmp_path: Path):
    df = pl.DataFrame(
        {
            "Test Time / ms": [0.0, 1.0],
            "Voltage / V": [3.7, 3.6],
            "Current / A": [0.1, 0.1],
        }
    )
    path = tmp_path / "sample.bdf.csv"
    with pytest.warns(UserWarning, match="Legacy BDF column labels detected"):
        io.save(df, path)
    with pytest.warns(UserWarning, match="Legacy BDF column labels detected"):
        io.save(df, path, strict=False)


def test_save_missing_col_warns(tmp_path: Path):
    df = pl.DataFrame(
        {
            "Voltage / V": [3.7, 3.6],
            "Current / A": [0.1, 0.1],
        }
    )
    path = tmp_path / "sample.bdf.csv"
    with pytest.raises(BDFValidationError):
        io.save(df, path)
    with pytest.warns(UserWarning, match="Missing required BDF columns: \['Test Time / s'\]"):
        io.save(df, path, strict=False)


def test_save_extra_col_warns(tmp_path: Path):
    df = pl.DataFrame(
        {
            "Test Time / s": [0.0, 1.0],
            "Voltage / V": [3.7, 3.6],
            "Current / A": [0.1, 0.1],
            "foo": [1, 2],
        }
    )
    path = tmp_path / "sample.bdf.csv"
    with pytest.warns(UserWarning, match="Non-BDF columns present"):
        io.save(df, path)
    with pytest.warns(UserWarning, match="Non-BDF columns present"):
        io.save(df, path, strict=False)


def test_save_non_canonical_units_warns(tmp_path: Path):
    df = pl.DataFrame(
        {
            "Test Time / s": [0.0, 1.0],
            "Voltage / mV": [3.7, 3.6],
            "Current / uA": [0.1, 0.1],
        }
    )
    path = tmp_path / "sample.bdf.csv"
    with pytest.warns(UserWarning, match="Columns not using the canonical BDF unit"):
        io.save(df, path)


def test_save_bad_columns_errors(tmp_path: Path):
    # Bad columns error contains both missing and unrecognized columns
    df = pl.DataFrame(
        {
            "test_time_s": [0.0, 1.0],
            "voltage_millivolt": [3.7, 3.6],
            "current_microampere": [0.1, 0.1],
        }
    )
    path = tmp_path / "sample.bdf.csv"
    with pytest.raises(
        BDFValidationError, match=r"(?=.*Missing required BDF columns)(?=.*unrecognized columns present)"
    ):
        io.save(df, path)


def test_save_good_columns_dont_warn(tmp_path: Path):
    df = pl.DataFrame(
        {
            "Test Time / s": [0.0, 1.0],
            "Voltage / V": [3.7, 3.6],
            "Current / A": [0.1, 0.1],
        }
    )
    path = tmp_path / "sample.bdf.csv"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        io.save(df, path)
        io.save(df, path, validate=False)
        io.save(df, path, labels="machine")
        io.save(df, path, labels="preferred")

    df = pl.DataFrame(
        {
            "test_time_second": [0.0, 1.0],
            "voltage_volt": [3.7, 3.6],
            "current_ampere": [0.1, 0.1],
        }
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        io.save(df, path)
        io.save(df, path, validate=False)
        io.save(df, path, labels="machine")
        io.save(df, path, labels="preferred")


@pytest.mark.parametrize("fname", ["roundtrip.bdf.csv", "roundtrip.bdf.parquet"])
def test_save_default_artifact_read_validate_roundtrip(tmp_path: Path, fname: str) -> None:
    """save() default notation output is readable by read() with validation enabled.

    Args:
        tmp_path: Temporary directory for the artifact.
        fname: Artifact filename under test.
    """
    df = pl.DataFrame(
        {
            "Test Time / s": [0, 1],
            "Voltage / V": [3.7, 3.6],
            "Current / A": [0.1, 0.1],
        }
    )

    path = tmp_path / fname
    io.save(df, path)
    loaded, meta = io.read(path)

    assert meta.bdf.source in {"bdf_csv", "bdf_parquet"}  # type: ignore[attr-defined]
    assert isinstance(loaded, pl.DataFrame)
    assert loaded.columns == ["Test Time / s", "Voltage / V", "Current / A"]


# ---------------------------------------------------------------------------
# read() orchestration (collaborators mocked)
#
# read() is a thin orchestrator: it resolves a plugin, delegates the actual read to
# table_parser.read(), takes metadata_parser.parse() as the metadata when no reserved
# sidecar exists, and returns the frame unchanged. The parsing/normalization/detection
# logic is covered by the per-module unit suites (test_table_parsers, test_table_normalizers,
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
    table_preamble = MagicMock(return_value=None)
    meta_parse = MagicMock(return_value=Metadata())
    detect = MagicMock(return_value=("detected_id", plugin))
    monkeypatch.setattr("bdf.table_parsers.TableParser.read", table_read)
    monkeypatch.setattr("bdf.table_parsers.DelimTxtParser.preamble", table_preamble)
    monkeypatch.setattr("bdf.metadata_parsers.MetadataParser.parse", meta_parse)
    monkeypatch.setattr("bdf.io.detect", detect)
    return SimpleNamespace(
        plugin=plugin, table_read=table_read, table_preamble=table_preamble, meta_parse=meta_parse, detect=detect
    )


def test_read_plugin_none_delegates_to_detect(read_mocks: SimpleNamespace, tmp_path: Path) -> None:
    """read(plugin=None) calls detect(path) and takes its plugin id as meta.bdf.source."""
    p = tmp_path / "f.csv"
    _, meta = read(p)
    read_mocks.detect.assert_called_once_with(p)
    assert meta.bdf.source == "detected_id"  # type: ignore[attr-defined]


def test_read_plugin_str_uses_registry_not_detect(
    read_mocks: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """read(plugin='vend') resolves via PLUGINS and never calls detect()."""
    monkeypatch.setattr("bdf.io.PLUGINS", {"vend": read_mocks.plugin})
    p = tmp_path / "f.csv"
    _, meta = read(p, plugin="vend")
    assert meta.bdf.source == "vend"  # type: ignore[attr-defined]
    read_mocks.detect.assert_not_called()


def test_read_plugin_instance_is_custom_and_skips_detect(read_mocks: SimpleNamespace, tmp_path: Path) -> None:
    """read(plugin=<Plugin>) uses it directly, sets meta.bdf.source='custom', never calls detect()."""
    p = tmp_path / "f.csv"
    _, meta = read(p, plugin=read_mocks.plugin)
    assert meta.bdf.source == "custom"  # type: ignore[attr-defined]
    read_mocks.detect.assert_not_called()


def test_read_plugin_invalid_type_raises(tmp_path: Path) -> None:
    """read(plugin=42) raises ValueError for an unsupported plugin argument type."""
    p = tmp_path / "f.csv"
    with pytest.raises(ValueError, match="invalid plugin argument"):
        read(p, plugin=42)  # type: ignore[arg-type]


def test_read_forwards_all_read_kwargs_to_table_parser(read_mocks: SimpleNamespace, tmp_path: Path) -> None:
    """read() forwards path + the six read-shaping kwargs verbatim, plus lazy=False."""
    p = tmp_path / "f.csv"
    read(
        p,
        plugin=read_mocks.plugin,
        validate=False,
        strict=False,
        normalize=False,
        include_unknown=True,
        tz="America/New_York",
    )
    read_mocks.table_read.assert_called_once_with(
        p,
        validate=False,
        strict=False,
        normalize=False,
        include_unknown=True,
        lazy=False,
        tz="America/New_York",
        day_month_order=None,
        ontology=COLUMN_ONTOLOGY,
    )


def test_scan_forwards_all_read_kwargs_to_table_parser(read_mocks: SimpleNamespace, tmp_path: Path) -> None:
    """scan() forwards path + the four read-shaping kwargs verbatim, plus lazy=True."""
    p = tmp_path / "f.csv"
    scan(
        p,
        plugin=read_mocks.plugin,
        normalize=False,
        validate=False,
        include_unknown=False,
        tz="America/New_York",
    )
    read_mocks.table_read.assert_called_once_with(
        p,
        normalize=False,
        validate=False,
        strict=True,
        include_unknown=False,
        lazy=True,
        tz="America/New_York",
        day_month_order=None,
        ontology=COLUMN_ONTOLOGY,
    )


def test_read_sets_bdf_source_on_the_parsed_metadata(read_mocks: SimpleNamespace, tmp_path: Path) -> None:
    """read() calls metadata_parser.parse(path, tz=...) and sets bdf.source on the result."""
    read_mocks.meta_parse.return_value = Metadata()
    p = tmp_path / "f.csv"
    _, meta = read(p, plugin=read_mocks.plugin)
    read_mocks.meta_parse.assert_called_once_with(p, tz="UTC", day_month_order=None, preamble_lines=None)
    assert meta.bdf.source == "custom"  # type: ignore[attr-defined]


def test_read_skips_the_preamble_scan_for_a_parser_that_ignores_the_boundary(
    read_mocks: SimpleNamespace, tmp_path: Path
) -> None:
    """read() never scans for the preamble boundary when the metadata parser discards it."""
    p = tmp_path / "f.csv"
    read(p, plugin=read_mocks.plugin)
    read_mocks.table_preamble.assert_not_called()


def test_read_scans_the_preamble_for_a_parser_that_reads_the_boundary(
    read_mocks: SimpleNamespace, tmp_path: Path
) -> None:
    """read() scans for the preamble boundary when the metadata parser reads it."""
    p = tmp_path / "f.csv"
    p.write_text("a,b\n1,2\n")
    plugin = Plugin(table_parser=read_mocks.plugin.table_parser, metadata_parser=TxtPreambleParser())
    read(p, plugin=plugin)
    read_mocks.table_preamble.assert_called_once_with(p)


def test_read_returns_table_parser_frame_unchanged(read_mocks: SimpleNamespace, tmp_path: Path) -> None:
    """read() returns the exact frame from table_parser.read (collection is the parser's job)."""
    sentinel = pl.DataFrame({"x": [1, 2]})
    read_mocks.table_read.return_value = sentinel
    p = tmp_path / "f.csv"
    result, _ = read(p, plugin=read_mocks.plugin)
    assert result is sentinel


def test_read_bdf_files(tmp_path: Path) -> None:
    """Read bdf from various files."""
    df1 = pl.DataFrame(
        {
            "Test Time / s": [1.0, 2.0, 3.0],
            "Voltage / V": [4.0, 4.1, 4.2],
            "Current / A": [0.1, 0.1, 0.1],
        }
    )

    for extra_ext in ("", ".bdf", ".a.b.c", ".a.b.c.bdf"):
        p = tmp_path / f"data{extra_ext}.csv"
        df1.write_csv(p)
        df2, _metadata = io.read(p)
        assert_frame_equal(df1, df2)

        p = tmp_path / f"data{extra_ext}.parquet"
        df1.write_parquet(p)
        df2, _metadata = io.read(p)
        assert_frame_equal(df1, df2)

        p = tmp_path / f"data{extra_ext}.json"
        df1.write_json(p)
        df2, _metadata = io.read(p)
        assert_frame_equal(df1, df2)

        p = tmp_path / f"data{extra_ext}.ndjson"
        df1.write_ndjson(p)
        df2, _metadata = io.read(p)
        assert_frame_equal(df1, df2)

        p = tmp_path / f"data{extra_ext}.ipc"
        df1.write_ipc(p)
        df2, _metadata = io.read(p)
        assert_frame_equal(df1, df2)

        p = tmp_path / f"data{extra_ext}.arrow"
        df1.write_ipc(p)
        df2, _metadata = io.read(p)
        assert_frame_equal(df1, df2)

        p = tmp_path / f"data{extra_ext}.feather"
        df1.write_ipc(p)
        df2, _metadata = io.read(p)
        assert_frame_equal(df1, df2)


def test_read_with_unknown(tmp_path: Path) -> None:
    """Test reading with unknown columns."""
    df1 = pl.DataFrame(
        {
            "Test Time / s": [1.0, 2.0, 3.0],
            "Voltage / V": [4.0, 4.1, 4.2],
            "Current / A": [0.1, 0.1, 0.1],
            "foo": [1, 2, 3],
            "bar": ["b", "a", "r"],
        }
    )
    p = tmp_path / "data.parquet"
    df1.write_parquet(p)

    df2, _metadata = io.read(p)
    assert "foo" not in df2.columns
    assert "bar" not in df2.columns

    df2, _metadata = io.read(p, include_unknown=True)
    assert "foo" in df2.columns
    assert "bar" in df2.columns
    assert_frame_equal(df1, df2)


def test_roundtrip_with_unknown(tmp_path: Path) -> None:
    """Test reading with unknown columns."""
    df1 = pl.DataFrame(
        {
            "Test Time / s": [1.0, 2.0, 3.0],
            "Voltage / V": [4.0, 4.1, 4.2],
            "Current / A": [0.1, 0.1, 0.1],
            "foo": [1, 2, 3],
            "bar": ["b", "a", "r"],
        }
    )
    p1 = tmp_path / "data.parquet"
    df1.write_parquet(p1)

    df2, _metadata = io.read(p1, include_unknown=True)
    assert "foo" in df2.columns
    assert "bar" in df2.columns

    # Save always includes unknown
    p2 = tmp_path / "data.parquet"
    io.save(df2, p2)
    df3, _metadata = io.read(p2, include_unknown=True)
    assert "foo" in df3.columns
    assert "bar" in df3.columns
    assert_frame_equal(df1, df3)

    # Saving/reading unknown works with other files/compression
    p3 = tmp_path / "data.ndjson.gz"
    io.save(df3, p3)
    df4, _metadata = io.read(p3, include_unknown=True)
    assert "foo" in df4.columns
    assert "bar" in df4.columns
    assert_frame_equal(df1, df4)


def test_save_labels(tmp_path: Path) -> None:
    """Test saving with different labels."""
    df_orig = pl.DataFrame(
        {
            "Test Time / s": [1.0, 2.0, 3.0],
            "Voltage / V": [4.0, 4.1, 4.2],
            "Current / A": [0.1, 0.1, 0.1],
        }
    )
    p = tmp_path / "data.parquet"

    def assert_preferred() -> None:
        assert "Test Time / s" in df.columns
        assert "Voltage / V" in df.columns
        assert "Current / A" in df.columns

    def assert_machine() -> None:
        assert "test_time_second" in df.columns
        assert "voltage_volt" in df.columns
        assert "current_ampere" in df.columns

    # Unchanged by default
    io.save(df_orig, p)
    df = pl.read_parquet(p)
    assert_preferred()

    # Explicit unchanged
    io.save(df_orig, p, labels="unchanged")
    df = pl.read_parquet(p)
    assert_preferred()

    # Explicit machine-readable
    io.save(df_orig, p, labels="machine")
    df = pl.read_parquet(p)
    assert_machine()

    # Explicit human-readable
    io.save(df_orig, p, labels="preferred")
    df = pl.read_parquet(p)
    assert_preferred()

    # Test starting from machine-readable
    df_orig = pl.DataFrame(
        {
            "test_time_second": [1.0, 2.0, 3.0],
            "voltage_volt": [4.0, 4.1, 4.2],
            "current_ampere": [0.1, 0.1, 0.1],
        }
    )

    # Unchanged by default
    io.save(df_orig, p)
    df = pl.read_parquet(p)
    assert_machine()

    # Explicit unchanged
    io.save(df_orig, p, labels="unchanged")
    df = pl.read_parquet(p)
    assert_machine()

    # Explicit machine-readable
    io.save(df_orig, p, labels="machine")
    df = pl.read_parquet(p)
    assert_machine()

    # Explicit preferred
    io.save(df_orig, p, labels="preferred")
    df = pl.read_parquet(p)
    assert_preferred()

    # Unknown mode raises
    with pytest.raises(ValueError, match="Mode 'foo' not understood"):
        io.save(df_orig, p, labels="foo")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# read()/scan() time reconciliation (GH #65)
# ---------------------------------------------------------------------------


def _write_bdf_csv_with_ms_test_time(tmp_path: Path, n: int = 30) -> Path:
    """BDF artifact whose Test Time / s values are actually milliseconds."""
    df = pl.DataFrame(
        {
            "Test Time / s": [i * 10.0 * 1e3 for i in range(n)],  # ms under a seconds header
            "Unix Time / s": [1.7e9 + i * 10.0 for i in range(n)],
            "Voltage / V": [3.7] * n,
            "Current / A": [0.1] * n,
        }
    )
    path = tmp_path / "corrupted.bdf.csv"
    df.write_csv(path)
    return path


def test_read_mismatch_raises_by_default(tmp_path: Path) -> None:
    """fsck model: detection is on, repair is not - loud failure, data untouched."""
    path = _write_bdf_csv_with_ms_test_time(tmp_path)
    with pytest.raises(BDFValidationError, match="appear to be milliseconds"):
        read(path)


def test_read_mismatch_warns_when_validate_false(tmp_path: Path) -> None:
    path = _write_bdf_csv_with_ms_test_time(tmp_path)
    with pytest.warns(UserWarning, match="appear to be milliseconds"):
        df, meta = read(path, strict=False)
    # values loaded as-is, nothing repaired or recorded
    assert df["Test Time / s"].to_list()[1] == 10_000.0
    assert meta.bdf.time_reconciliation is None  # type: ignore[attr-defined]


def test_read_reconcile_time_true_repairs_and_records(tmp_path: Path) -> None:
    path = _write_bdf_csv_with_ms_test_time(tmp_path)
    with pytest.warns(UserWarning, match="rescaled to seconds as requested"):
        df, meta = read(path, reconcile_time=True)
    assert df["Test Time / s"].to_list()[:3] == [0.0, 10.0, 20.0]
    (record,) = meta.bdf.time_reconciliation  # type: ignore[misc]
    assert record["column"] == "Test Time / s"
    assert record["actual_unit"] == "milliseconds"
    assert record["action"] == "divided by 1000"


def test_scan_reconcile_time_true_repairs_lazy(tmp_path: Path) -> None:
    path = _write_bdf_csv_with_ms_test_time(tmp_path)
    with pytest.warns(UserWarning, match="rescaled to seconds as requested"):
        lf, meta = scan(path, reconcile_time=True)
    assert isinstance(lf, pl.LazyFrame)
    assert lf.collect()["Test Time / s"].to_list()[1] == 10.0
    assert meta.bdf.time_reconciliation  # type: ignore[attr-defined]


def test_read_consistent_clocks_add_no_metadata(tmp_path: Path) -> None:
    n = 30
    df = pl.DataFrame(
        {
            "Test Time / s": [i * 10.0 for i in range(n)],
            "Unix Time / s": [1.7e9 + i * 10.0 for i in range(n)],
            "Voltage / V": [3.7] * n,
            "Current / A": [0.1] * n,
        }
    )
    path = tmp_path / "clean.bdf.csv"
    df.write_csv(path)
    out, meta = read(path)
    assert out["Test Time / s"].to_list()[1] == 10.0
    assert meta.bdf.time_reconciliation is None  # type: ignore[attr-defined]


def test_read_unexplained_ratio_stays_loud_even_with_reconcile_time(tmp_path: Path) -> None:
    """A ratio matching no known unit cannot be repaired, so it stays loud."""
    n = 30
    df = pl.DataFrame(
        {
            "Test Time / s": [i * 370.0 for i in range(n)],  # 37x wall clock: no known unit
            "Unix Time / s": [1.7e9 + i * 10.0 for i in range(n)],
            "Voltage / V": [3.7] * n,
            "Current / A": [0.1] * n,
        }
    )
    path = tmp_path / "odd.bdf.csv"
    df.write_csv(path)
    with pytest.raises(BDFValidationError, match="matches no known unit"):
        read(path, reconcile_time=True)
    with pytest.warns(UserWarning, match="matches no known unit"):
        out, meta = read(path, strict=False)
    assert out["Test Time / s"].to_list()[1] == 370.0
    assert meta.bdf.time_reconciliation is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# The read assembly: Metadata, single-source selection, and the round trip
# ---------------------------------------------------------------------------


def test_read_returns_metadata_with_six_entity_dicts(tmp_path: Path) -> None:
    """read() returns (frame, Metadata) carrying an empty dict for each entity, bdf, and extras."""
    df = pl.DataFrame({"Test Time / s": [0.0, 1.0], "Voltage / V": [3.7, 3.6], "Current / A": [0.1, 0.1]})
    p = tmp_path / "data.bdf.csv"
    io.save(df, p)
    _, meta = io.read(p)
    assert isinstance(meta, Metadata)
    assert meta.bdf.source == "bdf_csv"
    # The file was saved with no metadata, so each of the six BattINFO entities is an empty dict.
    assert [getattr(meta, entity) for entity in FIELD_SCHEMAS] == [{}] * 6
    assert meta.raw is None
    assert meta.extras is None


def test_save_metadata(tmp_path: Path) -> None:
    """save() writes the typed metadata to the `.metadata.json` sidecar."""
    df_orig = pl.DataFrame(
        {
            "Test Time / s": [1.0, 2.0, 3.0],
            "Voltage / V": [4.0, 4.1, 4.2],
            "Current / A": [0.1, 0.1, 0.1],
        }
    )
    p = tmp_path / "data.bdf.parquet"
    p_meta = tmp_path / "data.bdf.metadata.json"
    io.save(df_orig, p, metadata=Metadata(bdf=BdfReadInfo(source="bdf_parquet")))
    assert p.exists()
    assert p_meta.exists()
    metadata = json.loads(p_meta.read_text())
    assert metadata["bdf"]["source"] == "bdf_parquet"
    # every written sidecar also carries the writer's version stamps (GH #106), and no BattINFO stamp
    # where no entity holds data
    assert {"source", "bdf_version", "ontology_version"} <= set(metadata["bdf"])
    assert not {"battinfo_ref", "battinfo_version"} & set(metadata["bdf"])


def test_metadata_roundtrips_through_save_and_read(tmp_path: Path) -> None:
    """A fully populated Metadata round-trips through save() and read() with no conversion."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    original = Metadata()
    # occurred_at accepts either an int or a raw string; a re-run of AbsoluteTimeNormalization
    # would coerce or reject this ISO-shaped string, so it catches a restore that normalises.
    deviation = {"occurred_at": "2024-01-15T00:00:00Z", "category": "other", "type": "manual review flag"}
    original.battinfo_test = {  # type: ignore[assignment]
        "test": {
            "started_at": _PLACEHOLDER_EPOCH,
            "instrument_name": "Arbin",
            "conformance": {"deviations": [deviation]},
        }
    }
    original.raw = {"vendor_key": "vendor_value", "nested": {"a": None}}
    original.extras = {"rig_bay": "B7", "nested": {"a": 1}}

    p = tmp_path / "data.bdf.csv"
    io.save(df, p, metadata=original)
    _, restored = io.read(p)
    # Hand-built metadata states the writer in created_with and no source.
    assert restored.bdf.created_with is not None
    assert restored.bdf.source is None
    restored_without_bdf = restored.model_copy(update={"bdf": original.bdf})
    assert restored_without_bdf == original


def test_metadata_carrying_nothing_writes_no_sidecar(tmp_path: Path) -> None:
    """A ``Metadata`` stating nothing writes no sidecar, so a later read falls to the plugin parser."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    p = tmp_path / "data.bdf.csv"

    io.save(df, p, metadata=Metadata())
    assert not p.with_suffix(".metadata.json").exists()

    # Nor does one whose only value is a battinfo_ref, which a save does not write.
    io.save(df, p, metadata=Metadata.model_validate({"bdf": {"battinfo_ref": "abc"}}))
    assert not p.with_suffix(".metadata.json").exists()


def test_save_without_metadata_refuses_an_existing_sidecar(tmp_path: Path) -> None:
    """A save that states no metadata beside an existing sidecar raises, and writes nothing."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    other = pl.DataFrame({"Test Time / s": [1.0], "Voltage / V": [3.6], "Current / A": [0.2]})
    p = tmp_path / "data.bdf.csv"
    original = Metadata()
    original.battinfo_test = {"test": {"instrument_name": "Arbin"}}  # type: ignore[assignment]
    io.save(df, p, metadata=original)
    sidecar = p.with_suffix(".metadata.json")

    with pytest.raises(FileExistsError, match=re.escape(str(sidecar))):
        io.save(other, p)

    # The refusal precedes the table write, so neither file changed.
    table, meta = io.read(p)
    assert_frame_equal(df, table)
    assert meta.battinfo_test["test"]["instrument_name"] == "Arbin"


def test_save_without_metadata_writes_where_no_sidecar_exists(tmp_path: Path) -> None:
    """With no sidecar beside the target, a save that states no metadata writes the table alone."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    p = tmp_path / "data.bdf.csv"

    io.save(df, p)
    io.save(df, p)

    assert not p.with_suffix(".metadata.json").exists()


def test_empty_metadata_clears_a_previous_sidecar(tmp_path: Path) -> None:
    """``metadata=Metadata()`` deletes the sidecar, so a later read falls to the plugin parser."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    p = tmp_path / "data.bdf.csv"
    original = Metadata()
    original.battinfo_test = {"test": {"instrument_name": "Arbin"}}  # type: ignore[assignment]
    io.save(df, p, metadata=original)

    io.save(df, p, metadata=Metadata())

    assert not p.with_suffix(".metadata.json").exists()
    _, meta = io.read(p)
    assert meta.battinfo_test == {}


def test_reserved_sidecar_never_self_nests(tmp_path: Path) -> None:
    """Reading and saving a reserved sidecar repeatedly never nests a copy of itself into raw."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    p = tmp_path / "data.bdf.csv"
    original = Metadata()
    original.raw = {"vendor_key": "vendor_value"}
    io.save(df, p, metadata=original)

    for _ in range(3):
        _, meta = io.read(p)
        assert meta.raw == {"vendor_key": "vendor_value"}
        io.save(df, p, metadata=meta)


def test_a_stated_null_is_refused_by_the_schema(tmp_path: Path) -> None:
    """A null on a leaf that the schema does not allow to be null fails the read, naming the path."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    p = tmp_path / "data.bdf.csv"
    io.save(df, p)
    # A hand-edited sidecar puts null where the schema wants an integer.
    p.with_suffix(".metadata.json").write_text(
        json.dumps({"battinfo_equipment": {"equipment": {"commissioned_at": None}}})
    )

    # The read checks the sidecar against BattINFO and, with validate on (the default), raises.
    with pytest.raises(BDFMetadataError, match="battinfo_equipment.equipment.commissioned_at"):
        io.read(p)


def _preamble_plugin() -> tuple[str, Plugin]:
    """Build a CSV preamble, and the plugin whose metadata parser reads it.

    Returns:
        The CSV text (a one-line preamble plus a header and rows), and a
        ``Plugin`` whose metadata parser stages ``started_at`` from that
        preamble's ``~Start of Test:`` line.
    """
    header = "~Start of Test: 2024-01-15 00:00:00\nTest Time / s,Voltage / V,Current / A\n"
    rows = "".join(f"{i},3.7,0.1\n" for i in range(15))
    rule = RegexRule(
        pattern=re.compile(r"~Start of Test:\s*(.+)"),
        normalization=AbsoluteTimeNormalization(formats=("%Y-%m-%d %H:%M:%S",)),
    )

    metadata_parser = TxtPreambleParser(rules={("battinfo_test", "test", "started_at"): rule})
    plugin = Plugin(table_parser=DelimTxtParser(normalizer=TableNormalizer()), metadata_parser=metadata_parser)
    return header + rows, plugin


def test_reserved_sidecar_suppresses_the_plugin_parser(tmp_path: Path) -> None:
    """A reserved sidecar beside an extractable preamble is the sole source; the preamble is never
    parsed, so a field only the preamble states -- a gap a partial sidecar leaves -- stays unset."""
    p = tmp_path / "data.csv"
    text, plugin = _preamble_plugin()
    p.write_text(text)

    sidecar = p.with_suffix(".metadata.json")
    sidecar.write_text(json.dumps({"battinfo_test": {"test": {"instrument_name": "Arbin"}}}))
    _, meta = io.read(p, plugin=plugin)
    assert meta.battinfo_test == {"test": {"instrument_name": "Arbin"}}
    assert meta.raw is None  # type: ignore[attr-defined]


def test_plugin_parser_runs_when_no_sidecar_exists(tmp_path: Path) -> None:
    """With no reserved sidecar beside it, the returned metadata is the plugin parser's alone."""
    p = tmp_path / "data.csv"
    text, plugin = _preamble_plugin()
    p.write_text(text)

    _, meta = io.read(p, plugin=plugin)
    expected = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc).timestamp()
    assert meta.battinfo_test["test"]["started_at"] == pytest.approx(expected, abs=1e-6)
    assert "~Start of Test: 2024-01-15 00:00:00" in meta.raw  # type: ignore[attr-defined,operator]


def test_read_raw_excludes_table_rows(tmp_path: Path) -> None:
    """A read's raw carries the preamble alone, with neither the header row nor a data row."""
    p = tmp_path / "data.csv"
    text, plugin = _preamble_plugin()
    p.write_text(text)

    _, meta = io.read(p, plugin=plugin)
    assert "~Start of Test: 2024-01-15 00:00:00" in meta.raw  # type: ignore[attr-defined,operator]
    assert "Test Time / s" not in meta.raw  # type: ignore[attr-defined,operator]  # header row
    assert "3.7,0.1" not in meta.raw  # type: ignore[attr-defined,operator]  # data row


def test_read_rule_never_matches_inside_table(tmp_path: Path) -> None:
    """A rule pattern matching text only inside a data row leaves that rule's target unset."""
    from bdf.normalization import IdentityNormalization

    p = tmp_path / "data.csv"
    header = "~Meta: some info\nTest Time / s,Voltage / V,Current / A\n"
    rows = "".join(f"{i},3.7,0.1\n" for i in range(15))
    p.write_text(header + rows)

    rule = RegexRule(pattern=re.compile(r"(0\.1)"), normalization=IdentityNormalization())
    metadata_parser = TxtPreambleParser(rules={("battinfo_test", "test", "instrument_name"): rule})
    plugin = Plugin(table_parser=DelimTxtParser(normalizer=TableNormalizer()), metadata_parser=metadata_parser)

    _, meta = io.read(p, plugin=plugin)
    assert meta.battinfo_test == {}


def test_read_no_preamble_leaves_raw_none(tmp_path: Path) -> None:
    """A read of a file whose header row is its first line leaves raw as None and every rule target unset."""
    p = tmp_path / "data.csv"
    header = "Test Time / s,Voltage / V,Current / A\n"
    rows = "".join(f"{i},3.7,0.1\n" for i in range(15))
    p.write_text(header + rows)

    rule = RegexRule(
        pattern=re.compile(r"~Start of Test:\s*(.+)"),
        normalization=AbsoluteTimeNormalization(formats=("%Y-%m-%d %H:%M:%S",)),
    )

    metadata_parser = TxtPreambleParser(rules={("battinfo_test", "test", "started_at"): rule})
    plugin = Plugin(table_parser=DelimTxtParser(normalizer=TableNormalizer()), metadata_parser=metadata_parser)

    _, meta = io.read(p, plugin=plugin)
    assert meta.raw is None  # type: ignore[attr-defined]
    assert meta.battinfo_test == {}


def test_save_sidecar_raw_excludes_table_rows(tmp_path: Path) -> None:
    """A save() following a preamble read writes a sidecar whose raw carries no table row."""
    p = tmp_path / "data.bdf.csv"
    text, plugin = _preamble_plugin()
    p.write_text(text)

    df, meta = io.read(p, plugin=plugin)
    io.save(df, p, metadata=meta)

    sidecar = p.with_suffix(".metadata.json")
    written = json.loads(sidecar.read_text())
    assert "~Start of Test: 2024-01-15 00:00:00" in written["raw"]
    assert "Test Time / s" not in written["raw"]
    assert "3.7,0.1" not in written["raw"]


def test_read_unlocatable_boundary_keeps_the_whole_head(tmp_path: Path) -> None:
    """A read of a file whose head holds no table leaves raw carrying the whole head, and every rule reads it."""

    p = tmp_path / "data.csv"
    # Every line carries three fields, so a table parser that (wrongly) treats
    # the preamble line as the header still parses a consistent shape; only
    # one data row follows, too few for structure detection to find a table.
    text = "~Start of Test: 2024-01-15 00:00:00,,\nTest Time / s,Voltage / V,Current / A\n0,3.7,0.1\n"
    p.write_text(text, newline="\n")

    rule = RegexRule(
        pattern=re.compile(r"~Start of Test:\s*([^,]+)"),
        normalization=AbsoluteTimeNormalization(formats=("%Y-%m-%d %H:%M:%S",)),
    )
    metadata_parser = TxtPreambleParser(rules={("battinfo_test", "test", "started_at"): rule})
    plugin = Plugin(table_parser=DelimTxtParser(normalizer=TableNormalizer()), metadata_parser=metadata_parser)

    _, meta = io.read(p, plugin=plugin, validate=False)
    assert meta.raw == text  # type: ignore[attr-defined]
    expected = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc).timestamp()
    assert meta.battinfo_test["test"]["started_at"] == pytest.approx(expected, abs=1e-6)


def test_read_sidecar_written_before_the_trim_restores_unchanged(tmp_path: Path) -> None:
    """A .metadata.json whose raw holds a whole head restores that text unchanged."""
    p = tmp_path / "data.bdf.csv"
    text, plugin = _preamble_plugin()
    p.write_text(text)

    sidecar = p.with_suffix(".metadata.json")
    sidecar.write_text(json.dumps({"raw": text}))

    _, meta = io.read(p, plugin=plugin)
    assert meta.raw == text  # type: ignore[attr-defined]


def test_malformed_sidecar_fails_the_read(tmp_path: Path) -> None:
    """A reserved sidecar that does not parse fails the read, naming the file and the error."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    p = tmp_path / "data.bdf.csv"
    io.save(df, p)
    sidecar = p.with_suffix(".metadata.json")
    sidecar.write_text("{not valid json")

    with pytest.raises(BDFMetadataError, match="Expecting property name") as excinfo:
        io.read(p)
    assert str(sidecar) in str(excinfo.value)


def test_sidecar_that_is_not_a_json_object_fails_the_read(tmp_path: Path) -> None:
    """A reserved sidecar holding a JSON value other than an object fails the read, rather than read as empty."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    p = tmp_path / "data.bdf.csv"
    io.save(df, p)
    sidecar = p.with_suffix(".metadata.json")
    sidecar.write_text(json.dumps(["battinfo_test", "CELL-A"]))

    with pytest.raises(BDFMetadataError, match="holds a JSON array"):
        io.read(p)


def test_sidecar_that_does_not_decode_fails_the_read(tmp_path: Path) -> None:
    """A reserved sidecar that is not UTF-8 fails the read as a metadata error, not as a raw codec error."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    p = tmp_path / "data.bdf.csv"
    io.save(df, p)
    sidecar = p.with_suffix(".metadata.json")
    sidecar.write_bytes(b'{"battinfo_test": {"test": {"name": "\xff\xfe"}}}')

    with pytest.raises(BDFMetadataError, match="does not decode as UTF-8"):
        io.read(p)


def test_malformed_sidecar_does_not_fall_back_to_the_plugin_parser(tmp_path: Path) -> None:
    """A malformed reserved sidecar fails the read; the plugin parser never runs to fill it."""
    p = tmp_path / "data.csv"
    text, plugin = _preamble_plugin()
    p.write_text(text)
    sidecar = p.with_suffix(".metadata.json")
    sidecar.write_text("{not valid json")

    with pytest.raises(BDFMetadataError) as excinfo:
        io.read(p, plugin=plugin)
    assert str(sidecar) in str(excinfo.value)


def test_malformed_vendor_sidecar_fails_the_read(tmp_path: Path) -> None:
    """A plugin-declared sidecar that does not parse fails the read the same way the reserved one does."""
    p = tmp_path / "data.csv"
    rows = "".join(f"{i},3.7,0.1\n" for i in range(15))
    p.write_text("Test Time / s,Voltage / V,Current / A\n" + rows)
    sidecar = p.with_suffix(".json")
    sidecar.write_text('{"cell": {"name": "CELL-A"}')

    metadata_parser = JsonSidecarParser(
        rules={("battinfo_cell", "cell_instance", "name"): JsonRule(candidates=(("cell", "name"),))}
    )
    plugin = Plugin(table_parser=DelimTxtParser(normalizer=TableNormalizer()), metadata_parser=metadata_parser)

    with pytest.raises(BDFMetadataError, match="does not parse as JSON") as excinfo:
        io.read(p, plugin=plugin, validate=False)
    assert str(sidecar) in str(excinfo.value)


def test_vendor_sidecar_that_is_not_a_json_object_fails_the_read(tmp_path: Path) -> None:
    """A plugin-declared sidecar holding a JSON value other than an object fails the read."""
    p = tmp_path / "data.csv"
    rows = "".join(f"{i},3.7,0.1\n" for i in range(15))
    p.write_text("Test Time / s,Voltage / V,Current / A\n" + rows)
    p.with_suffix(".json").write_text(json.dumps(["cell", "CELL-A"]))

    metadata_parser = JsonSidecarParser(
        rules={("battinfo_cell", "cell_instance", "name"): JsonRule(candidates=(("cell", "name"),))}
    )
    plugin = Plugin(table_parser=DelimTxtParser(normalizer=TableNormalizer()), metadata_parser=metadata_parser)

    with pytest.raises(BDFMetadataError, match="holds a JSON array"):
        io.read(p, plugin=plugin, validate=False)


def test_undeclared_top_level_field_is_refused() -> None:
    """Constructing a Metadata with a keyword no field declares raises, naming that keyword."""
    with pytest.raises(ValidationError, match="not_a_real_field"):
        Metadata(not_a_real_field="x")  # type: ignore[call-arg]


def test_unrecognised_sidecar_key_fails_the_read(tmp_path: Path) -> None:
    """A reserved sidecar key no field declares raises, and an invalid value also fails the read."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    p = tmp_path / "data.bdf.csv"
    io.save(df, p)
    sidecar = p.with_suffix(".metadata.json")

    sidecar.write_text(json.dumps({"not_a_real_field": "x"}))
    with pytest.raises(BDFMetadataError, match="not_a_real_field"):
        io.read(p)

    sidecar.write_text(json.dumps({"battinfo_test": {"test": {"started_at": "not-an-int"}}}))
    with pytest.raises(BDFMetadataError):
        io.read(p)


def test_sidecar_from_a_newer_bdf_says_to_upgrade(tmp_path: Path) -> None:
    """A sidecar that fails validation and claims a newer bdf_version raises with
    an upgrade instruction instead of the raw validation error."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    p = tmp_path / "data.bdf.csv"
    io.save(df, p)
    p.with_suffix(".metadata.json").write_text(
        json.dumps({"field_from_the_future": "x", "bdf": {"bdf_version": "99.0.0"}}),
        encoding="utf-8",
    )

    with pytest.raises(BDFMetadataError, match="written by bdf 99.0.0.*Upgrade"):
        io.read(p)


def test_sidecar_with_a_malformed_claimed_version_still_wraps(tmp_path: Path) -> None:
    """An unparseable claimed version never crashes the error path; the generic
    BDFMetadataError wrap raises instead."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    p = tmp_path / "data.bdf.csv"
    io.save(df, p)
    p.with_suffix(".metadata.json").write_text(
        json.dumps({"field_from_the_future": "x", "bdf": {"bdf_version": "not-a-version"}}),
        encoding="utf-8",
    )

    with pytest.raises(BDFMetadataError, match="does not validate"):
        io.read(p)


# ---------------------------------------------------------------------------
# day_month_order override
# ---------------------------------------------------------------------------


def _ambiguous_date_plugin() -> tuple[str, Plugin]:
    """Build a CSV preamble and table that each state an ambiguous numeric date, and the plugin reading them.

    ``skip_rows`` and ``decimal_comma`` are stated explicitly: the one-line
    preamble is too short for structure auto-detection to find on its own,
    and a stated ``decimal_comma`` skips the sniff that would otherwise
    collect the frame to decide it.

    Returns:
        The CSV text (a one-line preamble plus a header and one data row),
        and a ``Plugin`` whose metadata parser stages ``started_at`` from
        the preamble's ``~Start Time:`` line, and whose table normalizer
        stages ``Unix Time / s`` from the ``ts`` column, both under the
        same month-first format.
    """

    header = "~Start Time: 02/03/2024 08:00:00\nTest Time / s,Voltage / V,Current / A,ts\n"
    row = "0,3.7,0.1,02/03/2024 12:00:00\n"
    rule = RegexRule(
        pattern=re.compile(r"~Start Time:\s*(.+)"),
        normalization=AbsoluteTimeNormalization(formats=("%m/%d/%Y %H:%M:%S",)),
    )
    metadata_parser = TxtPreambleParser(rules={("battinfo_test", "test", "started_at"): rule})
    normalizer = TableNormalizer(
        unix_time_second=(Syn(hdr="ts", normalization=AbsoluteTimeNormalization(formats=("%m/%d/%Y %H:%M:%S",))),)
    )
    table_parser = DelimTxtParser(normalizer=normalizer, skip_rows=1, decimal_comma=False)
    plugin = Plugin(table_parser=table_parser, metadata_parser=metadata_parser)
    return header + row, plugin


def test_one_override_covers_both_paths(tmp_path: Path) -> None:
    """day_month_order overrides both the table column and the preamble field from one read() call."""
    text, plugin = _ambiguous_date_plugin()
    p = tmp_path / "data.csv"
    p.write_text(text)

    table, meta = read(p, plugin=plugin, validate=False, day_month_order="day_first")  # type: ignore[call-arg]
    expected_table = datetime(2024, 3, 2, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    expected_meta = datetime(2024, 3, 2, 8, 0, 0, tzinfo=timezone.utc).timestamp()
    assert table["Unix Time / s"][0] == pytest.approx(expected_table, abs=1e-6)
    assert meta.battinfo_test["test"]["started_at"] == pytest.approx(expected_meta, abs=1e-6)

    table_declared, meta_declared = read(p, plugin=plugin, validate=False)
    expected_table_declared = datetime(2024, 2, 3, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    expected_meta_declared = datetime(2024, 2, 3, 8, 0, 0, tzinfo=timezone.utc).timestamp()
    assert table_declared["Unix Time / s"][0] == pytest.approx(expected_table_declared, abs=1e-6)
    assert meta_declared.battinfo_test["test"]["started_at"] == pytest.approx(expected_meta_declared, abs=1e-6)


def test_choosing_an_order_reads_no_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """scan(day_month_order=...) reads a table-column-only ambiguous date with no collection."""
    normalizer = TableNormalizer(
        unix_time_second=(Syn(hdr="ts", normalization=AbsoluteTimeNormalization(formats=("%m/%d/%Y %H:%M:%S",))),)
    )
    plugin = Plugin(table_parser=DelimTxtParser(normalizer=normalizer, decimal_comma=False))
    p = tmp_path / "data.csv"
    p.write_text("Test Time / s,Voltage / V,Current / A,ts\n0,3.7,0.1,02/03/2024 12:00:00\n")

    collected: list[bool] = []
    original_collect = pl.LazyFrame.collect

    def tracking_collect(self, *args, **kwargs):
        collected.append(True)
        return original_collect(self, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(pl.LazyFrame, "collect", tracking_collect)
        lazy, _ = scan(p, plugin=plugin, validate=False, day_month_order="day_first")  # type: ignore[call-arg]

    assert isinstance(lazy, pl.LazyFrame)
    assert collected == []

    eager, _ = read(p, plugin=plugin, validate=False, day_month_order="day_first")  # type: ignore[call-arg]
    assert_frame_equal(lazy.collect(), eager)


def test_no_override_changes_nothing_on_read(tmp_path: Path) -> None:
    """read(day_month_order=None) matches omitting the argument: same frame, same staged metadata."""
    text, plugin = _ambiguous_date_plugin()
    p = tmp_path / "data.csv"
    p.write_text(text)

    table_none, meta_none = read(p, plugin=plugin, validate=False, day_month_order=None)  # type: ignore[call-arg]
    table_omitted, meta_omitted = read(p, plugin=plugin, validate=False)
    assert_frame_equal(table_none, table_omitted)
    assert meta_none.battinfo_test["test"]["started_at"] == meta_omitted.battinfo_test["test"]["started_at"]


def test_table_parser_read_accepts_day_month_order(tmp_path: Path) -> None:
    """TableParser.read(day_month_order=...) reorders an ambiguous numeric date on request."""
    normalizer = TableNormalizer(
        unix_time_second=(Syn(hdr="ts", normalization=AbsoluteTimeNormalization(formats=("%m/%d/%Y %H:%M:%S",))),)
    )
    parser = DelimTxtParser(normalizer=normalizer)
    p = tmp_path / "data.csv"
    p.write_text("Test Time / s,Voltage / V,Current / A,ts\n0,3.7,0.1,02/03/2024 12:00:00\n")
    out = parser.read(p, validate=False, day_month_order="day_first").collect()  # type: ignore[call-arg]
    expected = datetime(2024, 3, 2, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    assert out["Unix Time / s"][0] == pytest.approx(expected, abs=1e-6)


# ---------------------------------------------------------------------------
# Writer-version stamps (GH #106)
# ---------------------------------------------------------------------------


def test_save_stamps_writer_versions_into_the_sidecar(tmp_path: Path) -> None:
    """A written sidecar carries bdf_version, ontology_version, and the BattINFO release it was checked
    against, and stamping never mutates the caller's object."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    meta = Metadata()
    meta.battinfo_test = {"test": {"name": "cell-1"}}  # type: ignore[assignment]
    p = tmp_path / "data.bdf.csv"

    io.save(df, p, metadata=meta)

    import bdf

    doc = json.loads(p.with_suffix(".metadata.json").read_text(encoding="utf-8"))
    assert doc["bdf"]["bdf_version"] == bdf.__version__
    assert doc["bdf"]["ontology_version"]
    # The metadata states no release, so the save checked against the bundle and names it.
    assert doc["bdf"]["battinfo_version"] == bdf.battinfo.BATTINFO_SCHEMA.version
    # battinfo_ref (a BattINFO commit, bdf 0.2.0) is replaced by battinfo_version.
    assert "battinfo_ref" not in doc["bdf"]
    assert meta.bdf.bdf_version is None


def test_stamps_overwrite_the_previous_writers_stamp(tmp_path: Path) -> None:
    """The stamps identify the writer: a read-then-save replaces an old file's
    stamp with the version performing the save."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    old = tmp_path / "old.bdf.csv"
    df.write_csv(old)
    old.with_suffix(".metadata.json").write_text(
        json.dumps({"battinfo_test": {"test": {"name": "cell-1"}}, "bdf": {"bdf_version": "0.1.0"}}),
        encoding="utf-8",
    )

    _, meta = io.read(old)
    assert meta.bdf.bdf_version == "0.1.0"
    new = tmp_path / "new.bdf.csv"
    io.save(df, new, metadata=meta)

    import bdf

    doc = json.loads(new.with_suffix(".metadata.json").read_text(encoding="utf-8"))
    assert doc["bdf"]["bdf_version"] == bdf.__version__


def test_empty_metadata_is_not_stamped_into_existence(tmp_path: Path) -> None:
    """Stamps describe a sidecar; they never create one for an empty Metadata."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    p = tmp_path / "data.bdf.csv"

    io.save(df, p, metadata=Metadata())

    assert not p.with_suffix(".metadata.json").exists()


# ---------------------------------------------------------------------------
# BattINFO check: a refused value, the release a save or a read checks against
# ---------------------------------------------------------------------------

# Most tests below use release_docs() stand-in releases: release_docs("legacy_field") gives a
# release that allows only "legacy_field" in the test record, and the real bundled release
# refuses that field. Which field a release accepts shows which release did the check.

_FRAME = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
# The BattINFO commit that bdf 0.2.0 wrote in battinfo_ref; it is release 0.8.0rc2.
_0_2_0_REF = "2ea8929074a7430b6e437065686cda881cfc23e7"

# An empty cache for the test, so a release is local only when the test puts it there.
uses_cache = pytest.mark.usefixtures("battinfo_cache")


def _meta(entity: dict, **stated: str) -> Metadata:
    """Return Metadata whose ``battinfo_test`` is ``entity`` and whose ``bdf`` states the given release fields."""
    meta = Metadata(bdf=BdfReadInfo(**stated))
    meta.battinfo_test = entity  # type: ignore[assignment]
    return meta


def _battinfo_warnings(caught: list[warnings.WarningMessage]) -> list[str]:
    """Return the messages of the BattINFO warnings: a fall-back (definition) or a refused value (metadata)."""
    return [str(w.message) for w in caught if w.category in (BDFDefinitionWarning, BDFMetadataWarning)]


def _stamp(p: Path) -> str | None:
    """Return the BattINFO release that the sidecar of ``p`` states."""
    return json.loads(p.with_suffix(".metadata.json").read_text())["bdf"].get("battinfo_version")


def _unreachable_github(monkeypatch: pytest.MonkeyPatch) -> FakeRawGitHub:
    """Make every fetch fail as if offline; the returned fake records the URLs that were tried."""
    fake = FakeRawGitHub()
    fake.down = True
    serve_raw_github(monkeypatch, fake)
    return fake


def test_a_refused_save_leaves_the_data_file_and_the_sidecar_unchanged(tmp_path: Path) -> None:
    # A good first save, so there are files that a failed save could damage.
    p = tmp_path / "data.bdf.csv"
    io.save(_FRAME, p, metadata=_meta({"test": {"name": "cell-1"}}))
    before = (p.read_bytes(), p.with_suffix(".metadata.json").read_bytes())
    # Two faults: a string where an integer must be, and a misspelled key.
    refused = _meta({"test": {"started_at": "yesterday", "nmae": "x"}})

    # A changed frame, so a partial write of the data file would show in the bytes.
    with pytest.raises(BDFMetadataError) as info:
        io.save(_FRAME.with_columns(pl.col("Voltage / V") + 1), p, metadata=refused)

    # The error lists every fault, not only the first...
    assert "battinfo_test.test.started_at" in str(info.value)
    assert "'nmae'" in str(info.value)
    # ...and the check runs before anything is written, so both files are as they were.
    assert (p.read_bytes(), p.with_suffix(".metadata.json").read_bytes()) == before


def test_a_refused_save_without_validation_warns_and_writes_both_files(tmp_path: Path) -> None:
    p = tmp_path / "data.bdf.csv"
    refused = _meta({"test": {"started_at": "yesterday", "nmae": "x"}})

    # strict=False turns the refusal into a warning, as it does for missing required columns.
    with pytest.warns(BDFMetadataWarning) as caught:
        io.save(_FRAME, p, metadata=refused, strict=False)

    # One warning lists both faults.
    (message,) = [str(w.message) for w in caught if w.category is BDFMetadataWarning]
    assert "battinfo_test.test.started_at" in message
    assert "'nmae'" in message
    # The refused values are written as given; nothing is dropped or converted.
    assert json.loads(p.with_suffix(".metadata.json").read_text())["battinfo_test"] == refused.battinfo_test
    assert p.exists()


def test_a_value_that_json_cannot_hold_leaves_the_files_unchanged(tmp_path: Path) -> None:
    p = tmp_path / "data.bdf.csv"
    io.save(_FRAME, p, metadata=_meta({"test": {"name": "cell-1"}}))
    before = (p.read_bytes(), p.with_suffix(".metadata.json").read_bytes())
    meta = _meta({"test": {"name": "cell-2"}})
    # A numpy integer: the BattINFO check does not see extras, and json.dumps cannot write it.
    meta.extras = {"count": numpy.int64(3)}

    with pytest.raises(ValueError):
        io.save(_FRAME.with_columns(pl.col("Voltage / V") + 1), p, metadata=meta)

    # The sidecar is serialised before the data file is written, so this failure also changes nothing.
    assert (p.read_bytes(), p.with_suffix(".metadata.json").read_bytes()) == before


@uses_cache
def test_a_re_save_checks_against_the_release_it_states_and_keeps_its_stamp(tmp_path: Path) -> None:
    # Release 0.7.0 is in the cache and allows "legacy_field"; the bundle would refuse it.
    cache_release(tmp_path, "0.7.0", release_docs("legacy_field"))
    p = tmp_path / "data.bdf.csv"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        io.save(_FRAME, p, metadata=_meta({"test": {"legacy_field": 1}}, battinfo_version="0.7.0"))

    # No refusal, so the save checked against 0.7.0 and not the bundle; and the sidecar still states 0.7.0.
    assert _battinfo_warnings(caught) == []
    assert _stamp(p) == "0.7.0"


@uses_cache
def test_a_stated_schema_that_cannot_be_loaded_raises_at_save_unless_strict_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 0.7.0 is not bundled or cached, and the fetch fails.
    _unreachable_github(monkeypatch)
    bundled = bdf.battinfo.BATTINFO_SCHEMA.version
    accepted = _meta({"test": {"name": "cell-1"}}, battinfo_version="0.7.0")
    refused = _meta({"test": {"legacy_field": 1}}, battinfo_version="0.7.0")

    # With strict on, a save needs the stated schema: it raises, says how to get past it, and writes nothing.
    with pytest.raises(BDFDefinitionError, match="pass battinfo="):
        io.save(_FRAME, tmp_path / "a.bdf.csv", metadata=accepted)
    assert list(tmp_path.glob("a.bdf*")) == []
    # With strict off, it warns that the metadata is not checked, and writes both files...
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        io.save(_FRAME, tmp_path / "c.bdf.csv", metadata=refused, strict=False)
    (message,) = _battinfo_warnings(caught)
    assert "The BattINFO metadata is not checked." in message
    # ...with no check against the bundled schema in its place, which would refuse "legacy_field"...
    assert bundled not in message
    # ...and the sidecar keeps the stated version, so the file's claim is not lost.
    assert _stamp(tmp_path / "c.bdf.csv") == "0.7.0"


@uses_cache
def test_a_save_with_a_named_set_checks_against_it_and_stamps_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The caller loads 0.8.0 from the cache; then the network goes down, so any other release would fail to load.
    cache_release(tmp_path, "0.8.0", release_docs("new_field"))
    _unreachable_github(monkeypatch)
    named = BattinfoSchema.from_version("0.8.0")
    p = tmp_path / "data.bdf.csv"

    # The metadata states 0.7.0, but battinfo= wins: no attempt to load 0.7.0, so no fall-back warning.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        io.save(_FRAME, p, metadata=_meta({"test": {"new_field": 1}}, battinfo_version="0.7.0"), battinfo=named)

    # 0.8.0 accepted "new_field", and the sidecar now names 0.8.0, the release that checked it.
    assert _battinfo_warnings(caught) == []
    assert _stamp(p) == "0.8.0"
    # The named set also refuses what it does not allow, and the error names it.
    with pytest.raises(BDFMetadataError, match="0.8.0"):
        io.save(_FRAME, p, metadata=_meta({"test": {"legacy_field": 1}}), battinfo=named)


@pytest.mark.parametrize(
    "docs",
    [
        # A pattern in a syntax that Python's re does not know.
        {
            "test.schema.json": {
                "$id": "https://example.org/test.schema.json",
                "type": "object",
                "properties": {"test": {"properties": {"name": {"type": "string", "pattern": "^\\p{L}+$"}}}},
            }
        },
        # No document for battinfo_test at all.
        {"cell-instance.schema.json": {"$id": "https://example.org/cell-instance.schema.json"}},
    ],
    ids=["pattern", "no-document"],
)
def test_a_schema_that_cannot_check_raises_at_save_unless_strict_is_off(tmp_path: Path, docs: dict) -> None:
    broken = BattinfoSchema(docs, "9.9.9")
    meta = _meta({"test": {"name": "cell"}})
    with pytest.raises(BDFDefinitionError, match="Could not check .*BattINFO schema 9.9.9.*strict=False"):
        io.save(_FRAME, tmp_path / "a.bdf.csv", metadata=meta, battinfo=broken)
    assert list(tmp_path.glob("a.bdf*")) == []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        io.save(_FRAME, tmp_path / "b.bdf.csv", metadata=meta, battinfo=broken, strict=False)
    (message,) = _battinfo_warnings(caught)
    assert "The BattINFO metadata is not checked." in message
    # A read handles it the same way.
    with pytest.raises(BDFDefinitionError, match="Could not check"):
        read(tmp_path / "b.bdf.csv", battinfo=broken)
    _, caught = _read_recording(tmp_path / "b.bdf.csv", battinfo=broken, strict=False)
    assert any("The BattINFO metadata is not checked." in message for message in _battinfo_warnings(caught))


@uses_cache
def test_a_save_with_no_battinfo_data_makes_no_request_and_states_no_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _unreachable_github(monkeypatch)
    p = tmp_path / "data.bdf.csv"
    # A stated release that cannot be loaded, but every entity is empty.
    meta = Metadata(bdf=BdfReadInfo(source="custom", battinfo_version="0.7.0"))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        io.save(_FRAME, p, metadata=meta)

    # With nothing to check, the save loads no release: no request, no warning, no error...
    assert fake.urls == []
    assert _battinfo_warnings(caught) == []
    # ...and the sidecar states no BattINFO release, since none checked anything.
    assert _stamp(p) is None


@uses_cache
def test_a_save_stamps_the_release_that_a_0_2_0_sidecars_ref_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 0.8.0rc2 is cached; a stand-in 0.9.0 is the bundle, so a fall-back to the bundle would show in the stamp.
    cache_release(tmp_path, "0.8.0rc2", bundled_docs())
    point_bundle_at(monkeypatch, tmp_path / "bundle", "0.9.0", release_docs("new_field"))
    p = tmp_path / "data.bdf.csv"

    io.save(_FRAME, p, metadata=_meta({"test": {"name": "cell-1"}}, battinfo_ref=_0_2_0_REF))

    # The old commit stamp becomes the release it names, and the old field is not written again.
    assert _stamp(p) == "0.8.0rc2"
    assert "battinfo_ref" not in json.loads(p.with_suffix(".metadata.json").read_text())["bdf"]


def _json_plugin_with_start_and_instrument(tmp_path: Path, content: dict) -> tuple[Path, Plugin]:
    """Return a file, and a plugin that stages ``start`` as started_at and ``instrument`` as instrument_name from its JSON ``content``."""

    data = tmp_path / "cell.csv"
    data.write_text("Test Time / s,Voltage / V,Current / A\n0,3.7,0.1\n")
    (tmp_path / "cell.json").write_text(json.dumps(content))
    rules = {
        ("battinfo_test", "test", "started_at"): JsonRule(candidates=(("start",),)),
        ("battinfo_test", "test", "instrument_name"): JsonRule(candidates=(("instrument",),)),
    }
    plugin = Plugin(
        table_parser=DelimTxtParser(normalizer=TableNormalizer()), metadata_parser=JsonSidecarParser(rules=rules)
    )
    return data, plugin


def test_a_plugin_read_of_a_refused_value_follows_validate(tmp_path: Path) -> None:
    # A vendor file whose metadata gives a string start time and a numeric instrument name: both wrong types.
    data, plugin = _json_plugin_with_start_and_instrument(tmp_path, {"start": "yesterday", "instrument": 400})

    # A read checks plugin metadata as it checks a sidecar: validate on raises, naming both paths.
    with pytest.raises(BDFMetadataError) as info:
        read(data, plugin=plugin)
    assert "battinfo_test.test.started_at" in str(info.value)
    assert "battinfo_test.test.instrument_name" in str(info.value)

    # validate off gives one warning with both paths...
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _, meta = read(data, plugin=plugin, strict=False)
    (message,) = [str(w.message) for w in caught if w.category is BDFMetadataWarning]
    assert "battinfo_test.test.started_at" in message
    assert "battinfo_test.test.instrument_name" in message
    # ...and returns the values as the plugin staged them, so the caller can see and fix them.
    assert meta.battinfo_test == {"test": {"started_at": "yesterday", "instrument_name": 400}}


# ---------------------------------------------------------------------------
# A read checks against the release that the sidecar states
# ---------------------------------------------------------------------------


def _sidecar_beside_data(folder: Path, entity: dict, **stated: str) -> Path:
    """Write a data file and a sidecar whose ``battinfo_test`` is ``entity`` and whose ``bdf`` is ``stated``."""
    folder.mkdir(exist_ok=True)
    p = folder / "data.bdf.csv"
    _FRAME.write_csv(p)
    p.with_suffix(".metadata.json").write_text(json.dumps({"battinfo_test": entity, "bdf": stated}))
    return p


def _read_recording(p: Path, **kwargs: object) -> tuple[Metadata, list[warnings.WarningMessage]]:
    """Read ``p`` and return its metadata with every warning the read gave."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _, meta = read(p, **kwargs)  # type: ignore[arg-type]
    return meta, caught


def _assert_not_checked(meta: Metadata, caught: list[warnings.WarningMessage], stated: str) -> None:
    """One BDFDefinitionWarning names the stated schema and a reason, and no check ran; the metadata keeps the stated version."""
    (message,) = _battinfo_warnings(caught)
    pattern = rf"Could not load BattINFO schema {stated} .* states: .+\. The BattINFO metadata is not checked\."
    assert re.search(pattern, message, re.DOTALL)
    assert meta.bdf.battinfo_version == stated


# In the "cannot be loaded" tests below, a read raises by default; with strict=False it warns,
# checks nothing, and the returned metadata keeps the stated version.


@uses_cache
def test_a_sidecar_from_another_release_is_checked_against_it(tmp_path: Path) -> None:
    # The sidecar states 0.7.0, which is cached and allows "legacy_field"; the bundle would refuse it.
    cache_release(tmp_path, "0.7.0", release_docs("legacy_field"))
    bundled = bdf.battinfo.BATTINFO_SCHEMA
    p = _sidecar_beside_data(tmp_path, {"test": {"legacy_field": 1}}, battinfo_version="0.7.0")

    meta, caught = _read_recording(p)

    # No refusal: the read checked against 0.7.0.
    assert _battinfo_warnings(caught) == []
    assert meta.bdf.battinfo_version == "0.7.0"
    # Reading an older file does not change the default set for other code in the process.
    assert bdf.battinfo.BATTINFO_SCHEMA is bundled


@uses_cache
def test_a_read_with_a_named_set_checks_against_it_and_loads_no_other_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The caller loads 0.8.0; then the network goes down.
    cache_release(tmp_path, "0.8.0", release_docs("new_field"))
    fake = _unreachable_github(monkeypatch)
    named = BattinfoSchema.from_version("0.8.0")
    p = _sidecar_beside_data(tmp_path, {"test": {"new_field": 1}}, battinfo_version="0.7.0")

    meta, caught = _read_recording(p, battinfo=named)

    # battinfo= overrides the sidecar's 0.7.0: 0.8.0 accepts "new_field", and nothing falls back.
    assert _battinfo_warnings(caught) == []
    # The returned metadata names the release that checked it.
    assert meta.bdf.battinfo_version == "0.8.0"
    # The stated 0.7.0 was never fetched.
    assert fake.urls == []


@uses_cache
def test_many_sidecars_at_an_unreachable_schema_each_raise_or_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _unreachable_github(monkeypatch)

    # A scan of many files: the source remembers the failed fetch, so a later read does not
    # try again, but each read must still raise or warn on its own; a remembered failure is not silent.
    for folder in ("a", "b"):
        p = _sidecar_beside_data(tmp_path / folder, {"test": {"name": "cell-1"}}, battinfo_version="0.7.0")
        with pytest.raises(BDFDefinitionError, match="strict=False"):
            read(p)
        meta, caught = _read_recording(p, strict=False)
        _assert_not_checked(meta, caught, "0.7.0")


@uses_cache
def test_a_stated_schema_that_cannot_be_fetched_is_not_checked_without_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Offline, with "legacy_field", which only 0.7.0 allows.
    _unreachable_github(monkeypatch)
    p = _sidecar_beside_data(tmp_path, {"test": {"legacy_field": 1}}, battinfo_version="0.7.0")

    meta, caught = _read_recording(p, strict=False)

    # Not checked, so no refusal: the bundled schema, which refuses "legacy_field", does not stand in.
    _assert_not_checked(meta, caught, "0.7.0")
    # The warning points at the caller's line (this file), not at a line inside bdf.
    (warning,) = [w for w in caught if w.category is BDFDefinitionWarning]
    assert warning.filename == __file__


@uses_cache
def test_a_0_2_0_sidecars_ref_is_read_as_release_0_8_0rc2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # As in the save test: 0.8.0rc2 is cached, and a stand-in 0.9.0 is the bundle.
    cache_release(tmp_path, "0.8.0rc2", bundled_docs())
    point_bundle_at(monkeypatch, tmp_path / "bundle", "0.9.0", release_docs("new_field"))
    p = _sidecar_beside_data(tmp_path, {"test": {"name": "cell-1"}}, battinfo_ref=_0_2_0_REF)

    meta, caught = _read_recording(p)

    # No fall-back, and the returned metadata states the release in place of the commit.
    assert _battinfo_warnings(caught) == []
    assert meta.bdf.battinfo_version == "0.8.0rc2"


@uses_cache
def test_a_stated_schema_that_is_not_a_version_raises_or_is_not_checked(tmp_path: Path) -> None:
    # A branch name, not a version: bdf cannot fetch it, so this is like a failed fetch.
    p = _sidecar_beside_data(tmp_path, {"test": {"name": "cell-1"}}, battinfo_version="main")

    with pytest.raises(BDFDefinitionError, match="'main' is not a version"):
        read(p)
    meta, caught = _read_recording(p, strict=False)
    _assert_not_checked(meta, caught, "main")


@uses_cache
def test_a_stated_schema_with_a_broken_reference_cannot_be_loaded(tmp_path: Path) -> None:
    # A cached 0.7.0 whose $ref names a document it does not hold.
    doc = {"$id": "https://example.test/test.schema.json", "properties": {"test": {"$ref": "missing.schema.json"}}}
    cache_release(tmp_path, "0.7.0", {"test.schema.json": doc})
    p = _sidecar_beside_data(tmp_path, {"test": {"name": "cell-1"}}, battinfo_version="0.7.0")

    # bdf resolves every $ref when it opens a schema, so a broken one fails as a schema that cannot be loaded.
    with pytest.raises(BDFDefinitionError, match="Could not load BattINFO schema 0.7.0"):
        read(p)
    meta, caught = _read_recording(p, strict=False)
    _assert_not_checked(meta, caught, "0.7.0")


# ---------------------------------------------------------------------------
# Sidecar origin: where the data came from, and the 0.2.0rc3 sidecar
# ---------------------------------------------------------------------------


def test_a_vendor_file_keeps_its_origin_through_save_and_read(tmp_path: Path) -> None:
    data, plugin = _json_plugin_with_start_and_instrument(tmp_path, {"start": 1700000000, "instrument": "BaSyTec CTS"})

    # Read the vendor file, save it as a bdf file, and read that file back.
    _, first = read(data, plugin=plugin)
    io.save(_FRAME, tmp_path / "out.bdf.csv", metadata=first)
    _, again = read(tmp_path / "out.bdf.csv")

    import bdf

    # The first read records which bdf converted the vendor file.
    assert first.bdf.created_with == bdf.__version__
    # The second read is of a bdf file, but the metadata still gives the first read's source and
    # the converting bdf, not "bdf_csv" and nothing: the origin is not lost on the way.
    assert (again.bdf.source, again.bdf.created_with) == (first.bdf.source, first.bdf.created_with)


def test_metadata_built_by_hand_states_the_writer_and_no_source(tmp_path: Path) -> None:
    # Metadata made in code, not read from any file.
    p = tmp_path / "data.bdf.csv"
    io.save(_FRAME, p, metadata=_meta({"test": {"name": "cell-1"}}))

    import bdf

    # The save fills created_with with the bdf that wrote it, and states no source, as there was none.
    stated = json.loads(p.with_suffix(".metadata.json").read_text())["bdf"]
    assert stated["created_with"] == bdf.__version__
    assert "source" not in stated


def test_a_0_2_0rc3_sidecar_reads_saves_and_reads_again_unchanged(tmp_path: Path, data_dir: Path) -> None:
    # A real sidecar written by bdf 0.2.0rc3, copied so the test does not change the fixture.
    folder = tmp_path / "old"
    shutil.copytree(data_dir / "sidecars" / "0.2.0rc3", folder)

    # Its battinfo_ref, eda4629, matches no BattINFO release, so the bundled schema stands in, with a warning...
    with pytest.warns(BDFDefinitionWarning, match="eda4629.*matches no BattINFO release") as record:
        _, first = read(folder / "data.bdf.csv")
    assert record[0].filename == __file__
    assert first.bdf.battinfo_version == bdf.battinfo.BATTINFO_SCHEMA.version
    # ...and the save states that version, so nothing warns again.
    with warnings.catch_warnings():
        warnings.simplefilter("error", BDFDefinitionWarning)
        io.save(_FRAME, tmp_path / "new.bdf.csv", metadata=first)
        _, again = read(tmp_path / "new.bdf.csv")

    # The old file reads with its values as written. It has no created_with, and nothing invents one.
    assert first.battinfo_test == {"test": {"instrument_name": "BaSyTec CTS", "name": "cell-1 formation"}}
    assert first.extras == {"rig_bay": "B3"}
    assert (first.bdf.source, first.bdf.created_with) == ("bdf_csv", None)
    # A save and a second read change none of it...
    for name in ("battinfo_test", "extras"):
        assert getattr(again, name) == getattr(first, name)
    assert (again.bdf.source, again.bdf.created_with) == ("bdf_csv", None)
    # ...except that the new sidecar no longer writes the old battinfo_ref field.
    assert "battinfo_ref" not in json.loads((tmp_path / "new.bdf.metadata.json").read_text())["bdf"]


# validate=False checks nothing: no release is loaded, nothing is refused, and both files are written.


@uses_cache
def test_a_save_without_validation_loads_and_checks_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _unreachable_github(monkeypatch)
    # A refused value, under releases that bdf cannot load offline: with validate on, this save would raise.
    refused = _meta({"test": {"legacy_field": 1}}, battinfo_version="0.7.0", ontology_version="9.9.9")
    p = tmp_path / "a.bdf.csv"

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        io.save(_FRAME.drop("Current / A"), p, metadata=refused, validate=False)

    # Nothing was fetched, and the sidecar states the releases that the metadata states.
    assert fake.urls == []
    assert _stamp(p) == "0.7.0"
    assert json.loads(p.with_suffix(".metadata.json").read_text())["bdf"]["ontology_version"] == "9.9.9"


@uses_cache
def test_a_read_without_validation_loads_and_checks_no_battinfo_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _unreachable_github(monkeypatch)
    p = _sidecar_beside_data(tmp_path, {"test": {"legacy_field": 1}}, battinfo_version="0.7.0")

    meta, caught = _read_recording(p, validate=False)

    # No fall-back and no refusal: the BattINFO release was never loaded, and the metadata keeps its claim.
    assert _battinfo_warnings(caught) == []
    assert fake.urls == []
    assert meta.bdf.battinfo_version == "0.7.0"
