from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import bdf
from bdf.data_sources.neware_nda import NewareNDA


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_ingest_converts_raw_and_validates_bdf(tmp_path: Path) -> None:
    raw_csv = tmp_path / "raw.csv"
    _write_text(
        raw_csv,
        "Voltage(V),Current(A),Time(s),Total Time(s)\n"
        "4.0,0.1,1,1\n"
        "4.1,0.1,2,2\n",
    )

    bdf_csv = tmp_path / "sample.bdf.csv"
    _write_text(
        bdf_csv,
        "Test Time / s,Voltage / V,Current / A\n"
        "1,4.0,0.1\n"
        "2,4.1,0.1\n",
    )

    out_dir = tmp_path / "out"
    summary = bdf.ingest(
        tmp_path,
        out_dir=out_dir,
        format="csv",
        recursive=False,
        validate_existing=True,
        validate_converted=True,
    )

    converted = [c["path"] for c in summary.get("converted", [])]
    validated = [v["path"] for v in summary.get("validated", [])]

    assert str(raw_csv) in converted
    assert str(out_dir / "sample.bdf.csv") in validated
    assert (out_dir / "raw.bdf.csv").exists()


def test_neware_nda_fixup_scales_milli_units() -> None:
    df = pd.DataFrame(
        {
            "Current / A": [5000.0, -5000.0],
            "Charging Capacity / Ah": [1000.0, 2000.0],
            "Discharging Capacity / Ah": [1500.0, 2500.0],
            "Charging Energy / Wh": [1000.0, 2000.0],
            "Discharging Energy / Wh": [1500.0, 2500.0],
        }
    )
    df.attrs["bdf:columns"] = {
        "Current / A": {"sourceHeader": "Current(mA)"},
        "Charging Capacity / Ah": {"sourceHeader": "Charge_Capacity(mAh)"},
        "Discharging Capacity / Ah": {"sourceHeader": "Discharge_Capacity(mAh)"},
        "Charging Energy / Wh": {"sourceHeader": "Charge_Energy(mWh)"},
        "Discharging Energy / Wh": {"sourceHeader": "Discharge_Energy(mWh)"},
    }

    out = NewareNDA().fixup(df)
    assert np.isclose(out["Current / A"].iloc[0], 5.0)
    assert np.isclose(out["Charging Capacity / Ah"].iloc[0], 1.0)
    assert np.isclose(out["Discharging Capacity / Ah"].iloc[0], 1.5)
    assert np.isclose(out["Charging Energy / Wh"].iloc[0], 1.0)
    assert np.isclose(out["Discharging Energy / Wh"].iloc[0], 1.5)


def test_neware_nda_fixup_skips_amp_values() -> None:
    df = pd.DataFrame({"Current / A": [5.0, -5.0]})
    df.attrs["bdf:columns"] = {"Current / A": {"sourceHeader": "Current(A)"}}

    out = NewareNDA().fixup(df)
    assert np.isclose(out["Current / A"].iloc[0], 5.0)


def test_ingest_existing_bdf_does_not_delete_source(tmp_path: Path) -> None:
    root = tmp_path / "collection"
    root.mkdir()

    # minimal metadata inputs
    (root / "collection.json").write_text(
        '{"title": "Test Collection", "description": "Test", "keywords": ["test"]}',
        encoding="utf-8",
    )
    (root / "person.json").write_text(
        '{"p1": {"name": "Test Person"}}',
        encoding="utf-8",
    )
    (root / "battery.json").write_text(
        '{"spec": {"manufacturer": "Test", "model": "X", "batch": "1"}, "ids": ["cell1"]}',
        encoding="utf-8",
    )

    # source BDF file in root
    src_bdf = root / "cell1.bdf.csv"
    _write_text(
        src_bdf,
        "Test Time / s,Voltage / V,Current / A\n"
        "1,4.0,0.1\n"
        "2,4.1,0.1\n",
    )

    # existing output in data/ to force conflict
    data_dir = root / "data"
    data_dir.mkdir()
    out_bdf = data_dir / "cell1.bdf.csv"
    _write_text(
        out_bdf,
        "Test Time / s,Voltage / V,Current / A\n"
        "1,4.0,0.1\n",
    )

    summary = bdf.ingest(
        root,
        layout="nested",
        format="csv",
        recursive=False,
        validate_existing=True,
        validate_converted=True,
    )

    assert src_bdf.exists(), "Source BDF should not be deleted when output exists."
    assert out_bdf.exists(), "Existing output BDF should be preserved."
    assert (root / "metadata.jsonld").exists(), "Collection metadata should be generated."
    assert (root / "test-x-1-cell1" / "metadata.jsonld").exists(), "Cell metadata should be generated."
    assert any(item.get("reason") == "output_exists" for item in summary.get("skipped", []))
