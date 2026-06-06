"""Unit and sample-data tests for bdf.table_parsers.

Each table parser carries a ``TableNormalizer`` field (default empty); ``read``
returns the normalized frame while ``_read_raw`` exposes the underlying mechanics.
Synthetic tests exercise each sniffing/parsing unit in isolation. Sample-data
tests run over the real files under ``tests/data/`` and skip when absent.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from conftest import ALL_CASES, SampleCase, resolve_source

from bdf.head_utils import read_head
from bdf.normalizers import ResolvedColumn, Syn, TableNormalizer
from bdf.plugins import PLUGINS
from bdf.spec import COLUMN_ONTOLOGY
from bdf.table_parsers import DelimTxtParser, ExcelParser, MatParser, NDAParser, ParquetParser, TableParser

_LABEL_DTYPE: dict[str, str] = {q.formatted_label: q.dtype for _, q in COLUMN_ONTOLOGY}

# ---------------------------------------------------------------------------
# TableParser.matches_ext
# ---------------------------------------------------------------------------


def test_base_reader_matches_ext_unique() -> None:
    assert DelimTxtParser(unique_exts=frozenset({".mpt"})).matches_ext(".mpt") is True


def test_base_reader_case_insensitive() -> None:
    assert DelimTxtParser().matches_ext(".CSV") is True


# ---------------------------------------------------------------------------
# TableParser concrete methods
# ---------------------------------------------------------------------------


def test_tableparser_instance_created() -> None:
    """TableParser can be instantiated with defaults."""
    p = TableParser()
    assert p.normalizer == TableNormalizer()
    assert p.unique_exts == frozenset()


def test_tableparser_hashable() -> None:
    """TableParser instances are hashable and can be in sets."""
    p1 = TableParser()
    p2 = TableParser()
    # Same content, should hash to same value
    assert hash(p1) == hash(p2)
    s = {p1, p2}
    # Both equivalent instances hash to same bucket
    assert len(s) == 1


def test_tableparser_normalizer_score_no_match_returns_zero() -> None:
    """TableParser.normalizer_score returns 0 for missing files."""
    p = TableParser()
    assert p.normalizer_score("/nonexistent/file.csv") == 0


def test_base_reader_hashable() -> None:
    """Frozen readers are hashable and can be put in sets."""
    r1 = DelimTxtParser()
    r2 = ExcelParser()
    r3 = MatParser()
    s = frozenset({r1, r2, r3})
    assert len(s) == 3


def test_normalizer_score_readable_file_returns_positive(tmp_path: Path) -> None:
    """normalizer_score returns a positive int when headers match the normalizer."""
    from bdf.normalizers import NORMALIZERS

    p = tmp_path / "bio.csv"
    rows = "\n".join("0.1,3.5,1" for _ in range(6))
    p.write_text(f"time/s,Ewe/V,I/mA\n{rows}\n")
    parser = DelimTxtParser(normalizer=NORMALIZERS["biologic"])
    assert parser.normalizer_score(p) > 0


def test_normalizer_score_unreadable_returns_zero(tmp_path: Path) -> None:
    """normalizer_score returns 0 when read_column_headings raises."""
    from bdf.normalizers import NORMALIZERS

    missing = tmp_path / "does_not_exist.csv"
    parser = DelimTxtParser(normalizer=NORMALIZERS["biologic"])
    assert parser.normalizer_score(missing) == 0


def test_delim_reader_rejects_no_header() -> None:
    """DelimTxtParser raises ValueError when has_header=False, pointing to polars directly."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="no headers"):
        DelimTxtParser(has_header=False)


