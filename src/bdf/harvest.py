from __future__ import annotations

import os
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


def _default_cache_dir() -> Path:
    env = os.getenv("BDF_CRAWL_CACHE")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".bdf" / "crawl"


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"}


def _parse_github_tree(url: str) -> tuple[str, str, str, str] | None:
    match = re.match(
        r"^https?://github\.com/(?P<org>[^/]+)/(?P<repo>[^/]+)"
        r"(?:/tree/(?P<branch>[^/]+)(?:/(?P<path>.*))?)?$",
        url,
    )
    if not match:
        return None
    org = match.group("org")
    repo = match.group("repo")
    branch = match.group("branch") or "main"
    subpath = match.group("path") or ""
    return org, repo, branch, subpath


def _download_github_repo(url: str, cache_dir: Path, refresh: bool) -> Path:
    parsed = _parse_github_tree(url)
    if not parsed:
        raise ValueError(f"Unsupported GitHub URL: {url}")
    org, repo, branch, subpath = parsed
    slug = f"{org}-{repo}-{branch}"
    zip_name = f"{slug}.zip"
    zip_path = cache_dir / zip_name
    extract_root = cache_dir / slug

    if refresh:
        if zip_path.exists():
            zip_path.unlink()
        if extract_root.exists():
            shutil.rmtree(extract_root)

    if not zip_path.exists():
        zip_url = f"https://github.com/{org}/{repo}/archive/refs/heads/{branch}.zip"
        resp = requests.get(zip_url, timeout=60)
        resp.raise_for_status()
        zip_path.write_bytes(resp.content)

    if not extract_root.exists():
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_root)

    extracted_dirs = [p for p in extract_root.iterdir() if p.is_dir()]
    if not extracted_dirs:
        raise FileNotFoundError(f"No extracted repo found in {extract_root}")
    repo_root = extracted_dirs[0]
    return repo_root / subpath if subpath else repo_root


def _resolve_source(source: str, cache_dir: Path, refresh: bool) -> Path:
    path = Path(source)
    if path.exists():
        return path.resolve()
    if _is_url(source):
        if "github.com" in source:
            return _download_github_repo(source, cache_dir, refresh)
        raise ValueError(f"Unsupported source URL: {source}")
    raise FileNotFoundError(source)


def _find_collection_roots(root: Path) -> list[Path]:
    if (root / "collection.json").exists():
        return [root]
    return sorted({p.parent for p in root.rglob("collection.json")})


def harvest(
    root: str | Path,
    *,
    layout: str = "nested",
    format: str = "parquet",
    recursive: bool = False,
    validate_existing: bool = True,
    validate_converted: bool = True,
    include_optional: bool = True,
    plugin: str | None = None,
    incremental: bool = True,
    force: bool = False,
    raise_on_error: bool = False,
) -> dict[str, Any]:
    """
    Discover collection roots and run ingest() for each.
    """
    from . import ingest  # lazy import to avoid cycles

    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(root_path)

    collection_roots = _find_collection_roots(root_path)
    if not collection_roots:
        raise FileNotFoundError("No collection.json found under root.")

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for collection_root in collection_roots:
        try:
            summary = ingest(
                collection_root,
                out_dir=collection_root,
                format=format,
                layout=layout,
                recursive=recursive,
                validate_existing=validate_existing,
                validate_converted=validate_converted,
                include_optional=include_optional,
                plugin=plugin,
                incremental=incremental,
                force=force,
                raise_on_error=raise_on_error,
            )
            results.append({"path": str(collection_root), "summary": summary})
        except Exception as exc:
            errors.append({"path": str(collection_root), "error": str(exc)})
            if raise_on_error:
                raise

    return {"roots": results, "errors": errors}


def crawl(
    sources: str | list[str],
    *,
    refresh: bool = False,
    cache_dir: str | Path | None = None,
    **harvest_kwargs: Any,
) -> dict[str, Any]:
    """
    Resolve remote or local sources, then harvest each.
    """
    sources_list = [sources] if isinstance(sources, str) else list(sources)
    cache_root = _ensure_dir(Path(cache_dir) if cache_dir else _default_cache_dir())

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for source in sources_list:
        try:
            resolved = _resolve_source(source, cache_root, refresh)
            summary = harvest(resolved, **harvest_kwargs)
            results.append({"source": source, "path": str(resolved), "summary": summary})
        except Exception as exc:
            errors.append({"source": source, "error": str(exc)})

    return {"sources": results, "errors": errors}

