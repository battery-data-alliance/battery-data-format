# tests/conftest.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

HERE = Path(__file__).parent
DATA = HERE / "data"

# For downloading test data from yadg repo
YADG_BASE_URL = "https://raw.githubusercontent.com/dgbowl/yadg/main/tests/test_x_eclab"

# Stems must match filenames in the yadg repo
# The file should exist at YADG_BASE_URL/{stem}.mpr and .mpt
BIOLOGIC_FILE_STEMS = [
    "bcd.issue_241",
    "ca.issue_149",
    "coc.issue_185",
    "cov.issue_185",
    "cp.issue_149",
    "cv.issue_217",
    "cva.issue_202",
    "gcpl.issue_226.CxN",
    "gcpl.issue_230",
    "geis.issue_149",
    "lsv.issue_195",
    "mb.issue_223",
    "mp.issue_183",
    "ocv.issue_149",
    "peis.issue_149",
]


def _biologic_cache_dir() -> Path:
    base = Path.home() / ".cache" / "bdf" / "biologic"
    base.mkdir(parents=True, exist_ok=True)
    return base

def _fetch_biologic_file(stem: str, ext: str) -> Path | None:
    """Return cached file path, downloading from yadg if needed. Returns None on failure."""
    cached = _biologic_cache_dir() / f"{stem}.{ext}"
    if cached.exists():
        return cached
    import requests
    url = f"{YADG_BASE_URL}/{stem}.{ext}"
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        cached.write_bytes(r.content)
        return cached
    except Exception:
        return None

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
    with open(data_dir / "minimal_registry.jsonld", encoding="utf-8") as f:
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

@pytest.fixture(
    params=BIOLOGIC_FILE_STEMS,
    ids=lambda s: s,
    scope="session",
)
def biologic_file_pair(request) -> tuple[Path, Path]:
    """Provide a (mpr, mpt) file pair for a biologic test stem.

    Files are downloaded from yadg on first use and cached locally.
    Tests are skipped with a warning when a file cannot be fetched.
    """
    stem = request.param
    mpr = _fetch_biologic_file(stem, "mpr")
    mpt = _fetch_biologic_file(stem, "mpt")
    if mpr is None or mpt is None:
        pytest.skip(f"biologic test file '{stem}' not cached and could not be downloaded")
    return mpr, mpt
