# tests/conftest.py
from __future__ import annotations
import os, json
from pathlib import Path
import pytest

HERE = Path(__file__).parent
DATA = HERE / "data"

@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA

@pytest.fixture(scope="session")
def tiny_files(data_dir: Path):
    """Minimal valid tiny files for each plugin (keep <1KB)."""
    return {
        "biologic": data_dir / "tiny_biologic.mpt",
        "neware": data_dir / "tiny_neware.csv",
        "landt_csv": data_dir / "tiny_landt.csv",
        "basytec": data_dir / "tiny_basytec.txt",
        "digatron": data_dir / "tiny_digatron.csv",
    }

@pytest.fixture(scope="session")
def minimal_registry(data_dir: Path) -> dict:
    with open(data_dir / "minimal_registry.jsonld", "r", encoding="utf-8") as f:
        return json.load(f)

@pytest.fixture(scope="session")
def offline_registry_url() -> str:
    """Fallback to the minimal local registry (used by some unit tests)."""
    return "file://minimal"

@pytest.fixture(scope="session")
def pint_ureg():
    try:
        import pint
        return pint.UnitRegistry()
    except Exception:
        pytest.skip("pint not available")
