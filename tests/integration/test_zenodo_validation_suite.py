# tests/integration/test_zenodo_validation_suite.py
"""
Parametrized integration tests against the BDF validation Zenodo record.

  DOI: https://doi.org/10.5281/zenodo.16994937

Every file in the record (except metadata.json) is downloaded, parsed
through bdf.read(), and subjected to physical-plausibility checks.

Files whose name contains a known-bug tag (e.g. __Time_Bug, __Outlier_Bug)
are tested for load-without-crash only — not for physical correctness.

Run with:
    pytest -m zenodo tests/integration/test_zenodo_validation_suite.py -v

Caching: downloaded files land in .pytest_cache/bdf_registry/ and are
reused on subsequent runs (no re-download unless the cache is cleared).

Environment overrides:
    BDF_ZENODO_MAX_MIB   max file size to download in MiB  (default: 300)
    BDF_OFFLINE          set to 1 to skip if not cached
"""
from __future__ import annotations

import hashlib
import os
import re
import warnings
from pathlib import Path
from typing import Optional

import pandas as pd
import pytest
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
RECORD_API = "https://zenodo.org/api/records/16994937"
CACHE_DIR   = Path(os.getenv("BDF_TEST_CACHE_DIR", ".pytest_cache/bdf_registry")).resolve()
MAX_MIB     = int(os.getenv("BDF_ZENODO_MAX_MIB", "300"))
OFFLINE     = os.getenv("BDF_OFFLINE", "").lower() in {"1", "true", "yes"}
HTTP_TIMEOUT = 120.0

CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Broad voltage bounds — covers Li-ion, Na-ion, full-cell AND half-cell vs Li/Li+
# Half-cell anodes (graphite vs Li) start at ~0.01 V. The small negative margin
# tolerates ADC-offset noise near 0 V seen on real instruments (e.g. a handful
# of points at ~-0.04 V during rest/pulse on Na-ion cells).
VOLTAGE_LO, VOLTAGE_HI = -0.1, 5.5

# Known-bug tag patterns: files matching these are robustness-only
BUG_PATTERNS = re.compile(r"_(bug|outlier|time.bug|outlier.bug)_?", re.IGNORECASE)

# Header markers for LAND .ccs binary variants that the landt-ccs plugin does
# not yet model. The current parser handles the "SNEL" block layout (payload at
# 0x1000, dense 0x0503 sample blocks on the 128-byte grid). The "ShortLandt"
# variant uses a different, not-yet-reverse-engineered block layout; such files
# are treated as a known-unsupported variant (xfail) rather than a hard failure.
_UNSUPPORTED_CCS_MARKERS = (b"ShortLandt",)


def _is_unsupported_ccs_variant(path: Path) -> bool:
    try:
        head = Path(path).read_bytes()[:64]
    except OSError:
        return False
    return any(marker in head for marker in _UNSUPPORTED_CCS_MARKERS)

# ---------------------------------------------------------------------------
# Plugin slug inference from filename
# ---------------------------------------------------------------------------
_EXT_VENDOR_MAP: dict[str, str] = {
    ".nda":  "neware-nda",
    ".ndax": "neware-nda",
    ".mpt":  "biologic-mpt",
    ".ccs":  "landt-ccs",
    ".xlsx": "excel-xlsx",
    ".xls":  "excel-xlsx",
}

_KEYWORD_MAP: list[tuple[str, str, str]] = [
    # (keyword, ext_or_*, plugin_slug)
    ("biologic",  "*",    "biologic-mpt"),
    ("neware",    ".csv", "neware-csv"),
    ("neware",    ".nda", "neware-nda"),
    ("landt",     ".csv", "landt-csv"),
    ("landt",     ".txt", "landt-txt"),
    ("landt",     ".ccs", "landt-ccs"),
    ("basytec",   ".txt", "basytec-txt"),
    ("basytec",   ".dat", "basytec-txt"),
    ("digatron",  ".csv", "digatron-csv"),
    ("novonix",   ".csv", "novonix-csv"),
    ("arbin",     ".csv",  "arbin-csv"),
    ("arbin",     ".xlsx", "arbin-xlsx"),
    ("maccor",    ".csv",  None),        # no plugin yet
]


def _infer_plugin(filename: str) -> Optional[str]:
    ext  = Path(filename).suffix
    name = filename.lower()
    # Vendor-keyword matches are more specific than the generic extension map
    # (e.g. an Arbin .xlsx should use arbin-xlsx, not the generic excel reader).
    for keyword, req_ext, slug in _KEYWORD_MAP:
        if keyword in name and (req_ext == "*" or req_ext.lower() == ext.lower()):
            return slug
    if ext in _EXT_VENDOR_MAP:
        return _EXT_VENDOR_MAP[ext]
    return None


# ---------------------------------------------------------------------------
# HTTP helpers  (reuse logic from test_raw_loading.py)
# ---------------------------------------------------------------------------
def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _cached(url: str, hint: str) -> Path:
    return CACHE_DIR / f"{_hash(url)}__{hint}"


