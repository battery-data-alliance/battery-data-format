import json
from pathlib import Path

from bdf._registry import load_registry, list_datasets, get_entry


def test_registry_load_list_get(tmp_path: Path):
    reg_path = tmp_path / "registry.json"
    data = {
        "schema_version": "0.1",
        "datasets": [
            {"id": "ds1", "url": "http://example.com/file1.csv"},
            {"id": "DS2", "url": "http://example.com/file2.csv", "plugin": "csv"},
        ],
    }
    reg_path.write_text(json.dumps(data), encoding="utf-8")

    reg = load_registry(reg_path)
    ids = list_datasets(reg_path)
    assert ids == ["ds1", "DS2"]

    ds1 = get_entry(reg, "ds1")
    assert ds1["url"] == "http://example.com/file1.csv"

    # case-insensitive lookup
    ds2 = get_entry(reg, "ds2")
    assert ds2["plugin"] == "csv"
