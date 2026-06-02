"""Unit and sample-data tests for bdf.readers (mechanics-only readers).

The readers carry parse configuration + behaviour only — no vendor identity,
magic, metadata, or normalizer (that lives in bdf.datasources / bdf.normalizers).
Synthetic tests exercise each sniffing/parsing unit in isolation. Sample-data
tests run over the real files under ``tests/data/`` and skip when absent.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from bdf.readers import HEAD_BYTES, DelimTxtReader, ExcelReader, MatReader


def test_delim_reader_rejects_no_header() -> None:
    """DelimTxtReader raises ValueError when has_header=False, pointing to polars directly."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="no headers"):
        DelimTxtReader(has_header=False)


def test_excel_reader_rejects_no_header() -> None:
    """ExcelReader raises ValueError when has_header=False, pointing to polars directly."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="no headers"):
        ExcelReader(has_header=False)


# --- DelimTxtReader sniffing -------------------------------------------------


@pytest.mark.parametrize("sep", [",", "\t", ";", "|"], ids=["comma", "tab", "semicolon", "pipe"])
def test_detect_separator(sep: str) -> None:
    """_detect_separator identifies the field delimiter from a sample string."""
    header = sep.join(["alpha", "beta", "gamma"])
    data = "\n".join(sep.join(["1", "2", "3"]) for _ in range(6))
    assert DelimTxtReader._detect_separator(f"{header}\n{data}") == sep


@pytest.mark.parametrize("preamble", [0, 1, 3, 7])
def test_detect_skiprows_preamble_sizes(preamble: int) -> None:
    """_detect_skiprows returns the number of non-data header lines."""
    pre = "\n".join(f"preamble metadata line {i}" for i in range(preamble))
    body = "a,b,c\n" + "\n".join("1,2,3" for _ in range(6))
    sample = (pre + "\n" + body) if preamble else body
    assert DelimTxtReader._detect_skiprows(sample) == preamble


def test_detect_skiprows_no_run_returns_zero() -> None:
    """_detect_skiprows returns 0 when no delimited run is found."""
    sample = "\n".join("a single undelimited column line" for _ in range(10))
    assert DelimTxtReader._detect_skiprows(sample) == 0


def test_detect_skiprows_short_run_below_min_returns_zero() -> None:
    """_detect_skiprows returns 0 when the delimited run is shorter than min_run."""
    sample = "pre\npre\na,b,c\n1,2,3\n4,5,6"
    assert DelimTxtReader._detect_skiprows(sample) == 0


@pytest.mark.parametrize(
    "values,expected",
    [
        (["3,5", "3,6", "0,1"], True),
        (["3.5", "3.6", "0.1"], False),
    ],
    ids=["comma-decimal", "dot-decimal"],
)
def test_sniff_decimal(values: list[str], expected: bool) -> None:
    """_sniff_decimal returns True when comma-decimal strings dominate, else False."""
    df = pl.DataFrame({"v": values})
    assert DelimTxtReader._sniff_decimal(df) == expected


def test_coerce_decimal_comma_rewrites_to_dot() -> None:
    """_coerce_decimal replaces commas with dots in string columns only."""
    lf = pl.DataFrame({"v": ["3,5", "3,6"], "n": [1, 2]}).lazy()
    out = DelimTxtReader._coerce_decimal(lf, True).collect()
    assert out["v"].to_list() == ["3.5", "3.6"]
    assert out["n"].to_list() == [1, 2]


def test_coerce_decimal_dot_is_noop() -> None:
    """_coerce_decimal is a no-op when decimal_comma is False."""
    lf = pl.DataFrame({"v": ["3.5", "3.6"]}).lazy()
    out = DelimTxtReader._coerce_decimal(lf, False).collect()
    assert out["v"].to_list() == ["3.5", "3.6"]


# --- head threading + read/headers/preamble ---------------------------------


def test_read_head_caps_at_head_bytes(tmp_path: Path) -> None:
    """_read_head returns at most HEAD_BYTES bytes."""
    p = tmp_path / "big.csv"
    p.write_bytes(b"x" * (HEAD_BYTES * 2))
    assert len(DelimTxtReader.read_head(p)) == HEAD_BYTES


def test_read_reuses_supplied_head(tmp_path: Path) -> None:
    """read() sniffs from a supplied head buffer (no separate head read needed)."""
    p = tmp_path / "data.csv"
    rows = "\n".join(f"{i},0.1,{3.5 + i / 10}" for i in range(6))
    p.write_text(f"t,i,v\n{rows}\n")
    head = DelimTxtReader.read_head(p)
    lf = DelimTxtReader().read(p, head)
    assert lf.collect_schema().names() == ["t", "i", "v"]


def test_headers_honour_separator_config(tmp_path: Path) -> None:
    """headers() returns columns parsed with the reader's own config."""
    p = tmp_path / "semi.csv"
    p.write_text("a;b;c\n1;2;3\n4;5;6\n")
    assert DelimTxtReader(separator=";").headers(p) == ["a", "b", "c"]


