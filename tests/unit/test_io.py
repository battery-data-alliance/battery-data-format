from __future__ import annotations

import json
import re
import warnings
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest
from polars.testing import assert_frame_equal, assert_series_equal
from pydantic import ValidationError

from bdf import (
    BattinfoCellInstance,
    BattinfoChannelInstance,
    BattinfoEquipmentInstance,
    BattinfoTest,
    BattinfoTestProtocol,
    BDFMetadataError,
    BdfReadInfo,
    BDFValidationError,
    Metadata,
    io,
)
from bdf.io import read, scan
from bdf.metadata_parsers import JsonRule, JsonSidecarParser, RegexRule, TxtPreambleParser
from bdf.normalization import AbsoluteTimeNormalization
from bdf.plugins import Plugin
from bdf.table_normalizers import Syn, TableNormalizer
from bdf.table_parsers import DelimTxtParser

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
        io.save(df, path, validate=False)


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
        io.save(df, path, validate=False)


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
        io.save(df, path, validate=False)


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
    meta_parse = MagicMock(return_value=Metadata())
    detect = MagicMock(return_value=("detected_id", plugin))
    monkeypatch.setattr("bdf.table_parsers.TableParser.read", table_read)
    monkeypatch.setattr("bdf.metadata_parsers.MetadataParser.parse", meta_parse)
    monkeypatch.setattr("bdf.io.detect", detect)
    return SimpleNamespace(plugin=plugin, table_read=table_read, meta_parse=meta_parse, detect=detect)


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
    """read() forwards path + the five read-shaping kwargs verbatim, plus lazy=False."""
    p = tmp_path / "f.csv"
    read(
        p,
        plugin=read_mocks.plugin,
        validate=False,
        normalize=False,
        include_unknown=True,
        tz="America/New_York",
    )
    read_mocks.table_read.assert_called_once_with(
        p,
        validate=False,
        normalize=False,
        include_unknown=True,
        lazy=False,
        tz="America/New_York",
        day_month_order=None,
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
        include_unknown=False,
        lazy=True,
        tz="America/New_York",
        day_month_order=None,
    )


def test_read_sets_bdf_source_on_the_parsed_metadata(read_mocks: SimpleNamespace, tmp_path: Path) -> None:
    """read() calls metadata_parser.parse(path, tz=...) and sets bdf.source on the result."""
    read_mocks.meta_parse.return_value = Metadata()
    p = tmp_path / "f.csv"
    _, meta = read(p, plugin=read_mocks.plugin)
    read_mocks.meta_parse.assert_called_once_with(p, tz="UTC", day_month_order=None)
    assert meta.bdf.source == "custom"  # type: ignore[attr-defined]


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
        df, meta = read(path, validate=False)
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
        out, meta = read(path, validate=False)
    assert out["Test Time / s"].to_list()[1] == 370.0
    assert meta.bdf.time_reconciliation is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# The read assembly: Metadata, single-source selection, and the round trip
# ---------------------------------------------------------------------------


def test_read_returns_typed_metadata_with_five_records(tmp_path: Path) -> None:
    """read() returns (frame, Metadata) carrying the five entity records, bdf, and extras."""
    df = pl.DataFrame({"Test Time / s": [0.0, 1.0], "Voltage / V": [3.7, 3.6], "Current / A": [0.1, 0.1]})
    p = tmp_path / "data.bdf.csv"
    io.save(df, p)
    _, meta = io.read(p)
    assert isinstance(meta, Metadata)
    assert meta.bdf.source == "bdf_csv"
    assert isinstance(meta.battinfo_test, BattinfoTest)
    assert isinstance(meta.battinfo_cell, BattinfoCellInstance)
    assert isinstance(meta.battinfo_channel, BattinfoChannelInstance)
    assert isinstance(meta.battinfo_equipment, BattinfoEquipmentInstance)
    assert isinstance(meta.battinfo_test_protocol, BattinfoTestProtocol)
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
    assert metadata == {"bdf": {"source": "bdf_parquet"}}


def test_metadata_roundtrips_through_save_and_read(tmp_path: Path) -> None:
    """A fully populated Metadata round-trips through save() and read() with no conversion."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    original = Metadata()
    original.battinfo_test.test.started_at = _PLACEHOLDER_EPOCH  # type: ignore[union-attr]
    original.battinfo_test.test.instrument_name = "Arbin"  # type: ignore[union-attr]
    # occurred_at accepts either an int or a raw string; a re-run of AbsoluteTimeNormalization
    # would coerce or reject this ISO-shaped string, so it catches a restore that normalises.
    original.battinfo_test.test.conformance.deviations = [  # type: ignore[union-attr]
        {"occurred_at": "2024-01-15T00:00:00Z", "category": "other", "type": "manual review flag"}
    ]
    original.raw = {"vendor_key": "vendor_value", "nested": {"a": None}}
    original.extras = {"rig_bay": "B7", "nested": {"a": 1}}

    p = tmp_path / "data.bdf.csv"
    io.save(df, p, metadata=original)
    _, restored = io.read(p)
    # bdf.source is the plugin id this read resolved, assigned unconditionally,
    # so it cannot round-trip against an instance that never set it.
    assert restored.bdf.source is not None
    restored_without_bdf = restored.model_copy(update={"bdf": original.bdf})
    assert restored_without_bdf == original


def test_metadata_carrying_nothing_writes_no_sidecar(tmp_path: Path) -> None:
    """A ``Metadata`` stating nothing writes no sidecar, so a later read falls to the plugin parser."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    p = tmp_path / "data.bdf.csv"

    io.save(df, p, metadata=Metadata())

    assert not p.with_suffix(".metadata.json").exists()


