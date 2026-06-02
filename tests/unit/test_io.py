from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from bdf import io
from bdf.datasources import DataSource
from bdf.io import _score_by_headers, detect, read
from bdf.normalizers import MetadataParser, Normalizer, Syn
from bdf.readers import DelimTxtReader, MatReader


def test_detect_format_known_and_unknown(tmp_path: Path):
    # Known formats
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
# detect() — extension, magic, tie-break, error cases
# ---------------------------------------------------------------------------


def test_detect_by_distinctive_extension(tmp_path: Path) -> None:
    """detect resolves by distinctive extension (.mpt → biologic_mpt)."""
    p = tmp_path / "data.mpt"
    rows = "\n".join("\t".join([f"{i}", "0.1", f"{3.5 + i / 10}"]) for i in range(6))
    p.write_text(f"col1\tcol2\tcol3\n{rows}\n")
    assert detect(p).id == "biologic_mpt"


def test_detect_by_magic(tmp_path: Path) -> None:
    """detect falls to magic when the extension (.csv) is shared across candidates."""
    p = tmp_path / "data.csv"
    rows = "\n".join(f"{3.5 + i / 10},0.1,{i}" for i in range(6))
    p.write_text(f"Today's Date,foo\nVoltage,Current,Test Time\n{rows}\n")
    assert detect(p).id == "maccor_csv"


def test_detect_tie_breaks_by_per_candidate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On a same-engine, no-magic tie, each candidate sniffs with its OWN reader config.

    Two csv candidates declare different separators; only the one whose configured
    separator parses the file yields matching headers and wins the score tie-break.
    """
    comma_src = DataSource(
        id="comma",
        normalizer=Normalizer(voltage_volt=[Syn("v")], current_ampere=[Syn("i")]),
        reader=DelimTxtReader(separator=","),
    )
    semi_src = DataSource(
        id="semi",
        normalizer=Normalizer(test_time_second=[Syn("t")], voltage_volt=[Syn("u")]),
        reader=DelimTxtReader(separator=";"),
    )
    monkeypatch.setattr(io, "DATASOURCES", {"comma": comma_src, "semi": semi_src})
    monkeypatch.setattr(io, "EXT_TO_READER", {".csv": "txt"})

    p = tmp_path / "tie.csv"
    rows = "\n".join("1;2;3" for _ in range(6))
    p.write_text(f"t;u;x\n{rows}\n")  # only the ';' reader sees headers t,u,x
    assert detect(p).id == "semi"


def test_detect_tie_break_isolates_failing_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """detect returns the surviving candidate when one tied candidate raises during sniff."""
    good_src = DataSource(
        id="good",
        normalizer=Normalizer(voltage_volt=[Syn("v")], current_ampere=[Syn("i")]),
        reader=DelimTxtReader(separator=","),
    )
    bad_src = DataSource(
        id="bad",
        normalizer=Normalizer(voltage_volt=[Syn("v")]),
        reader=DelimTxtReader(separator=","),
    )
    monkeypatch.setattr(io, "DATASOURCES", {"good": good_src, "bad": bad_src})
    monkeypatch.setattr(io, "EXT_TO_READER", {".csv": "txt"})

    original_headers = DelimTxtReader.headers

    def patched_headers(
        self: DelimTxtReader, path: str | Path, head: bytes | None = None, *, var_names: list[str] | None = None
    ) -> list[str]:
        if self is bad_src.reader:
            raise RuntimeError("simulated parse failure")
        return original_headers(self, path, head, var_names=var_names)  # type: ignore[call-arg]

    monkeypatch.setattr(DelimTxtReader, "headers", patched_headers)

    p = tmp_path / "tie.csv"
    p.write_text("v,i\n" + "\n".join("1,2" for _ in range(6)) + "\n")
    assert detect(p).id == "good"


def test_detect_all_candidates_fail_raises_valueerror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """detect raises ValueError (not a reader exception) when every tied candidate fails."""
    src_a = DataSource(id="a", normalizer=Normalizer(voltage_volt=[Syn("v")]), reader=DelimTxtReader(separator=","))
    src_b = DataSource(id="b", normalizer=Normalizer(current_ampere=[Syn("i")]), reader=DelimTxtReader(separator=","))
    monkeypatch.setattr(io, "DATASOURCES", {"a": src_a, "b": src_b})
    monkeypatch.setattr(io, "EXT_TO_READER", {".csv": "txt"})

    def always_raise(self: DelimTxtReader, path: object, head: object = None, *, var_names: object = None) -> list[str]:
        raise RuntimeError("always fails")

    monkeypatch.setattr(DelimTxtReader, "headers", always_raise)

    p = tmp_path / "tie.csv"
    p.write_text("v,i\n1,2\n3,4\n")
    with pytest.raises(ValueError, match="no data source matched"):
        detect(p)


def test_score_by_headers_all_zero_raises(tmp_path: Path) -> None:
    """_score_by_headers raises when every candidate scores zero against file headers."""
    src_a = DataSource(id="a", normalizer=Normalizer(voltage_volt=[Syn("v")]), reader=DelimTxtReader(separator=","))
    src_b = DataSource(id="b", normalizer=Normalizer(current_ampere=[Syn("i")]), reader=DelimTxtReader(separator=","))

    p = tmp_path / "no_match.csv"
    p.write_text("x,y,z\n" + "\n".join("1,2,3" for _ in range(6)) + "\n")

    with pytest.raises(ValueError, match="no data source matched"):
        _score_by_headers([src_a, src_b], p, None)


def test_score_by_headers_equal_score_raises(tmp_path: Path) -> None:
    """_score_by_headers raises when two candidates score equally above zero."""
    src_a = DataSource(id="a", normalizer=Normalizer(voltage_volt=[Syn("v")]), reader=DelimTxtReader(separator=","))
    src_b = DataSource(id="b", normalizer=Normalizer(current_ampere=[Syn("i")]), reader=DelimTxtReader(separator=","))

    p = tmp_path / "ambiguous.csv"
    p.write_text("v,i\n" + "\n".join("1,2" for _ in range(6)) + "\n")

    with pytest.raises(ValueError, match="ambiguous match"):
        _score_by_headers([src_a, src_b], p, None)


def test_detect_unknown_extension_raises(tmp_path: Path) -> None:
    """detect raises for an extension with no registered reader."""
    p = tmp_path / "data.unknownext"
    p.write_text("a,b,c\n1,2,3\n")
    with pytest.raises(ValueError, match="no reader registered"):
        detect(p)


# ---------------------------------------------------------------------------
# read() — preamble metadata, mat reader, sample-data parametrize
# ---------------------------------------------------------------------------


def test_preamble_honours_explicit_separator_extracts_start_time(tmp_path: Path) -> None:
    """preamble() uses the reader's explicit separator so metadata is sliced from the correct lines."""
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

    src = DataSource(
        id="test_sep",
        metadata=MetadataParser(start_time=r"Start Time:\s*(.+)"),
        normalizer=Normalizer(
            test_time_second=[Syn("time")], voltage_volt=[Syn("voltage")], current_ampere=[Syn("current")]
        ),
        reader=DelimTxtReader(separator=";"),
    )
    _, metadata = read(p, datasource=src)
    assert metadata.get("start_time") == "2024-01-15 08:30:00"