def test_excel_reader_rejects_no_header() -> None:
    """ExcelParser raises ValueError when has_header=False, pointing to polars directly."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="no headers"):
        ExcelParser(has_header=False)


# --- DelimTxtParser sniffing -------------------------------------------------


def test_detect_structure_no_preamble() -> None:
    """_detect_structure returns (0, ',') when no preamble lines exist."""
    header = "a,b,c"
    data = "\n".join("1,2,3" for _ in range(15))
    skip, sep = DelimTxtParser._detect_structure(f"{header}\n{data}")
    assert skip == 0
    assert sep == ","


@pytest.mark.parametrize("preamble", [1, 3, 7])
def test_detect_structure_preamble_sizes(preamble: int) -> None:
    """_detect_structure returns correct skiprows for various preamble lengths."""
    pre = "\n".join(f"preamble line {i}" for i in range(preamble))
    body = "a,b,c\n" + "\n".join("1,2,3" for _ in range(15))
    sample = pre + "\n" + body
    skip, sep = DelimTxtParser._detect_structure(sample)
    assert skip == preamble
    assert sep == ","


def test_detect_structure_structured_preamble() -> None:
    """_detect_structure correctly detects data separator when preamble is space-delimited."""
    pre = "\n".join(f"preamble metadata line {i}" for i in range(16))
    body = "a,b,c\n" + "\n".join("1,2,3" for _ in range(15))
    sample = pre + "\n" + body
    skip, sep = DelimTxtParser._detect_structure(sample)
    assert skip == 16
    assert sep == ","


@pytest.mark.parametrize("sep", ["\t", ";", "|"])
def test_detect_structure_separator_variants(sep: str) -> None:
    """_detect_structure detects tab, semicolon, and pipe separators."""
    header = sep.join(["alpha", "beta", "gamma"])
    data = "\n".join(sep.join(["1", "2", "3"]) for _ in range(15))
    skip, detected_sep = DelimTxtParser._detect_structure(f"{header}\n{data}")
    assert skip == 0
    assert detected_sep == sep


def test_detect_structure_no_run_returns_default() -> None:
    """_detect_structure returns (0, ',') when no multi-field runs exist."""
    sample = "\n".join("a single undelimited column line" for _ in range(20))
    assert DelimTxtParser._detect_structure(sample) == (0, ",")


def test_detect_structure_short_run_returns_default() -> None:
    """_detect_structure returns (0, ',') when the data run is shorter than min_run."""
    sample = "pre\na,b,c\n" + "\n".join("1,2,3" for _ in range(4))
    assert DelimTxtParser._detect_structure(sample) == (0, ",")


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
    assert DelimTxtParser._sniff_decimal(df) == expected


def test_coerce_decimal_comma_rewrites_to_dot() -> None:
    """_coerce_decimal replaces commas with dots in string columns only."""
    lf = pl.DataFrame({"v": ["3,5", "3,6"], "n": [1, 2]}).lazy()
    out = DelimTxtParser._coerce_decimal(lf, True).collect()
    assert out["v"].to_list() == ["3.5", "3.6"]
    assert out["n"].to_list() == [1, 2]


def test_coerce_decimal_dot_is_noop() -> None:
    """_coerce_decimal is a no-op when decimal_comma is False."""
    lf = pl.DataFrame({"v": ["3.5", "3.6"]}).lazy()
    out = DelimTxtParser._coerce_decimal(lf, False).collect()
    assert out["v"].to_list() == ["3.5", "3.6"]


# --- head threading + read/headers/preamble ---------------------------------


def test_read_blank_normalizer_is_raw_passthrough(tmp_path: Path) -> None:
    """read() with the default empty normalizer keeps the source column names unchanged."""
    p = tmp_path / "data.csv"
    rows = "\n".join(f"{i},0.1,{3.5 + i / 10}" for i in range(6))
    p.write_text(f"t,i,v\n{rows}\n")
    lf = DelimTxtParser().read(p)
    assert lf.collect_schema().names() == ["t", "i", "v"]


def test_read_normalizes_to_bdf_columns(tmp_path: Path) -> None:
    """read() with a vendor normalizer returns BDF-canonical column labels."""
    p = tmp_path / "data.csv"
    rows = "\n".join(f"{i},0.1,{3.5 + i / 10}" for i in range(6))
    p.write_text(f"time,current,voltage\n{rows}\n")
    norm = TableNormalizer(
        test_time_second=ResolvedColumn(source_header="time"),
        current_ampere=ResolvedColumn(source_header="current"),
        voltage_volt=ResolvedColumn(source_header="voltage"),
    )
    df = DelimTxtParser(normalizer=norm).read(p).collect()
    assert df.columns == ["Test Time / s", "Voltage / V", "Current / A"]
    assert len(df) == 6


def test_headers_honour_separator_config(tmp_path: Path) -> None:
    """headers() returns columns parsed with the reader's own config."""
    p = tmp_path / "semi.csv"
    p.write_text("a;b;c\n1;2;3\n4;5;6\n")
    assert DelimTxtParser(separator=";").read_column_headings(p) == ["a", "b", "c"]


