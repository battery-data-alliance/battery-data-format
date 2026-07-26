from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

import requests  # type: ignore[import-untyped]

# Allow running from repo root without an editable install.
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


DEFAULT_REGISTRY_URL = "https://zenodo.org/records/18214281/files/metadata.json"
DEFAULT_RECORD_API_URL = "https://zenodo.org/api/records/18214281"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _strip_all_suffixes(path: Path) -> str:
    name = path.name
    while True:
        suffix = Path(name).suffix
        if not suffix:
            return name
        name = Path(name).stem


def _cached_path_for_url(cache_dir: Path, url: str, suffix: str = "") -> Path:
    tail = url.rstrip("/").split("/")[-1] or "index"
    return cache_dir / f"{_hash(url)}__{tail}{suffix}"


def _http_get(url: str, *, timeout: float, stream: bool = False) -> requests.Response:
    headers = {
        "User-Agent": "bdf-reference-generator/1.0",
        "Accept": "application/json, text/plain, */*",
    }
    response = requests.get(url, headers=headers, stream=stream, timeout=timeout)
    response.raise_for_status()
    return response


def _http_head_content_length(url: str, *, timeout: float) -> Optional[int]:
    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        if not response.ok:
            return None
        value = response.headers.get("Content-Length")
        if value and value.isdigit():
            return int(value)
    except Exception:
        return None
    return None


def _fetch_registry(cache_dir: Path, registry_url: str, *, timeout: float, offline: bool) -> dict[str, Any]:
    cache_path = _cached_path_for_url(cache_dir, registry_url, suffix="__registry.json")
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    if offline:
        raise RuntimeError(f"Offline mode enabled and cache missing: {cache_path}")

    response = _http_get(registry_url, timeout=timeout, stream=False)
    data = response.json()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _fetch_record_files(
    cache_dir: Path,
    record_api_url: str,
    *,
    timeout: float,
    offline: bool,
    max_items: int,
) -> list[dict[str, str]]:
    cache_path = _cached_path_for_url(cache_dir, record_api_url, suffix="__record.json")
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    if offline:
        raise RuntimeError(f"Offline mode enabled and cache missing: {cache_path}")

    response = _http_get(record_api_url, timeout=timeout, stream=False)
    data = response.json()
    files = data.get("files") or []

    out: list[dict[str, str]] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key.lower() == "metadata.json":
            continue
        raw_links = item.get("links")
        links: dict[str, Any] = raw_links if isinstance(raw_links, dict) else {}
        download_url_obj = links.get("download") or links.get("self")
        if not isinstance(download_url_obj, str) or not download_url_obj.strip():
            continue
        download_url = download_url_obj.strip()
        out.append({"key": key or download_url.split("/")[-1], "download_url": download_url})
        if max_items > 0 and len(out) >= max_items:
            break

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _download_file(
    cache_dir: Path,
    url: str,
    *,
    timeout: float,
    max_download_mib: int,
    offline: bool,
    filename_hint: str | None = None,
) -> Path:
    destination = (
        cache_dir / f"{_hash(url)}__{filename_hint}" if filename_hint else _cached_path_for_url(cache_dir, url)
    )
    if destination.exists() and destination.stat().st_size > 0:
        return destination
    if offline:
        raise RuntimeError(f"Offline mode enabled and cached file missing: {url}")

    size_bytes = _http_head_content_length(url, timeout=timeout)
    if size_bytes is not None and size_bytes / (1024 * 1024) > max_download_mib:
        raise RuntimeError(f"Download exceeds size cap ({size_bytes / (1024 * 1024):.1f} MiB > {max_download_mib} MiB)")

    with _http_get(url, timeout=timeout, stream=True) as response:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
    return destination


def _plugin_slug_from_is_based_on(is_based_on: Any) -> Optional[str]:
    if is_based_on is None:
        return None

    candidates: list[str] = []

    def _pick(value: Any) -> None:
        if isinstance(value, str):
            candidates.append(value)
        elif isinstance(value, dict):
            identifier = value.get("@id")
            name = value.get("name")
            if isinstance(identifier, str):
                candidates.append(identifier)
            if isinstance(name, str):
                candidates.append(name)

    if isinstance(is_based_on, list):
        for entry in is_based_on:
            _pick(entry)
    else:
        _pick(is_based_on)

    for candidate in candidates:
        tail = candidate.rstrip("/").split("/")[-1]
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", tail).strip("_").lower()
        if slug:
            return slug
    return None


