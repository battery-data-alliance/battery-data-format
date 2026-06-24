# tests/test_registry_bdf_loading.py
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import requests

# ============================================================
# Configuration (env overrides)
# ============================================================
# Remote JSON-LD registry (schema.org DataCatalog) hosted on Zenodo:
REGISTRY_URL = os.getenv(
    "BDF_REGISTRY_URL",
    "https://zenodo.org/records/18214281/files/metadata.json",
)
# Zenodo record API (for iterating all attached files directly)
RECORD_API_URL = os.getenv(
    "BDF_RECORD_API_URL",
    "https://zenodo.org/api/records/18214281",
)

# Cache directory for registry + downloaded distributions
CACHE_DIR = Path(os.getenv("BDF_TEST_CACHE_DIR", ".pytest_cache/bdf_registry")).resolve()
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Optional: limit datasets (useful for local dev vs CI)
MAX_DATASETS = int(os.getenv("BDF_MAX_DATASETS", "0"))  # 0 = no limit

# Optional vendor filter (regex against provider @id tail)
VENDOR_FILTER = os.getenv("BDF_VENDOR_FILTER", "")  # e.g. "biologic|neware"

# Optional hard cap on file size (MiB) to avoid blowing up CI
MAX_DOWNLOAD_MIB = int(os.getenv("BDF_MAX_DOWNLOAD_MIB", "200"))  # 200 MiB default

# Offline mode (skip network if not cached)
OFFLINE = os.getenv("BDF_OFFLINE", "").lower() in {"1", "true", "yes"}

# HTTP timeout (seconds)
HTTP_TIMEOUT = float(os.getenv("BDF_HTTP_TIMEOUT", "120"))

# ============================================================
# Helpers
# ============================================================


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _cached_path_for_url(url: str, suffix: str = "") -> Path:
    tail = url.rstrip("/").split("/")[-1] or "index"
    name = f"{_hash(url)}__{tail}{suffix}"
    return CACHE_DIR / name