def test_preamble_returns_skipped_lines() -> None:
    """preamble() decodes head bytes and returns the skipped preamble lines."""
    text = "meta line 1\nmeta line 2\n" + "a,b,c\n" + "\n".join("1,2,3" for _ in range(15)) + "\n"
    head = text.encode("utf-8")
    assert DelimTxtParser().preamble(head) == ["meta line 1", "meta line 2"]


# --- _decode_head ------------------------------------------------------------


def test_decode_head_strips_partial_trailing_line() -> None:
    """_decode_head drops bytes after the last newline (incomplete line)."""
    head = b"line1\nline2\npartial"
    assert DelimTxtParser._decode_head(head) == "line1\nline2"


def test_decode_head_no_newline_returns_full_text() -> None:
    """_decode_head returns the full text when no newline is present."""
    assert DelimTxtParser._decode_head(b"no newline here") == "no newline here"


def test_decode_head_respects_encoding() -> None:
    """_decode_head decodes with the supplied encoding."""
    text = "héllo"
    assert DelimTxtParser._decode_head(text.encode("latin-1"), encoding="latin-1").startswith("h")


# --- ExcelParser -------------------------------------------------------------


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
    """ExcelParser.read() parses an xlsx file to a LazyFrame with correct columns."""
    pytest.importorskip("fastexcel")
    lf = ExcelParser().read(xlsx_file)
    df = lf.collect()
    assert df.columns == ["time", "voltage", "current"]
    assert len(df) == 2


def test_excel_headers_returns_column_names(xlsx_file: Path) -> None:
    """ExcelParser.read_column_headings() returns column names without reading data rows."""
    pytest.importorskip("fastexcel")
    assert ExcelParser().read_column_headings(xlsx_file) == ["time", "voltage", "current"]


def test_excel_headers_uses_n_rows_zero(xlsx_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ExcelParser.read_column_headings() passes n_rows=0 to avoid reading data rows."""
    pytest.importorskip("fastexcel")
    seen: list[dict] = []
    original = pl.read_excel

    def spy(*args: object, **kwargs: object) -> object:
        seen.append(dict(kwargs))
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pl, "read_excel", spy)
    ExcelParser().read_column_headings(xlsx_file)
    assert seen[0].get("read_options", {}).get("n_rows") == 0


def test_excel_all_sheets_selection_raises(xlsx_file: Path) -> None:
    """ExcelParser raises ValueError when sheet_id=0 makes polars return all sheets as a dict."""
    pytest.importorskip("fastexcel")
    with pytest.raises(ValueError, match="single sheet"):
        ExcelParser(sheet_id=0).read(xlsx_file)


def test_excel_read_options_forwarded(xlsx_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ExcelParser forwards read_options dict to polars.read_excel."""
    pytest.importorskip("fastexcel")
    seen: list[dict] = []
    original = pl.read_excel

    def spy(*args: object, **kwargs: object) -> object:
        seen.append(dict(kwargs))
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pl, "read_excel", spy)
    ExcelParser(read_options={"header_row": 0}).read(xlsx_file)
    assert seen[0].get("read_options", {}).get("header_row") == 0


# --- MatParser ---------------------------------------------------------------


@pytest.fixture
def mat_file(tmp_path: Path) -> Path:
    pytest.importorskip("scipy")
    import numpy as np
    from scipy.io import savemat

    p = tmp_path / "sample.mat"
    savemat(str(p), {"time": np.array([0.0, 1.0, 2.0]), "voltage": np.array([3.5, 3.6, 3.7])})
    return p


def _mat_normalizer(*headers: str) -> TableNormalizer:
    """Build a TableNormalizer whose known_header_names() are ``headers`` (MAT var names)."""
    fields = ["test_time_second", "voltage_volt", "current_ampere", "cycle_count"]
    return TableNormalizer(**{fields[i]: ResolvedColumn(source_header=h) for i, h in enumerate(headers)})


