from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

from bdf import io
from bdf.io import read
from bdf.metadata_parsers import MetadataSchema, TxtPreambleParser
from bdf.normalizers import Syn, TableNormalizer
from bdf.plugins import Plugin, detect
from bdf.table_parsers import DelimTxtParser, MatParser


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


# ---------------------------------------------------------------------------
def test_neware_shared_normalizer_across_two_tables() -> None:
    """The shared NEWARE normalizer is carried by two distinct table parsers."""
    from bdf.plugins import NEWARE_CSV, NEWARE_XLSX, PLUGINS

    assert NEWARE_CSV.table_parser.normalizer is NEWARE_XLSX.table_parser.normalizer
    assert sum(1 for p in PLUGINS.values() if p.table_parser.normalizer is NEWARE_CSV.table_parser.normalizer) == 2


# ---------------------------------------------------------------------------
# read() tests
# ---------------------------------------------------------------------------


def test_txt_preamble_parser_extracts_start_time_through_read(tmp_path: Path) -> None:
    """read() extracts metadata via TxtPreambleParser.parse(path), regardless of separator."""
    preamble_lines = [
        "vendor: AcmeCycler",
        "Start Time: 2024-01-15 08:30:00",
        "Channel: 3",
    ]
    header = "time;voltage;current"
    rows = "\n".join(f"{i};{3.5 + i / 10};0.1" for i in range(6))
    content = "\n".join(preamble_lines) + "\n" + header + "\n" + rows + "\n"
    p = tmp_path / "data.csv"
    p.write_text(content)

    src = Plugin(
        table_parser=DelimTxtParser(
            normalizer=TableNormalizer(
                test_time_second=(Syn("time"),), voltage_volt=(Syn("voltage"),), current_ampere=(Syn("current"),)
            ),
            separator=";",
        ),
        metadata_parser=TxtPreambleParser(regex_patterns=MetadataSchema(start_time=re.compile(r"Start Time:\s*(.+)"))),
    )
    _, metadata = read(p, plugin=src)
    assert metadata.get("start_time") == "2024-01-15 08:30:00"


def test_basytec_metadata_latin1_through_read(data_dir: Path) -> None:
    """read() of a latin-1 Basytec sample extracts start_time via TxtPreambleParser.parse(path)."""
    p = data_dir / "basytec/sample_data_basytec.txt"
    if not p.exists():
        pytest.skip("basytec sample not present")
    _, metadata = read(p)
    assert metadata["source"] == "basytec_txt"
    assert metadata.get("start_time")


def test_read_explicit_plugin_source_is_custom(tmp_path: Path) -> None:
    """read() with a Plugin instance sets metadata['source'] = 'custom'."""
    p = tmp_path / "data.csv"
    rows = "\n".join(f"{i},{3.5 + i / 10},0.1" for i in range(6))
    p.write_text(f"time,voltage,current\n{rows}\n")
    src = Plugin(
        table_parser=DelimTxtParser(
            normalizer=TableNormalizer(
                test_time_second=(Syn("time"),),
                voltage_volt=(Syn("voltage"),),
                current_ampere=(Syn("current"),),
            ),
        ),
    )
    _, meta = read(p, plugin=src)
    assert meta["source"] == "custom"


def test_mat_datasource_with_syn_normalizer_reads_canonical_frame(tmp_path: Path) -> None:
    """User-built MatParser Plugin with Syn-based normalizer produces a non-empty BDF frame."""
    pytest.importorskip("scipy")
    import numpy as np
    from scipy.io import savemat

    mat_path = tmp_path / "sample.mat"
    savemat(
        str(mat_path),
        {"time": np.array([0.0, 1.0, 2.0]), "voltage": np.array([3.5, 3.6, 3.7]), "current": np.array([0.1, 0.1, 0.1])},
    )

    src = Plugin(
        table_parser=MatParser(
            normalizer=TableNormalizer(
                test_time_second=(Syn("time"),), voltage_volt=(Syn("voltage"),), current_ampere=(Syn("current"),)
            ),
        ),
    )
    df, meta = read(mat_path, plugin=src)
    assert len(df) == 3
    assert "Test Time / s" in df.columns
    assert "Voltage / V" in df.columns
    assert "Current / A" in df.columns
    assert meta["source"] == "custom"


