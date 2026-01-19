from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import zipfile
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

import requests

_GRAPH_CACHE: dict[str, Any] = {}


def _require_rdflib():
    try:
        import rdflib  # type: ignore
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "Linked-data registry requires rdflib. "
            "Install with `pip install bdf`."
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
) -> Path:
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


def _iter_metadata_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("metadata.*"):
        if not path.is_file():
            continue
        suffix = "".join(path.suffixes).lower()
        if suffix.endswith(".jsonld") or suffix.endswith(".json") or suffix.endswith(".ttl"):
            yield path


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
    rdflib = _require_rdflib()

    if isinstance(sources, str):
        sources_list = [sources]
    else:
        sources_list = list(sources)

    reg_dir = _ensure_dir(Path(registry_dir) if registry_dir else _default_registry_dir())
    cache_dir = _ensure_dir(reg_dir / "sources")

    graph = rdflib.Graph()
    metadata_files: list[Path] = []
    parse_errors: list[dict[str, str]] = []

    for source in sources_list:
        root = _resolve_source(source, cache_dir, refresh)
        for path in _iter_metadata_files(root):
            fmt = _guess_format(path)
            try:
                abs_path = path.resolve()
                graph.parse(abs_path, format=fmt, publicID=abs_path.as_uri())
                metadata_files.append(path)
            except Exception as exc:
                parse_errors.append({"path": str(path), "error": str(exc)})

    graph = _normalize_schema_org(graph)

    ttl_path = reg_dir / "registry.ttl"
    ttl_path.write_text(graph.serialize(format="turtle"), encoding="utf-8")

    dataset_rows = _extract_dataset_rows(graph)
    db_path = reg_dir / "registry.db"
    _write_registry_db(db_path, dataset_rows)

    return {
        "registry_dir": str(reg_dir),
        "registry_ttl": str(ttl_path),
        "registry_db": str(db_path),
        "sources": sources_list,
        "metadata_files": [str(p) for p in metadata_files],
        "datasets": len(dataset_rows),
        "errors": parse_errors,
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


def search(
    query: str,
    registry_dir: str | Path | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    reg_dir = Path(registry_dir) if registry_dir else _default_registry_dir()
    db_path = reg_dir / "registry.db"
    if not db_path.exists():
        raise FileNotFoundError(f"{db_path} not found. Run build_registry() first.")

    tokens, numeric_filters = _parse_search_query(query)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = _search_db(conn, tokens, numeric_filters, limit)
    finally:
        conn.close()
    return [dict(row) for row in rows]


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