def test_matparser_read_raw_loads_normalizer_var_names(mat_file: Path) -> None:
    """MatParser._read_raw() loads the variables named by its normalizer."""
    lf = MatParser(normalizer=_mat_normalizer("time", "voltage"))._read_raw(mat_file)
    df = lf.collect()
    assert df.columns == ["time", "voltage"]
    assert len(df) == 3


def test_matparser_read_normalizes_to_bdf_columns(mat_file: Path) -> None:
    """MatParser.read() loads the normalizer's vars and returns BDF-canonical columns."""
    df = MatParser(normalizer=_mat_normalizer("time", "voltage")).read(mat_file).collect()
    assert len(df) == 3
    assert df.columns != ["time", "voltage"]
    assert len(df.columns) == 2


def test_matparser_blank_normalizer_loads_nothing(mat_file: Path) -> None:
    """A MatParser with the default empty normalizer sources no variables."""
    df = MatParser().read(mat_file).collect()
    assert df.width == 0


def test_matparser_read_missing_var_raises(mat_file: Path) -> None:
    """MatParser.read() raises ValueError when a normalizer var is absent from the file."""
    with pytest.raises(ValueError, match="not found"):
        MatParser(normalizer=_mat_normalizer("missing")).read(mat_file).collect()


def test_matparser_headers_returns_present_vars(mat_file: Path) -> None:
    """MatParser.read_column_headings() returns only normalizer vars present in the file."""
    present = MatParser(normalizer=_mat_normalizer("time", "voltage", "missing")).read_column_headings(mat_file)
    assert present == ["time", "voltage"]


def test_matparser_read_non_1d_var_raises(tmp_path: Path) -> None:
    """MatParser.read() raises ValueError for a variable that is not 1-D after squeeze."""
    pytest.importorskip("scipy")
    import numpy as np
    from scipy.io import savemat

    p = tmp_path / "matrix.mat"
    savemat(str(p), {"grid": np.arange(6).reshape(2, 3).astype(float)})
    with pytest.raises(ValueError, match="must be 1-D"):
        MatParser(normalizer=_mat_normalizer("grid")).read(p).collect()


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
    result = DelimTxtParser._build_rename_map(raw, "latin-1", skip=0, sep=",")
    assert result == {"T1[�C]": "T1[\N{DEGREE SIGN}C]"}


def test_build_rename_map_ascii_only_returns_empty() -> None:
    """ASCII-only headers produce an empty rename map (no mangling occurs)."""
    raw = _make_raw("time,voltage,current", ["1.0,3.5,0.1"])
    result = DelimTxtParser._build_rename_map(raw, "latin-1", skip=0, sep=",")
    assert result == {}


def test_build_rename_map_mixed_ascii_and_non_ascii() -> None:
    """Only columns with non-ASCII names appear in the rename map."""
    raw = _make_raw("time,T[\xb0C],current", ["1.0,25.0,0.1"])
    result = DelimTxtParser._build_rename_map(raw, "latin-1", skip=0, sep=",")
    assert result == {"T[�C]": "T[\N{DEGREE SIGN}C]"}
    assert "time" not in result
    assert "current" not in result


def test_build_rename_map_multiple_non_ascii_columns() -> None:
    """All non-ASCII column names are present in the rename map."""
    raw = _make_raw("T[\xb0C],\xe9tag,current", ["25.0,1,0.1"])
    result = DelimTxtParser._build_rename_map(raw, "latin-1", skip=0, sep=",")
    assert result == {"T[�C]": "T[\N{DEGREE SIGN}C]", "�tag": "\xe9tag"}
    assert "current" not in result


def test_build_rename_map_with_preamble_skip() -> None:
    """Correctly targets the header line when skip > 0 (preamble present)."""
    preamble = "meta line 1\nmeta line 2\n"
    data = "T[\xb0C],V\n1.0,3.5\n2.0,3.6\n"
    raw = (preamble + data).encode("latin-1")
    result = DelimTxtParser._build_rename_map(raw, "latin-1", skip=2, sep=",")
    assert result == {"T[�C]": "T[\N{DEGREE SIGN}C]"}


