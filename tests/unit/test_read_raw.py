from pathlib import Path
import pandas as pd

import bdf


def test_read_raw_to_bdf_with_stub_plugin(tmp_path, monkeypatch):
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

    df = bdf.read_raw_to_bdf(raw, as_=None, validate=True)
    assert list(df.columns)[:3] == ["Test Time / s", "Voltage / V", "Current / A"]
