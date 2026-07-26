from __future__ import annotations

import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest
from polars.testing import assert_frame_equal, assert_series_equal

from bdf import BDFValidationError, io
from bdf.io import read, scan
from bdf.plugins import Plugin
from bdf.table_normalizers import TableNormalizer
from bdf.table_parsers import DelimTxtParser


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