def test_build_rename_map_skip_beyond_content_returns_empty() -> None:
    """Returns empty dict when skip is beyond the buffered content."""
    raw = _make_raw("T[\xb0C],V", ["1.0,3.5"])
    result = DelimTxtParser._build_rename_map(raw, "latin-1", skip=99, sep=",")
    assert result == {}


def test_build_rename_map_tab_separator() -> None:
    """Works correctly with tab-separated files."""
    raw = "T[\xb0C]\tCurrent\n1.0\t0.5\n".encode("latin-1")
    result = DelimTxtParser._build_rename_map(raw, "latin-1", skip=0, sep="\t")
    assert result == {"T[�C]": "T[\N{DEGREE SIGN}C]"}


def test_build_rename_map_cp1252_euro_sign() -> None:
    """cp1252 encoding: € (byte 0x80) is renamed from its utf8-lossy replacement."""
    # byte 0x80 in cp1252 = € (U+20AC); invalid as a standalone UTF-8 byte
    raw = b"Cost[\x80],Count\n10.0,5\n"
    result = DelimTxtParser._build_rename_map(raw, "cp1252", skip=0, sep=",")
    assert result == {"Cost[�]": "Cost[\N{EURO SIGN}]"}


def test_build_rename_map_oslash_variant() -> None:
    """Maps ø (0xF8 in latin-1) in a slash-unit column name; utf8-lossy replaces with U+FFFD."""
    # ø = 0xF8 in latin-1; invalid as a standalone UTF-8 byte → utf8-lossy gives U+FFFD
    raw = _make_raw("temperature/øc,Current", ["25.0,0.5"])
    result = DelimTxtParser._build_rename_map(raw, "latin-1", skip=0, sep=",")
    assert result == {"temperature/\N{REPLACEMENT CHARACTER}c": "temperature/øc"}


# read() integration tests for encoding


def test_latin1_encoding_renames_degree_symbol_column(tmp_path: Path) -> None:
    """DelimTxtParser(encoding='latin-1') yields correct column names from a latin-1 file."""
    # ° is 0xB0 in latin-1; utf8-lossy would mangle it to the replacement char
    content = "T1[\xb0C],Current\n1.0,0.5\n2.0,0.6\n"
    p = tmp_path / "latin1.csv"
    p.write_bytes(content.encode("latin-1"))

    lf = DelimTxtParser(encoding="latin-1")._read_raw(p)
    assert isinstance(lf, pl.LazyFrame)
    cols = lf.collect_schema().names()
    assert "T1[\N{DEGREE SIGN}C]" in cols  # ° present, not mangled
    assert "Current" in cols  # ASCII column unaffected


def test_latin1_encoding_ascii_only_no_rename(tmp_path: Path) -> None:
    """All-ASCII column names are unaffected (no rename) when encoding='latin-1'."""
    content = "time,voltage,current\n1.0,3.5,0.1\n2.0,3.6,0.2\n"
    p = tmp_path / "ascii_latin1.csv"
    p.write_bytes(content.encode("latin-1"))

    lf = DelimTxtParser(encoding="latin-1")._read_raw(p)
    assert lf.collect_schema().names() == ["time", "voltage", "current"]


def test_latin1_degree_slash_normalizes_to_ambient_temperature(tmp_path: Path) -> None:
    """temperature/°C (slash notation) in a latin-1 file normalizes to Ambient Temperature / degC."""
    from bdf.normalizers import NORMALIZERS

    p = tmp_path / "bio_deg.csv"
    rows = "".join(f"{i}\t{3.5 + i / 10:.1f}\t{1.0 + i / 100:.2f}\t25.0\n" for i in range(15))
    p.write_bytes(("time/s\tEwe/V\tI/mA\ttemperature/°C\n" + rows).encode("latin-1"))
    assert "°".encode("latin-1") in p.read_bytes()
    parser = DelimTxtParser(encoding="latin-1", normalizer=NORMALIZERS["biologic"])
    df = parser.read(p).collect()
    assert "Ambient Temperature / degC" in df.columns