def test_preamble_returns_skipped_lines() -> None:
    """preamble() decodes head bytes and returns the skipped preamble lines."""
    text = "meta line 1\nmeta line 2\n" + "a,b,c\n" + "\n".join("1,2,3" for _ in range(6)) + "\n"
    head = text.encode("utf-8")
    assert DelimTxtReader().preamble(head) == ["meta line 1", "meta line 2"]


# --- _numeric_ratio ----------------------------------------------------------


def test_numeric_ratio_all_numeric() -> None:
    """_numeric_ratio returns 1.0 when all fields parse as floats."""
    assert DelimTxtReader._numeric_ratio("1.0,2.0,3.0", ",") == 1.0


def test_numeric_ratio_mixed() -> None:
    """_numeric_ratio returns the fraction of float-parseable fields."""
    assert DelimTxtReader._numeric_ratio("a,1.0,2.0", ",") == pytest.approx(2 / 3)


def test_numeric_ratio_empty_string() -> None:
    """_numeric_ratio returns 0.0 for an empty line."""
    assert DelimTxtReader._numeric_ratio("", ",") == 0.0


# --- _best_run ---------------------------------------------------------------


def test_best_run_uniform_block() -> None:
    """_best_run returns (0, n, fc) for a uniform block with no preamble."""
    lines = ["a,b", "1,2", "3,4", "5,6"]
    start, run_len, fc = DelimTxtReader._best_run(lines, ",")
    assert (start, run_len, fc) == (0, 4, 2)


def test_best_run_skips_preamble() -> None:
    """_best_run returns the start index after preamble lines."""
    pre = ["meta", "meta"]
    body = ["a,b,c"] + ["1,2,3"] * 6
    start, run_len, fc = DelimTxtReader._best_run(pre + body, ",")
    assert start == 2
    assert run_len == 7
    assert fc == 3


def test_best_run_empty_input() -> None:
    """_best_run returns zeros for empty input."""
    assert DelimTxtReader._best_run([], ",") == (0, 0, 0)


def test_best_run_prefers_longer_run_on_tie() -> None:
    """_best_run picks the run with more lines when score ties."""
    # 2 lines of 4 fields == 4 lines of 2 fields; prefer the longer run
    lines = ["a,b", "1,2", "3,4", "5,6", "x,y,z,w", "1,2,3,4"]
    _start, run_len, _fc = DelimTxtReader._best_run(lines, ",")
    assert run_len == 4


# --- _decode_head ------------------------------------------------------------


def test_decode_head_strips_partial_trailing_line() -> None:
    """_decode_head drops bytes after the last newline (incomplete line)."""
    head = b"line1\nline2\npartial"
    assert DelimTxtReader._decode_head(head) == "line1\nline2"


def test_decode_head_no_newline_returns_full_text() -> None:
    """_decode_head returns the full text when no newline is present."""
    assert DelimTxtReader._decode_head(b"no newline here") == "no newline here"


def test_decode_head_respects_encoding() -> None:
    """_decode_head decodes with the supplied encoding."""
    text = "héllo"
    assert DelimTxtReader._decode_head(text.encode("latin-1"), encoding="latin-1").startswith("h")


# --- ExcelReader -------------------------------------------------------------