def test_save_without_metadata_refuses_an_existing_sidecar(tmp_path: Path) -> None:
    """A save that states no metadata beside an existing sidecar raises, and writes nothing."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    other = pl.DataFrame({"Test Time / s": [1.0], "Voltage / V": [3.6], "Current / A": [0.2]})
    p = tmp_path / "data.bdf.csv"
    original = Metadata()
    original.battinfo_test.test.instrument_name = "Arbin"  # type: ignore[union-attr]
    io.save(df, p, metadata=original)
    sidecar = p.with_suffix(".metadata.json")

    with pytest.raises(FileExistsError, match=str(sidecar)):
        io.save(other, p)

    # The refusal precedes the table write, so neither file changed.
    table, meta = io.read(p)
    assert_frame_equal(df, table)
    assert meta.battinfo_test.test.instrument_name == "Arbin"  # type: ignore[union-attr]


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
    original.battinfo_test.test.instrument_name = "Arbin"  # type: ignore[union-attr]
    io.save(df, p, metadata=original)

    io.save(df, p, metadata=Metadata())

    assert not p.with_suffix(".metadata.json").exists()
    _, meta = io.read(p)
    assert meta.battinfo_test.test.instrument_name is None  # type: ignore[union-attr]


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


def test_stated_null_on_a_wrapper_typed_leaf_roundtrips(tmp_path: Path) -> None:
    """A curated null on a wrapper-typed leaf stays null through repeated save/read cycles."""
    df = pl.DataFrame({"Test Time / s": [0.0], "Voltage / V": [3.7], "Current / A": [0.1]})
    p = tmp_path / "data.bdf.csv"
    io.save(df, p)
    sidecar = p.with_suffix(".metadata.json")
    sidecar.write_text(json.dumps({"battinfo_equipment": {"equipment": {"commissioned_at": None}}}))

    for _ in range(2):
        _, meta = io.read(p)
        assert meta.battinfo_equipment.equipment.commissioned_at is None  # type: ignore[union-attr]
        io.save(df, p, metadata=meta)
        payload = json.loads(sidecar.read_text())
        assert payload["battinfo_equipment"]["equipment"]["commissioned_at"] is None
        # a leaf neither side ever stated does not leak a matching bare null
        assert "expires_at" not in payload.get("battinfo_cell", {}).get("cell_instance", {})


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
    from bdf.metadata_targets import METADATA

    metadata_parser = TxtPreambleParser(rules={METADATA.battinfo_test.test.started_at: rule})
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
    assert meta.battinfo_test.test.instrument_name == "Arbin"  # type: ignore[union-attr]
    assert meta.battinfo_test.test.started_at is None  # type: ignore[union-attr]
    assert meta.raw is None  # type: ignore[attr-defined]


def test_plugin_parser_runs_when_no_sidecar_exists(tmp_path: Path) -> None:
    """With no reserved sidecar beside it, the returned metadata is the plugin parser's alone."""
    p = tmp_path / "data.csv"
    text, plugin = _preamble_plugin()
    p.write_text(text)

    _, meta = io.read(p, plugin=plugin)
    expected = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc).timestamp()
    assert meta.battinfo_test.test.started_at == pytest.approx(expected, abs=1e-6)  # type: ignore[union-attr]
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

    from bdf.metadata_targets import METADATA

    metadata_parser = JsonSidecarParser(
        rules={METADATA.battinfo_cell.cell_instance.name: JsonRule(candidates=(("cell", "name"),))}
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

    from bdf.metadata_targets import METADATA

    metadata_parser = JsonSidecarParser(
        rules={METADATA.battinfo_cell.cell_instance.name: JsonRule(candidates=(("cell", "name"),))}
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
    with pytest.raises(ValidationError, match="not_a_real_field"):
        io.read(p)

    sidecar.write_text(json.dumps({"battinfo_test": {"test": {"started_at": "not-an-int"}}}))
    with pytest.raises(ValidationError):
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
    from bdf.metadata_targets import METADATA

    header = "~Start Time: 02/03/2024 08:00:00\nTest Time / s,Voltage / V,Current / A,ts\n"
    row = "0,3.7,0.1,02/03/2024 12:00:00\n"
    rule = RegexRule(
        pattern=re.compile(r"~Start Time:\s*(.+)"),
        normalization=AbsoluteTimeNormalization(formats=("%m/%d/%Y %H:%M:%S",)),
    )
    metadata_parser = TxtPreambleParser(rules={METADATA.battinfo_test.test.started_at: rule})
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
    assert meta.battinfo_test.test.started_at == pytest.approx(expected_meta, abs=1e-6)  # type: ignore[union-attr]

    table_declared, meta_declared = read(p, plugin=plugin, validate=False)
    expected_table_declared = datetime(2024, 2, 3, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    expected_meta_declared = datetime(2024, 2, 3, 8, 0, 0, tzinfo=timezone.utc).timestamp()
    assert table_declared["Unix Time / s"][0] == pytest.approx(expected_table_declared, abs=1e-6)
    assert meta_declared.battinfo_test.test.started_at == pytest.approx(  # type: ignore[union-attr]
        expected_meta_declared, abs=1e-6
    )


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
    assert meta_none.battinfo_test.test.started_at == meta_omitted.battinfo_test.test.started_at  # type: ignore[union-attr]


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
