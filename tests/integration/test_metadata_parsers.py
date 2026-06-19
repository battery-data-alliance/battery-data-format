"""Integration tests for bdf.metadata_parsers — real file and URL sources."""

from __future__ import annotations

from pathlib import Path

import pytest

from bdf.plugins import PLUGINS
from integration.test_cases import ALL_CASES, SampleCase, get_sample_data_source


@pytest.mark.parametrize(
    "case",
    [pytest.param(c, id=cid, marks=c.marks) for cid, c in ALL_CASES if c.expected_metadata],
)
def test_parse_metadata_from_source(case: SampleCase, data_dir: Path) -> None:
    pytest.importorskip("requests")
    resolved = get_sample_data_source(case.source, case.is_url, data_dir)
    parser = PLUGINS[case.plugin_id].metadata_parser
    assert parser.parse(resolved) == case.expected_metadata
