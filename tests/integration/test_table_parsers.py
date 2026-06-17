"""Integration tests for bdf.table_parsers — real file and URL sources."""

from __future__ import annotations

from pathlib import Path

import pytest

from bdf.file_utils import read_head
from bdf.plugins import PLUGINS
from bdf.spec import COLUMN_ONTOLOGY
from bdf.table_parsers import DelimTxtParser
from integration.test_cases import (
    ALL_CASES,
    SampleCase,
    expected_labels,
    get_sample_data_source,
)

_LABEL_DTYPE: dict[str, str] = {q.formatted_label: q.dtype for _, q in COLUMN_ONTOLOGY}

_SNIFF_CASES = [pytest.param(cid, c, marks=c.marks, id=cid) for cid, c in ALL_CASES if c.skip is not None]
_COLUMN_CASES = [pytest.param(cid, c, marks=c.marks, id=cid) for cid, c in ALL_CASES if c.expected_columns]
_CURRENT_CASES = [
    pytest.param(cid, c, marks=c.marks, id=cid) for cid, c in ALL_CASES if c.current_max_abs_amps is not None
] or [
    pytest.param(
        None,
        None,
        marks=pytest.mark.skip(
            reason="test_sample_current_magnitude has no cases: no SampleCase sets current_max_abs_amps"
        ),
        id="none-configured",
    )
]


@pytest.mark.parametrize("cid,case", _SNIFF_CASES)
def test_sample_detect_structure(cid: str, case: SampleCase, data_dir: Path) -> None:
    """_detect_structure returns the line count and separator for vendor samples."""
    path = get_sample_data_source(case.source, case.is_url, data_dir)
    skip, sep = DelimTxtParser._detect_structure(DelimTxtParser._decode_head(read_head(path)))
    assert skip == case.skip
    assert sep == case.sep


@pytest.mark.parametrize("cid,case", _COLUMN_CASES)
def test_sample_read_includes_expected_columns(cid: str, case: SampleCase, data_dir: Path) -> None:
    """read() returns expected BDF columns with correct dtypes and no nulls."""
    path = get_sample_data_source(case.source, case.is_url, data_dir)
    assert case.expected_columns is not None
    labels = expected_labels(case)
    df = PLUGINS[case.plugin_id].table_parser.read(path).collect()
    assert frozenset(df.columns) == labels

    schema = df.schema
    for col in labels:
        spec_dtype = _LABEL_DTYPE.get(col)
        if spec_dtype == "float":
            assert schema[col].is_float(), f"{col}: expected float dtype, got {schema[col]}"
        elif spec_dtype == "int":
            assert schema[col].is_integer(), f"{col}: expected int dtype, got {schema[col]}"

    null_counts = df.null_count()
    for col in labels - case.null_ok_columns:
        assert null_counts[col][0] == 0, f"{col}: contains nulls"


@pytest.mark.parametrize("cid,case", _COLUMN_CASES)
def test_sample_resolution_exact(cid: str, case: SampleCase, data_dir: Path) -> None:
    """resolve() over the real headers maps each mr_name to its exact source_header and scale."""
    path = get_sample_data_source(case.source, case.is_url, data_dir)
    assert case.expected_columns is not None
    parser = PLUGINS[case.plugin_id].table_parser
    headers = parser.read_column_headings(path)
    resolved = parser.normalizer.resolve(headers)
    assert set(resolved) == set(case.expected_columns)
    for mr, exp in case.expected_columns.items():
        rc = resolved[mr]
        assert rc.source_header == exp.source_header, f"{mr}: {rc.source_header!r} != {exp.source_header!r}"
        if exp.is_datetime:
            assert bool(rc.datetime_fmts), f"{mr}: expected datetime_fmts"
        else:
            assert rc.scale == pytest.approx(exp.scale), f"{mr}: scale {rc.scale} != {exp.scale}"


@pytest.mark.parametrize("cid,case", _CURRENT_CASES)
def test_sample_current_magnitude(cid: str, case: SampleCase, data_dir: Path) -> None:
    """Current / A stays within expected range after unit conversion (catches mA→A regressions)."""
    path = get_sample_data_source(case.source, case.is_url, data_dir)
    df = PLUGINS[case.plugin_id].table_parser.read(path).collect()
    assert "Current / A" in df.columns
    max_abs = df["Current / A"].abs().max()
    assert max_abs <= case.current_max_abs_amps
