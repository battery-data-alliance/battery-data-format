from pathlib import Path

import pandas as pd

import bdf


def test_read_with_stub_plugin(tmp_path, monkeypatch):
    # Create a dummy file path to satisfy _resolve_source
    raw = tmp_path / "raw.dat"
    raw.write_text("dummy")

    class StubPlugin:
        def parse(self, path: Path):
            return pd.DataFrame(
                {
                    "time": [0, 1],
                    "voltage": [3.7, 3.6],
                    "current": [0.1, 0.1],
                }
            )

        def augment(self, df_raw: pd.DataFrame):
            return df_raw

        def fixup(self, df: pd.DataFrame):
            return df

        # Map plugin-specific names to canonical via column_synonyms in normalize
        column_synonyms = {
            "Test Time / s": ["time"],
            "Voltage / V": ["voltage"],
            "Current / A": ["current"],
        }

    monkeypatch.setattr(bdf, "load_plugin", lambda path, plugin_id=None: StubPlugin())

    df = bdf.read(raw, plugin=None, validate=True)
    assert list(df.columns)[:3] == ["Test Time / s", "Voltage / V", "Current / A"]


def test_read_falls_back_to_alternate_plugin_when_first_fails(tmp_path, monkeypatch):
    raw = tmp_path / "raw.dat"
    raw.write_text("dummy")

    class BadPlugin:
        id = "bad"
        column_synonyms = {}

        def parse(self, path: Path):
            raise ValueError("parse failed")

        def augment(self, df_raw: pd.DataFrame):
            return df_raw

    class GoodPlugin:
        id = "good"
        column_synonyms = {
            "Test Time / s": ["time"],
            "Voltage / V": ["voltage"],
            "Current / A": ["current"],
        }

        def parse(self, path: Path):
            return pd.DataFrame(
                {
                    "time": [0, 1],
                    "voltage": [3.7, 3.6],
                    "current": [0.1, 0.1],
                }
            )

        def augment(self, df_raw: pd.DataFrame):
            return df_raw

        def fixup(self, df: pd.DataFrame):
            return df

    monkeypatch.setattr(
        bdf,
        "_candidate_plugins",
        lambda path, *, plugin, plugin_hint: [BadPlugin(), GoodPlugin()],
    )

    df = bdf.read(raw, plugin=None, validate=True)
    assert list(df.columns)[:3] == ["Test Time / s", "Voltage / V", "Current / A"]
