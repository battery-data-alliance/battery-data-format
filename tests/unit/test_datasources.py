"""Unit + sample-data tests for bdf.datasources.

Covers the ``DataSource`` model (JSON round-trip, match_ext/match_magic, score),
``EXT_TO_READER`` single-valuedness, ``detect()`` staging (ext / magic / tie
break by per-candidate config sniff), and parametrized reads over ``tests/data``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import bdf.datasources as D
from bdf.datasources import (
    DATASOURCES,
    DataSource,
    _score_by_headers,
    build_ext_to_reader,
    detect,
)
from bdf.normalizers import NORMALIZERS, MetadataParser, Normalizer, Syn
from bdf.readers import DelimTxtReader, ExcelReader, MatReader


@pytest.mark.parametrize("ds", list(DATASOURCES.values()), ids=list(DATASOURCES))
def test_datasource_json_round_trip(ds: DataSource) -> None:
    """Every built-in DataSource survives model_dump_json → model_validate_json."""
    assert DataSource.model_validate_json(ds.model_dump_json()) == ds


def test_match_ext() -> None:
    """match_ext is true only for the source's distinctive (case-insensitive) extensions."""
    assert D.BIOLOGIC_MPT.match_ext(".mpt")
    assert D.BIOLOGIC_MPT.match_ext(".MPT")
    assert not D.BIOLOGIC_MPT.match_ext(".csv")
    assert not D.ARBIN_CSV.match_ext(".csv")  # arbin declares no distinctive exts


def test_match_magic_text() -> None:
    """match_magic matches case-insensitive string tokens against decoded head text."""
    assert D.MACCOR_CSV.match_magic(b"...Date of Test:,2021...")
    assert not D.MACCOR_CSV.match_magic(b"nothing relevant here")
    assert not D.ARBIN_CSV.match_magic(b"anything")  # no magic declared


def test_match_magic_bytes_token() -> None:
    """match_magic supports raw byte tokens (binary-safe), not just str tokens."""
    ds = DataSource(id="bin", magic=(b"\x89PNG",), normalizer=Normalizer(), reader=DelimTxtReader())
    assert ds.match_magic(b"\x89PNG\r\n\x1a\n rest")
    assert not ds.match_magic(b"plain text")


def test_score_delegates_to_normalizer() -> None:
    """DataSource.score counts headers resolved by the referenced normalizer."""
    ds = DataSource(id="x", normalizer=Normalizer(voltage_volt=[Syn("v")]), reader=DelimTxtReader())
    assert ds.score(["v"]) == 1
    assert ds.score(["nope"]) == 0


def test_neware_csv_and_xlsx_share_normalizer() -> None:
    """NEWARE_CSV and NEWARE_XLSX reference one shared normalizer (different engines)."""
    assert D.NEWARE_CSV.normalizer is D.NEWARE_XLSX.normalizer is NORMALIZERS["neware"]
    assert D.NEWARE_CSV.reader.name == "txt"
    assert D.NEWARE_XLSX.reader.name == "excel"


def test_binary_engine_magic_warns() -> None:
    """Setting magic/metadata on a binary-engine source warns (text-only relevance)."""
    with pytest.warns(UserWarning, match="binary reader"):
        DataSource(id="b", magic=("foo",), normalizer=Normalizer(), reader=ExcelReader())


def test_binary_reader_default_metadata_no_warn() -> None:
    """Binary-reader source with no magic and default metadata fields does not warn."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        DataSource(id="b", normalizer=Normalizer(), reader=ExcelReader())


def test_conflicting_extension_raises() -> None:
    """A fixture where two readers claim the same extension raises ValueError."""
    csv_src = DataSource(id="c", exts=(".zct",), normalizer=Normalizer(), reader=DelimTxtReader())
    xls_src = DataSource(id="x", exts=(".zct",), normalizer=Normalizer(), reader=ExcelReader())
    with pytest.raises(ValueError, match="claimed by readers"):
        build_ext_to_reader({"c": csv_src, "x": xls_src})


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
    monkeypatch.setattr(D, "DATASOURCES", {"comma": comma_src, "semi": semi_src})
    monkeypatch.setattr(D, "EXT_TO_READER", {".csv": "txt"})

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
    monkeypatch.setattr(D, "DATASOURCES", {"good": good_src, "bad": bad_src})
    monkeypatch.setattr(D, "EXT_TO_READER", {".csv": "txt"})

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
    monkeypatch.setattr(D, "DATASOURCES", {"a": src_a, "b": src_b})
    monkeypatch.setattr(D, "EXT_TO_READER", {".csv": "txt"})

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
        normalizer=Normalizer(test_time_second=[Syn("time")], voltage_volt=[Syn("voltage")]),
        reader=DelimTxtReader(separator=";"),
    )
    _, metadata = D.read(p, datasource=src)
    assert metadata.get("start_time") == "2024-01-15 08:30:00"


def test_mat_datasource_with_syn_normalizer_reads_canonical_frame(tmp_path: Path) -> None:
    """User-built MatReader DataSource with Syn-based normalizer produces a non-empty BDF frame."""
    pytest.importorskip("scipy")
    import numpy as np
    from scipy.io import savemat

    mat_path = tmp_path / "sample.mat"
    savemat(str(mat_path), {"time": np.array([0.0, 1.0, 2.0]), "voltage": np.array([3.5, 3.6, 3.7])})

    src = DataSource(
        id="test_mat",
        normalizer=Normalizer(test_time_second=[Syn("time")], voltage_volt=[Syn("voltage")]),
        reader=MatReader(),
    )
    df, meta = D.read(mat_path, datasource=src)
    assert len(df) == 3
    assert "Test Time / s" in df.columns
    assert "Voltage / V" in df.columns
    assert meta["source"] == "test_mat"


READ_SAMPLES = [
    dict(
        rel="arbin/sample_data_arbin.csv",
        source="arbin_csv",
        cols=[
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
        ],
    ),
    dict(
        rel="basytec/sample_data_basytec.txt",
        source="basytec_txt",
        cols=["Test Time / s", "Voltage / V", "Current / A"],
    ),
    dict(
        rel="biologic/Sample_data_biologic_CA1.txt",
        source="biologic_mpt",
        cols=[
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
        ],
    ),
    dict(
        rel="biologic/Sample_data_biologic_no_header.mpt",
        source="biologic_mpt",
        cols=[
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
        ],
    ),
    dict(
        rel="maccor/sample_data_maccor.csv",
        source="maccor_csv",
        cols=[
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
        ],
    ),
    dict(
        rel="novonix/sample_data_novonix.csv",
        source="novonix_csv",
        cols=[
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
        ],
    ),
    dict(
        rel="neware/sample_data_neware.xlsx",
        source="neware_xlsx",
        cols=["Test Time / s", "Voltage / V", "Current / A", "Unix Time / s"],
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
    df, metadata = D.read(path, include_optional=True)
    assert list(df.columns) == spec["cols"]
    assert metadata["source"] == spec["source"]