READ_SAMPLES = [
    dict(
        rel="arbin/sample_data_arbin.csv",
        source="arbin_csv",
        cols={
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
        },
    ),
    dict(
        rel="basytec/sample_data_basytec.txt",
        source="basytec_txt",
        cols={"Test Time / s", "Voltage / V", "Current / A", "Net Capacity / Ah", "Step Index / 1"},
    ),
    dict(
        rel="biologic/Sample_data_biologic_CA1.txt",
        source="biologic_mpt",
        cols={
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
        },
    ),
    dict(
        rel="biologic/Sample_data_biologic_no_header.mpt",
        source="biologic_mpt",
        cols={
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
        },
    ),
    dict(
        rel="maccor/sample_data_maccor.csv",
        source="maccor_csv",
        cols={
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
        },
    ),
    dict(
        rel="novonix/sample_data_novonix.csv",
        source="novonix_csv",
        cols={
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
        },
    ),
    dict(
        rel="neware/sample_data_neware.xlsx",
        source="neware_xlsx",
        cols={"Test Time / s", "Voltage / V", "Current / A", "Unix Time / s"},
    ),
]


@pytest.fixture(params=READ_SAMPLES, ids=[s["rel"] for s in READ_SAMPLES])
def read_sample(request: pytest.FixtureRequest, data_dir: Path) -> tuple[dict, Path]:
    spec = request.param
    path = data_dir / spec["rel"]
    if not path.exists():
        pytest.skip(f"sample data not present: {spec['rel']}")
    return spec, path


def test_sample_detect(read_sample: tuple[dict, Path]) -> None:
    """detect resolves each vendor×format sample to the expected plugin id."""
    spec, path = read_sample
    plugin_id, _plugin = detect(path)
    assert plugin_id == spec["source"]


def test_sample_read_include_optional_columns(read_sample: tuple[dict, Path]) -> None:
    """read(include_optional=True) yields exactly the expected canonical columns and source id."""
    spec, path = read_sample
    df, metadata = read(path, include_optional=True)
    assert set(df.columns) == spec["cols"]
    assert metadata["source"] == spec["source"]


@pytest.mark.network
def test_read_url_arbin_csv() -> None:
    """bdf.read() accepts an https:// URL and returns a normalised DataFrame."""
    pytest.importorskip("requests")

    url = "https://zenodo.org/records/18214281/files/sample_data_arbin.csv"
    df, meta = read(url)
    assert "Voltage / V" in df.columns
    assert meta["source"] == "arbin_csv"


# ---------------------------------------------------------------------------
# normalize=False, validate=True
# ---------------------------------------------------------------------------


def _make_csv_plugin(tmp_path: Path) -> tuple[Path, Plugin]:
    p = tmp_path / "data.csv"
    rows = "\n".join(f"{i},{3.5 + i / 10},0.1" for i in range(6))
    p.write_text(f"time,voltage,current\n{rows}\n")
    plugin = Plugin(
        table_parser=DelimTxtParser(
            normalizer=TableNormalizer(
                test_time_second=(Syn("time"),),
                voltage_volt=(Syn("voltage"),),
                current_ampere=(Syn("current"),),
            ),
        ),
    )
    return p, plugin


def test_read_normalize_false_returns_raw_columns(tmp_path: Path) -> None:
    """read(normalize=False) returns the raw frame with source column names."""
    p, plugin = _make_csv_plugin(tmp_path)
    df, _ = read(p, plugin=plugin, normalize=False)
    assert "time" in df.columns
    assert "Test Time / s" not in df.columns