def test_latin1_oslash_slash_normalizes_to_ambient_temperature(tmp_path: Path) -> None:
    """temperature/øc (slash notation, ø=0xF8) in a latin-1 file normalizes to Ambient Temperature / degC."""
    from bdf.normalizers import NORMALIZERS

    p = tmp_path / "bio_oslash.csv"
    rows = "".join(f"{i}\t{3.5 + i / 10:.1f}\t{1.0 + i / 100:.2f}\t25.0\n" for i in range(15))
    p.write_bytes(("time/s\tEwe/V\tI/mA\ttemperature/øc\n" + rows).encode("latin-1"))
    assert "ø".encode("latin-1") in p.read_bytes()
    parser = DelimTxtParser(encoding="latin-1", normalizer=NORMALIZERS["biologic"])
    df = parser.read(p).collect()
    assert "Ambient Temperature / degC" in df.columns


# --- sample-data sniffing and column-output (real files under tests/data) ----

_SNIFF_CASES = [pytest.param(cid, c, marks=c.marks, id=cid) for cid, c in ALL_CASES if c.skip is not None]
_COLUMN_CASES = [pytest.param(cid, c, marks=c.marks, id=cid) for cid, c in ALL_CASES if c.expected_columns]
_CURRENT_CASES = [pytest.param(cid, c, marks=c.marks, id=cid) for cid, c in ALL_CASES if c.current_max_abs is not None]


@pytest.mark.parametrize("cid,case", _SNIFF_CASES)
def test_sample_detect_structure(cid: str, case: SampleCase, data_dir: Path) -> None:
    """_detect_structure returns the line count and separator for vendor samples."""
    path = resolve_source(case.source, case.is_url, data_dir)
    skip, sep = DelimTxtParser._detect_structure(DelimTxtParser._decode_head(read_head(path)))
    assert skip == case.skip
    assert sep == case.sep


@pytest.mark.parametrize("cid,case", _COLUMN_CASES)
def test_sample_read_includes_expected_columns(cid: str, case: SampleCase, data_dir: Path) -> None:
    """read() returns expected BDF columns with correct dtypes and no nulls."""
    path = resolve_source(case.source, case.is_url, data_dir)
    assert case.expected_columns is not None
    df = PLUGINS[case.plugin_id].table_parser.read(path).collect()
    assert frozenset(df.columns) == case.expected_columns

    schema = df.schema
    for col in case.expected_columns:
        spec_dtype = _LABEL_DTYPE.get(col)
        if spec_dtype == "float":
            assert schema[col].is_float(), f"{col}: expected float dtype, got {schema[col]}"
        elif spec_dtype == "int":
            assert schema[col].is_integer(), f"{col}: expected int dtype, got {schema[col]}"

    null_counts = df.null_count()
    for col in case.expected_columns - case.null_ok_columns:
        assert null_counts[col][0] == 0, f"{col}: contains nulls"


@pytest.mark.parametrize("cid,case", _CURRENT_CASES)
def test_sample_current_magnitude(cid: str, case: SampleCase, data_dir: Path) -> None:
    """Current / A stays within expected range after unit conversion (catches mA→A regressions)."""
    path = resolve_source(case.source, case.is_url, data_dir)
    df = PLUGINS[case.plugin_id].table_parser.read(path).collect()
    assert "Current / A" in df.columns
    max_abs = df["Current / A"].abs().max()
    assert max_abs <= case.current_max_abs


def test_excel_sheet_name_honoured_in_headers(data_dir: Path) -> None:
    """ExcelParser.read_column_headings() reads from the configured sheet_name."""
    pytest.importorskip("fastexcel")
    p = data_dir / "neware/sample_data_neware.xlsx"
    if not p.exists():
        pytest.skip("neware xlsx sample not present")
    headers = ExcelParser(sheet_name="record").read_column_headings(p)
    assert "Voltage(V)" in headers and "Current(mA)" in headers


def test_preamble_honours_explicit_separator() -> None:
    """preamble() correctly identifies skip rows when preamble lines contain the data separator."""
    pre = "key: a, b, c\nother: x, y, z\n"
    header = "time;voltage;current"
    rows = "\n".join(f"{i};{3.5 + i / 10};0.1" for i in range(15))
    head = (pre + header + "\n" + rows + "\n").encode("utf-8")

    assert DelimTxtParser(separator=";").preamble(head) == ["key: a, b, c", "other: x, y, z"]


# ---------------------------------------------------------------------------
# TableParser.read(validate=...) tests
# ---------------------------------------------------------------------------


