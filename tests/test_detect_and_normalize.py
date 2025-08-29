# tests/test_detect_and_normalize.py
from pathlib import Path
import pandas as pd
import pytest

# Adjust if you rename the file or move it
FNAME = "SINTEF__NaCR32140-MP10-04__2025-08-25__CCCV_0p02C_25degC__BioLogic.mpt"
EXPECTED_PLUGIN = "biologic-mpt"  # set to your plugin id

def _data_path() -> Path:
    root = Path(__file__).resolve().parents[1]  # repo root
    p = root / "data" / FNAME
    if not p.exists():
        pytest.skip(f"Test data not found: {p}")
    return p

def test_detect_biologic_mpt():
    """Detect the cycler plugin for a Bio-Logic .mpt file."""
    from bdf import detect_cycler  # public facade
    p = _data_path()
    sr = detect_cycler(p)
    assert sr is not None, "No SniffResult returned"
    assert isinstance(sr.id, str) and sr.id, "Plugin id missing"
    # Accept either exact match or any biologic-* id
    assert (sr.id == EXPECTED_PLUGIN) or ("biologic" in sr.id.lower())
    assert sr.confidence >= 0.5, f"Low detection confidence: {sr.confidence} ({sr.reason})"

def test_normalize_to_bdf_required_columns():
    """Parse vendor file and normalize to BDF canonical columns."""
    from bdf import read_raw_to_bdf  # public facade
    p = _data_path()
    df = read_raw_to_bdf(p)  # auto-detect plugin

    required = ["Test Time / s", "Voltage / V", "Current / A"]
    for col in required:
        assert col in df.columns, f"Missing required BDF column: {col}"
        assert pd.api.types.is_numeric_dtype(df[col]), f"{col} must be numeric"

    assert len(df) > 0, "No rows parsed"
    # Basic sanity on time axis (Bio-Logic time/s should be non-negative)
    assert df["Test Time / s"].min() >= 0

def test_force_plugin_override():
    """Optionally ensure explicit plugin override works."""
    from bdf import read_raw_to_bdf
    p = _data_path()
    df = read_raw_to_bdf(p, as_=EXPECTED_PLUGIN)
    for col in ["Test Time / s", "Voltage / V", "Current / A"]:
        assert col in df.columns
