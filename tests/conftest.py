# tests/conftest.py
from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

import pytest

HERE = Path(__file__).parent
DATA = HERE / "data"

BIOLOGIC_DATA = DATA / "biologic"

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

def _biologic_gz_pairs() -> list[tuple[Path, Path]]:
    """Get all pairs of .mpr.gz and .mpt.gz files in Biologic data folder."""
    mprs = {p.with_suffix("").stem: p for p in BIOLOGIC_DATA.glob("*.mpr.gz")}
    mpts = {p.with_suffix("").stem: p for p in BIOLOGIC_DATA.glob("*.mpt.gz")}
    unpaired = mprs.keys() ^ mpts.keys()
    assert not unpaired, f"Unpaired biologic files: {unpaired}"
    return [(mprs[stem], mpts[stem]) for stem in sorted(mprs) if stem in mpts]

@pytest.fixture(scope="session")
def biologic_unzip_dir(tmp_path_factory) -> Path:
    """Decompress all .mpr.gz and .mpt.gz files into temp directory."""
    tmp = tmp_path_factory.mktemp("biologic")
    for gz_path in BIOLOGIC_DATA.glob("*.gz"):
        out_path = tmp / gz_path.with_suffix("").name
        with gzip.open(gz_path, "rb") as f_in, open(out_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    return tmp

@pytest.fixture(
    params=_biologic_gz_pairs(),
    ids=lambda p: p[0].with_suffix("").stem,
    scope="session",
)
def biologic_file_pair(request, biologic_unzip_dir) -> tuple[Path, Path]:
    """Get a pair of .mpr and .mpt biologic files."""
    gz_mpr, gz_mpt = request.param
    return (
        biologic_unzip_dir / gz_mpr.with_suffix("").name,
        biologic_unzip_dir / gz_mpt.with_suffix("").name,
    )
