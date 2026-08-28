"""Unit tests for bdf.metadata_parsers (targets, rules, and the staging engine)."""

from __future__ import annotations

import json
import re
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import pytest
from pydantic import ValidationError

from bdf.metadata_parsers import (
    JsonRule,
    JsonSidecarParser,
    RegexRule,
    TxtPreambleParser,
)
from bdf.normalization import _ARBIN_DT_FMTS, _LANDT_DT_FMTS, _MACCOR_DT_FMTS, AbsoluteTimeNormalization

START_TIME_RX = re.compile(r"~Start of Test:\s*(.+)")

# An epoch with no significance beyond being a value; the round-trip check
# below asserts it survives unchanged, not that it names any particular instant.
_PLACEHOLDER_EPOCH = 1700000000


def test_nested_source_path_extracts(tmp_path: Path) -> None:
    """A sidecar rule mapping a target to a nested source path stages the value there."""
    from bdf.metadata_targets import METADATA

    data = tmp_path / "cell.csv"
    data.write_text("a,b\n1,2\n")
    (tmp_path / "cell.json").write_text(json.dumps({"test": {"started_at": _PLACEHOLDER_EPOCH}}))

    rule = JsonRule(candidates=(("test", "started_at"),))
    parser = JsonSidecarParser(rules={METADATA.battinfo_test.test.started_at: rule})
    meta = parser.parse(data)
    assert meta.battinfo_test.test.started_at == _PLACEHOLDER_EPOCH  # type: ignore[union-attr]


def test_preamble_rule_owns_its_interpretation(tmp_path: Path) -> None:
    """A preamble rule's own datetime normalization converts the matched text, not the parser."""
    from bdf.metadata_targets import METADATA

    p = tmp_path / "f.txt"
    p.write_text("~Start of Test: 30-Apr-24 08:00:00 AM\n")

    rule = RegexRule(pattern=START_TIME_RX, normalization=AbsoluteTimeNormalization(formats=_MACCOR_DT_FMTS))
    parser = TxtPreambleParser(rules={METADATA.battinfo_test.test.started_at: rule})
    meta = parser.parse(p)
    expected = datetime(2024, 4, 30, 8, 0, 0, tzinfo=timezone.utc).timestamp()
    assert meta.battinfo_test.test.started_at == pytest.approx(expected, abs=1e-6)  # type: ignore[union-attr]
    assert not hasattr(parser, "datetime_formats")


def test_direct_parse_keeps_the_whole_head(tmp_path: Path) -> None:
    """A parse() call that states no boundary keeps the whole decoded head in raw."""
    from bdf.metadata_targets import METADATA

    p = tmp_path / "f.txt"
    text = "~Start of Test: 30-Apr-24 08:00:00 AM\nsome other line\n"
    p.write_text(text)

    rule = RegexRule(pattern=START_TIME_RX, normalization=AbsoluteTimeNormalization(formats=_MACCOR_DT_FMTS))
    parser = TxtPreambleParser(rules={METADATA.battinfo_test.test.started_at: rule})
    meta = parser.parse(p)
    assert meta.raw == text  # type: ignore[attr-defined]


def test_parse_boundary_keeps_crlf_terminators_in_raw(tmp_path: Path) -> None:
    """A parse() call that states a boundary over a CRLF head keeps its preamble's \\r\\n terminators in raw."""
    from bdf.metadata_targets import METADATA

    p = tmp_path / "f.txt"
    preamble = "~Start of Test: 30-Apr-24 08:00:00 AM\r\nsome other line\r\n"
    p.write_bytes((preamble + "time,voltage\r\n0,3.7\r\n").encode("utf-8"))

    rule = RegexRule(pattern=START_TIME_RX, normalization=AbsoluteTimeNormalization(formats=_MACCOR_DT_FMTS))
    parser = TxtPreambleParser(rules={METADATA.battinfo_test.test.started_at: rule})
    meta = parser.parse(p, preamble_lines=2)
    assert meta.raw == preamble  # type: ignore[attr-defined]