def test_column_ontology_validate_warns_on_extra_columns(tmp_path: Path) -> None:
    """COLUMN_ONTOLOGY.validate warns on extra non-BDF columns."""
    import polars as pl

    from bdf.spec import COLUMN_ONTOLOGY

    df = pl.DataFrame(
        {
            "Test Time / s": [0.0, 1.0],
            "Voltage / V": [3.7, 3.6],
            "Current / A": [0.1, 0.1],
            "vendor_extra_col": ["a", "b"],
        }
    )
    with pytest.warns(UserWarning, match="Non-BDF columns"):
        COLUMN_ONTOLOGY.validate(df)


# ---------------------------------------------------------------------------
# plugin= argument variants
# ---------------------------------------------------------------------------


def test_read_plugin_string_valid_sets_source(tmp_path: Path) -> None:
    """read(plugin='arbin_csv') resolves the named plugin and sets metadata['source']."""
    p, _ = _make_csv_plugin(tmp_path)
    _, meta = read(p, plugin="arbin_csv", normalize=False)
    assert meta["source"] == "arbin_csv"


def test_read_plugin_string_unknown_raises(tmp_path: Path) -> None:
    """read(plugin='no_such_plugin') raises ValueError listing available plugins."""
    p, _ = _make_csv_plugin(tmp_path)
    with pytest.raises(ValueError, match="unknown plugin"):
        read(p, plugin="no_such_plugin")


def test_read_plugin_invalid_type_raises(tmp_path: Path) -> None:
    """read(plugin=42) raises ValueError for unsupported plugin argument type."""
    p, _ = _make_csv_plugin(tmp_path)
    with pytest.raises(ValueError, match="invalid plugin argument"):
        read(p, plugin=42)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# include_optional, extra_columns, lazy
# ---------------------------------------------------------------------------


def test_read_include_optional_false_omits_optional_columns(tmp_path: Path) -> None:
    """read(include_optional=False) returns only required BDF columns."""
    p = tmp_path / "data.csv"
    rows = "\n".join(f"{i},{3.5 + i / 10},0.1,{i}" for i in range(6))
    p.write_text(f"time,voltage,current,cycle\n{rows}\n")
    plugin = Plugin(
        table_parser=DelimTxtParser(
            normalizer=TableNormalizer(
                test_time_second=(Syn("time"),),
                voltage_volt=(Syn("voltage"),),
                current_ampere=(Syn("current"),),
                cycle_count=(Syn("cycle"),),
            ),
        ),
    )
    df, _ = read(p, plugin=plugin, include_optional=False)
    cols = set(df.collect_schema().names())
    assert {"Test Time / s", "Voltage / V", "Current / A"} <= cols
    assert "Cycle Count / 1" not in cols


def test_read_extra_columns_passthrough(tmp_path: Path) -> None:
    """read(extra_columns={'foo': 'my_foo'}) includes the renamed passthrough column."""
    p = tmp_path / "data.csv"
    rows = "\n".join(f"{i},{3.5 + i / 10},0.1,extra{i}" for i in range(6))
    p.write_text(f"time,voltage,current,foo\n{rows}\n")
    plugin = Plugin(
        table_parser=DelimTxtParser(
            normalizer=TableNormalizer(
                test_time_second=(Syn("time"),),
                voltage_volt=(Syn("voltage"),),
                current_ampere=(Syn("current"),),
            ),
        ),
    )
    df, _ = read(p, plugin=plugin, extra_columns={"foo": "my_foo"})
    assert "my_foo" in df.collect_schema().names()


def test_read_lazy_false_returns_dataframe(tmp_path: Path) -> None:
    """read(lazy=False) returns a collected pl.DataFrame, not a LazyFrame."""
    import polars as pl

    p, plugin = _make_csv_plugin(tmp_path)
    result, _ = read(p, plugin=plugin, lazy=False)
    assert isinstance(result, pl.DataFrame)
