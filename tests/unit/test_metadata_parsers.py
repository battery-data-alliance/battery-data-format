"""Unit tests for bdf.metadata_parsers (MetadataSchema and parser classes)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from bdf.metadata_parsers import (
    JsonSidecarParser,
    MetadataParser,
    MetadataSchema,
    TxtPreambleParser,
)

START_TIME_RX = re.compile(r"~Start of Test:\s*(.+)")


class TestMetadataSchema:
    def test_schema_rejects_unknown_field(self) -> None:
        """extra='forbid' rejects unknown metadata field names at construction."""
        with pytest.raises(ValidationError):
            MetadataSchema(unknown_field="x")  # type: ignore[call-arg]

    def test_schema_is_hashable(self) -> None:
        """A MetadataSchema instance can be placed in a frozenset."""
        s = MetadataSchema(start_time="x")
        assert s in frozenset({s})

    def test_schema_iter_yields_only_set_fields(self) -> None:
        """Iterating yields (field_name, rule) only for set fields."""
        s = MetadataSchema[str](start_time=r"X:(.+)")
        assert list(s) == [("start_time", r"X:(.+)")]
        assert list(MetadataSchema[str]()) == []

    def test_schema_pattern_compiles_string_input(self) -> None:
        """Pydantic coerces a str to re.Pattern when T=re.Pattern[str]."""
        s = MetadataSchema[re.Pattern[str]](start_time="X:(.+)")  # type: ignore[arg-type]
        _, pattern = next(iter(s))
        assert isinstance(pattern, re.Pattern)
        assert pattern == re.compile("X:(.+)")

    def test_extract_applies_matcher_to_set_fields(self) -> None:
        """extract() calls match_one per set field and keeps non-None results."""
        s = MetadataSchema[str](start_time="rule")
        seen: list[str] = []

        def match_one(rule: str) -> str:
            seen.append(rule)
            return f"value:{rule}"

        assert s.extract(match_one) == {"start_time": "value:rule"}
        assert seen == ["rule"]

    def test_extract_skips_fields_when_matcher_returns_none(self) -> None:
        """A field whose matcher returns None is omitted from the result."""
        s = MetadataSchema[str](start_time="rule")
        assert s.extract(lambda _rule: None) == {}

    def test_extract_skips_unset_fields(self) -> None:
        """extract() never invokes the matcher for unset (None) fields."""
        calls = 0

        def match_one(_rule: str) -> str:
            nonlocal calls
            calls += 1
            return "x"

        assert MetadataSchema[str]().extract(match_one) == {}
        assert calls == 0


class TestMetadataParserBase:
    """MetadataParser base (null case)."""

    def test_base_never_matches(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("anything")
        assert MetadataParser().matches(p) is False

    def test_base_parse_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("anything")
        assert MetadataParser().parse(p) == {}

    def test_base_is_hashable(self) -> None:
        assert MetadataParser() in frozenset({MetadataParser()})


class TestTxtPreambleParser:
    def test_txt_matches_true_when_magic_present(self, tmp_path: Path) -> None:
        p = tmp_path / "basytec.txt"
        p.write_text("ResultFile from BaSyTec Battery Test System\n~Start of Test: 01.01.2024\n")
        assert TxtPreambleParser(magic=("basytec battery test system",)).matches(p) is True

    def test_txt_matches_false_when_magic_absent(self, tmp_path: Path) -> None:
        p = tmp_path / "biologic.txt"
        p.write_text("BT-Lab ASCII FILE\n")
        assert TxtPreambleParser(magic=("basytec battery test system",)).matches(p) is False

    def test_txt_matches_bytes_token(self, tmp_path: Path) -> None:
        p = tmp_path / "raw.bin"
        p.write_bytes(b"\x00\x01\x02 data")
        assert TxtPreambleParser(magic=(b"\x00\x01",)).matches(p) is True

    def test_txt_matches_empty_magic_false(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("anything")
        assert TxtPreambleParser().matches(p) is False

    def test_txt_parse_extracts_field(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("header\n~Start of Test: 19.06.2023 17:56:53\nmore\n")
        parser = TxtPreambleParser(regex_patterns=MetadataSchema[re.Pattern[str]](start_time=START_TIME_RX))
        assert parser.parse(p) == {"start_time": "19.06.2023 17:56:53"}

    def test_txt_parse_honours_encoding(self, tmp_path: Path) -> None:
        """parse() decodes the head with the configured encoding (latin-1)."""
        p = tmp_path / "latin1.txt"
        p.write_bytes("~Start of Test: caf\xe9\n".encode("latin-1"))
        parser = TxtPreambleParser(
            encoding="latin-1",
            regex_patterns=MetadataSchema[re.Pattern[str]](start_time=START_TIME_RX),
        )
        assert parser.parse(p) == {"start_time": "caf\xe9"}

    def test_txt_parse_returns_only_matched_fields(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("no relevant lines here\n")
        parser = TxtPreambleParser(regex_patterns=MetadataSchema[re.Pattern[str]](start_time=START_TIME_RX))
        assert parser.parse(p) == {}

    def test_txt_parse_strips_captured_value(self, tmp_path: Path) -> None:
        """match_one strips surrounding whitespace from group(1)."""
        p = tmp_path / "f.txt"
        p.write_text("~Start of Test:   19.06.2023   \n")
        parser = TxtPreambleParser(regex_patterns=MetadataSchema[re.Pattern[str]](start_time=START_TIME_RX))
        assert parser.parse(p) == {"start_time": "19.06.2023"}

    def test_txt_parse_first_matching_line_wins(self, tmp_path: Path) -> None:
        """match_one returns the first matching line, ignoring later matches."""
        p = tmp_path / "f.txt"
        p.write_text("~Start of Test: first\n~Start of Test: second\n")
        parser = TxtPreambleParser(regex_patterns=MetadataSchema[re.Pattern[str]](start_time=START_TIME_RX))
        assert parser.parse(p) == {"start_time": "first"}

    def test_txt_is_hashable(self) -> None:
        parser = TxtPreambleParser(
            magic=("x",),
            regex_patterns=MetadataSchema[re.Pattern[str]](start_time=re.compile(r"a(.+)")),
        )
        assert parser in frozenset({parser})

    def test_txt_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            TxtPreambleParser(bogus="x")  # type: ignore[call-arg]


class TestJsonSidecarParser:
    def test_json_matches_true_when_sidecar_exists(self, tmp_path: Path) -> None:
        data = tmp_path / "cell.csv"
        data.write_text("a,b\n1,2\n")
        (tmp_path / "cell.json").write_text("{}")
        assert JsonSidecarParser().matches(data) is True

    def test_json_matches_false_when_no_sidecar(self, tmp_path: Path) -> None:
        data = tmp_path / "cell.csv"
        data.write_text("a,b\n1,2\n")
        assert JsonSidecarParser().matches(data) is False

    def test_json_parse_resolves_synonyms(self, tmp_path: Path) -> None:
        data = tmp_path / "cell.csv"
        data.write_text("a,b\n1,2\n")
        (tmp_path / "cell.json").write_text(json.dumps({"StartTime": "2024-01-01"}))
        parser = JsonSidecarParser(key_synonyms=MetadataSchema(start_time=("start_time", "StartTime", "test_start")))
        assert parser.parse(data) == {"start_time": "2024-01-01"}

    def test_json_parse_returns_only_matched_fields(self, tmp_path: Path) -> None:
        data = tmp_path / "cell.csv"
        data.write_text("a,b\n1,2\n")
        (tmp_path / "cell.json").write_text(json.dumps({"other": "x"}))
        parser = JsonSidecarParser(key_synonyms=MetadataSchema(start_time=("start_time",)))
        assert parser.parse(data) == {}

    def test_json_parse_no_sidecar_returns_empty(self, tmp_path: Path) -> None:
        data = tmp_path / "cell.csv"
        data.write_text("a,b\n1,2\n")
        parser = JsonSidecarParser(key_synonyms=MetadataSchema(start_time=("start_time",)))
        assert parser.parse(data) == {}

    def test_json_parse_first_synonym_in_order_wins(self, tmp_path: Path) -> None:
        """match_one picks the first synonym present in tuple order, not file order."""
        data = tmp_path / "cell.csv"
        data.write_text("a,b\n1,2\n")
        (tmp_path / "cell.json").write_text(json.dumps({"StartTime": "tuple_second", "start_time": "tuple_first"}))
        parser = JsonSidecarParser(key_synonyms=MetadataSchema(start_time=("start_time", "StartTime")))
        assert parser.parse(data) == {"start_time": "tuple_first"}

    def test_json_parse_coerces_non_string_value(self, tmp_path: Path) -> None:
        """match_one coerces a non-string JSON value with str()."""
        data = tmp_path / "cell.csv"
        data.write_text("a,b\n1,2\n")
        (tmp_path / "cell.json").write_text(json.dumps({"start_time": 1700000000}))
        parser = JsonSidecarParser(key_synonyms=MetadataSchema(start_time=("start_time",)))
        assert parser.parse(data) == {"start_time": "1700000000"}

    def test_json_is_hashable(self) -> None:
        parser = JsonSidecarParser(key_synonyms=MetadataSchema(start_time=("start_time",)))
        assert parser in frozenset({parser})


class TestMixedParserTypes:
    """Mixed parser types coexist in a frozenset."""

    def test_parsers_share_a_frozenset(self) -> None:
        parsers = frozenset(
            {
                MetadataParser(),
                TxtPreambleParser(magic=("x",)),
                JsonSidecarParser(),
            }
        )
        assert len(parsers) == 3