def test_tableparser_read_validate_true_passes_for_valid_frame(tmp_path: Path) -> None:
    """TableParser.read(validate=True) does not raise for a fully normalised frame."""

    p = tmp_path / "data.csv"
    rows = "\n".join(f"{i},{3.5 + i / 10},0.1" for i in range(6))
    p.write_text(f"time,voltage,current\n{rows}\n")
    parser = DelimTxtParser(
        normalizer=TableNormalizer(
            test_time_second=(Syn("time"),),
            voltage_volt=(Syn("voltage"),),
            current_ampere=(Syn("current"),),
        ),
    )
    df = parser.read(p, validate=True).collect()
    assert "Test Time / s" in df.columns


def test_tableparser_read_validate_true_raises_for_missing_required(tmp_path: Path) -> None:
    """TableParser.read(validate=True) raises BDFValidationError when required columns are absent."""
    from bdf.validate import BDFValidationError

    p = tmp_path / "data.csv"
    rows = "\n".join(f"{i},{3.5 + i / 10}" for i in range(6))
    p.write_text(f"time,voltage\n{rows}\n")
    parser = DelimTxtParser(
        normalizer=TableNormalizer(
            test_time_second=(Syn("time"),),
            voltage_volt=(Syn("voltage"),),
        ),
    )
    with pytest.raises(BDFValidationError, match="Missing required BDF columns"):
        parser.read(p, validate=True).collect()


def test_tableparser_read_validate_true_lazy_returns_lazyframe(tmp_path: Path) -> None:
    """TableParser.read(validate=True) returns LazyFrame and validates without collecting."""

    p = tmp_path / "data.csv"
    rows = "\n".join(f"{i},{3.5 + i / 10},0.1" for i in range(6))
    p.write_text(f"time,voltage,current\n{rows}\n")
    parser = DelimTxtParser(
        normalizer=TableNormalizer(
            test_time_second=(Syn("time"),),
            voltage_volt=(Syn("voltage"),),
            current_ampere=(Syn("current"),),
        ),
    )
    result = parser.read(p, validate=True)
    assert isinstance(result, pl.LazyFrame)


# ---------------------------------------------------------------------------
# ParquetParser
# ---------------------------------------------------------------------------


class TestParquetParser:
    def test_read_raw(self, tmp_path: Path) -> None:
        """ParquetParser._read_raw returns LazyFrame with correct column names."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"a": [1, 2], "b": [3.0, 4.0]}).write_parquet(p)
        parser = ParquetParser()
        lf = parser._read_raw(p)
        assert isinstance(lf, pl.LazyFrame)
        assert lf.collect_schema().names() == ["a", "b"]

    def test_read_column_headings(self, tmp_path: Path) -> None:
        """ParquetParser.read_column_headings returns column names without data rows."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1], "y": [2]}).write_parquet(p)
        parser = ParquetParser()
        assert parser.read_column_headings(p) == ["x", "y"]

    def test_read_normalized(self, tmp_path: Path) -> None:
        """ParquetParser applies normalizer to produce BDF columns with correct scaling."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"voltage_V": [3.7], "current_mA": [500.0]}).write_parquet(p)
        norm = TableNormalizer(
            voltage_volt=(Syn("voltage_{unit}"),),
            current_ampere=(Syn("current_{unit}"),),
        )
        parser = ParquetParser(normalizer=norm)
        df = parser.read(p).collect()
        assert "Voltage / V" in df.columns
        assert "Current / A" in df.columns
        assert pytest.approx(df["Current / A"][0]) == 0.5


# ---------------------------------------------------------------------------
# NDAParser integration (skip if fastnda unavailable)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    pytest.importorskip("fastnda", reason="fastnda not installed") is None,
    reason="fastnda not installed",
)
class TestNDAParserIntegration:
    def test_read_raw_returns_lazyframe(self, tmp_path: Path):
        nda_fixture = Path(__file__).parent.parent / "data" / "neware" / "sample.nda"
        if not nda_fixture.exists():
            pytest.skip("No .nda fixture available")
        parser = NDAParser()
        lf = parser._read_raw(nda_fixture)
        assert isinstance(lf, pl.LazyFrame)
        assert len(lf.collect_schema().names()) > 0