@pytest.fixture
def xlsx_file(tmp_path: Path) -> Path:
    pytest.importorskip("openpyxl")
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["time", "voltage", "current"])
    ws.append([0.0, 3.5, 0.1])
    ws.append([1.0, 3.6, 0.2])
    p = tmp_path / "sample.xlsx"
    wb.save(p)
    return p


def test_excel_read_returns_lazyframe(xlsx_file: Path) -> None:
    """ExcelReader.read() parses an xlsx file to a LazyFrame with correct columns."""
    pytest.importorskip("fastexcel")
    lf = ExcelReader().read(xlsx_file)
    df = lf.collect()
    assert df.columns == ["time", "voltage", "current"]
    assert len(df) == 2


def test_excel_headers_returns_column_names(xlsx_file: Path) -> None:
    """ExcelReader.headers() returns column names without reading data rows."""
    pytest.importorskip("fastexcel")
    assert ExcelReader().headers(xlsx_file) == ["time", "voltage", "current"]


def test_excel_headers_uses_n_rows_zero(xlsx_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ExcelReader.headers() passes n_rows=0 to avoid reading data rows."""
    pytest.importorskip("fastexcel")
    seen: list[dict] = []
    original = pl.read_excel

    def spy(*args: object, **kwargs: object) -> object:
        seen.append(dict(kwargs))
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pl, "read_excel", spy)
    ExcelReader().headers(xlsx_file)
    assert seen[0].get("read_options", {}).get("n_rows") == 0


def test_excel_all_sheets_selection_raises(xlsx_file: Path) -> None:
    """ExcelReader raises ValueError when sheet_id=0 makes polars return all sheets as a dict."""
    pytest.importorskip("fastexcel")
    with pytest.raises(ValueError, match="single sheet"):
        ExcelReader(sheet_id=0).read(xlsx_file)


def test_excel_read_options_forwarded(xlsx_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ExcelReader forwards read_options dict to polars.read_excel."""
    pytest.importorskip("fastexcel")
    seen: list[dict] = []
    original = pl.read_excel

    def spy(*args: object, **kwargs: object) -> object:
        seen.append(dict(kwargs))
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pl, "read_excel", spy)
    ExcelReader(read_options={"header_row": 0}).read(xlsx_file)
    assert seen[0].get("read_options", {}).get("header_row") == 0


# --- MatReader ---------------------------------------------------------------


@pytest.fixture
def mat_file(tmp_path: Path) -> Path:
    pytest.importorskip("scipy")
    import numpy as np
    from scipy.io import savemat

    p = tmp_path / "sample.mat"
    savemat(str(p), {"time": np.array([0.0, 1.0, 2.0]), "voltage": np.array([3.5, 3.6, 3.7])})
    return p


def test_matreader_read_requires_var_names(tmp_path: Path) -> None:
    """MatReader.read() requires keyword-only var_names."""
    with pytest.raises(TypeError):
        MatReader().read(tmp_path / "x.mat")  # type: ignore[call-arg]


def test_matreader_read_returns_lazyframe(mat_file: Path) -> None:
    """MatReader.read() loads named variables into a LazyFrame."""
    lf = MatReader().read(mat_file, var_names=["time", "voltage"])
    df = lf.collect()
    assert df.columns == ["time", "voltage"]
    assert len(df) == 3


def test_matreader_read_missing_var_raises(mat_file: Path) -> None:
    """MatReader.read() raises ValueError for a variable not in the file."""
    with pytest.raises(ValueError, match="not found"):
        MatReader().read(mat_file, var_names=["missing"]).collect()


def test_matreader_headers_returns_present_vars(mat_file: Path) -> None:
    """MatReader.headers() returns only the variable names that exist in the file."""
    present = MatReader().headers(mat_file, var_names=["time", "voltage", "missing"])
    assert present == ["time", "voltage"]


def test_matreader_read_non_1d_var_raises(tmp_path: Path) -> None:
    """MatReader.read() raises ValueError for a variable that is not 1-D after squeeze."""
    pytest.importorskip("scipy")
    import numpy as np
    from scipy.io import savemat

    p = tmp_path / "matrix.mat"
    savemat(str(p), {"grid": np.arange(6).reshape(2, 3).astype(float)})
    with pytest.raises(ValueError, match="must be 1-D"):
        MatReader().read(p, var_names=["grid"]).collect()