def _get(url: str, stream: bool = False) -> requests.Response:
    r = requests.get(
        url,
        headers={"User-Agent": "bdf-zenodo-tester/1.0"},
        stream=stream,
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return r


def _head_size(url: str) -> Optional[int]:
    try:
        r = requests.head(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
        cl = r.headers.get("Content-Length", "")
        return int(cl) if cl.isdigit() else None
    except Exception:
        return None


def _download(url: str, filename: str) -> Path:
    dest = _cached(url, filename)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    if OFFLINE:
        pytest.skip(f"Offline; no cached copy of {filename}")

    size = _head_size(url)
    if size is not None and size / 1024 / 1024 > MAX_MIB:
        pytest.skip(f"{filename}: {size/1024/1024:.0f} MiB > {MAX_MIB} MiB limit")

    with _get(url, stream=True) as r:
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)
    return dest


# ---------------------------------------------------------------------------
# Discover files from Zenodo API at collection time
# ---------------------------------------------------------------------------
def _fetch_file_list() -> list[dict]:
    cache = CACHE_DIR / f"{_hash(RECORD_API)}__record.json"
    if cache.exists() and cache.stat().st_size > 0:
        import json
        return json.loads(cache.read_text())

    if OFFLINE:
        return []

    import json
    try:
        data = _get(RECORD_API).json()
    except Exception:
        return []

    files = []
    for f in data.get("files") or []:
        key = f.get("key", "")
        if key.lower() == "metadata.json":
            continue
        links = f.get("links") or {}
        url = links.get("content") or links.get("download") or links.get("self")
        if url:
            files.append({"key": key, "url": url})

    cache.write_text(json.dumps(files, indent=2))
    return files


_FILES = _fetch_file_list()


# ---------------------------------------------------------------------------
# Test parametrisation
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.zenodo


@pytest.fixture(scope="session")
def _bdf():
    try:
        import bdf
        return bdf
    except ImportError:
        pytest.skip("bdf package not importable")


def pytest_generate_tests(metafunc):
    if "file_entry" in metafunc.fixturenames:
        ids  = [e["key"] for e in _FILES]
        metafunc.parametrize("file_entry", _FILES, ids=ids)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_frame(result) -> pd.DataFrame:
    if isinstance(result, pd.DataFrame):
        return result
    if isinstance(result, dict):
        frames = [v for v in result.values() if isinstance(v, pd.DataFrame)]
        assert frames, "bdf.read() returned a dict with no DataFrames"
        return frames[0]
    pytest.fail(f"bdf.read() returned unexpected type: {type(result)}")


def _is_bug_file(filename: str) -> bool:
    return bool(BUG_PATTERNS.search(filename))


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------
def test_zenodo_file_loads_and_is_plausible(file_entry, _bdf):
    """
    Download the file, parse with bdf.read(), and check physical plausibility.

    Bug files (_Bug suffix) only check that loading does not raise an exception.
    """
    filename = file_entry["key"]
    url      = file_entry["url"]
    plugin   = _infer_plugin(filename)

    if plugin is None:
        pytest.xfail(f"No plugin available for '{filename}' — expected failure")

    # Download (cached after first run)
    path = _download(url, filename)

    # Load
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            result = _bdf.read(path, plugin=plugin)
        except Exception as exc:
            if _is_bug_file(filename):
                pytest.xfail(f"Known-bug file raised: {exc}")
            if Path(filename).suffix.lower() == ".ccs" and _is_unsupported_ccs_variant(path):
                pytest.xfail(f"Unsupported LAND .ccs variant (ShortLandt): {exc}")
            raise AssertionError(
                f"bdf.read() raised for {filename} (plugin={plugin!r}): {exc}"
            ) from exc

    df = _to_frame(result)
    assert not df.empty, f"{filename}: loaded DataFrame is empty"

    # --- Required columns ---
    required = {"Test Time / s", "Voltage / V", "Current / A"}
    missing  = required - set(df.columns)
    assert not missing, f"{filename}: missing required columns {sorted(missing)}"

    # Bug files: done — no physical checks
    if _is_bug_file(filename):
        return

    # --- Physical plausibility checks ---
    t = pd.to_numeric(df["Test Time / s"], errors="coerce").dropna()
    v = pd.to_numeric(df["Voltage / V"],   errors="coerce").dropna()
    I = pd.to_numeric(df["Current / A"],   errors="coerce").dropna()

    assert not t.empty, f"{filename}: Test Time / s has no valid numeric values"
    assert not v.empty, f"{filename}: Voltage / V has no valid numeric values"
    assert not I.empty, f"{filename}: Current / A has no valid numeric values"

    # Time must be non-decreasing
    assert (t.diff().dropna() >= -1e-6).all(), \
        f"{filename}: Test Time / s is not monotonically non-decreasing"

    # Voltage in plausible range
    assert v.between(VOLTAGE_LO, VOLTAGE_HI).all(), \
        (f"{filename}: voltage outside [{VOLTAGE_LO}, {VOLTAGE_HI}] V "
         f"— min={v.min():.3f}, max={v.max():.3f}")

    # Capacity columns: must be in Ah scale (< 1000).
    # Monotonicity is NOT checked here — EC-Lab and some other instruments reset
    # Q_charge to 0 at technique boundaries, which is expected instrument behaviour.
    for col in ("Charging Capacity / Ah", "Discharging Capacity / Ah"):
        if col not in df.columns:
            continue
        c = pd.to_numeric(df[col], errors="coerce").dropna()
        assert c.max() < 1000, \
            f"{filename}: '{col}' max={c.max():.1f} — looks like mAh not Ah (expected Ah)"

    # Step capacity must be non-negative (unsigned)
    if "Step Capacity / Ah" in df.columns:
        step = pd.to_numeric(df["Step Capacity / Ah"], errors="coerce").dropna()
        assert (step >= -1e-9).all(), \
            f"{filename}: 'Step Capacity / Ah' has negative values — expected unsigned"
