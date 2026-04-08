from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

import requests

_GRAPH_CACHE: dict[str, Any] = {}

# ---------------------------------------------------------------------------
# Well-known source aliases
# ---------------------------------------------------------------------------

_SOURCE_ALIASES: dict[str, str] = {
    "bdf-datastore": "https://github.com/battery-data-alliance/bdf-datastore",
}


# ---------------------------------------------------------------------------
# User-managed sources (persisted to ~/.bdf/sources.json)
# ---------------------------------------------------------------------------

def _sources_config_path() -> Path:
    return Path.home() / ".bdf" / "sources.json"


def _load_user_sources() -> dict[str, str]:
    p = _sources_config_path()
    if not p.exists():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if isinstance(v, str)}
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_user_sources(sources: dict[str, str]) -> None:
    p = _sources_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(sources, f, indent=2)


def _all_sources() -> dict[str, str]:
    """Return built-in aliases merged with user-configured sources."""
    merged = dict(_SOURCE_ALIASES)
    merged.update(_load_user_sources())
    return merged


def add_registry(name: str, url: str) -> None:
    """Add a persistent registry source.

    The source is saved to ~/.bdf/sources.json and will appear in
    future calls to ``registries()`` and ``search()``.
    """
    sources = _load_user_sources()
    sources[name] = url
    _save_user_sources(sources)


def remove_registry(name: str) -> None:
    """Remove a user-added registry source.

    Built-in sources (like 'bdf-datastore') cannot be removed.
    """
    if name in _SOURCE_ALIASES:
        raise ValueError(f"Cannot remove built-in source {name!r}")
    sources = _load_user_sources()
    if name not in sources:
        raise KeyError(f"No user source named {name!r}")
    del sources[name]
    _save_user_sources(sources)


@dataclass
class ResolvedSource:
    """A source resolved to a local path, with optional GitHub origin info."""
    local_path: Path
    github_info: Optional[tuple[str, str, str, str]] = None  # (org, repo, branch, subpath)


def _parse_json_or_none(value: Any) -> Any:
    """Parse a JSON string into a Python object, or return the value as-is."""
    if not isinstance(value, str):
        return value
    s = value.strip()
    if s.startswith("[") or s.startswith("{"):
        try:
            return json.loads(s)
        except (json.JSONDecodeError, ValueError):
            pass
    return value


class DatasetResult:
    """A single search result from the registry.

    Provides clean attribute access with parsed JSON fields,
    and convenience methods for loading and plotting data.
    """

    # Fields parsed from JSON strings into Python lists
    _JSON_FIELDS = frozenset({
        "materials", "pe_materials", "ne_materials",
        "battery_ids", "methods", "keywords",
    })

    def __init__(self, row: dict[str, Any]) -> None:
        self._raw = dict(row)
        # Parse JSON string fields into real Python objects
        for key in self._JSON_FIELDS:
            if key in self._raw:
                self._raw[key] = _parse_json_or_none(self._raw[key])
        # Parse url into a urls list
        url_val = self._raw.get("url")
        if url_val is None:
            self._urls: list[str] = []
        elif isinstance(url_val, str) and url_val.strip().startswith("["):
            self._urls = json.loads(url_val)
        else:
            self._urls = [url_val] if url_val else []

    # --- Attribute access ---

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._raw[name]
        except KeyError:
            raise AttributeError(f"DatasetResult has no field {name!r}") from None

    def __getitem__(self, key: str) -> Any:
        return self._raw[key]

    def __contains__(self, key: str) -> bool:
        return key in self._raw

    def get(self, key: str, default: Any = None) -> Any:
        return self._raw.get(key, default)

    @property
    def urls(self) -> list[str]:
        """Parsed list of data file URLs."""
        return list(self._urls)

    # --- Actions ---

    def load(self, index: int = 0, **kwargs: Any) -> "pd.DataFrame":
        """Fetch and read a data file. Use index to select which file (default: first)."""
        import bdf
        if not self._urls:
            raise ValueError(f"No data files for {self.dataset_id}")
        if index >= len(self._urls):
            raise IndexError(f"File index {index} out of range (have {len(self._urls)} files)")
        return bdf.read(self._urls[index], **kwargs)

    def load_all(self, **kwargs: Any) -> "list[pd.DataFrame]":
        """Fetch and read all data files for this dataset."""
        return [self.load(i, **kwargs) for i in range(len(self._urls))]

    def plot(self, index: int = 0, **kwargs: Any) -> Any:
        """Load and plot a data file."""
        import bdf
        df = self.load(index)
        defaults = {
            "xdata": "Test Time / s",
            "xunit": "h",
            "ydata": ["Voltage / V"],
            "yydata": "Current / A",
        }
        defaults.update(kwargs)
        return bdf.plot(df, **defaults)

    # --- Display ---

    def __repr__(self) -> str:
        parts = [f"DatasetResult({self._raw.get('dataset_id', '?')!r}"]
        for key in ("manufacturer", "model", "chemistry", "form_factor"):
            val = self._raw.get(key)
            if val:
                parts.append(f"  {key}={val!r}")
        cap = self._raw.get("rated_capacity_ah")
        if cap is not None:
            parts.append(f"  rated_capacity_ah={cap}")
        volt = self._raw.get("nominal_voltage_v")
        if volt is not None:
            parts.append(f"  nominal_voltage_v={volt}")
        parts.append(f"  files={len(self._urls)}")
        return ",\n".join(parts) + ")"

    def keys(self) -> list[str]:
        return list(self._raw.keys())

    def to_dict(self) -> dict[str, Any]:
        """Return the parsed result as a plain dictionary."""
        d = dict(self._raw)
        d["url"] = self._urls
        return d