def test_mat_datasource_with_syn_normalizer_reads_canonical_frame(tmp_path: Path) -> None:
    """User-built MatReader DataSource with Syn-based normalizer produces a non-empty BDF frame."""
    pytest.importorskip("scipy")
    import numpy as np
    from scipy.io import savemat

    mat_path = tmp_path / "sample.mat"
    savemat(
        str(mat_path),
        {"time": np.array([0.0, 1.0, 2.0]), "voltage": np.array([3.5, 3.6, 3.7]), "current": np.array([0.1, 0.1, 0.1])},
    )

    src = DataSource(
        id="test_mat",
        normalizer=Normalizer(
            test_time_second=[Syn("time")], voltage_volt=[Syn("voltage")], current_ampere=[Syn("current")]
        ),
        reader=MatReader(),
    )
    df, meta = read(mat_path, datasource=src)
    assert len(df) == 3
    assert "Test Time / s" in df.columns
    assert "Voltage / V" in df.columns
    assert "Current / A" in df.columns
    assert meta["source"] == "test_mat"


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
    """detect resolves each vendor×format sample to the expected DataSource id."""
    spec, path = read_sample
    assert detect(path).id == spec["source"]


def test_sample_read_include_optional_columns(read_sample: tuple[dict, Path]) -> None:
    """read(include_optional=True) yields exactly the expected canonical columns and source id."""
    spec, path = read_sample
    df, metadata = read(path, include_optional=True)
    assert set(df.columns) == spec["cols"]
    assert metadata["source"] == spec["source"]
