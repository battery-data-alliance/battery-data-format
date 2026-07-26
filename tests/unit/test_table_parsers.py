"""Unit and sample-data tests for bdf.table_parsers.

Each table parser carries a ``TableNormalizer`` field (default empty); ``read``
returns the normalized frame while ``_read_raw`` exposes the underlying mechanics.
Synthetic tests exercise each sniffing/parsing unit in isolation. Sample-data
tests run over the real files under ``tests/data/`` and skip when absent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl
import pytest

import bdf
from bdf.table_normalizers import ResolvedColumn, Syn, TableNormalizer
from bdf.table_parsers import (
    DelimTxtParser,
    ExcelParser,
    IpcParser,
    JsonParser,
    MatParser,
    NDAParser,
    NdjsonParser,
    ParquetParser,
    TableParser,
)


class TestMatchesExt:
    """TableParser.matches_ext."""

    def test_base_reader_matches_ext_unique(self) -> None:
        assert DelimTxtParser(unique_exts=frozenset({".mpt"})).matches_ext(".mpt") is True

    def test_base_reader_case_insensitive(self) -> None:
        assert DelimTxtParser().matches_ext(".CSV") is True


class TestMatchesMagicBytes:
    """TableParser.matches_magic_bytes / is_text per parser."""

    def test_excel_matches_zip_magic(self) -> None:
        assert ExcelParser().matches_magic_bytes(b"PK\x03\x04rest of zip") is True

    def test_excel_rejects_unrelated_bytes(self) -> None:
        assert ExcelParser().matches_magic_bytes(b"not a zip at all") is False

    def test_mat_matches_v5_magic(self) -> None:
        assert MatParser().matches_magic_bytes(b"MATLAB 5.0 MAT-file, rest") is True

    def test_mat_rejects_unrelated_bytes(self) -> None:
        assert MatParser().matches_magic_bytes(b"PK\x03\x04") is False

    def test_parquet_matches_par1_magic(self) -> None:
        assert ParquetParser().matches_magic_bytes(b"PAR1rest") is True

    def test_parquet_rejects_unrelated_bytes(self) -> None:
        assert ParquetParser().matches_magic_bytes(b"NEWARE") is False

    def test_nda_matches_neware_magic(self) -> None:
        assert NDAParser().matches_magic_bytes(b"NEWARErest") is True

    def test_nda_rejects_unrelated_bytes(self) -> None:
        assert NDAParser().matches_magic_bytes(b"PAR1") is False

    def test_is_text_flags(self) -> None:
        assert DelimTxtParser().is_text is True
        assert ExcelParser().is_text is False
        assert MatParser().is_text is False
        assert ParquetParser().is_text is False
        assert NDAParser().is_text is False


class TestDelimTxtTextPlausibilityGate:
    """DelimTxtParser.matches_magic_bytes is a gated last-resort, not unconditional True."""

    def test_ascii_csv_head_passes(self) -> None:
        head = b"time/s,voltage/V,current/A\n0.1,3.7,1.0\n0.2,3.6,1.0\n"
        assert DelimTxtParser().matches_magic_bytes(head) is True

    def test_latin1_preamble_head_passes(self) -> None:
        """A latin-1 file (e.g. basytec/biologic preamble) stays under the replacement-char threshold.

        A handful of non-UTF-8 bytes (accented operator names, degree symbols) against
        a realistically long ASCII-dominant preamble keeps the replacement-char ratio
        well under 1%; a single stray byte in a short string would not (see threshold
        math in DelimTxtParser.matches_magic_bytes).
        """
        ascii_line = "Acquisition started on : 01/02/2021, current/A, voltage/V, time/s\n"
        head = (ascii_line * 50 + "Op\xe9rateur: Nicolas\n").encode("latin-1")
        assert DelimTxtParser().matches_magic_bytes(head) is True

    def test_nul_byte_rejected(self) -> None:
        head = b"some,header\n1,2\x00garbage"
        assert DelimTxtParser().matches_magic_bytes(head) is False

    def test_random_binary_rejected(self) -> None:
        head = bytes(range(256)) * 4
        assert DelimTxtParser().matches_magic_bytes(head) is False

    def test_neware_magic_bytes_rejected_by_text_gate(self) -> None:
        """Binary formats with their own magic bytes also fail the text-plausibility gate."""
        head = b"NEWARE" + bytes(range(256))
        assert DelimTxtParser().matches_magic_bytes(head) is False

    def test_empty_head_rejected(self) -> None:
        assert DelimTxtParser().matches_magic_bytes(b"") is False


class TestTableParserConcreteMethods:
    def test_tableparser_instance_created(self) -> None:
        """TableParser can be instantiated with defaults."""
        p = TableParser()
        assert p.normalizer == TableNormalizer()
        assert p.unique_exts == frozenset()

    def test_tableparser_hashable(self) -> None:
        """TableParser instances are hashable and can be in sets."""
        p1 = TableParser()
        p2 = TableParser()
        # Same content, should hash to same value
        assert hash(p1) == hash(p2)
        s = {p1, p2}
        # Both equivalent instances hash to same bucket
        assert len(s) == 1

    def test_tableparser_normalizer_score_no_match_returns_zero(self) -> None:
        """TableParser.normalizer_score returns 0 for missing files."""
        p = TableParser()
        assert p.normalizer_score("/nonexistent/file.csv") == 0

    def test_base_reader_hashable(self) -> None:
        """Frozen readers are hashable and can be put in sets."""
        r1 = DelimTxtParser()
        r2 = ExcelParser()
        r3 = MatParser()
        s = frozenset({r1, r2, r3})
        assert len(s) == 3

    def test_normalizer_score_readable_file_returns_positive(self, tmp_path: Path) -> None:
        """normalizer_score returns a positive int when headers match the normalizer."""
        from bdf.table_normalizers import NORMALIZERS

        p = tmp_path / "bio.csv"
        rows = "\n".join("0.1,3.5,1" for _ in range(6))
        p.write_text(f"time/s,Ewe/V,I/mA\n{rows}\n")
        parser = DelimTxtParser(normalizer=NORMALIZERS["biologic"])
        assert parser.normalizer_score(p) > 0

    def test_normalizer_score_unreadable_returns_zero(self, tmp_path: Path) -> None:
        """normalizer_score returns 0 when read_column_headings raises."""
        from bdf.table_normalizers import NORMALIZERS

        missing = tmp_path / "does_not_exist.csv"
        parser = DelimTxtParser(normalizer=NORMALIZERS["biologic"])
        assert parser.normalizer_score(missing) == 0

    def test_delim_reader_rejects_no_header(self) -> None:
        """DelimTxtParser raises ValueError when has_header=False, pointing to polars directly."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="no headers"):
            DelimTxtParser(has_header=False)

    def test_excel_reader_rejects_no_header(self) -> None:
        """ExcelParser raises ValueError when has_header=False, pointing to polars directly."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="no headers"):
            ExcelParser(has_header=False)


class TestDelimTxtParserSniffing:
    def test_detect_structure_no_preamble(self) -> None:
        """_detect_structure returns (0, ',') when no preamble lines exist."""
        header = "a,b,c"
        data = "\n".join("1,2,3" for _ in range(15))
        skip, sep = DelimTxtParser._detect_structure(f"{header}\n{data}")
        assert skip == 0
        assert sep == ","

    @pytest.mark.parametrize("preamble", [1, 3, 7])
    def test_detect_structure_preamble_sizes(self, preamble: int) -> None:
        """_detect_structure returns correct skiprows for various preamble lengths."""
        pre = "\n".join(f"preamble line {i}" for i in range(preamble))
        body = "a,b,c\n" + "\n".join("1,2,3" for _ in range(15))
        sample = pre + "\n" + body
        skip, sep = DelimTxtParser._detect_structure(sample)
        assert skip == preamble
        assert sep == ","

    def test_detect_structure_structured_preamble(self) -> None:
        """_detect_structure correctly detects data separator when preamble is space-delimited."""
        pre = "\n".join(f"preamble metadata line {i}" for i in range(16))
        body = "a,b,c\n" + "\n".join("1,2,3" for _ in range(15))
        sample = pre + "\n" + body
        skip, sep = DelimTxtParser._detect_structure(sample)
        assert skip == 16
        assert sep == ","

    @pytest.mark.parametrize("sep", ["\t", ";", "|"])
    def test_detect_structure_separator_variants(self, sep: str) -> None:
        """_detect_structure detects tab, semicolon, and pipe separators."""
        header = sep.join(["alpha", "beta", "gamma"])
        data = "\n".join(sep.join(["1", "2", "3"]) for _ in range(15))
        skip, detected_sep = DelimTxtParser._detect_structure(f"{header}\n{data}")
        assert skip == 0
        assert detected_sep == sep

    def test_detect_structure_no_run_returns_default(self) -> None:
        """_detect_structure returns (0, ',') when no multi-field runs exist."""
        sample = "\n".join("a single undelimited column line" for _ in range(20))
        assert DelimTxtParser._detect_structure(sample) == (0, ",")

    def test_detect_structure_short_run_returns_default(self) -> None:
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
    def test_sniff_decimal(self, values: list[str], expected: bool) -> None:
        """_sniff_decimal returns True when comma-decimal strings dominate, else False."""
        df = pl.DataFrame({"v": values})
        assert DelimTxtParser._sniff_decimal(df) == expected

    def test_coerce_decimal_comma_rewrites_to_dot(self) -> None:
        """_coerce_decimal replaces commas with dots in string columns only."""
        lf = pl.DataFrame({"v": ["3,5", "3,6"], "n": [1, 2]}).lazy()
        out = DelimTxtParser._coerce_decimal(lf, True).collect()
        assert out["v"].to_list() == ["3.5", "3.6"]
        assert out["n"].to_list() == [1, 2]

    def test_coerce_decimal_dot_is_noop(self) -> None:
        """_coerce_decimal is a no-op when decimal_comma is False."""
        lf = pl.DataFrame({"v": ["3.5", "3.6"]}).lazy()
        out = DelimTxtParser._coerce_decimal(lf, False).collect()
        assert out["v"].to_list() == ["3.5", "3.6"]


class TestHeadThreadingAndRead:
    """Head threading + read/headers/preamble."""

    def test_read_blank_normalizer_is_raw_passthrough(self, tmp_path: Path) -> None:
        """read() with the default empty normalizer keeps the source column names unchanged."""
        p = tmp_path / "data.csv"
        rows = "\n".join(f"{i},0.1,{3.5 + i / 10}" for i in range(6))
        p.write_text(f"t,i,v\n{rows}\n")
        lf = DelimTxtParser().read(p, validate=False)
        assert lf.collect_schema().names() == ["t", "i", "v"]

    def test_read_normalizes_to_bdf_columns(self, tmp_path: Path) -> None:
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

    def test_headers_honour_separator_config(self, tmp_path: Path) -> None:
        """headers() returns columns parsed with the reader's own config."""
        p = tmp_path / "semi.csv"
        p.write_text("a;b;c\n1;2;3\n4;5;6\n")
        assert DelimTxtParser(separator=";").read_column_headings(p) == ["a", "b", "c"]

    def test_preamble_returns_skipped_lines(self) -> None:
        """preamble() decodes head bytes and returns the skipped preamble lines."""
        text = "meta line 1\nmeta line 2\n" + "a,b,c\n" + "\n".join("1,2,3" for _ in range(15)) + "\n"
        head = text.encode("utf-8")
        assert DelimTxtParser().preamble(head) == ["meta line 1", "meta line 2"]