# --- encoding / column rename ------------------------------------------------

# _build_rename_map unit tests
# Each test constructs raw bytes directly so it exercises the method in isolation,
# independent of file I/O and separator/skip sniffing.


def _make_raw(header: str, rows: list[str], encoding: str = "latin-1") -> bytes:
    """Encode a minimal CSV (header + rows) with the given encoding."""
    return ("\n".join([header] + rows) + "\n").encode(encoding)


def test_build_rename_map_degree_symbol() -> None:
    """Maps the utf8-lossy mangled name to the properly-decoded name for °."""
    # ° = 0xB0 in latin-1; utf8-lossy renders it as U+FFFD
    raw = _make_raw("T1[\xb0C],Current", ["1.0,0.5"])
    result = DelimTxtReader._build_rename_map(raw, "latin-1", skip=0, sep=",")
    assert result == {"T1[�C]": "T1[\N{DEGREE SIGN}C]"}


def test_build_rename_map_ascii_only_returns_empty() -> None:
    """ASCII-only headers produce an empty rename map (no mangling occurs)."""
    raw = _make_raw("time,voltage,current", ["1.0,3.5,0.1"])
    result = DelimTxtReader._build_rename_map(raw, "latin-1", skip=0, sep=",")
    assert result == {}


def test_build_rename_map_mixed_ascii_and_non_ascii() -> None:
    """Only columns with non-ASCII names appear in the rename map."""
    raw = _make_raw("time,T[\xb0C],current", ["1.0,25.0,0.1"])
    result = DelimTxtReader._build_rename_map(raw, "latin-1", skip=0, sep=",")
    assert result == {"T[�C]": "T[\N{DEGREE SIGN}C]"}
    assert "time" not in result
    assert "current" not in result


def test_build_rename_map_multiple_non_ascii_columns() -> None:
    """All non-ASCII column names are present in the rename map."""
    raw = _make_raw("T[\xb0C],\xe9tag,current", ["25.0,1,0.1"])
    result = DelimTxtReader._build_rename_map(raw, "latin-1", skip=0, sep=",")
    assert result == {"T[�C]": "T[\N{DEGREE SIGN}C]", "�tag": "\xe9tag"}
    assert "current" not in result


def test_build_rename_map_with_preamble_skip() -> None:
    """Correctly targets the header line when skip > 0 (preamble present)."""
    preamble = "meta line 1\nmeta line 2\n"
    data = "T[\xb0C],V\n1.0,3.5\n2.0,3.6\n"
    raw = (preamble + data).encode("latin-1")
    result = DelimTxtReader._build_rename_map(raw, "latin-1", skip=2, sep=",")
    assert result == {"T[�C]": "T[\N{DEGREE SIGN}C]"}


def test_build_rename_map_skip_beyond_content_returns_empty() -> None:
    """Returns empty dict when skip is beyond the buffered content."""
    raw = _make_raw("T[\xb0C],V", ["1.0,3.5"])
    result = DelimTxtReader._build_rename_map(raw, "latin-1", skip=99, sep=",")
    assert result == {}


def test_build_rename_map_tab_separator() -> None:
    """Works correctly with tab-separated files."""
    raw = "T[\xb0C]\tCurrent\n1.0\t0.5\n".encode("latin-1")
    result = DelimTxtReader._build_rename_map(raw, "latin-1", skip=0, sep="\t")
    assert result == {"T[�C]": "T[\N{DEGREE SIGN}C]"}


def test_build_rename_map_cp1252_euro_sign() -> None:
    """cp1252 encoding: € (byte 0x80) is renamed from its utf8-lossy replacement."""
    # byte 0x80 in cp1252 = € (U+20AC); invalid as a standalone UTF-8 byte
    raw = b"Cost[\x80],Count\n10.0,5\n"
    result = DelimTxtReader._build_rename_map(raw, "cp1252", skip=0, sep=",")
    assert result == {"Cost[�]": "Cost[\N{EURO SIGN}]"}