def _infer_plugin_from_filename(
    provider_org_id: Optional[str],
    filepath: Path,
    *,
    dataset_identifier: Optional[str] = None,
    keywords: Optional[list[str]] = None,
) -> Optional[str]:
    ext = filepath.suffix.lower()
    name = filepath.name.lower()
    ident = (dataset_identifier or "").lower()
    keyword_text = " ".join(k.lower() for k in (keywords or []) if isinstance(k, str))
    haystack = " ".join([name, ident, keyword_text])

    vendor_tail = None
    if provider_org_id and isinstance(provider_org_id, str):
        vendor_tail = provider_org_id.rstrip("/").split("/")[-1].lower()

    if ext in {".nda", ".ndax"}:
        return "neware_nda"
    if ext == ".mpt":
        return "biologic_mpt"
    if "biologic" in haystack and ext in {".mpt", ".txt", ".csv"}:
        return "biologic_mpt"
    if "neware" in haystack and ext == ".csv":
        return "neware_csv"
    if "landt" in haystack:
        if ext == ".csv":
            return "landt_csv"
        if ext == ".txt":
            return "landt_txt"
    if "basytec" in haystack and ext == ".txt":
        return "basytec_txt"
    if "digatron" in haystack and ext == ".csv":
        return "digatron_csv"
    if "novonix" in haystack and ext == ".csv":
        return "novonix_csv"
    if ext == ".csv" and vendor_tail:
        return f"{vendor_tail}_csv"
    if ext == ".txt" and vendor_tail:
        return f"{vendor_tail}_txt"
    return None


def _iter_registry_cases(
    registry: dict[str, Any],
    *,
    max_items: int,
    vendor_filter: str,
) -> list[dict[str, Any]]:
    datasets = registry.get("dataset", [])
    if not isinstance(datasets, list):
        return []

    count = 0
    cases: list[dict[str, Any]] = []
    for ds in datasets:
        if not isinstance(ds, dict):
            continue
        ds_id = ds.get("@id") or ds.get("id")
        dataset_identifier = ds.get("identifier")
        if not dataset_identifier and isinstance(ds_id, str):
            dataset_identifier = ds_id.rstrip("/").split("/")[-1]
        if not isinstance(dataset_identifier, str):
            dataset_identifier = None

        raw_provider = ds.get("provider")
        provider: dict[str, Any] = raw_provider if isinstance(raw_provider, dict) else {}
        provider_id_obj = provider.get("@id") or provider.get("id")
        provider_id = provider_id_obj if isinstance(provider_id_obj, str) else None
        if vendor_filter:
            vendor_tail = str(provider_id or "").rstrip("/").split("/")[-1].lower()
            if not re.search(vendor_filter, vendor_tail):
                continue

        plugin_slug = _plugin_slug_from_is_based_on(ds.get("isBasedOn"))
        keywords = ds.get("keywords") if isinstance(ds.get("keywords"), list) else None
        distributions = ds.get("distribution", [])
        if not isinstance(distributions, list):
            continue
        for dist in distributions:
            if not isinstance(dist, dict):
                continue
            url = dist.get("contentUrl") or dist.get("url")
            dist_id = dist.get("@id") or dist.get("id") or url
            if not isinstance(url, str) or not url.strip():
                continue
            cases.append(
                {
                    "source": "registry",
                    "dataset_id": ds_id,
                    "dataset_identifier": dataset_identifier,
                    "distribution_id": dist_id,
                    "download_url": url,
                    "plugin_slug": plugin_slug,
                    "provider_id": provider_id,
                    "dataset_keywords": keywords,
                }
            )
            count += 1
            if max_items > 0 and count >= max_items:
                return cases
    return cases


@dataclass
class CaseResult:
    source: str
    download_url: str
    output_path: str | None
    status: str
    plugin: str | None
    rows: int | None = None
    cols: int | None = None
    error: str | None = None


def _output_path(
    output_dir: Path,
    source_name: str,
) -> Path:
    base = _strip_all_suffixes(Path(Path(source_name).name))
    return output_dir / f"{base}.bdf.csv"


