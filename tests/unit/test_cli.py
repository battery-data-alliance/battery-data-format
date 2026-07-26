import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from bdf.cli import app

runner = CliRunner()


def _make_sample_bdf(tmp_path: Path) -> Path:
    df = pd.DataFrame(
        {
            "Test Time / s": [0, 1, 2],
            "Voltage / V": [3.7, 3.6, 3.5],
            "Current / A": [0.1, 0.1, 0.1],
        }
    )
    path = tmp_path / "sample.bdf.csv"
    df.to_csv(path, index=False)
    return path


def test_cli_help():
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "Battery Data Format utilities" in res.stdout


def test_cli_validate_success_and_failure(tmp_path: Path):
    good = _make_sample_bdf(tmp_path)
    bad = tmp_path / "bad.bdf.csv"
    pd.DataFrame({"Test Time / s": [0, 1], "Voltage / V": [3.7, 3.6]}).to_csv(bad, index=False)

    ok = runner.invoke(app, ["validate", str(good)])
    assert ok.exit_code == 0
    fail = runner.invoke(app, ["validate", str(bad)])
    assert fail.exit_code != 0


def test_cli_clean(tmp_path: Path):
    src = _make_sample_bdf(tmp_path)
    out = tmp_path / "cleaned.bdf.csv"
    res = runner.invoke(app, ["clean", str(src), "--out", str(out)])
    assert res.exit_code == 0
    assert out.exists()


def test_cli_convert_and_plot(tmp_path: Path, monkeypatch):
    # Use Agg to avoid GUI
    monkeypatch.setenv("MPLBACKEND", "Agg")

    src = _make_sample_bdf(tmp_path)
    conv = tmp_path / "converted.bdf.csv"
    plot_path = tmp_path / "plot.png"

    res_conv = runner.invoke(app, ["convert", str(src), "--to", str(conv)])
    assert res_conv.exit_code == 0
    assert conv.exists()
    conv_df = pd.read_csv(conv)
    assert "test_time_second" in conv_df.columns
    assert "voltage_volt" in conv_df.columns
    assert "current_ampere" in conv_df.columns

    conv_human = tmp_path / "converted-human.bdf.csv"
    res_conv_human = runner.invoke(app, ["convert", str(src), "--to", str(conv_human), "--human"])
    assert res_conv_human.exit_code == 0
    conv_human_df = pd.read_csv(conv_human)
    assert "Test Time / s" in conv_human_df.columns
    assert "Voltage / V" in conv_human_df.columns
    assert "Current / A" in conv_human_df.columns

    res_plot = runner.invoke(
        app,
        [
            "plot",
            str(src),
            "--save",
            str(plot_path),
        ],
    )
    assert res_plot.exit_code == 0
    assert plot_path.exists()


def test_cli_meta_jsonld(tmp_path: Path):
    src = _make_sample_bdf(tmp_path)
    out = tmp_path / "meta.jsonld"
    res = runner.invoke(
        app,
        [
            "meta-jsonld",
            str(src),
            "--out",
            str(out),
            "--title",
            "Sample",
            "--description",
            "Desc",
            "--creator",
            "Alice|0000-0000-0000-0000|Org",
        ],
    )
    assert res.exit_code == 0
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data.get("@type") == "schema:Dataset"


def test_cli_ingest_existing_bdf(tmp_path: Path, monkeypatch):
    root = tmp_path / "my-contribution"
    raw_dir = root / "timeseries" / "raw"
    raw_dir.mkdir(parents=True)
    src = raw_dir / "sample.bdf.csv"
    df = pd.DataFrame(
        {
            "Test Time / s": [0, 1, 2],
            "Voltage / V": [3.7, 3.6, 3.5],
            "Current / A": [0.1, 0.1, 0.1],
        }
    )
    df.to_csv(src, index=False)

    monkeypatch.chdir(root)
    res = runner.invoke(
        app,
        [
            "ingest",
            "--format",
            "csv",
        ],
    )
    assert res.exit_code == 0
    out = root / "timeseries" / "sample.bdf.csv"
    assert out.exists()
