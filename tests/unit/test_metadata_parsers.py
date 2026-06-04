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
    _read_url_head,
)

# ---------------------------------------------------------------------------
# Zenodo URLs (record 18214281)
# ---------------------------------------------------------------------------

_ZENODO_BASE = "https://zenodo.org/api/records/18214281/files"
_ZENODO_BASYTEC_URL = f"{_ZENODO_BASE}/DLR__LiLNMOHydra0b__20221130__GITT__25degC__Basytec.txt/content"
_ZENODO_BIOLOGIC_URL = f"{_ZENODO_BASE}/SINTEF__NaCR32140-MP10-04__2025-08-25__GITT_0p05C_25degC__BioLogic.mpt/content"

_BASYTEC_PARSER = TxtPreambleParser(
    magic=("basytec battery test system",),
    regex_patterns=MetadataSchema[re.Pattern[str]](start_time=re.compile(r"~Start of Test:\s*(.+)")),
)
_BIOLOGIC_PARSER = TxtPreambleParser(
    magic=("bt-lab",),
    regex_patterns=MetadataSchema[re.Pattern[str]](start_time=re.compile(r"Acquisition started on\s*:\s*(.+)")),
)

_METADATA_CASES = [
    pytest.param(
        "basytec/sample_data_basytec.txt",
        False,
        _BASYTEC_PARSER,
        {"start_time": "19.06.2023 17:56:53"},
        id="basytec/local",
    ),
    pytest.param(
        "biologic/Sample_data_biologic_01_MB_CA1.txt",
        False,
        _BIOLOGIC_PARSER,
        {"start_time": "05/13/2024 11:19:51.602"},
        id="biologic/local",
    ),
    pytest.param(
        _ZENODO_BASYTEC_URL,
        True,
        _BASYTEC_PARSER,
        {"start_time": "30.11.2022 15:00:21"},
        id="basytec/url",
        marks=pytest.mark.network,
    ),
    pytest.param(
        _ZENODO_BIOLOGIC_URL,
        True,
        _BIOLOGIC_PARSER,
        {"start_time": "06/23/2025 09:17:30.015"},
        id="biologic/url",
        marks=pytest.mark.network,
    ),
]

# ---------------------------------------------------------------------------
# MetadataSchema
# ---------------------------------------------------------------------------


def test_schema_rejects_unknown_field() -> None:
    """extra='forbid' rejects unknown metadata field names at construction."""
    with pytest.raises(ValidationError):
        MetadataSchema(unknown_field="x")  # type: ignore[call-arg]


def test_schema_is_hashable() -> None:
    """A MetadataSchema instance can be placed in a frozenset."""
    s = MetadataSchema(start_time="x")
    assert s in frozenset({s})


def test_schema_iter_yields_only_set_fields() -> None:
    """Iterating yields (field_name, rule) only for set fields."""
    s = MetadataSchema[str](start_time=r"X:(.+)")
    assert list(s) == [("start_time", r"X:(.+)")]
    assert list(MetadataSchema[str]()) == []


def test_schema_pattern_compiles_string_input() -> None:
    """Pydantic coerces a str to re.Pattern when T=re.Pattern[str]."""
    s = MetadataSchema[re.Pattern[str]](start_time="X:(.+)")  # type: ignore[arg-type]
    _, pattern = next(iter(s))
    assert isinstance(pattern, re.Pattern)
    assert pattern == re.compile("X:(.+)")


# ---------------------------------------------------------------------------
# MetadataParser base (null case)
# ---------------------------------------------------------------------------


def test_base_never_matches(tmp_path: Path) -> None:
    p = tmp_path / "f.txt"
    p.write_text("anything")
    assert MetadataParser().matches(p) is False


def test_base_parse_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "f.txt"
    p.write_text("anything")
    assert MetadataParser().parse(p) == {}


def test_base_is_hashable() -> None:
    assert MetadataParser() in frozenset({MetadataParser()})


# ---------------------------------------------------------------------------
# TxtPreambleParser
# ---------------------------------------------------------------------------


def test_txt_matches_true_when_magic_present(tmp_path: Path) -> None:
    p = tmp_path / "basytec.txt"
    p.write_text("ResultFile from BaSyTec Battery Test System\n~Start of Test: 01.01.2024\n")
    assert TxtPreambleParser(magic=("basytec battery test system",)).matches(p) is True


def test_txt_matches_false_when_magic_absent(tmp_path: Path) -> None:
    p = tmp_path / "biologic.txt"
    p.write_text("BT-Lab ASCII FILE\n")
    assert TxtPreambleParser(magic=("basytec battery test system",)).matches(p) is False


def test_txt_matches_bytes_token(tmp_path: Path) -> None:
    p = tmp_path / "raw.bin"
    p.write_bytes(b"\x00\x01\x02 data")
    assert TxtPreambleParser(magic=(b"\x00\x01",)).matches(p) is True


def test_txt_matches_empty_magic_false(tmp_path: Path) -> None:
    p = tmp_path / "f.txt"
    p.write_text("anything")
    assert TxtPreambleParser().matches(p) is False