def test_parse_boundary_counts_lines_like_splitlines(tmp_path: Path) -> None:
    """A parse() call cuts a preamble the way str.splitlines() counts it, so raw holds no header row.

    A table parser counts a preamble with ``str.splitlines()``, which breaks on
    U+0085. A cut that counts line terminators alone reads that count as one
    line too many, and keeps the header row in raw.
    """
    from bdf.metadata_targets import METADATA

    p = tmp_path / "f.txt"
    preamble = "~Start of Test: 30-Apr-24 08:00:00 AM\r\n~Comment: measured \x85 fine\r\n"
    p.write_bytes((preamble + "time,voltage\r\n0,3.7\r\n").encode("latin-1"))
    assert len(preamble.splitlines()) == 3

    rule = RegexRule(pattern=START_TIME_RX, normalization=AbsoluteTimeNormalization(formats=_MACCOR_DT_FMTS))
    parser = TxtPreambleParser(encoding="latin-1", rules={METADATA.battinfo_test.test.started_at: rule})
    meta = parser.parse(p, preamble_lines=3)
    assert meta.raw == preamble  # type: ignore[attr-defined]


def test_matches_reads_the_whole_head_below_the_preamble(tmp_path: Path) -> None:
    """matches() finds a magic token below the preamble, because identification reads the whole head."""
    p = tmp_path / "f.txt"
    preamble = "\n".join(f"preamble line {i}" for i in range(20))
    p.write_text(f"{preamble}\nMACCOR ASCII\ntime,voltage\n0,3.7\n")

    parser = TxtPreambleParser(magic=("MACCOR ASCII",))
    assert parser.matches(p) is True


def test_json_sidecar_ignores_the_boundary(tmp_path: Path) -> None:
    """A JsonSidecarParser given a boundary still reads its whole document, unaffected."""
    from bdf.metadata_targets import METADATA

    data = tmp_path / "cell.csv"
    data.write_text("a,b\n1,2\n")
    document = {"start_time": "2024-01-15T00:00:00Z", "rig_bay": "B7"}
    (tmp_path / "cell.json").write_text(json.dumps(document))

    rule = JsonRule(candidates=(("start_time",),), normalization=AbsoluteTimeNormalization())
    parser = JsonSidecarParser(rules={METADATA.battinfo_test.test.started_at: rule})
    meta = parser.parse(data, preamble_lines=1)
    assert meta.raw == document  # type: ignore[attr-defined]


def test_json_sidecar_raw_captures_the_whole_document(tmp_path: Path) -> None:
    """A JSON sidecar parser's raw carries the whole loaded document, unmapped keys included."""
    from bdf.metadata_targets import METADATA

    data = tmp_path / "cell.csv"
    data.write_text("a,b\n1,2\n")
    document = {"start_time": "2024-01-15T00:00:00Z", "rig_bay": "B7", "nested": {"a": None}}
    (tmp_path / "cell.json").write_text(json.dumps(document))

    rule = JsonRule(candidates=(("start_time",),), normalization=AbsoluteTimeNormalization())
    parser = JsonSidecarParser(rules={METADATA.battinfo_test.test.started_at: rule})
    meta = parser.parse(data)
    assert meta.raw == document  # type: ignore[attr-defined]


def test_old_name_is_gone() -> None:
    """bdf.metadata_parsers exports neither the retired MetadataRules nor MetadataSchema names."""
    import bdf.metadata_parsers as metadata_parsers

    assert not hasattr(metadata_parsers, "MetadataRules")
    assert not hasattr(metadata_parsers, "MetadataSchema")
    assert not hasattr(TxtPreambleParser, "datetime_formats")
    assert not hasattr(JsonSidecarParser, "datetime_formats")


def test_parity_holds() -> None:
    """Every shipped plugin's rule targets resolve through the generated namespace to a real model path."""
    from bdf.metadata_targets import METADATA
    from bdf.plugins import PLUGINS

    for plugin in PLUGINS.values():
        for target, _rule in plugin.metadata_parser.rules:
            node = METADATA
            for segment in target.path:
                node = getattr(node, segment)
            assert node is not None


def test_typo_fails_construction() -> None:
    """A misspelled attribute on the generated namespace raises before any parser is built."""
    from bdf.metadata_targets import METADATA

    with pytest.raises(AttributeError):
        _ = METADATA.battinfo_test.test.startedat