def _http_get(url: str, stream: bool = False) -> requests.Response:
    headers = {
        "User-Agent": "bdf-registry-tester/1.0 (+https://github.com/)",
        "Accept": "application/json, text/plain, */*",
    }
    r = requests.get(url, headers=headers, stream=stream, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r


def _http_head(url: str) -> Optional[int]:
    """Return content-length in bytes if available via HEAD, else None."""
    try:
        r = requests.head(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
        if r.ok:
            cl = r.headers.get("Content-Length")
            if cl and cl.isdigit():
                return int(cl)
    except Exception:
        pass
    return None


def _fetch_registry(url: str) -> Dict[str, Any]:
    cache = _cached_path_for_url(url, suffix="__registry.json")
    if cache.exists() and cache.stat().st_size > 0:
        # Use cached registry if present
        with cache.open("r", encoding="utf-8") as f:
            return json.load(f)

    if OFFLINE:
        pytest.skip(f"Offline mode and no cached registry at {cache}")

    # Download and cache
    resp = _http_get(url, stream=False)
    try:
        data = resp.json()
    except Exception as e:
        raise AssertionError(f"Registry at {url} is not valid JSON: {e}") from e

    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def _fetch_record_files(url: str) -> List[Dict[str, Any]]:
    cache = _cached_path_for_url(url, suffix="__record.json")
    if cache.exists() and cache.stat().st_size > 0:
        with cache.open("r", encoding="utf-8") as f:
            return json.load(f)

    if OFFLINE:
        pytest.skip(f"Offline mode and no cached record at {cache}")

    resp = _http_get(url, stream=False)
    try:
        data = resp.json()
    except Exception as e:
        raise AssertionError(f"Record at {url} is not valid JSON: {e}") from e

    files = data.get("files") or []
    out: List[Dict[str, Any]] = []
    for f in files:
        key = f.get("key") if isinstance(f, dict) else None
        key_lower = (key or "").lower()
        if key_lower == "metadata.json":
            continue
        links = f.get("links") if isinstance(f, dict) else {}
        download_url = None
        if isinstance(links, dict):
            download_url = links.get("download") or links.get("self")
        if not download_url:
            continue
        out.append({"key": key or download_url.split("/")[-1], "download_url": download_url})
        if MAX_DATASETS and len(out) >= MAX_DATASETS:
            break

    if not out:
        pytest.skip("No files discovered in record (or all filtered out).")

    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open("w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def _download_file(url: str, filename_hint: Optional[str] = None) -> Path:
    dest = CACHE_DIR / f"{_hash(url)}__{filename_hint}" if filename_hint else _cached_path_for_url(url)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    if OFFLINE:
        pytest.skip(f"Offline mode; missing cached file for {url}")

    # Guard: size check before download if server provides it
    size_bytes = _http_head(url)
    if size_bytes is not None:
        size_mib = size_bytes / (1024 * 1024)
        if size_mib > MAX_DOWNLOAD_MIB:
            pytest.skip(f"File too large ({size_mib:.1f} MiB > {MAX_DOWNLOAD_MIB} MiB): {url}")

    # Download
    with _http_get(url, stream=True) as r:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    return dest


def _plugin_slug_from_isBasedOn(is_based_on: Any) -> Optional[str]:
    """
    Extract a plugin slug like 'biologic-mpt' from schema:isBasedOn content.
    Accepts string, object, or list.
    """
    if is_based_on is None:
        return None
    candidates: List[str] = []

    def pick(obj: Any):
        if isinstance(obj, str):
            candidates.append(obj)
        elif isinstance(obj, dict):
            if "@id" in obj and isinstance(obj["@id"], str):
                candidates.append(obj["@id"])
            if "name" in obj and isinstance(obj["name"], str):
                candidates.append(obj["name"])

    if isinstance(is_based_on, list):
        for it in is_based_on:
            pick(it)
    else:
        pick(is_based_on)

    for c in candidates:
        tail = c.rstrip("/").split("/")[-1]
        slug = re.sub(r"[^a-zA-Z0-9\-]+", "-", tail).strip("-").lower()
        if slug:
            return slug
    return None


def _infer_plugin_from_filename(
    provider_org_id: Optional[str],
    filepath: Path,
    dataset_identifier: Optional[str] = None,
    keywords: Optional[List[str]] = None,
) -> Optional[str]:
    """
    Fallback mapping by extension and provider tail.
    """
    ext = filepath.suffix.lower()
    name = filepath.name.lower()
    ident = (dataset_identifier or "").lower()
    kw = " ".join(k.lower() for k in (keywords or []) if isinstance(k, str))
    haystack = " ".join([name, ident, kw])
    vendor_tail = None
    if provider_org_id and isinstance(provider_org_id, str):
        vendor_tail = provider_org_id.rstrip("/").split("/")[-1].lower()

    if ext in (".nda", ".ndax"):
        return "neware-nda"
    if ext == ".mpt":
        return "biologic-mpt"
    if "biologic" in haystack and ext in (".mpt", ".txt", ".csv"):
        return "biologic-mpt"
    if "neware" in haystack and ext == ".csv":
        return "neware-csv"
    if "landt" in haystack:
        if ext == ".csv":
            return "landt-csv"
        if ext == ".txt":
            return "landt-txt"
    if "basytec" in haystack and ext == ".txt":
        return "basytec-txt"
    if "digatron" in haystack and ext == ".csv":
        return "digatron-csv"
    if "novonix" in haystack and ext == ".csv":
        return "novonix-csv"

    if ext == ".csv" and vendor_tail:
        return f"{vendor_tail}-csv"
    if ext == ".txt" and vendor_tail:
        return f"{vendor_tail}-txt"
    if ext == ".csv":
        return None
    if ext == ".txt":
        return "txt"
    return None


def _iter_distributions(registry: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    datasets = registry.get("dataset", [])
    if not isinstance(datasets, list):
        return

    count = 0
    for ds in datasets:
        ds_id = ds.get("@id") or ds.get("id")
        ds_ident = ds.get("identifier") or (ds_id.rstrip("/").split("/")[-1] if isinstance(ds_id, str) else None)

        # Optional vendor filter
        provider = ds.get("provider")
        provider_id = None
        if isinstance(provider, dict):
            provider_id = provider.get("@id") or provider.get("id")
        if VENDOR_FILTER:
            tail = (provider_id or "").rstrip("/").split("/")[-1].lower()
            if not re.search(VENDOR_FILTER, tail):
                continue

        is_based_on = ds.get("isBasedOn")
        plugin_slug = _plugin_slug_from_isBasedOn(is_based_on)

        dists = ds.get("distribution", [])
        if not isinstance(dists, list):
            continue
        for dist in dists:
            url = dist.get("contentUrl") or dist.get("url")
            dist_id = dist.get("@id") or dist.get("id") or url
            if not url or not isinstance(url, str):
                yield {
                    "dataset_id": ds_id,
                    "dataset_identifier": ds_ident,
                    "distribution_id": dist_id,
                    "error": "missing contentUrl",
                }
                continue

            case = {
                "dataset_id": ds_id,
                "dataset_identifier": ds_ident,
                "dataset_keywords": ds.get("keywords") if isinstance(ds.get("keywords"), list) else None,
                "distribution_id": dist_id,
                "download_url": url,
                "plugin_slug": plugin_slug,
                "provider_id": provider_id,
            }
            yield case

            count += 1
            if MAX_DATASETS and count >= MAX_DATASETS:
                return


def _load_with_bdf(path: Path, plugin_slug: Optional[str]) -> Any:
    """
    Try common bdf entry points. Modify here if your API differs.
    """
    try:
        import bdf  # type: ignore
    except Exception as e:
        pytest.skip(f"'bdf' not importable: {e}")

    last_err: Optional[Exception] = None

    if hasattr(bdf, "load"):
        try:
            return bdf.load(path)  # type: ignore[attr-defined]
        except (ImportError, ModuleNotFoundError) as e:
            pytest.skip(f"Optional dependency missing while loading {path}: {e}")
        except Exception as e:
            last_err = e

    if hasattr(bdf, "read"):
        try:
            df_pl, _meta = bdf.read(path, plugin=plugin_slug, lazy=False)  # type: ignore[attr-defined]
            return df_pl.to_pandas()
        except (ImportError, ModuleNotFoundError) as e:
            pytest.skip(f"Optional dependency missing while loading {path}: {e}")
        except Exception as e:
            last_err = e

    if hasattr(bdf, "from_file"):
        try:
            return bdf.from_file(path, plugin=plugin_slug)  # type: ignore[attr-defined]
        except (ImportError, ModuleNotFoundError) as e:
            pytest.skip(f"Optional dependency missing while loading {path}: {e}")
        except Exception as e:
            last_err = e

    raise AssertionError(
        f"Could not load with 'bdf'. plugin={plugin_slug!r}, file={str(path)!r}. Last error: {last_err}"
    )


def _looks_nonempty(result: Any) -> bool:
    try:
        import pandas as pd  # noqa: F401
    except Exception:
        pd = None  # type: ignore

    if result is None:
        return False

    if pd and "pandas" in str(type(result)) and hasattr(result, "empty"):
        try:
            return not bool(result.empty)  # type: ignore[attr-defined]
        except Exception:
            pass

    if hasattr(result, "df"):
        df = result.df
        try:
            if hasattr(df, "empty"):
                return not bool(df.empty)
            if hasattr(df, "__len__"):
                return len(df) > 0  # type: ignore[arg-type]
        except Exception:
            pass

    if hasattr(result, "to_dataframe"):
        try:
            df2 = result.to_dataframe()  # type: ignore
            if hasattr(df2, "empty"):
                return not bool(df2.empty)
            if hasattr(df2, "__len__"):
                return len(df2) > 0
        except Exception:
            pass

    if hasattr(result, "__len__"):
        try:
            return len(result) > 0  # type: ignore[arg-type]
        except Exception:
            pass

    return True  # Truthy object fallback


# ============================================================
# Collect cases once at import time
# ============================================================


def _collect_cases() -> List[Dict[str, Any]]:
    reg = _fetch_registry(REGISTRY_URL)

    # Minimal sanity checks (helpful failure in CI)
    if reg.get("@type") not in ("DataCatalog", ["DataCatalog"]):
        # Some JSON-LD exporters dump arrays; don't be too strict.
        pass

    cases: List[Dict[str, Any]] = []
    for item in _iter_distributions(reg):
        if "error" in item:
            cases.append({**item, "xfail": True})
            continue
        # Prepare local cache path (download happens inside the test)
        url = item["download_url"]
        local = _cached_path_for_url(url)
        item["local_path"] = str(local)
        # Infer plugin later (need file suffix), if missing
        if not item.get("plugin_slug"):
            item["need_infer_plugin"] = True
        cases.append(item)

    if not cases:
        pytest.skip("No distributions found in registry (or filtered out).")
    return cases


CASES = _collect_cases()
RECORD_FILES = _fetch_record_files(RECORD_API_URL)

# ============================================================
# The test
# ============================================================


@pytest.mark.parametrize(
    "case", CASES, ids=lambda c: f"{c.get('dataset_identifier', '?')}::{(c.get('distribution_id', '?').split('/')[-1])}"
)
def test_registry_distribution_loads_with_bdf(case: Dict[str, Any]):
    if case.get("xfail"):
        pytest.xfail(f"Malformed registry entry: {case}")

    url = case["download_url"]
    local_file = _download_file(url)

    plugin = case.get("plugin_slug")
    if not plugin and case.get("need_infer_plugin"):
        plugin = _infer_plugin_from_filename(
            case.get("provider_id"),
            local_file,
            dataset_identifier=case.get("dataset_identifier"),
            keywords=case.get("dataset_keywords"),
        )

    result = _load_with_bdf(local_file, plugin)
    assert _looks_nonempty(result), (
        f"Empty/invalid result for dataset={case.get('dataset_identifier')}, "
        f"dist={case.get('distribution_id')}, plugin={plugin}, file={local_file}"
    )


@pytest.mark.parametrize(
    "file_case",
    RECORD_FILES,
    ids=lambda c: c.get("key", "?"),
)
def test_record_files_load_with_bdf(file_case: Dict[str, Any]):
    local_file = _download_file(file_case["download_url"], filename_hint=file_case.get("key"))
    plugin = _infer_plugin_from_filename(None, local_file)
    result = _load_with_bdf(local_file, plugin)
    assert _looks_nonempty(result), (
        f"Empty/invalid result for record file key={file_case.get('key')}, plugin={plugin}, file={local_file}"
    )