# read() integration tests for encoding


def test_latin1_encoding_renames_degree_symbol_column(tmp_path: Path) -> None:
    """DelimTxtReader(encoding='latin-1') yields correct column names from a latin-1 file."""
    # ° is 0xB0 in latin-1; utf8-lossy would mangle it to the replacement char
    content = "T1[\xb0C],Current\n1.0,0.5\n2.0,0.6\n"
    p = tmp_path / "latin1.csv"
    p.write_bytes(content.encode("latin-1"))

    lf = DelimTxtReader(encoding="latin-1").read(p)
    assert isinstance(lf, pl.LazyFrame)
    cols = lf.collect_schema().names()
    assert "T1[\N{DEGREE SIGN}C]" in cols  # ° present, not mangled
    assert "Current" in cols  # ASCII column unaffected


def test_latin1_encoding_ascii_only_no_rename(tmp_path: Path) -> None:
    """All-ASCII column names are unaffected (no rename) when encoding='latin-1'."""
    content = "time,voltage,current\n1.0,3.5,0.1\n2.0,3.6,0.2\n"
    p = tmp_path / "ascii_latin1.csv"
    p.write_bytes(content.encode("latin-1"))

    lf = DelimTxtReader(encoding="latin-1").read(p)
    assert lf.collect_schema().names() == ["time", "voltage", "current"]


# --- sample-data sniffing (real files under tests/data) ----------------------

SAMPLES = [
    dict(rel="arbin/sample_data_arbin.csv", skip=0, sep=","),
    dict(rel="basytec/sample_data_basytec.txt", skip=12, sep="\t"),
    dict(rel="biologic/Sample_data_biologic_CA1.txt", skip=102, sep="\t"),
    dict(rel="biologic/Sample_data_biologic_no_header.mpt", skip=0, sep="\t"),
    dict(rel="maccor/sample_data_maccor.csv", skip=2, sep=","),
    dict(rel="novonix/sample_data_novonix.csv", skip=20, sep=","),
]


@pytest.fixture(params=SAMPLES, ids=[s["rel"] for s in SAMPLES])
def sample(request: pytest.FixtureRequest, data_dir: Path) -> tuple[dict, Path]:
    spec = request.param
    path = data_dir / spec["rel"]
    if not path.exists():
        pytest.skip(f"sample data not present: {spec['rel']}")
    return spec, path


def test_sample_skiprows(sample: tuple[dict, Path]) -> None:
    """_detect_skiprows returns the expected preamble line count per vendor sample."""
    spec, path = sample

    assert DelimTxtReader._detect_skiprows(DelimTxtReader._decode_head(DelimTxtReader.read_head(path))) == spec["skip"]


def test_sample_separator(sample: tuple[dict, Path]) -> None:
    """_detect_separator returns the expected delimiter per vendor sample."""
    spec, path = sample

    assert DelimTxtReader._detect_separator(DelimTxtReader._decode_head(DelimTxtReader.read_head(path))) == spec["sep"]


def test_excel_sheet_name_honoured_in_headers(data_dir: Path) -> None:
    """ExcelReader.headers() reads from the configured sheet_name."""
    pytest.importorskip("fastexcel")
    p = data_dir / "neware/sample_data_neware.xlsx"
    if not p.exists():
        pytest.skip("neware xlsx sample not present")
    headers = ExcelReader(sheet_name="record").headers(p)
    assert "Voltage(V)" in headers and "Current(mA)" in headers


def test_preamble_honours_explicit_separator() -> None:
    """preamble() uses the reader's explicit separator for skip-row detection.

    Preamble lines contain commas; without explicit sep=";", _detect_separator
    could pick "," which under-counts skip rows (comma run too short) and returns
    an empty preamble.  With sep=";" the data run is detected correctly.
    """
    pre = "key: a, b, c\nother: x, y, z\n"
    header = "time;voltage;current"
    rows = "\n".join(f"{i};{3.5 + i / 10};0.1" for i in range(6))
    head = (pre + header + "\n" + rows + "\n").encode("utf-8")

    assert DelimTxtReader(separator=";").preamble(head) == ["key: a, b, c", "other: x, y, z"]
