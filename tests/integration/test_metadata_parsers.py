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
    metadata = parser.parse(resolved)
    assert case.expected_metadata is not None
    for dotted_path, expected in case.expected_metadata.items():
        # Entities are plain dicts, so follow the dotted path by key below the entity.
        entity, *keys = dotted_path.split(".")
        node = getattr(metadata, entity)
        for key in keys:
            node = node[key]
        assert node == pytest.approx(expected)