def test_cell_naming_and_conditions_targets_construct(tmp_path: Path) -> None:
    """A cell-naming target and a subscripted open-map target both construct a parser."""
    from bdf.metadata_targets import METADATA

    data = tmp_path / "cell.csv"
    data.write_text("a,b\n1,2\n")
    (tmp_path / "cell.json").write_text(
        json.dumps({"cell_name": "Cell-01", "temp_c": {"value": 25, "unit_text": "degC"}})
    )

    parser = JsonSidecarParser(
        rules={
            METADATA.battinfo_cell.cell_instance.name: JsonRule(candidates=(("cell_name",),)),
            METADATA.battinfo_test.test.conditions["temperature_c"]: JsonRule(candidates=(("temp_c",),)),
        }
    )
    meta = parser.parse(data)
    cond = meta.battinfo_test.test.conditions["temperature_c"]  # type: ignore[union-attr,index]
    assert cond.value == 25
    assert cond.unit_text == "degC"


def test_wrong_typed_sidecar_value_raises(tmp_path: Path) -> None:
    """A sidecar value of the wrong type raises out of parse(), staging nothing."""
    from bdf.metadata_targets import METADATA

    data = tmp_path / "cell.csv"
    data.write_text("a,b\n1,2\n")
    (tmp_path / "cell.json").write_text(json.dumps({"instrument": 400}))

    rule = JsonRule(candidates=(("instrument",),))
    parser = JsonSidecarParser(rules={METADATA.battinfo_test.test.instrument_name: rule})
    with pytest.raises(ValidationError):
        parser.parse(data)


def test_unmapped_and_losing_synonym_keys_survive_in_raw(tmp_path: Path) -> None:
    """An unmapped key and a losing synonym key both survive in raw, and extras stays unset."""
    from bdf.metadata_targets import METADATA

    data = tmp_path / "cell.csv"
    data.write_text("a,b\n1,2\n")
    (tmp_path / "cell.json").write_text(
        json.dumps({"start_time": "2024-01-15T00:00:00Z", "date": "2024-01-14", "rig_bay": "B7"})
    )

    rule = JsonRule(candidates=(("start_time",), ("date",)), normalization=AbsoluteTimeNormalization())
    parser = JsonSidecarParser(rules={METADATA.battinfo_test.test.started_at: rule})
    meta = parser.parse(data)
    assert meta.battinfo_test.test.started_at is not None  # type: ignore[union-attr]
    assert meta.raw == {  # type: ignore[attr-defined]
        "start_time": "2024-01-15T00:00:00Z",
        "date": "2024-01-14",
        "rig_bay": "B7",
    }
    assert meta.extras is None  # type: ignore[attr-defined]


def test_explicit_extras_rule_stages_and_raw_still_carries_everything(tmp_path: Path) -> None:
    """An explicit extras rule stages where asked, without duplicating a canonical value; raw stays whole."""
    from bdf.metadata_targets import METADATA

    data = tmp_path / "cell.csv"
    data.write_text("a,b\n1,2\n")
    (tmp_path / "cell.json").write_text(
        json.dumps({"fw_version": "1.2.3", "start_time": "2024-01-15T00:00:00Z", "rig_bay": "B7"})
    )

    parser = JsonSidecarParser(rules={METADATA.extras["vendor_version"]: JsonRule(candidates=(("fw_version",),)), METADATA.battinfo_test.test.started_at: JsonRule(candidates=(("start_time",),), normalization=AbsoluteTimeNormalization())})  # fmt: skip
    meta = parser.parse(data)
    assert meta.extras == {"vendor_version": "1.2.3"}  # type: ignore[attr-defined]
    assert meta.raw == {  # type: ignore[attr-defined]
        "fw_version": "1.2.3",
        "start_time": "2024-01-15T00:00:00Z",
        "rig_bay": "B7",
    }


def test_naive_preamble_timestamp_is_localised(tmp_path: Path) -> None:
    """A naive preamble timestamp localises to the timezone parse() is called with."""
    from bdf.metadata_targets import METADATA

    p = tmp_path / "f.txt"
    p.write_text("~Start of Test: 2024-04-30 08:00:00\n")

    rule = RegexRule(pattern=START_TIME_RX, normalization=AbsoluteTimeNormalization(formats=("%Y-%m-%d %H:%M:%S",)))
    parser = TxtPreambleParser(rules={METADATA.battinfo_test.test.started_at: rule})
    meta = parser.parse(p, tz="Europe/Oslo")  # type: ignore[call-arg]
    expected = datetime(2024, 4, 30, 8, 0, 0, tzinfo=ZoneInfo("Europe/Oslo")).timestamp()
    assert meta.battinfo_test.test.started_at == pytest.approx(expected, abs=1e-6)  # type: ignore[union-attr]


