from __future__ import annotations

import json
from pathlib import Path

import bdf


def test_templates_create_files(tmp_path: Path) -> None:
    result = bdf.templates("contribution", "battery", "excel", root=tmp_path)

    created = set(result.get("created", []))
    assert str(tmp_path / "contribution.json") in created
    assert str(tmp_path / "battery.json") in created
    assert str(tmp_path / "excel.json") in created

    contribution = json.loads((tmp_path / "contribution.json").read_text(encoding="utf-8"))
    battery = json.loads((tmp_path / "battery.json").read_text(encoding="utf-8"))
    excel = json.loads((tmp_path / "excel.json").read_text(encoding="utf-8"))

    assert contribution["dataset_doi"] == "REQUIRED"
    assert contribution["license"] == "REQUIRED"
    assert battery["cells"][0]["name"] == "REQUIRED"
    assert excel["sheet_index"] == "OPTIONAL"


def test_templates_skip_existing(tmp_path: Path) -> None:
    (tmp_path / "contribution.json").write_text("{}", encoding="utf-8")
    result = bdf.templates("contribution", root=tmp_path)
    skipped = set(result.get("skipped", []))
    assert str(tmp_path / "contribution.json") in skipped