def _source_name_for_case(case: dict[str, Any]) -> str:
    filename_hint = case.get("filename_hint")
    if isinstance(filename_hint, str) and filename_hint.strip():
        return Path(filename_hint.strip()).name

    distribution_id = case.get("distribution_id")
    if isinstance(distribution_id, str) and distribution_id.strip():
        candidate = distribution_id.strip()
        if "/" in candidate:
            parsed = urlparse(candidate)
            candidate = Path(unquote(parsed.path)).name
        candidate = Path(candidate).name
        if candidate:
            return candidate

    download_url = str(case.get("download_url") or "").strip()
    if download_url:
        parsed = urlparse(download_url)
        candidate = Path(unquote(parsed.path)).name
        if candidate:
            return candidate
    return f"source-{_hash(download_url or json.dumps(case, sort_keys=True))}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-time generator for reference BDF CSV files from Zenodo registry/record sources."
    )
    parser.add_argument("--registry-url", default=DEFAULT_REGISTRY_URL)
    parser.add_argument("--record-api-url", default=DEFAULT_RECORD_API_URL)
    parser.add_argument("--cache-dir", default=str((REPO_ROOT / ".pytest_cache" / "bdf_registry").resolve()))
    parser.add_argument("--output-dir", default=str((REPO_ROOT / "docs" / "examples" / "reference").resolve()))
    parser.add_argument("--max-items", type=int, default=0, help="Maximum number of registry distributions (0=all).")
    parser.add_argument("--max-record-files", type=int, default=0, help="Maximum number of record files (0=all).")
    parser.add_argument("--max-download-mib", type=int, default=200)
    parser.add_argument("--vendor-filter", default="", help="Regex against provider id tail.")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--offline", action="store_true", help="Use only cached metadata/files.")
    parser.add_argument("--include-record-files", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--human", action="store_true", help="Write human-prefLabel headers instead of machine labels.")
    parser.add_argument("--fail-fast", action="store_true")
    return parser.parse_args()


def main() -> int:
    import bdf

    args = parse_args()
    cache_dir = Path(args.cache_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    registry = _fetch_registry(cache_dir, args.registry_url, timeout=args.timeout, offline=args.offline)
    cases = _iter_registry_cases(registry, max_items=args.max_items, vendor_filter=args.vendor_filter)

    if args.include_record_files:
        record_files = _fetch_record_files(
            cache_dir,
            args.record_api_url,
            timeout=args.timeout,
            offline=args.offline,
            max_items=args.max_record_files,
        )
        for item in record_files:
            cases.append(
                {
                    "source": "record",
                    "dataset_id": None,
                    "dataset_identifier": None,
                    "distribution_id": item.get("key"),
                    "download_url": item["download_url"],
                    "plugin_slug": None,
                    "provider_id": None,
                    "dataset_keywords": None,
                    "filename_hint": item.get("key"),
                }
            )

    # De-duplicate by URL while preserving order.
    deduped: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for case in cases:
        url = str(case.get("download_url") or "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        deduped.append(case)
    cases = deduped

    results: list[CaseResult] = []
    for case in cases:
        download_url = str(case["download_url"])
        filename_hint = case.get("filename_hint")
        try:
            local_file = _download_file(
                cache_dir,
                download_url,
                timeout=args.timeout,
                max_download_mib=args.max_download_mib,
                offline=args.offline,
                filename_hint=filename_hint,
            )
            plugin = case.get("plugin_slug")
            if not plugin:
                plugin = _infer_plugin_from_filename(
                    case.get("provider_id"),
                    local_file,
                    dataset_identifier=case.get("dataset_identifier"),
                    keywords=case.get("dataset_keywords"),
                )

            source_name = _source_name_for_case(case)
            out_path = _output_path(output_dir, source_name=source_name)
            if out_path.exists() and not args.overwrite:
                results.append(
                    CaseResult(
                        source=str(case.get("source")),
                        download_url=download_url,
                        output_path=str(out_path),
                        status="skipped_exists",
                        plugin=plugin,
                    )
                )
                continue

            df, _ = bdf.read(local_file, plugin=plugin, validate=False)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            bdf.save(df, out_path, labels="preferred" if args.human else "machine")
            results.append(
                CaseResult(
                    source=str(case.get("source")),
                    download_url=download_url,
                    output_path=str(out_path),
                    status="ok",
                    plugin=plugin,
                    rows=int(len(df)),
                    cols=int(len(df.columns)),
                )
            )
            print(f"[ok] {download_url} -> {out_path}")
        except Exception as exc:
            results.append(
                CaseResult(
                    source=str(case.get("source")),
                    download_url=download_url,
                    output_path=None,
                    status="failed",
                    plugin=case.get("plugin_slug"),
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            print(f"[failed] {download_url}: {type(exc).__name__}: {exc}")
            if args.fail_fast:
                break

    ok = sum(1 for r in results if r.status == "ok")
    failed = sum(1 for r in results if r.status == "failed")
    skipped = sum(1 for r in results if r.status == "skipped_exists")

    manifest = {
        "registry_url": args.registry_url,
        "record_api_url": args.record_api_url,
        "output_dir": str(output_dir),
        "cache_dir": str(cache_dir),
        "human_headers": bool(args.human),
        "summary": {"total": len(results), "ok": ok, "failed": failed, "skipped_exists": skipped},
        "results": [asdict(r) for r in results],
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote manifest: {manifest_path}")
    print(f"Summary: ok={ok} failed={failed} skipped_exists={skipped}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