def test_offset_bearing_preamble_timestamp_is_left_alone(tmp_path: Path) -> None:
    """A preamble timestamp carrying an explicit offset honours that offset over tz."""
    from bdf.metadata_targets import METADATA

    p = tmp_path / "f.txt"
    p.write_text("~Start of Test: 2024-04-30 08:00:00+02:00\n")

    rule = RegexRule(pattern=START_TIME_RX, normalization=AbsoluteTimeNormalization(formats=("%Y-%m-%d %H:%M:%S%:z",)))
    parser = TxtPreambleParser(rules={METADATA.battinfo_test.test.started_at: rule})
    meta = parser.parse(p, tz="America/New_York")  # type: ignore[call-arg]
    expected = datetime(2024, 4, 30, 8, 0, 0, tzinfo=timezone(timedelta(hours=2))).timestamp()
    assert meta.battinfo_test.test.started_at == pytest.approx(expected, abs=1e-6)  # type: ignore[union-attr]


def test_localised_month_name_parses(tmp_path: Path) -> None:
    """A preamble stating a localised month name stages under the declared format."""
    from bdf.metadata_targets import METADATA

    p = tmp_path / "f.txt"
    p.write_text("~Start of Test: 13-Mai-24\n")

    rule = RegexRule(pattern=START_TIME_RX, normalization=AbsoluteTimeNormalization(formats=("%d-%b-%y",)))
    parser = TxtPreambleParser(rules={METADATA.battinfo_test.test.started_at: rule})
    meta = parser.parse(p)
    expected = datetime(2024, 5, 13, 0, 0, 0, tzinfo=timezone.utc).timestamp()
    assert meta.battinfo_test.test.started_at == pytest.approx(expected, abs=1e-6)  # type: ignore[union-attr]


def test_naive_timestamp_without_explicit_timezone_warns(tmp_path: Path) -> None:
    """A naive preamble match with no tz supplied warns once and reads as UTC."""
    from bdf.metadata_targets import METADATA

    p = tmp_path / "f.txt"
    p.write_text("~Start of Test: 2024-04-30 08:00:00\n")

    rule = RegexRule(pattern=START_TIME_RX, normalization=AbsoluteTimeNormalization(formats=("%Y-%m-%d %H:%M:%S",)))
    parser = TxtPreambleParser(rules={METADATA.battinfo_test.test.started_at: rule})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        meta = parser.parse(p)
    user_warnings = [w for w in caught if issubclass(w.category, UserWarning)]
    assert len(user_warnings) == 1
    expected = datetime(2024, 4, 30, 8, 0, 0, tzinfo=timezone.utc).timestamp()
    assert meta.battinfo_test.test.started_at == pytest.approx(expected, abs=1e-6)  # type: ignore[union-attr]


def test_epoch_number_is_not_exempt_and_empty_declaration_stages(tmp_path: Path) -> None:
    """An integer datetime value fails naming the value, and an empty tuple reads ISO text."""
    from bdf.metadata_targets import METADATA

    data = tmp_path / "cell.csv"
    data.write_text("a,b\n1,2\n")
    (tmp_path / "cell.json").write_text(json.dumps({"started": 1714456800}))

    rule = JsonRule(candidates=(("started",),), normalization=AbsoluteTimeNormalization(formats=("%Y-%m-%d",)))
    parser = JsonSidecarParser(rules={METADATA.battinfo_test.test.started_at: rule})
    with pytest.raises(ValueError, match=re.escape(repr(1714456800))):
        parser.parse(data)

    p = tmp_path / "f.txt"
    p.write_text("~Start of Test: 2024-05-06T07:08:09.123Z\n")
    rule2 = RegexRule(pattern=START_TIME_RX, normalization=AbsoluteTimeNormalization())
    parser2 = TxtPreambleParser(rules={METADATA.battinfo_test.test.started_at: rule2})
    meta2 = parser2.parse(p)
    expected = int(datetime(2024, 5, 6, 7, 8, 9, 123000, tzinfo=timezone.utc).timestamp())
    assert meta2.battinfo_test.test.started_at == expected  # type: ignore[union-attr]