class TestDecodeHead:
    def test_decode_head_strips_partial_trailing_line(self) -> None:
        """_decode_head drops bytes after the last newline (incomplete line)."""
        head = b"line1\nline2\npartial"
        assert DelimTxtParser._decode_head(head) == "line1\nline2"

    def test_decode_head_no_newline_returns_full_text(self) -> None:
        """_decode_head returns the full text when no newline is present."""
        assert DelimTxtParser._decode_head(b"no newline here") == "no newline here"

    def test_decode_head_respects_encoding(self) -> None:
        """_decode_head decodes with the supplied encoding."""
        text = "héllo"
        assert DelimTxtParser._decode_head(text.encode("latin-1"), encoding="latin-1").startswith("h")


class TestExcelParser:
    @pytest.fixture
    def xlsx_file(self, tmp_path: Path) -> Path:
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

    def test_excel_read_returns_lazyframe(self, xlsx_file: Path) -> None:
        """ExcelParser.read() parses an xlsx file to a LazyFrame with correct columns."""
        pytest.importorskip("fastexcel")
        lf = ExcelParser().read(xlsx_file, validate=False)
        df = lf.collect()
        assert df.columns == ["time", "voltage", "current"]
        assert len(df) == 2

    def test_excel_headers_returns_column_names(self, xlsx_file: Path) -> None:
        """ExcelParser.read_column_headings() returns column names without reading data rows."""
        pytest.importorskip("fastexcel")
        assert ExcelParser().read_column_headings(xlsx_file) == ["time", "voltage", "current"]

    def test_excel_headers_uses_n_rows_zero(self, xlsx_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def test_excel_all_sheets_selection_raises(self, xlsx_file: Path) -> None:
        """ExcelParser raises ValueError when sheet_id=0 makes polars return all sheets as a dict."""
        pytest.importorskip("fastexcel")
        with pytest.raises(ValueError, match="single sheet"):
            ExcelParser(sheet_id=0).read(xlsx_file)

    def test_excel_read_options_forwarded(self, xlsx_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ExcelParser forwards read_options dict to polars.read_excel."""
        pytest.importorskip("fastexcel")
        seen: list[dict] = []
        original = pl.read_excel

        def spy(*args: object, **kwargs: object) -> object:
            seen.append(dict(kwargs))
            return original(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(pl, "read_excel", spy)
        ExcelParser(read_options={"header_row": 0}).read(xlsx_file, validate=False)
        assert seen[0].get("read_options", {}).get("header_row") == 0


def _mat_normalizer(*headers: str) -> TableNormalizer:
    """Build a TableNormalizer whose known_header_names() are ``headers`` (MAT var names)."""
    fields = ["test_time_second", "voltage_volt", "current_ampere", "cycle_count"]
    return TableNormalizer(**{fields[i]: ResolvedColumn(source_header=h) for i, h in enumerate(headers)})


class TestMatParser:
    @pytest.fixture
    def mat_file(self, tmp_path: Path) -> Path:
        pytest.importorskip("scipy")
        import numpy as np
        from scipy.io import savemat

        p = tmp_path / "sample.mat"
        savemat(str(p), {"time": np.array([0.0, 1.0, 2.0]), "voltage": np.array([3.5, 3.6, 3.7])})
        return p

    def test_matparser_read_raw_loads_normalizer_var_names(self, mat_file: Path) -> None:
        """MatParser._read_raw() loads the variables named by its normalizer."""
        lf = MatParser(normalizer=_mat_normalizer("time", "voltage"))._read_raw(mat_file)
        df = lf.collect()
        assert df.columns == ["time", "voltage"]
        assert len(df) == 3

    def test_matparser_read_normalizes_to_bdf_columns(self, mat_file: Path) -> None:
        """MatParser.read() loads the normalizer's vars and returns BDF-canonical columns."""
        df = MatParser(normalizer=_mat_normalizer("time", "voltage")).read(mat_file, validate=False).collect()
        assert len(df) == 3
        assert df.columns != ["time", "voltage"]
        assert len(df.columns) == 2

    def test_matparser_blank_normalizer_loads_nothing(self, mat_file: Path) -> None:
        """A MatParser with the default empty normalizer sources no variables."""
        df = MatParser().read(mat_file, validate=False).collect()
        assert df.width == 0

    def test_matparser_read_missing_var_raises(self, mat_file: Path) -> None:
        """MatParser.read() raises ValueError when a normalizer var is absent from the file."""
        with pytest.raises(ValueError, match="not found"):
            MatParser(normalizer=_mat_normalizer("missing")).read(mat_file).collect()

    def test_matparser_headers_returns_present_vars(self, mat_file: Path) -> None:
        """MatParser.read_column_headings() returns only normalizer vars present in the file."""
        present = MatParser(normalizer=_mat_normalizer("time", "voltage", "missing")).read_column_headings(mat_file)
        assert present == ["time", "voltage"]

    def test_matparser_read_non_1d_var_raises(self, tmp_path: Path) -> None:
        """MatParser.read() raises ValueError for a variable that is not 1-D after squeeze."""
        pytest.importorskip("scipy")
        import numpy as np
        from scipy.io import savemat

        p = tmp_path / "matrix.mat"
        savemat(str(p), {"grid": np.arange(6).reshape(2, 3).astype(float)})
        with pytest.raises(ValueError, match="must be 1-D"):
            MatParser(normalizer=_mat_normalizer("grid")).read(p).collect()


def _make_raw(header: str, rows: list[str], encoding: str = "latin-1") -> bytes:
    """Encode a minimal CSV (header + rows) with the given encoding."""
    return ("\n".join([header] + rows) + "\n").encode(encoding)


class TestBuildRenameMap:
    """``_build_rename_map`` unit tests.

    Each test constructs raw bytes directly so it exercises the method in isolation,
    independent of file I/O and separator/skip sniffing.
    """

    def test_build_rename_map_degree_symbol(self) -> None:
        """Maps the utf8-lossy mangled name to the properly-decoded name for °."""
        # ° = 0xB0 in latin-1; utf8-lossy renders it as U+FFFD
        raw = _make_raw("T1[\xb0C],Current", ["1.0,0.5"])
        result = DelimTxtParser._build_rename_map(raw, "latin-1", skip=0, sep=",")
        assert result == {"T1[�C]": "T1[\N{DEGREE SIGN}C]"}

    def test_build_rename_map_ascii_only_returns_empty(self) -> None:
        """ASCII-only headers produce an empty rename map (no mangling occurs)."""
        raw = _make_raw("time,voltage,current", ["1.0,3.5,0.1"])
        result = DelimTxtParser._build_rename_map(raw, "latin-1", skip=0, sep=",")
        assert result == {}

    def test_build_rename_map_mixed_ascii_and_non_ascii(self) -> None:
        """Only columns with non-ASCII names appear in the rename map."""
        raw = _make_raw("time,T[\xb0C],current", ["1.0,25.0,0.1"])
        result = DelimTxtParser._build_rename_map(raw, "latin-1", skip=0, sep=",")
        assert result == {"T[�C]": "T[\N{DEGREE SIGN}C]"}
        assert "time" not in result
        assert "current" not in result

    def test_build_rename_map_multiple_non_ascii_columns(self) -> None:
        """All non-ASCII column names are present in the rename map."""
        raw = _make_raw("T[\xb0C],\xe9tag,current", ["25.0,1,0.1"])
        result = DelimTxtParser._build_rename_map(raw, "latin-1", skip=0, sep=",")
        assert result == {"T[�C]": "T[\N{DEGREE SIGN}C]", "�tag": "\xe9tag"}
        assert "current" not in result

    def test_build_rename_map_with_preamble_skip(self) -> None:
        """Correctly targets the header line when skip > 0 (preamble present)."""
        preamble = "meta line 1\nmeta line 2\n"
        data = "T[\xb0C],V\n1.0,3.5\n2.0,3.6\n"
        raw = (preamble + data).encode("latin-1")
        result = DelimTxtParser._build_rename_map(raw, "latin-1", skip=2, sep=",")
        assert result == {"T[�C]": "T[\N{DEGREE SIGN}C]"}

    def test_build_rename_map_skip_beyond_content_returns_empty(self) -> None:
        """Returns empty dict when skip is beyond the buffered content."""
        raw = _make_raw("T[\xb0C],V", ["1.0,3.5"])
        result = DelimTxtParser._build_rename_map(raw, "latin-1", skip=99, sep=",")
        assert result == {}

    def test_build_rename_map_tab_separator(self) -> None:
        """Works correctly with tab-separated files."""
        raw = "T[\xb0C]\tCurrent\n1.0\t0.5\n".encode("latin-1")
        result = DelimTxtParser._build_rename_map(raw, "latin-1", skip=0, sep="\t")
        assert result == {"T[�C]": "T[\N{DEGREE SIGN}C]"}

    def test_build_rename_map_cp1252_euro_sign(self) -> None:
        """cp1252 encoding: € (byte 0x80) is renamed from its utf8-lossy replacement."""
        # byte 0x80 in cp1252 = € (U+20AC); invalid as a standalone UTF-8 byte
        raw = b"Cost[\x80],Count\n10.0,5\n"
        result = DelimTxtParser._build_rename_map(raw, "cp1252", skip=0, sep=",")
        assert result == {"Cost[�]": "Cost[\N{EURO SIGN}]"}

    def test_build_rename_map_oslash_variant(self) -> None:
        """Maps ø (0xF8 in latin-1) in a slash-unit column name; utf8-lossy replaces with U+FFFD."""
        # ø = 0xF8 in latin-1; invalid as a standalone UTF-8 byte → utf8-lossy gives U+FFFD
        raw = _make_raw("temperature/øc,Current", ["25.0,0.5"])
        result = DelimTxtParser._build_rename_map(raw, "latin-1", skip=0, sep=",")
        assert result == {"temperature/\N{REPLACEMENT CHARACTER}c": "temperature/øc"}


class TestEncodingIntegration:
    """``read()`` integration tests for encoding."""

    def test_latin1_encoding_renames_degree_symbol_column(self, tmp_path: Path) -> None:
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

    def test_latin1_encoding_ascii_only_no_rename(self, tmp_path: Path) -> None:
        """All-ASCII column names are unaffected (no rename) when encoding='latin-1'."""
        content = "time,voltage,current\n1.0,3.5,0.1\n2.0,3.6,0.2\n"
        p = tmp_path / "ascii_latin1.csv"
        p.write_bytes(content.encode("latin-1"))

        lf = DelimTxtParser(encoding="latin-1")._read_raw(p)
        assert lf.collect_schema().names() == ["time", "voltage", "current"]

    def test_latin1_degree_slash_normalizes_to_temperature_t1(self, tmp_path: Path) -> None:
        """temperature/°C (slash notation) in a latin-1 file normalizes to Temperature T1 / degC."""
        from bdf.table_normalizers import NORMALIZERS

        p = tmp_path / "bio_deg.csv"
        rows = "".join(f"{i}\t{3.5 + i / 10:.1f}\t{1.0 + i / 100:.2f}\t25.0\n" for i in range(15))
        p.write_bytes(("time/s\tEwe/V\tI/mA\tTemperature/°C\n" + rows).encode("latin-1"))
        assert "°".encode("latin-1") in p.read_bytes()
        parser = DelimTxtParser(encoding="latin-1", normalizer=NORMALIZERS["biologic"])
        df = parser.read(p).collect()
        assert "Temperature T1 / degC" in df.columns

    def test_latin1_oslash_slash_normalizes_to_temperature_t1(self, tmp_path: Path) -> None:
        """temperature/øc (slash notation, ø=0xF8) in a latin-1 file normalizes to Temperature T1 / degC."""
        from bdf.table_normalizers import NORMALIZERS

        p = tmp_path / "bio_oslash.csv"
        rows = "".join(f"{i}\t{3.5 + i / 10:.1f}\t{1.0 + i / 100:.2f}\t25.0\n" for i in range(15))
        p.write_bytes(("time/s\tEwe/V\tI/mA\tTemperature/øc\n" + rows).encode("latin-1"))
        assert "ø".encode("latin-1") in p.read_bytes()
        parser = DelimTxtParser(encoding="latin-1", normalizer=NORMALIZERS["biologic"])
        df = parser.read(p).collect()
        assert "Temperature T1 / degC" in df.columns

    def test_preamble_honours_explicit_separator(self) -> None:
        """preamble() correctly identifies skip rows when preamble lines contain the data separator."""
        pre = "key: a, b, c\nother: x, y, z\n"
        header = "time;voltage;current"
        rows = "\n".join(f"{i};{3.5 + i / 10};0.1" for i in range(15))
        head = (pre + header + "\n" + rows + "\n").encode("utf-8")

        assert DelimTxtParser(separator=";").preamble(head) == ["key: a, b, c", "other: x, y, z"]


class TestReadValidate:
    """TableParser.read(validate=...) tests."""

    def test_tableparser_read_validate_true_passes_for_valid_frame(self, tmp_path: Path) -> None:
        """TableParser.read(validate=True) does not raise for a fully normalised frame."""

        p = tmp_path / "data.csv"
        rows = "\n".join(f"{i},{3.5 + i / 10},0.1" for i in range(6))
        p.write_text(f"time,voltage,current\n{rows}\n")
        parser = DelimTxtParser(
            normalizer=TableNormalizer(
                test_time_second=(Syn(hdr="time"),),
                voltage_volt=(Syn(hdr="voltage"),),
                current_ampere=(Syn(hdr="current"),),
            ),
        )
        df = parser.read(p, validate=True).collect()
        assert "Test Time / s" in df.columns

    def test_tableparser_read_validate_true_raises_for_missing_required(self, tmp_path: Path) -> None:
        """TableParser.read(validate=True) raises BDFValidationError when required columns are absent."""
        from bdf.validate import BDFValidationError

        p = tmp_path / "data.csv"
        rows = "\n".join(f"{i},{3.5 + i / 10}" for i in range(6))
        p.write_text(f"time,voltage\n{rows}\n")
        parser = DelimTxtParser(
            normalizer=TableNormalizer(
                test_time_second=(Syn(hdr="time"),),
                voltage_volt=(Syn(hdr="voltage"),),
            ),
        )
        with pytest.raises(BDFValidationError, match="Missing required BDF columns"):
            parser.read(p, validate=True).collect()

    def test_tableparser_read_validate_true_lazy_returns_lazyframe(self, tmp_path: Path) -> None:
        """TableParser.read(validate=True) returns LazyFrame and validates without collecting."""

        p = tmp_path / "data.csv"
        rows = "\n".join(f"{i},{3.5 + i / 10},0.1" for i in range(6))
        p.write_text(f"time,voltage,current\n{rows}\n")
        parser = DelimTxtParser(
            normalizer=TableNormalizer(
                test_time_second=(Syn(hdr="time"),),
                voltage_volt=(Syn(hdr="voltage"),),
                current_ampere=(Syn(hdr="current"),),
            ),
        )
        result = parser.read(p, validate=True)
        assert isinstance(result, pl.LazyFrame)

    def test_tableparser_read_lazy_false_collects_normalized_frame(self, tmp_path: Path) -> None:
        """TableParser.read(lazy=False) collects the normalized result to a DataFrame."""

        p = tmp_path / "data.csv"
        rows = "\n".join(f"{i},{3.5 + i / 10},0.1" for i in range(6))
        p.write_text(f"time,voltage,current\n{rows}\n")
        parser = DelimTxtParser(
            normalizer=TableNormalizer(
                test_time_second=(Syn(hdr="time"),),
                voltage_volt=(Syn(hdr="voltage"),),
                current_ampere=(Syn(hdr="current"),),
            ),
        )
        result = parser.read(p, lazy=False)
        assert isinstance(result, pl.DataFrame)
        assert "Test Time / s" in result.columns

    def test_tableparser_read_lazy_false_collects_raw_frame(self, tmp_path: Path) -> None:
        """TableParser.read(normalize=False) collects the raw frame to a DataFrame."""

        p = tmp_path / "data.csv"
        rows = "\n".join(f"{i},{3.5 + i / 10},0.1" for i in range(6))
        p.write_text(f"time,voltage,current\n{rows}\n")
        result = DelimTxtParser().read(p, normalize=False, lazy=False)
        assert isinstance(result, pl.DataFrame)
        assert "time" in result.columns


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
            voltage_volt=(Syn(hdr="voltage_{unit}"),),
            current_ampere=(Syn(hdr="current_{unit}"),),
        )
        parser = ParquetParser(normalizer=norm)
        df = parser.read(p, validate=False).collect()
        assert "Voltage / V" in df.columns
        assert "Current / A" in df.columns
        assert pytest.approx(df["Current / A"][0]) == 0.5

    def test_read_raw_without_extension(self, tmp_path: Path) -> None:
        """ParquetParser._read_raw works without extension (using magic bytes)."""
        p = tmp_path / "data"
        pl.DataFrame({"a": [1, 2], "b": [3.0, 4.0]}).write_parquet(p)
        lf, _metadata = bdf.read(p, normalize=False)
        assert isinstance(lf, pl.DataFrame)
        assert lf.collect_schema().names() == ["a", "b"]


class TestJsonParser:
    def test_read_raw(self, tmp_path: Path) -> None:
        """JsonParser._read_raw returns LazyFrame with correct column names."""
        p = tmp_path / "data.json"
        pl.DataFrame({"a": [1, 2], "b": [3.0, 4.0]}).write_json(p)
        parser = JsonParser()
        lf = parser._read_raw(p)
        assert isinstance(lf, pl.LazyFrame)
        assert lf.collect_schema().names() == ["a", "b"]

    def test_read_column_headings(self, tmp_path: Path) -> None:
        """JsonParser.read_column_headings returns column names without data rows."""
        p = tmp_path / "data.json"
        pl.DataFrame({"x": [1], "y": [2]}).write_json(p)
        parser = JsonParser()
        assert parser.read_column_headings(p) == ["x", "y"]

    def test_read_normalized(self, tmp_path: Path) -> None:
        """JsonParser applies normalizer to produce BDF columns with correct scaling."""
        p = tmp_path / "data.json"
        pl.DataFrame({"voltage_V": [3.7], "current_mA": [500.0]}).write_json(p)
        norm = TableNormalizer(
            voltage_volt=(Syn(hdr="voltage_{unit}"),),
            current_ampere=(Syn(hdr="current_{unit}"),),
        )
        parser = JsonParser(normalizer=norm)
        df = parser.read(p, validate=False).collect()
        assert "Voltage / V" in df.columns
        assert "Current / A" in df.columns
        assert pytest.approx(df["Current / A"][0]) == 0.5

    def test_read_raw_column_oriented(self, tmp_path: Path) -> None:
        """JsonParser._read_raw works for both record-oriented and list-oriented json."""
        parser = JsonParser()

        p_record = tmp_path / "data_record.json"
        data_record = [
            {"a": 1.0, "b": 3.0},
            {"a": 2.0, "b": 4.0},
        ]
        with p_record.open("w") as f:
            json.dump(data_record, f)
        lf = parser._read_raw(p_record)
        assert isinstance(lf, pl.LazyFrame)
        assert lf.collect_schema().names() == ["a", "b"]
        df = lf.collect()
        assert len(df) == 2

        p_list = tmp_path / "data_list.json"
        data_list = {
            "a": [1.0, 2.0],
            "b": [3.0, 4.0],
        }
        with p_list.open("w") as f:
            json.dump(data_list, f)
        lf = parser._read_raw(p_list)
        assert isinstance(lf, pl.LazyFrame)
        assert lf.collect_schema().names() == ["a", "b"]
        df = lf.collect()
        assert len(df) == 2

    def test_special_characters(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Spy on Path.open to check encodings used
        original_open = Path.open
        calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def spy_open(self: Path, *args: Any, **kwargs: Any) -> object:
            calls.append((args, kwargs))
            return original_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", spy_open)

        # Open file with special characters and assert they do not become garbled
        parser = JsonParser()
        p_record = tmp_path / "data_record.json"
        data_record = [
            {"q [µA·h]": 1.0, "R [Ω]": 2.0, "sweep [mV s⁻¹]": 3.0, "T1 [°C]": 4.0, "T2 [℃]": 5.0},
            {"q [µA·h]": 1.1, "R [Ω]": 2.1, "sweep [mV s⁻¹]": 3.1, "T1 [°C]": 4.1, "T2 [℃]": 5.1},
        ]
        with p_record.open("w", encoding="utf-8") as f:
            json.dump(data_record, f, ensure_ascii=False)
        lf = parser._read_raw(p_record)
        assert isinstance(lf, pl.LazyFrame)
        df = lf.collect()
        assert len(df) == 2
        assert all(col in df.columns for col in ["q [µA·h]", "R [Ω]", "sweep [mV s⁻¹]", "T1 [°C]", "T2 [℃]"])
        columns = lf = parser.read_column_headings(p_record)
        assert columns == ["q [µA·h]", "R [Ω]", "sweep [mV s⁻¹]", "T1 [°C]", "T2 [℃]"]

        # Assert that all Path.open was explicitly given utf-8 encoding
        # Otherwise Linux CI runners pass this test whether or not encoding was given
        assert calls, "Path.open was never called"
        for args, kwargs in calls:
            mode = args[0] if args else kwargs.get("mode", "r")
            if "b" in mode:
                continue
            encoding = args[1] if len(args) > 1 else kwargs.get("encoding")
            assert encoding == "utf-8", f"opened in text mode without encoding='utf-8': args={args} kwargs={kwargs}"


class TestNdjsonParser:
    def test_read_raw(self, tmp_path: Path) -> None:
        """NdjsonParser._read_raw returns LazyFrame with correct column names."""
        p = tmp_path / "data.ndjson"
        pl.DataFrame({"a": [1, 2], "b": [3.0, 4.0]}).write_ndjson(p)
        parser = NdjsonParser()
        lf = parser._read_raw(p)
        assert isinstance(lf, pl.LazyFrame)
        assert lf.collect_schema().names() == ["a", "b"]

    def test_read_column_headings(self, tmp_path: Path) -> None:
        """NdjsonParser.read_column_headings returns column names without data rows."""
        p = tmp_path / "data.ndjson"
        pl.DataFrame({"x": [1], "y": [2]}).write_ndjson(p)
        parser = NdjsonParser()
        assert parser.read_column_headings(p) == ["x", "y"]

    def test_read_normalized(self, tmp_path: Path) -> None:
        """NdjsonParser applies normalizer to produce BDF columns with correct scaling."""
        p = tmp_path / "data.ndjson"
        pl.DataFrame({"voltage_V": [3.7], "current_mA": [500.0]}).write_ndjson(p)
        norm = TableNormalizer(
            voltage_volt=(Syn(hdr="voltage_{unit}"),),
            current_ampere=(Syn(hdr="current_{unit}"),),
        )
        parser = NdjsonParser(normalizer=norm)
        df = parser.read(p, validate=False).collect()
        assert "Voltage / V" in df.columns
        assert "Current / A" in df.columns
        assert pytest.approx(df["Current / A"][0]) == 0.5


class TestIpcParser:
    def test_read_raw(self, tmp_path: Path) -> None:
        """IpcParser._read_raw returns LazyFrame with correct column names."""
        p = tmp_path / "data.ipc"
        pl.DataFrame({"a": [1, 2], "b": [3.0, 4.0]}).write_ipc(p)
        parser = IpcParser()
        lf = parser._read_raw(p)
        assert isinstance(lf, pl.LazyFrame)
        assert lf.collect_schema().names() == ["a", "b"]

    def test_read_column_headings(self, tmp_path: Path) -> None:
        """IpcParser.read_column_headings returns column names without data rows."""
        p = tmp_path / "data.ipc"
        pl.DataFrame({"x": [1], "y": [2]}).write_ipc(p)
        parser = IpcParser()
        assert parser.read_column_headings(p) == ["x", "y"]

    def test_read_normalized(self, tmp_path: Path) -> None:
        """IpcParser applies normalizer to produce BDF columns with correct scaling."""
        p = tmp_path / "data.ipc"
        pl.DataFrame({"voltage_V": [3.7], "current_mA": [500.0]}).write_ipc(p)
        norm = TableNormalizer(
            voltage_volt=(Syn(hdr="voltage_{unit}"),),
            current_ampere=(Syn(hdr="current_{unit}"),),
        )
        parser = IpcParser(normalizer=norm)
        df = parser.read(p, validate=False).collect()
        assert "Voltage / V" in df.columns
        assert "Current / A" in df.columns
        assert pytest.approx(df["Current / A"][0]) == 0.5

    def test_read_raw_without_extension(self, tmp_path: Path) -> None:
        """IpcParser._read_raw works without extension (using magic bytes)."""
        p = tmp_path / "data"
        pl.DataFrame({"a": [1, 2], "b": [3.0, 4.0]}).write_ipc(p)
        lf, _metadata = bdf.read(p, normalize=False)
        assert isinstance(lf, pl.DataFrame)
        assert lf.collect_schema().names() == ["a", "b"]


class TestNDAParser:
    """NDAParser, with fastnda mocked so no binary fixture or real dependency is needed."""

    @pytest.fixture
    def fake_fastnda(self, monkeypatch: pytest.MonkeyPatch):
        """Install a stub `fastnda` module exposing a spyable `read(path)`."""
        import sys
        import types

        df = pl.DataFrame({"a": [1, 2], "b": [3, 4]})
        calls: list[str] = []

        def fake_read(path: str) -> pl.DataFrame:
            calls.append(path)
            return df

        module = types.ModuleType("fastnda")
        module.read = fake_read  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "fastnda", module)
        return calls

    def test_read_raw_missing_dependency_raises_runtime_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_read_raw raises RuntimeError with install hint when fastnda is not importable."""
        import builtins

        real_import = builtins.__import__

        def blocked_import(name: str, *args, **kwargs):
            if name == "fastnda":
                raise ImportError("no fastnda")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked_import)
        parser = NDAParser()
        with pytest.raises(RuntimeError, match="fastnda"):
            parser._read_raw("cell.nda")

    def test_read_raw_passes_resolved_local_path_to_fastnda(self, fake_fastnda, tmp_path: Path) -> None:
        """_read_raw resolves a local path and forwards it as a string to fastnda.read."""
        nda_path = tmp_path / "cell.nda"
        nda_path.write_bytes(b"")
        parser = NDAParser()
        lf = parser._read_raw(nda_path)
        assert isinstance(lf, pl.LazyFrame)
        assert lf.collect_schema().names() == ["a", "b"]
        assert fake_fastnda == [str(nda_path)]

    def test_read_raw_resolves_url_via_fetch_url(
        self, fake_fastnda, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """_read_raw downloads URL sources through fetch_url before handing the local path to fastnda."""
        cached = tmp_path / "downloaded.nda"
        cached.write_bytes(b"")
        monkeypatch.setattr("bdf.fetch.fetch_url", lambda url: cached)
        parser = NDAParser()
        parser._read_raw("https://example.com/cell.nda")
        assert fake_fastnda == [str(cached)]

    def test_read_column_headings_returns_schema_names(self, fake_fastnda, tmp_path: Path) -> None:
        """read_column_headings reflects fastnda's column names without requiring data rows."""
        nda_path = tmp_path / "cell.nda"
        nda_path.write_bytes(b"")
        parser = NDAParser()
        assert parser.read_column_headings(nda_path) == ["a", "b"]


class TestExcelSheetPattern:
    """ExcelParser.sheet_pattern: regex-based sheet selection for variable sheet names."""

    @staticmethod
    def _arbin_workbook(tmp_path):
        openpyxl = pytest.importorskip("openpyxl")
        wb = openpyxl.Workbook()
        gi = wb.active
        gi.title = "Global_Info"
        gi.append(["Schedule", "HPPC_test.sdx"])
        ch = wb.create_sheet("Channel_1-002")
        ch.append(["Data_Point", "Test_Time(s)", "Voltage(V)", "Current(A)"])
        ch.append([1, 0.0, 3.6, 0.5])
        wb.create_sheet("ACIM_chan_1").append(["Frequency", "Zreal", "Zimg"])
        path = tmp_path / "arbin.xlsx"
        wb.save(path)
        return path

    def test_pattern_selects_matching_sheet(self, tmp_path):
        pytest.importorskip("fastexcel")
        path = self._arbin_workbook(tmp_path)
        parser = ExcelParser(sheet_pattern=r"^Channel[_-]")
        assert parser.read_column_headings(path) == ["Data_Point", "Test_Time(s)", "Voltage(V)", "Current(A)"]

    def test_pattern_skips_non_matching_sheets(self, tmp_path):
        """Global_Info and ACIM_chan_* must not match the channel pattern."""
        pytest.importorskip("fastexcel")
        path = self._arbin_workbook(tmp_path)
        parser = ExcelParser(sheet_pattern=r"^Channel[_-]")
        assert "Frequency" not in parser.read_column_headings(path)

    def test_no_matching_sheet_raises_with_sheet_list(self, tmp_path):
        pytest.importorskip("fastexcel")
        path = self._arbin_workbook(tmp_path)
        parser = ExcelParser(sheet_pattern=r"^record$")
        with pytest.raises(ValueError, match="no sheet matching"):
            parser.read_column_headings(path)

    def test_pattern_mutually_exclusive_with_sheet_name(self):
        with pytest.raises(ValueError, match="mutually exclusive"):
            ExcelParser(sheet_pattern=r"^Channel", sheet_name="record")

    def test_pattern_mutually_exclusive_with_sheet_id(self):
        with pytest.raises(ValueError, match="mutually exclusive"):
            ExcelParser(sheet_pattern=r"^Channel", sheet_id=1)

    def test_multiple_matching_sheets_raise(self, tmp_path):
        """A multi-channel workbook must not silently read only the first channel."""
        openpyxl = pytest.importorskip("openpyxl")
        pytest.importorskip("fastexcel")
        wb = openpyxl.Workbook()
        wb.active.title = "Global_Info"
        for ch in ("Channel_1-001", "Channel_1-002"):
            ws = wb.create_sheet(ch)
            ws.append(["Test_Time(s)", "Voltage(V)"])
        path = tmp_path / "two_channels.xlsx"
        wb.save(path)
        parser = ExcelParser(sheet_pattern=r"^Channel[_-]")
        with pytest.raises(ValueError, match="multiple sheets match"):
            parser.read_column_headings(path)