def _require_rdflib():
    try:
        import rdflib  # type: ignore
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "Linked-data registry requires rdflib. "
            "Install with `pip install batterydf`."
        ) from exc
    return rdflib


def _normalize_schema_org(graph: Any) -> Any:
    rdflib = _require_rdflib()
    schema_http = "http://schema.org/"
    schema_https = "https://schema.org/"

    def _norm_term(term: Any):
        if isinstance(term, rdflib.URIRef):
            value = str(term)
            if value.startswith(schema_http):
                return rdflib.URIRef(schema_https + value[len(schema_http):])
        return term

    normalized = rdflib.Graph()
    for prefix, namespace in graph.namespace_manager.namespaces():
        if str(namespace) == schema_http:
            normalized.namespace_manager.bind(prefix, rdflib.Namespace(schema_https), replace=True)
        else:
            normalized.namespace_manager.bind(prefix, namespace, replace=True)

    for s, p, o in graph:
        normalized.add((_norm_term(s), _norm_term(p), _norm_term(o)))
    return normalized


def _default_registry_dir() -> Path:
    env = os.getenv("BDF_REGISTRY_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".bdf" / "registry"


def _strip_all_suffixes(name: str) -> str:
    base = Path(name).name
    while True:
        suffix = Path(base).suffix
        if not suffix:
            break
        base = Path(base).stem
    return base


def _registry_key_from_url(url: str) -> Optional[str]:
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if not parsed.path:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None
    if "battery-data" in parts:
        idx = parts.index("battery-data") + 1
        parts = parts[idx:]
    if parts and parts[-2:-1] == ["data"]:
        parts = parts[:-2] + [parts[-1]]
    filename = parts[-1]
    stem = _strip_all_suffixes(filename)
    if not stem:
        return None
    prefix = parts[:-1]
    if prefix and prefix[-1] == "data":
        prefix = prefix[:-1]
    key_parts = prefix + [stem] if prefix else [stem]
    return "/".join(key_parts).lower()


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"}


def _parse_github_tree(url: str) -> Optional[tuple[str, str, str, str]]:
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


def _download_github_repo(
    url: str, cache_dir: Path, refresh: bool
) -> ResolvedSource:
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

    # GitHub zip extracts to <repo>-<branch>/...
    extracted_dirs = [p for p in extract_root.iterdir() if p.is_dir()]
    if not extracted_dirs:
        raise FileNotFoundError(f"No extracted repo found in {extract_root}")
    repo_root = extracted_dirs[0]
    local = repo_root / subpath if subpath else repo_root
    return ResolvedSource(local_path=local, github_info=(org, repo, branch, subpath))


def _resolve_source(source: str, cache_dir: Path, refresh: bool) -> ResolvedSource:
    # Check aliases (built-in + user-configured)
    all_src = _all_sources()
    source = all_src.get(source, source)

    path = Path(source)
    if path.exists():
        return ResolvedSource(local_path=path.resolve())
    if _is_url(source):
        if "github.com" in source:
            return _download_github_repo(source, cache_dir, refresh)
        raise ValueError(f"Unsupported source URL: {source}")
    raise FileNotFoundError(source)


def _iter_metadata_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("metadata.*"):
        if not path.is_file():
            continue
        suffix = "".join(path.suffixes).lower()
        if suffix.endswith(".jsonld") or suffix.endswith(".json") or suffix.endswith(".ttl"):
            yield path


# ---------------------------------------------------------------------------
# Datastore convention scanners
# ---------------------------------------------------------------------------

def _iter_battery_json_files(root: Path) -> Iterable[Path]:
    """Yield battery.json files at depth 2: {contributor}/{cell}/battery.json."""
    for path in root.rglob("battery.json"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if len(rel.parts) == 3 and rel.parts[2] == "battery.json":
            yield path


def _iter_datastore_jsonld_files(root: Path) -> Iterable[Path]:
    """Yield JSON-LD test metadata files in the datastore tree.

    These are .json files inside data-type directories (timeseries/, eis/)
    that contain an ``@type`` key, indicating JSON-LD content.
    """
    for battery_json in _iter_battery_json_files(root):
        cell_dir = battery_json.parent
        for data_type in ("timeseries", "eis"):
            dt_dir = cell_dir / data_type
            if not dt_dir.is_dir():
                continue
            for path in sorted(dt_dir.glob("*.json")):
                if not path.is_file():
                    continue
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict) and "@type" in data:
                        yield path
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue


def _extract_datastore_rows(
    battery_json_files: list[Path],
    github_info: Optional[tuple[str, str, str, str]],
    repo_root: Path,
) -> list[dict[str, Any]]:
    """Convert datastore battery.json files into registry dataset rows."""
    rows: list[dict[str, Any]] = []
    for bjson in battery_json_files:
        try:
            with open(bjson, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        spec = data.get("spec", {})
        ids = data.get("ids", [])

        # contributor/cell_name from path
        rel = bjson.relative_to(repo_root)
        contributor = rel.parts[0]
        cell_name = rel.parts[1]
        cell_dir = bjson.parent

        # Collect processed data file paths
        data_files: list[Path] = []
        for subdir in ("timeseries/processed", "eis/processed"):
            d = cell_dir / Path(subdir)
            if d.is_dir():
                data_files.extend(sorted(d.glob("*.bdf.csv")))

        # Build URLs
        # Use media.githubusercontent.com for GitHub repos (handles LFS files)
        urls: list[str] = []
        for df_path in data_files:
            if github_info:
                org, repo, branch, subpath = github_info
                rel_to_root = df_path.relative_to(repo_root)
                url = f"https://media.githubusercontent.com/media/{org}/{repo}/{branch}/{rel_to_root}"
            else:
                url = str(df_path.resolve())
            urls.append(url)

        dataset_id = f"{contributor}/{cell_name}"
        dataset_uri = f"datastore:{dataset_id}"

        pe_mats = spec.get("pe_materials", [])
        ne_mats = spec.get("ne_materials", [])
        all_mats = sorted(set(pe_mats + ne_mats))

        row = {
            "dataset_uri": dataset_uri,
            "dataset_id": dataset_id,
            "title": cell_name,
            "description": None,
            "url": json.dumps(urls) if len(urls) > 1 else (urls[0] if urls else None),
            "license": None,
            "methods": None,
            "keywords": json.dumps([contributor]),
            "chemistry": spec.get("chemistry"),
            "materials": json.dumps(all_mats) if all_mats else None,
            "pe_materials": json.dumps(pe_mats) if pe_mats else None,
            "ne_materials": json.dumps(ne_mats) if ne_mats else None,
            "form_factor": spec.get("form_factor"),
            "manufacturer": spec.get("manufacturer"),
            "model": spec.get("model"),
            "battery_ids": json.dumps(ids) if ids else None,
            "rated_capacity_ah": spec.get("rated_capacity_ah"),
            "rated_energy_wh": spec.get("rated_energy_wh"),
            "nominal_voltage_v": spec.get("nominal_voltage_v"),
            "mass_g": spec.get("mass_g"),
            "volume_l": spec.get("volume_l"),
        }
        rows.append(row)
    return rows


def _guess_format(path: Path) -> str:
    suffix = "".join(path.suffixes).lower()
    if suffix.endswith(".ttl"):
        return "turtle"
    return "json-ld"


def _local_name(term: Any) -> str:
    text = str(term)
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    if "/" in text:
        return text.rsplit("/", 1)[-1]
    return text


def _literal_text(obj: Any) -> Optional[str]:
    try:
        from rdflib.term import Literal  # type: ignore
    except Exception:
        return None
    if isinstance(obj, Literal):
        return str(obj)
    return None


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _extract_graph(registry_dir: Path) -> Any:
    ttl_path = registry_dir / "registry.ttl"
    if not ttl_path.exists():
        raise FileNotFoundError(f"{ttl_path} not found. Run build_registry() first.")
    cache_key = str(ttl_path.resolve())
    if cache_key in _GRAPH_CACHE:
        return _GRAPH_CACHE[cache_key]
    rdflib = _require_rdflib()
    graph = rdflib.Graph()
    graph.parse(ttl_path, format="turtle")
    _GRAPH_CACHE[cache_key] = graph
    return graph


def build_registry(
    sources: str | list[str],
    registry_dir: str | Path | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    sources_list = [sources] if isinstance(sources, str) else list(sources)

    reg_dir = _ensure_dir(Path(registry_dir) if registry_dir else _default_registry_dir())
    cache_dir = _ensure_dir(reg_dir / "sources")

    graph = None  # lazy — only created when metadata files are found
    metadata_files: list[Path] = []
    parse_errors: list[dict[str, str]] = []
    all_datastore_rows: list[dict[str, Any]] = []
    all_battery_jsons: list[Path] = []

    for source in sources_list:
        resolved = _resolve_source(source, cache_dir, refresh)
        root = resolved.local_path

        # --- JSON-LD / TTL metadata path (existing) ---
        for path in _iter_metadata_files(root):
            if graph is None:
                rdflib = _require_rdflib()
                graph = rdflib.Graph()
            fmt = _guess_format(path)
            try:
                abs_path = path.resolve()
                graph.parse(abs_path, format=fmt, publicID=abs_path.as_uri())
                metadata_files.append(path)
            except Exception as exc:
                parse_errors.append({"path": str(path), "error": str(exc)})

        # --- Datastore convention path (new) ---
        battery_jsons = list(_iter_battery_json_files(root))
        if battery_jsons:
            all_battery_jsons.extend(battery_jsons)
            ds_rows = _extract_datastore_rows(
                battery_jsons, resolved.github_info, root,
            )
            all_datastore_rows.extend(ds_rows)

            # Also ingest any JSON-LD test metadata files from the datastore
            for path in _iter_datastore_jsonld_files(root):
                if graph is None:
                    rdflib = _require_rdflib()
                    graph = rdflib.Graph()
                try:
                    abs_path = path.resolve()
                    graph.parse(abs_path, format="json-ld", publicID=abs_path.as_uri())
                    metadata_files.append(path)
                except Exception as exc:
                    parse_errors.append({"path": str(path), "error": str(exc)})

    # --- Combine and write outputs ---
    dataset_rows: list[dict[str, Any]] = []
    ttl_path = reg_dir / "registry.ttl"

    if graph is not None:
        graph = _normalize_schema_org(graph)
        ttl_path.write_text(graph.serialize(format="turtle"), encoding="utf-8")
        dataset_rows = _extract_dataset_rows(graph)
    else:
        # Write empty TTL so sparql() doesn't error on missing file
        ttl_path.write_text("", encoding="utf-8")

    dataset_rows.extend(all_datastore_rows)

    db_path = reg_dir / "registry.db"
    _write_registry_db(db_path, dataset_rows)

    return {
        "registry_dir": str(reg_dir),
        "sources": sources_list,
        "datasets": len(dataset_rows),
        "metadata_files": len(metadata_files),
        "battery_json_files": len(all_battery_jsons),
        "errors": len(parse_errors),
    }


def sparql(query: str, registry_dir: str | Path | None = None) -> list[dict[str, str]]:
    reg_dir = Path(registry_dir) if registry_dir else _default_registry_dir()
    graph = _extract_graph(reg_dir)
    results = []
    for row in graph.query(query):
        row_dict = {str(key): str(value) for key, value in row.asdict().items()}
        results.append(row_dict)
    if results:
        return results

    if "schema.org" not in query:
        return results

    alt_query = query
    if "https://schema.org/" in alt_query:
        alt_query = alt_query.replace("https://schema.org/", "http://schema.org/")
    elif "http://schema.org/" in alt_query:
        alt_query = alt_query.replace("http://schema.org/", "https://schema.org/")
    else:
        return results

    for row in graph.query(alt_query):
        row_dict = {str(key): str(value) for key, value in row.asdict().items()}
        results.append(row_dict)
    return results


def _default_sources() -> list[str]:
    """Return all source names (built-in + user-configured)."""
    return list(_all_sources().keys())


def _count_values(rows: list, col: str) -> dict[str, int]:
    """Count occurrences of each value in a column."""
    counts: dict[str, int] = {}
    for row in rows:
        val = row[col]
        if val is None:
            continue
        # Handle JSON arrays stored as strings
        if isinstance(val, str) and val.startswith("["):
            try:
                items = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                items = [val]
        else:
            items = [val]
        for item in items:
            item = str(item).strip()
            if item:
                counts[item] = counts.get(item, 0) + 1
    return counts


def _format_counts(counts: dict[str, int], max_items: int = 6) -> str:
    """Format a dict of counts into a compact string."""
    sorted_items = sorted(counts.items(), key=lambda x: -x[1])
    parts = [f"{k} ({v})" for k, v in sorted_items[:max_items]]
    if len(sorted_items) > max_items:
        parts.append(f"... +{len(sorted_items) - max_items} more")
    return ", ".join(parts)


class RegistryInfo:
    """Display-friendly info about a single registry source."""

    def __init__(self, name: str, url: str, cells: int = 0,
                 contributors: dict[str, int] | None = None,
                 summary: dict[str, dict[str, int]] | None = None) -> None:
        self.name = name
        self.url = url
        self.cells = cells
        self.contributors = contributors or {}
        self.summary = summary or {}

    def __repr__(self) -> str:
        lines = [f"  {self.name}"]
        lines.append(f"    url:   {self.url}")
        if self.cells:
            lines.append(f"    cells: {self.cells}")
        if self.contributors:
            for contrib, count in sorted(self.contributors.items()):
                lines.append(f"      {contrib}: {count}")
        for label, counts in self.summary.items():
            if counts:
                lines.append(f"    {label}: {_format_counts(counts)}")
        return "\n".join(lines)


class RegistryList:
    """Display-friendly list of available registry sources."""

    def __init__(self, entries: list[RegistryInfo]) -> None:
        self._entries = entries

    def __repr__(self) -> str:
        if not self._entries:
            return "No registries configured."
        header = f"Battery Data Registries ({len(self._entries)})"
        sep = "─" * len(header)
        parts = [header, sep]
        for entry in self._entries:
            parts.append(repr(entry))
        return "\n".join(parts)

    def __iter__(self):
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __getitem__(self, key):
        if isinstance(key, str):
            for e in self._entries:
                if e.name == key:
                    return e
            raise KeyError(key)
        return self._entries[key]


def registries(registry_dir: str | Path | None = None) -> RegistryList:
    """Return the available built-in registry sources with summary stats."""
    reg_dir = Path(registry_dir) if registry_dir else _default_registry_dir()
    db_path = reg_dir / "registry.db"

    entries: list[RegistryInfo] = []
    for name, url in _all_sources().items():
        cells = 0
        contributors: list[str] = []

        if db_path.exists():
            try:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM datasets WHERE dataset_uri LIKE 'datastore:%'"
                ).fetchall()
                contrib_counts: dict[str, int] = {}
                for row in rows:
                    did = row["dataset_id"] or ""
                    if "/" in did:
                        contrib = did.split("/")[0]
                        contrib_counts[contrib] = contrib_counts.get(contrib, 0) + 1
                cells = len(rows)
                contributors = contrib_counts
                summary = {}
                for label, col in [
                    ("chemistries", "chemistry"),
                    ("form factors", "form_factor"),
                    ("manufacturers", "manufacturer"),
                ]:
                    counts = _count_values(rows, col)
                    if counts:
                        summary[label] = counts
                # Capacity range
                caps = [r["rated_capacity_ah"] for r in rows if r["rated_capacity_ah"] is not None]
                if caps:
                    summary["capacity"] = {f"{min(caps):.1f} – {max(caps):.1f} Ah": len(caps)}
                conn.close()
            except Exception:
                pass

        entries.append(RegistryInfo(name, url, cells, contributors, summary))
    return RegistryList(entries)


def search(
    query: str = "",
    registry_dir: str | Path | None = None,
    limit: int = 50,
) -> list[DatasetResult]:
    reg_dir = Path(registry_dir) if registry_dir else _default_registry_dir()
    db_path = reg_dir / "registry.db"
    if not db_path.exists():
        build_registry(_default_sources(), registry_dir=str(reg_dir))

    tokens, numeric_filters = _parse_search_query(query)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = _search_db(conn, tokens, numeric_filters, limit)
    finally:
        conn.close()
    return [DatasetResult(dict(row)) for row in rows]


def _parse_search_query(query: str) -> tuple[list[str], list[tuple[str, str, float]]]:
    text = query or ""
    numeric_filters: list[tuple[str, str, float]] = []

    numeric_pattern = re.compile(
        r"(>=|<=|=|<|>)\s*([0-9]*\.?[0-9]+)\s*([a-zA-Z]+)?"
    )
    unit_map = {
        "ah": "rated_capacity_ah",
        "wh": "rated_energy_wh",
        "v": "nominal_voltage_v",
        "g": "mass_g",
        "kg": "mass_g",
        "l": "volume_l",
    }

    def _strip_match(match: re.Match[str]) -> str:
        op = match.group(1)
        value = match.group(2)
        unit = (match.group(3) or "").lower()
        column = unit_map.get(unit)
        if column:
            numeric_filters.append((column, op, float(value)))
            return ""
        return match.group(0)

    text = numeric_pattern.sub(_strip_match, text)
    tokens = [t.strip().lower() for t in re.split(r"[\s,;]+", text) if t.strip()]
    return tokens, numeric_filters


def _search_db(
    conn: sqlite3.Connection,
    tokens: list[str],
    numeric_filters: list[tuple[str, str, float]],
    limit: int,
) -> list[sqlite3.Row]:
    conditions = []
    params: list[Any] = []

    for col, op, value in numeric_filters:
        conditions.append(f"d.{col} {op} ?")
        params.append(value)

    if tokens:
        placeholders = ",".join("?" for _ in tokens)
        params_terms = list(tokens)
        params_limit = list(params)
        params = params_terms + params_limit
        sql = f"""
            SELECT d.*
            FROM datasets d
            JOIN dataset_terms t ON d.dataset_uri = t.dataset_uri
            WHERE t.term IN ({placeholders})
        """
        if conditions:
            sql += " AND " + " AND ".join(conditions)
        sql += """
            GROUP BY d.dataset_uri
            HAVING COUNT(DISTINCT t.term) = ?
            LIMIT ?
        """
        params.extend([len(tokens), limit])
        return list(conn.execute(sql, params))

    sql = "SELECT d.* FROM datasets d"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " LIMIT ?"
    params.append(limit)
    return list(conn.execute(sql, params))


def _write_registry_db(db_path: Path, rows: list[dict[str, Any]]) -> None:
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE datasets (
                dataset_uri TEXT PRIMARY KEY,
                dataset_id TEXT,
                title TEXT,
                description TEXT,
                url TEXT,
                license TEXT,
                methods TEXT,
                keywords TEXT,
                chemistry TEXT,
                materials TEXT,
                pe_materials TEXT,
                ne_materials TEXT,
                form_factor TEXT,
                manufacturer TEXT,
                model TEXT,
                battery_ids TEXT,
                rated_capacity_ah REAL,
                rated_energy_wh REAL,
                nominal_voltage_v REAL,
                mass_g REAL,
                volume_l REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE dataset_terms (
                dataset_uri TEXT,
                term TEXT,
                kind TEXT
            )
            """
        )
        conn.execute("CREATE INDEX idx_terms_term ON dataset_terms(term)")
        conn.execute("CREATE INDEX idx_terms_dataset ON dataset_terms(dataset_uri)")

        for row in rows:
            conn.execute(
                """
                INSERT INTO datasets (
                    dataset_uri, dataset_id, title, description, url, license,
                    methods, keywords, chemistry, materials, pe_materials, ne_materials,
                    form_factor, manufacturer, model, battery_ids,
                    rated_capacity_ah, rated_energy_wh, nominal_voltage_v,
                    mass_g, volume_l
                ) VALUES (
                    :dataset_uri, :dataset_id, :title, :description, :url, :license,
                    :methods, :keywords, :chemistry, :materials, :pe_materials, :ne_materials,
                    :form_factor, :manufacturer, :model, :battery_ids,
                    :rated_capacity_ah, :rated_energy_wh, :nominal_voltage_v,
                    :mass_g, :volume_l
                )
                """,
                row,
            )

            for term, kind in _row_terms(row):
                conn.execute(
                    "INSERT INTO dataset_terms (dataset_uri, term, kind) VALUES (?, ?, ?)",
                    (row["dataset_uri"], term, kind),
                )
        conn.commit()
    finally:
        conn.close()


def _row_terms(row: dict[str, Any]) -> list[tuple[str, str]]:
    terms: list[tuple[str, str]] = []
    for key in ("keywords", "methods", "chemistry", "materials", "pe_materials", "ne_materials"):
        values = _split_terms(row.get(key))
        for value in values:
            terms.append((value, key))
    for key in ("form_factor", "manufacturer", "model"):
        values = _split_terms(row.get(key))
        for value in values:
            terms.append((value, key))
    for key in ("battery_ids", "dataset_id"):
        values = _split_terms(row.get(key))
        for value in values:
            terms.append((value, key))
    return terms


def _split_terms(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        text = str(value)
        if text.startswith("[") and text.endswith("]"):
            try:
                items = json.loads(text)
            except Exception:
                items = [text]
        else:
            items = [text]
    out: list[str] = []
    for item in items:
        if item is None:
            continue
        for token in re.split(r"[\s,;/]+", str(item)):
            token = token.strip().lower()
            if token:
                out.append(token)
    return out


def _extract_dataset_rows(graph: Any) -> list[dict[str, Any]]:
    rdflib = _require_rdflib()
    rdf = rdflib.RDF
    rdfs = rdflib.RDFS
    schema = rdflib.Namespace("https://schema.org/")
    schema_http = rdflib.Namespace("http://schema.org/")

    dataset_types = {schema.Dataset, schema_http.Dataset}
    dataset_nodes = set()
    for dtype in dataset_types:
        dataset_nodes.update(graph.subjects(rdf.type, dtype))

    rows: list[dict[str, Any]] = []
    for node in dataset_nodes:
        row = _extract_dataset_row(graph, node, schema, schema_http, rdfs)
        if row:
            rows.append(row)
    return rows


def _extract_dataset_row(graph: Any, node: Any, schema: Any, schema_http: Any, rdfs: Any) -> dict[str, Any]:
    dataset_uri = str(node)

    def _values(pred: Any) -> list[Any]:
        return list(graph.objects(node, pred))

    def _first_literal(preds: list[Any]) -> Optional[str]:
        for pred in preds:
            for obj in graph.objects(node, pred):
                lit = _literal_text(obj)
                if lit is not None:
                    return lit
        return None

    dataset_id = _first_literal([schema.identifier, schema_http.identifier]) or dataset_uri
    title = _first_literal([schema.name, schema_http.name])
    description = _first_literal([schema.description, schema_http.description])
    license_value = _first_literal([schema.license, schema_http.license])

    methods = _literal_list(
        graph,
        node,
        [schema.measurementTechnique, schema_http.measurementTechnique,
         schema.measurementMethod, schema_http.measurementMethod],
    )
    keywords = _literal_list(graph, node, [schema.keywords, schema_http.keywords])

    distributions = []
    for pred in (schema.distribution, schema_http.distribution):
        distributions.extend(graph.objects(node, pred))
    urls = []
    for dist in distributions:
        for pred in (schema.contentUrl, schema_http.contentUrl, schema.url, schema_http.url):
            for obj in graph.objects(dist, pred):
                lit = _literal_text(obj)
                if lit:
                    urls.append(lit)

    url = urls[0] if urls else None

    battery_nodes = []
    for pred in (schema.about, schema_http.about):
        battery_nodes.extend(graph.objects(node, pred))

    battery_info = _extract_battery_info(graph, battery_nodes, schema, schema_http, rdfs)

    return {
        "dataset_uri": dataset_uri,
        "dataset_id": dataset_id,
        "title": title,
        "description": description,
        "url": url,
        "license": license_value,
        "methods": json.dumps(methods) if methods else None,
        "keywords": json.dumps(keywords) if keywords else None,
        "chemistry": battery_info.get("chemistry") if battery_info else None,
        "materials": json.dumps(battery_info.get("materials") or []) if battery_info else None,
        "pe_materials": json.dumps(battery_info.get("pe_materials") or []) if battery_info else None,
        "ne_materials": json.dumps(battery_info.get("ne_materials") or []) if battery_info else None,
        "form_factor": battery_info.get("form_factor") if battery_info else None,
        "manufacturer": battery_info.get("manufacturer") if battery_info else None,
        "model": battery_info.get("model") if battery_info else None,
        "battery_ids": json.dumps(battery_info.get("battery_ids") or []) if battery_info else None,
        "rated_capacity_ah": battery_info.get("rated_capacity_ah") if battery_info else None,
        "rated_energy_wh": battery_info.get("rated_energy_wh") if battery_info else None,
        "nominal_voltage_v": battery_info.get("nominal_voltage_v") if battery_info else None,
        "mass_g": battery_info.get("mass_g") if battery_info else None,
        "volume_l": battery_info.get("volume_l") if battery_info else None,
    }


def _literal_list(graph: Any, node: Any, preds: list[Any]) -> list[str]:
    values: list[str] = []
    for pred in preds:
        for obj in graph.objects(node, pred):
            lit = _literal_text(obj)
            if lit is None:
                continue
            for token in _split_keywords(lit):
                values.append(token)
    return values


def _split_keywords(text: str) -> list[str]:
    if "," in text:
        return [t.strip() for t in text.split(",") if t.strip()]
    return [text.strip()] if text.strip() else []


def _extract_battery_info(
    graph: Any,
    battery_nodes: list[Any],
    schema: Any,
    schema_http: Any,
    rdfs: Any,
) -> dict[str, Any]:
    info: dict[str, Any] = {
        "battery_ids": [],
        "materials": [],
        "pe_materials": [],
        "ne_materials": [],
    }

    for battery in battery_nodes:
        identifiers = _literal_list(
            graph,
            battery,
            [schema.identifier, schema_http.identifier],
        )
        for ident in identifiers:
            if ident not in info["battery_ids"]:
                info["battery_ids"].append(ident)

        model = _first_literal_obj(graph, battery, [schema.model, schema_http.model])
        manufacturer = _manufacturer_name(graph, battery, schema, schema_http)
        if model and not info.get("model"):
            info["model"] = model
        if manufacturer and not info.get("manufacturer"):
            info["manufacturer"] = manufacturer

        props = _extract_property_values(graph, battery, schema, schema_http)
        for key, value in props.items():
            if key not in info or info.get(key) is None:
                info[key] = value

        materials = _extract_materials(graph, battery)
        for key in ("materials", "pe_materials", "ne_materials"):
            for item in materials.get(key, []):
                if item not in info[key]:
                    info[key].append(item)

    return info


def _first_literal_obj(graph: Any, node: Any, preds: list[Any]) -> Optional[str]:
    for pred in preds:
        for obj in graph.objects(node, pred):
            lit = _literal_text(obj)
            if lit is not None:
                return lit
    return None


def _manufacturer_name(graph: Any, node: Any, schema: Any, schema_http: Any) -> Optional[str]:
    for pred in (schema.manufacturer, schema_http.manufacturer):
        for obj in graph.objects(node, pred):
            name = _first_literal_obj(graph, obj, [schema.name, schema_http.name])
            if name:
                return name
            lit = _literal_text(obj)
            if lit:
                return lit
    return None


def _extract_property_values(graph: Any, node: Any, schema: Any, schema_http: Any) -> dict[str, Any]:
    rdflib = _require_rdflib()
    rdfs = rdflib.RDFS
    prop_map = {
        "bdf:chemistry": "chemistry",
        "bdf:form_factor": "form_factor",
        "bdf:nominal_voltage_v": "nominal_voltage_v",
        "bdf:rated_capacity_ah": "rated_capacity_ah",
        "bdf:rated_energy_wh": "rated_energy_wh",
        "bdf:mass_g": "mass_g",
        "bdf:volume_l": "volume_l",
    }
    label_map = {
        "nominal voltage": "nominal_voltage_v",
        "rated capacity": "rated_capacity_ah",
        "rated energy": "rated_energy_wh",
        "mass": "mass_g",
        "volume": "volume_l",
    }
    out: dict[str, Any] = {}

    for pred in (schema.additionalProperty, schema_http.additionalProperty):
        for prop in graph.objects(node, pred):
            prop_id = _first_literal_obj(graph, prop, [schema.propertyID, schema_http.propertyID])
            value = _first_literal_obj(graph, prop, [schema.value, schema_http.value])
            if prop_id and value is not None:
                key = prop_map.get(prop_id)
                if key:
                    if key in {"form_factor", "chemistry"}:
                        out[key] = value
                    else:
                        out[key] = _as_float(value)
                elif prop_id == "bdf:form_factor":
                    out["form_factor"] = value

    for pred, prop in graph.predicate_objects(node):
        if _local_name(pred) != "hasProperty":
            continue
        label = _first_literal_obj(graph, prop, [rdfs.label])
        number = None
        for p2, o2 in graph.predicate_objects(prop):
            if _local_name(p2) == "hasNumberValue":
                number = _literal_text(o2)
                break
        if label:
            key = label_map.get(label.strip().lower())
            if key and number is not None:
                out[key] = _as_float(number)

    return out


def _extract_materials(graph: Any, node: Any) -> dict[str, list[str]]:
    pe: list[str] = []
    ne: list[str] = []

    for pred, obj in graph.predicate_objects(node):
        local = _local_name(pred)
        if local == "hasPositiveElectrode":
            pe.extend(_materials_from_electrode(graph, obj))
            continue
        if local == "hasNegativeElectrode":
            ne.extend(_materials_from_electrode(graph, obj))
            continue
        label = _label_or_type(graph, obj)
        if not label:
            continue
        label_lower = label.lower()
        if label_lower == "positive electrode":
            pe.extend(_materials_from_electrode(graph, obj))
        elif label_lower == "negative electrode":
            ne.extend(_materials_from_electrode(graph, obj))

    materials = sorted(set(pe + ne))
    return {"materials": materials, "pe_materials": sorted(set(pe)), "ne_materials": sorted(set(ne))}


def _materials_from_electrode(graph: Any, electrode: Any) -> list[str]:
    materials: list[str] = []
    for pred, obj in graph.predicate_objects(electrode):
        if _local_name(pred) != "hasActiveMaterial":
            label = _label_or_type(graph, obj)
            if label:
                label_lower = label.lower()
                if label_lower not in {"positive electrode", "negative electrode", "electrode"}:
                    materials.append(label_lower)
            continue
        label = _label_or_type(graph, obj)
        if label:
            materials.append(label.lower())
    return materials


def _label_or_type(graph: Any, node: Any) -> Optional[str]:
    try:
        rdflib = _require_rdflib()
    except Exception:
        return None
    label = _first_literal_obj(graph, node, [rdflib.RDFS.label])
    if label:
        return label
    for obj in graph.objects(node, rdflib.RDF.type):
        return _local_name(obj)
    return None
