from __future__ import annotations

import json
import os
import re
import warnings
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from rdflib import Graph
from rdflib.namespace import OWL, RDF, SKOS

from .normalize import spec

_SLUG = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")


def _label_base(label: str) -> str:
    return label.split(" / ", 1)[0].strip()


def _label_unit(label: str) -> Optional[str]:
    if " / " in label:
        return label.split(" / ", 1)[1].strip()
    return None


def _env_path() -> Optional[str]:
    return os.getenv("BDF_ONTOLOGY_PATH") or os.getenv("BDF_ONTOLOGY")


def _default_cache_path() -> Path:
    return Path.home() / ".bdf" / "ontology_labels.json"


@dataclass(frozen=True)
class AliasInfo:
    quantity: str
    label: str
    unit: str
    source_unit: Optional[str]
    deprecated: bool


def _pick_labels(graph: Graph, subject, predicate) -> list[str]:
    out: list[str] = []
    for lit in graph.objects(subject, predicate):
        try:
            text = str(lit)
        except Exception:
            continue
        if getattr(lit, "language", None) not in (None, "en"):
            continue
        out.append(text)
    return out


def _build_alias_entries(graph: Graph) -> list[tuple[str, AliasInfo]]:
    spec_base: dict[str, str] = {}
    for q in spec.COLUMNS:
        label = spec._label_for(q)
        # Keep first match so ontology extensions that reuse a base label
        # (e.g., deprecated unit variants) do not override canonical targets.
        spec_base.setdefault(_label_base(label).lower(), q)

    alias_entries: list[tuple[str, AliasInfo]] = []

    for subject in graph.subjects(RDF.type, OWL.Class):
        iri = str(subject)
        if "#" not in iri:
            continue
        fragment = iri.rsplit("#", 1)[-1]

        pref_labels = _pick_labels(graph, subject, SKOS.prefLabel)
        if not pref_labels:
            continue
        pref_label = pref_labels[0]

        deprecated = False
        for lit in graph.objects(subject, OWL.deprecated):
            try:
                deprecated = str(lit).lower() == "true"
            except Exception:
                continue

        base_key = _label_base(pref_label).lower()
        quantity = fragment if fragment in spec.COLUMNS else spec_base.get(base_key)
        if deprecated:
            preferred = spec_base.get(base_key)
            if preferred:
                quantity = preferred
        if not quantity:
            continue

        target_label = spec._label_for(quantity)
        target_unit = spec.unit_for(quantity)
        source_unit = None
        pref_unit = _label_unit(pref_label)
        if pref_unit and pref_unit != target_unit:
            source_unit = pref_unit

        alias_info = AliasInfo(
            quantity=quantity,
            label=target_label,
            unit=target_unit,
            source_unit=source_unit,
            deprecated=deprecated,
        )

        alt_labels = _pick_labels(graph, subject, SKOS.altLabel)
        hidden_labels = _pick_labels(graph, subject, SKOS.hiddenLabel)
        notations = _pick_labels(graph, subject, SKOS.notation)
        seen_aliases: set[str] = set()
        for alias in alt_labels + hidden_labels + notations:
            if alias in seen_aliases:
                continue
            seen_aliases.add(alias)
            slug = _slugify(alias.replace("/", " ").replace("#", " "))
            if slug:
                alias_entries.append((slug, alias_info))

    return alias_entries


def _load_cache(path: Path) -> dict[str, AliasInfo]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    aliases = raw.get("aliases", [])
    out: dict[str, AliasInfo] = {}
    for item in aliases:
        out[item["slug"]] = AliasInfo(
            quantity=item["quantity"],
            label=item["label"],
            unit=item["unit"],
            source_unit=item.get("source_unit"),
            deprecated=bool(item.get("deprecated")),
        )
    return out


def _save_cache(path: Path, *, source: str, mtime: float | None, entries: Iterable[tuple[str, AliasInfo]]) -> None:
    payload = {
        "source": source,
        "mtime": mtime,
        "aliases": [
            {
                "slug": slug,
                "quantity": info.quantity,
                "label": info.label,
                "unit": info.unit,
                "source_unit": info.source_unit,
                "deprecated": info.deprecated,
            }
            for slug, info in entries
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_alias_index(ontology_path: str | Path | None = None, *, refresh: bool = False) -> dict[str, AliasInfo]:
    source = str(ontology_path or _env_path() or "").strip()
    if not source:
        return {}
    if refresh:
        return _load_alias_index_uncached(source, refresh=True)
    return _load_alias_index_cached(source)


@lru_cache(maxsize=4)
def _load_alias_index_cached(source: str) -> dict[str, AliasInfo]:
    return _load_alias_index_uncached(source, refresh=False)


def _load_alias_index_uncached(source: str, *, refresh: bool) -> dict[str, AliasInfo]:
    cache_path = _default_cache_path()
    source_path = Path(source)
    mtime = None
    if source_path.exists():
        mtime = source_path.stat().st_mtime

    if cache_path.exists() and not refresh:
        try:
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            if raw.get("source") == source and (mtime is None or raw.get("mtime") == mtime):
                return _load_cache(cache_path)
        except Exception:
            pass

    try:
        graph = Graph()
        graph.parse(source)
    except Exception as exc:
        warnings.warn(f"Failed to load ontology for label aliases: {exc}", stacklevel=2)
        return {}

    entries = _build_alias_entries(graph)
    if entries:
        _save_cache(cache_path, source=source, mtime=mtime, entries=entries)
    return {slug: info for slug, info in entries}