@pytest.mark.parametrize("started", [20221130150021, "20221130150021"], ids=["int", "digit-string"])
def test_naive_digit_value_warns_regardless_of_type(tmp_path: Path, started) -> None:
    """An integer and the identical digit string each warn once parsing under a naive digit format."""
    from bdf.metadata_targets import METADATA

    data = tmp_path / "cell.csv"
    data.write_text("a,b\n1,2\n")
    (tmp_path / "cell.json").write_text(json.dumps({"started": started}))

    rule = JsonRule(candidates=(("started",),), normalization=AbsoluteTimeNormalization(formats=("%Y%m%d%H%M%S",)))
    parser = JsonSidecarParser(rules={METADATA.battinfo_test.test.started_at: rule})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        parser.parse(data)
    user_warnings = [w for w in caught if issubclass(w.category, UserWarning)]
    assert len(user_warnings) == 1


def test_declared_vendor_tuple_gets_no_tail(tmp_path: Path) -> None:
    """A one-format vendor rule fails the read on ISO text, naming that format as the set tried."""
    from bdf.metadata_targets import METADATA

    p = tmp_path / "f.txt"
    p.write_text("~Start of Test: 2024-05-06T07:08:09.123Z\n")

    rule = RegexRule(pattern=START_TIME_RX, normalization=AbsoluteTimeNormalization(formats=_LANDT_DT_FMTS))
    parser = TxtPreambleParser(rules={METADATA.battinfo_test.test.started_at: rule})
    with pytest.raises(ValueError, match=re.escape(_LANDT_DT_FMTS[0])):
        parser.parse(p)


def test_unparseable_timestamp_omits_or_fails(tmp_path: Path) -> None:
    """A blank preamble value omits the field; a date-shaped unparseable one fails the read."""
    from bdf.metadata_targets import METADATA

    rule = RegexRule(
        pattern=re.compile(r"~Start of Test:\s*(\S*)"), normalization=AbsoluteTimeNormalization(formats=("%Y-%m-%d",))
    )

    blank = tmp_path / "blank.txt"
    blank.write_text("~Start of Test: \nmore\n")
    parser = TxtPreambleParser(rules={METADATA.battinfo_test.test.started_at: rule})
    meta = parser.parse(blank)
    assert meta.battinfo_test.test.started_at is None  # type: ignore[union-attr]

    unparseable = tmp_path / "unparseable.txt"
    unparseable.write_text("~Start of Test: 30/04/2024\n")
    with pytest.raises(ValueError) as excinfo:
        parser.parse(unparseable)
    message = str(excinfo.value)
    assert "started_at" in message
    assert "30/04/2024" in message
    assert "%Y-%m-%d" in message


def test_one_text_two_paths_one_epoch(tmp_path: Path) -> None:
    """The same timestamp text stages the same epoch through a table column and a metadata rule."""
    from bdf.metadata_targets import METADATA

    text = "04/30/2024 08:00:00.000"
    normalization = AbsoluteTimeNormalization(formats=_ARBIN_DT_FMTS)
    table_epoch = pl.select(normalization.expr(pl.lit(text))).item()

    p = tmp_path / "f.txt"
    p.write_text(f"~Start of Test: {text}\n")
    rule = RegexRule(pattern=START_TIME_RX, normalization=normalization)
    parser = TxtPreambleParser(rules={METADATA.battinfo_test.test.started_at: rule})
    meta = parser.parse(p)
    assert meta.battinfo_test.test.started_at == pytest.approx(table_epoch, abs=1e-6)  # type: ignore[union-attr]


def test_regex_rule_without_capturing_group_fails_construction() -> None:
    """A RegexRule whose pattern declares no capturing group fails construction."""
    with pytest.raises(ValueError):
        RegexRule(pattern=re.compile(r"no group here"))


def test_json_rule_with_empty_candidates_fails_construction() -> None:
    """A JsonRule with an empty candidates tuple fails construction."""
    with pytest.raises(ValueError):
        JsonRule(candidates=())