def test_txt_parse_extracts_field(tmp_path: Path) -> None:
    p = tmp_path / "f.txt"
    p.write_text("header\n~Start of Test: 19.06.2023 17:56:53\nmore\n")
    parser = TxtPreambleParser(
        regex_patterns=MetadataSchema[re.Pattern[str]](start_time=re.compile(r"~Start of Test:\s*(.+)"))
    )
    assert parser.parse(p) == {"start_time": "19.06.2023 17:56:53"}


def test_txt_parse_honours_encoding(tmp_path: Path) -> None:
    """parse() decodes the head with the configured encoding (latin-1)."""
    p = tmp_path / "latin1.txt"
    p.write_bytes("~Start of Test: caf\xe9\n".encode("latin-1"))
    parser = TxtPreambleParser(
        encoding="latin-1",
        regex_patterns=MetadataSchema[re.Pattern[str]](start_time=re.compile(r"~Start of Test:\s*(.+)")),
    )
    assert parser.parse(p) == {"start_time": "caf\xe9"}


def test_txt_parse_returns_only_matched_fields(tmp_path: Path) -> None:
    p = tmp_path / "f.txt"
    p.write_text("no relevant lines here\n")
    parser = TxtPreambleParser(
        regex_patterns=MetadataSchema[re.Pattern[str]](start_time=re.compile(r"~Start of Test:\s*(.+)"))
    )
    assert parser.parse(p) == {}


def test_txt_is_hashable() -> None:
    parser = TxtPreambleParser(
        magic=("x",),
        regex_patterns=MetadataSchema[re.Pattern[str]](start_time=re.compile(r"a(.+)")),
    )
    assert parser in frozenset({parser})


def test_txt_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        TxtPreambleParser(bogus="x")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# JsonSidecarParser
# ---------------------------------------------------------------------------


def test_json_matches_true_when_sidecar_exists(tmp_path: Path) -> None:
    data = tmp_path / "cell.csv"
    data.write_text("a,b\n1,2\n")
    (tmp_path / "cell.json").write_text("{}")
    assert JsonSidecarParser().matches(data) is True


def test_json_matches_false_when_no_sidecar(tmp_path: Path) -> None:
    data = tmp_path / "cell.csv"
    data.write_text("a,b\n1,2\n")
    assert JsonSidecarParser().matches(data) is False


def test_json_parse_resolves_synonyms(tmp_path: Path) -> None:
    data = tmp_path / "cell.csv"
    data.write_text("a,b\n1,2\n")
    (tmp_path / "cell.json").write_text(json.dumps({"StartTime": "2024-01-01"}))
    parser = JsonSidecarParser(key_synonyms=MetadataSchema(start_time=("start_time", "StartTime", "test_start")))
    assert parser.parse(data) == {"start_time": "2024-01-01"}


def test_json_parse_returns_only_matched_fields(tmp_path: Path) -> None:
    data = tmp_path / "cell.csv"
    data.write_text("a,b\n1,2\n")
    (tmp_path / "cell.json").write_text(json.dumps({"other": "x"}))
    parser = JsonSidecarParser(key_synonyms=MetadataSchema(start_time=("start_time",)))
    assert parser.parse(data) == {}


def test_json_parse_no_sidecar_returns_empty(tmp_path: Path) -> None:
    data = tmp_path / "cell.csv"
    data.write_text("a,b\n1,2\n")
    parser = JsonSidecarParser(key_synonyms=MetadataSchema(start_time=("start_time",)))
    assert parser.parse(data) == {}


def test_json_is_hashable() -> None:
    parser = JsonSidecarParser(key_synonyms=MetadataSchema(start_time=("start_time",)))
    assert parser in frozenset({parser})


# ---------------------------------------------------------------------------
# Mixed parser types coexist in a frozenset
# ---------------------------------------------------------------------------


def test_parsers_share_a_frozenset() -> None:
    parsers = frozenset(
        {
            MetadataParser(),
            TxtPreambleParser(magic=("x",)),
            JsonSidecarParser(),
        }
    )
    assert len(parsers) == 3


# ---------------------------------------------------------------------------
# _read_url_head
# ---------------------------------------------------------------------------


@pytest.mark.network
@pytest.mark.parametrize(
    "url,expected_content",
    [
        (_ZENODO_BASYTEC_URL, b"Basytec"),
        (_ZENODO_BIOLOGIC_URL, b"BT-Lab"),
    ],
    ids=["basytec", "biologic"],
)
def test_read_url_head_returns_file_start(url: str, expected_content: bytes) -> None:
    pytest.importorskip("requests")
    head = _read_url_head(url, n_bytes=4096)
    assert len(head) == 4096
    assert expected_content in head


# ---------------------------------------------------------------------------
# TxtPreambleParser.parse — local and URL sources
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source,is_url,parser,expected", _METADATA_CASES)
def test_parse_metadata_from_source(
    source: str,
    is_url: bool,
    parser: TxtPreambleParser,
    expected: dict,
    data_dir: Path,
) -> None:
    pytest.importorskip("requests")
    if is_url:
        resolved: str | Path = source
    else:
        resolved = data_dir / source
        if not resolved.exists():
            pytest.skip(f"sample data not present: {source}")
    assert parser.parse(resolved) == expected
