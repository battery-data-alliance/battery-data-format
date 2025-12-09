# src/bdf/metadata.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Optional pandas (only required if you pass a DataFrame)
try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None  # type: ignore

# Optional units helper
try:
    from bdf.units import resolve_unit  # type: ignore
except Exception:
    def resolve_unit(value: Any, *, as_string: bool = False):  # fallback stub
        raise RuntimeError("bdf.units.resolve_unit is unavailable")

# Lazy-loaded spec to avoid import-order issues
_SPEC = None  # set by _ensure_spec()

BDF_CSVW_SCHEMA_URL = "https://w3id.org/battery-data-alliance/ontology/battery-data-format/schema"


# ---------------------------
# Dataclasses
# ---------------------------

@dataclass
class Creator:
    """schema.org Person or Organization; emits ORCID/ROR in sameAs."""
    name: str
    type: str = "Person"                # 'Person' or 'Organization'
    orcid: Optional[str] = None         # Person: ORCID (URL or bare)
    ror: Optional[str] = None           # Org: ROR (URL or bare)
    given_name: Optional[str] = None    # Person
    family_name: Optional[str] = None   # Person
    affiliation: Optional[str] = None   # Person → Organization name

    def _id_or_sameas(self) -> Dict[str, Any]:
        same_as = None
        if self.type.lower() == "person" and self.orcid:
            same_as = self.orcid if self.orcid.startswith("http") else f"https://orcid.org/{self.orcid}"
        if self.type.lower() == "organization" and self.ror:
            same_as = self.ror if self.ror.startswith("http") else f"https://ror.org/{self.ror}"
        return {"sameAs": same_as} if same_as else {}

    def to_schema_org(self) -> Dict[str, Any]:
        node: Dict[str, Any] = {
            "@type": "Person" if self.type.lower() == "person" else "Organization",
            "name": self.name,
            **self._id_or_sameas(),
        }
        if self.type.lower() == "person":
            if self.given_name:
                node["givenName"] = self.given_name
            if self.family_name:
                node["familyName"] = self.family_name
            if self.affiliation:
                node["affiliation"] = {"@type": "Organization", "name": self.affiliation}
        return node


@dataclass
class PropertyValue:
    """For variableMeasured as PropertyValue."""
    name: str
    property_id: Optional[str] = None   # e.g., your BDF IRI
    unit_text: Optional[str] = None     # e.g., "V", "A", "A*h", "degC"

    def to_schema_org(self) -> Dict[str, Any]:
        out = {"@type": "PropertyValue", "name": self.name}
        if self.property_id:
            out["propertyID"] = self.property_id
        if self.unit_text:
            out["unitText"] = self.unit_text
        return out


@dataclass
class RelatedIdentifier:
    identifier: str
    relation: str = "isSupplementTo"
    scheme: Optional[str] = None

    def to_zenodo(self) -> Dict[str, Any]:
        d = {"identifier": self.identifier, "relation": self.relation}
        if self.scheme:
            d["scheme"] = self.scheme
        return d


@dataclass
class DataDownload:
    """
    schema.org DataDownload (downloadable distribution)
    + optional CSVW metadata (embedded under Dataset.mainEntity).
    """
    url: str                               # contentUrl
    name: Optional[str] = None
    encoding_format: Optional[str] = None  # e.g., "text/csv", "application/zip"
    description: Optional[str] = None
    id: Optional[str] = None               # defaults to url

    # CSVW extras (optional, used to embed a CSVW Table node in mainEntity)
    csvw_table_schema_url: Optional[str] = None
    csvw_table_id: Optional[str] = None

    def _csvw_id(self) -> Optional[str]:
        if not self.csvw_table_schema_url:
            return None
        return self.csvw_table_id or f"{(self.id or self.url)}#csvw"

    def to_schema_org(self) -> Dict[str, Any]:
        node: Dict[str, Any] = {
            "@type": "DataDownload",
            "@id": self.id or self.url,
            "name": self.name or self.url.split("/")[-1],
            "contentUrl": self.url,
        }
        if self.encoding_format:
            node["encodingFormat"] = self.encoding_format
        if self.description:
            node["description"] = self.description
        return node

    def to_csvw_table_embedded(self) -> Optional[Dict[str, Any]]:
        """Return a CSVW Table object for inline embedding under Dataset.mainEntity."""
        if not self.csvw_table_schema_url:
            return None
        return {
            "@id": self._csvw_id(),
            "@type": "Table",
            "url": self.url,
            "tableSchema": self.csvw_table_schema_url,
        }

    @classmethod
    def for_file(cls, path: Union[str, Path], *, csvw_schema_url: Optional[str] = None) -> DataDownload:
        """
        Convenience to build a DataDownload from a local file path.
        """
        p = Path(path)
        return cls(url=p.name, name=p.name, encoding_format=None, id=None, csvw_table_schema_url=csvw_schema_url)


# ---------------------------
# Helpers
# ---------------------------

def _license_to_url(license_value: str) -> str:
    """Accept a full URL or an SPDX id (e.g., 'CC-BY-4.0') and return a URL."""
    if license_value.startswith(("http://", "https://")):
        return license_value
    return f"https://spdx.org/licenses/{license_value}"

def _left_of_label(label: str) -> str:
    return label.split("/", 1)[0].strip()

def _ensure_spec():
    """Lazy import spec to avoid import-order issues during development/CI."""
    global _SPEC
    if _SPEC is None:
        try:
            from bdf.normalize import spec as _loaded  # type: ignore
            _SPEC = _loaded
        except Exception:
            _SPEC = None

def _spec_match_by_left(left: str) -> Optional[Dict[str, Any]]:
    """Return the spec column entry dict for a given preferred-label 'left' text."""
    _ensure_spec()
    if not _SPEC or not hasattr(_SPEC, "COLUMNS"):
        return None
    for _mr, meta in _SPEC.COLUMNS.items():  # type: ignore[attr-defined]
        canon = meta.get("label_template", "")
        if _left_of_label(canon).lower() == left.lower():
            return meta
    return None

def _required_pvs_from_spec() -> List[Dict[str, Any]]:
    """
    Build default PropertyValue list for required quantities directly from spec.COLUMNS.
    Uses label_template → left name, unit, and iri.
    If spec is unavailable, returns [] (caller has emergency fallback).
    """
    _ensure_spec()
    pvs: List[Dict[str, Any]] = []
    if _SPEC and hasattr(_SPEC, "COLUMNS"):
        for _mr, meta in _SPEC.COLUMNS.items():  # type: ignore[attr-defined]
            if meta.get("required"):
                label = meta.get("label_template", "")
                name = _left_of_label(label) if label else meta.get("mr_name", _mr)
                unit_text = meta.get("unit")
                iri = meta.get("iri")
                pvs.append(PropertyValue(name=name, property_id=iri, unit_text=unit_text).to_schema_org())
    return pvs

def _variable_measured_from_df(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Build a list of schema.org PropertyValue from DataFrame columns:
      - Prefer df.attrs["bdf:columns"] for quantity+unit if present
      - Else, derive name from column label left side; unit via bdf.units.resolve_unit
      - Attach BDA IRI if found in spec (by matching preferred-label left side)
      - Keep numeric-looking columns by default
    """
    if pd is None or not isinstance(df, pd.DataFrame):
        return []

    items: List[Dict[str, Any]] = []

    # 1) Use normalization metadata if available (most accurate)
    meta = getattr(df, "attrs", {}).get("bdf:columns", {})
    if isinstance(meta, dict) and meta:
        for canon_label, info in meta.items():
            name = _left_of_label(str(canon_label))
            unit_text = None
            try:
                unit_text = resolve_unit(canon_label, as_string=True)
            except Exception:
                unit_text = str(info.get("unit")) if info.get("unit") else None

            iri = None
            match = _spec_match_by_left(name)
            if match:
                iri = match.get("iri")

            items.append(PropertyValue(name=name, property_id=iri, unit_text=unit_text).to_schema_org())

        # Deduplicate by (name, propertyID)
        seen = set()
        dedup: List[Dict[str, Any]] = []
        for it in items:
            key = (it.get("name"), it.get("propertyID"))
            if key not in seen:
                seen.add(key)
                dedup.append(it)
        return dedup

    # 2) Fallback: infer from headers directly for numeric columns
    for col in df.columns:
        s = df[col]
        if not pd.api.types.is_numeric_dtype(s):
            continue
        left = _left_of_label(str(col))
        unit_text = None
        try:
            unit_text = resolve_unit(str(col), as_string=True)
        except Exception:
            unit_text = None
        iri = None
        m = _spec_match_by_left(left)
        if m:
            iri = m.get("iri")
        items.append(PropertyValue(name=left, property_id=iri, unit_text=unit_text).to_schema_org())

    # Deduplicate
    seen = set()
    dedup = []
    for it in items:
        key = (it.get("name"), it.get("propertyID"))
        if key not in seen:
            seen.add(key)
            dedup.append(it)
    return dedup


# ---------------------------
# BDFMetadata
# ---------------------------

@dataclass
class BDFMetadata:
    # Core
    title: str
    creators: List[Creator]
    description: str

    # Common
    keywords: List[str] = field(default_factory=list)
    license: str = "CC-BY-4.0"
    version: Optional[str] = None
    publication_date: Optional[str] = None
    language: Optional[str] = "en"

    # Google / schema.org friendly additions
    url: Optional[str] = None                    # landing page URL
    same_as: List[str] = field(default_factory=list)
    identifiers: List[str] = field(default_factory=list)  # include DOI/compact IDs
    citation: List[Union[str, Dict[str, Any]]] = field(default_factory=list)
    is_based_on: List[str] = field(default_factory=list)
    variable_measured: List[Union[str, PropertyValue]] = field(default_factory=list)
    measurement_technique: Optional[Union[str, List[str]]] = None
    spatial_coverage: Optional[str] = None
    temporal_coverage: Optional[str] = None
    included_in_data_catalog: Optional[Dict[str, Any]] = None  # {"@type":"DataCatalog","name":"...","url":"..."}
    is_accessible_for_free: Optional[bool] = None
    publisher: Optional[Creator] = None
    funder: Optional[List[Creator]] = None

    def __init__(
        self,
        *,
        title: str,
        creators: List[Creator],
        description: str,
        keywords: Optional[List[str]] = None,
        license: str = "CC-BY-4.0",
        access_right: Optional[str] = None,
        version: Optional[str] = None,
        publication_date: Optional[str] = None,
        language: Optional[str] = "en",
        url: Optional[str] = None,
        same_as: Optional[List[str]] = None,
        identifiers: Optional[List[str]] = None,
        citation: Optional[List[Union[str, Dict[str, Any]]]] = None,
        is_based_on: Optional[List[str]] = None,
        variable_measured: Optional[List[Union[str, PropertyValue]]] = None,
        measurement_technique: Optional[Union[str, List[str]]] = None,
        spatial_coverage: Optional[str] = None,
        temporal_coverage: Optional[str] = None,
        included_in_data_catalog: Optional[Dict[str, Any]] = None,
        publisher: Optional[Creator] = None,
        funder: Optional[List[Creator]] = None,
        doi: Optional[str] = None,
        communities: Optional[List[str]] = None,
        related_identifiers: Optional[List[RelatedIdentifier]] = None,
        **extra: Any,
    ):
        self.title = title
        self.creators = creators
        self.description = description
        self.keywords = keywords or []
        self.license = license
        self.version = version
        self.publication_date = publication_date
        self.language = language
        self.url = url
        self.same_as = same_as or []
        self.identifiers = identifiers or []
        if doi:
            self.identifiers.append(doi)
        self.citation = citation or []
        self.is_based_on = is_based_on or []
        self.variable_measured = variable_measured or []
        self.measurement_technique = measurement_technique
        self.spatial_coverage = spatial_coverage
        self.temporal_coverage = temporal_coverage
        self.included_in_data_catalog = included_in_data_catalog
        self.publisher = publisher
        self.funder = funder
        # access_right maps to schema.org isAccessibleForFree when obviously open
        if access_right:
            self.is_accessible_for_free = access_right.lower() in {"open", "open access", "public"}
        else:
            self.is_accessible_for_free = None
        # Extra Zenodo-ish fields (kept for completeness)
        self.communities = communities or []
        self.related_identifiers = related_identifiers or []
        # Accept but ignore any additional fields for forward compatibility
        self._extra = extra

    def to_schemaorg_dataset(
        self,
        *,
        dataset_uri: Optional[str] = None,             # optional @id
        identifier: Optional[str] = None,              # short slug or DOI
        distributions: List[DataDownload] = (),
        context: Union[str, List[Any]] = ("https://schema.org/", "http://www.w3.org/ns/csvw"),
        extra_fields: Optional[Dict[str, Any]] = None,
        df: Optional[pd.DataFrame] = None,           # auto variableMeasured from DataFrame
        merge_variables: bool = True,                  # merge with self.variable_measured
        enforce_desc_bounds: bool = True,              # trim description to Google bounds
        max_description_len: int = 5000,
    ) -> Dict[str, Any]:
        # Description with guardrails (Google recommends ≤ 5000 chars)
        desc = self.description or ""
        if enforce_desc_bounds and len(desc) > max_description_len:
            desc = desc[:max_description_len]

        creators = [c.to_schema_org() for c in self.creators]
        dataset: Dict[str, Any] = {
            "@type": "Dataset",
            "name": self.title,
            "description": desc,
            "creator": creators,
            "keywords": self.keywords or [],
            "license": _license_to_url(self.license),
            "inLanguage": self.language or "en",
        }
        if dataset_uri:
            dataset["@id"] = dataset_uri
        if identifier:
            dataset["identifier"] = identifier
        if self.url:
            dataset["url"] = self.url
        if self.version:
            dataset["version"] = self.version
        if self.publication_date:
            dataset["datePublished"] = self.publication_date
        if self.same_as:
            dataset["sameAs"] = self.same_as
        if self.identifiers:
            dataset.setdefault("identifier", self.identifiers if len(self.identifiers) > 1 else self.identifiers[0])
        if self.is_based_on:
            dataset["isBasedOn"] = self.is_based_on
        if self.is_accessible_for_free is not None:
            dataset["isAccessibleForFree"] = self.is_accessible_for_free
        if self.publisher:
            dataset["publisher"] = self.publisher.to_schema_org()
        if self.funder:
            dataset["funder"] = [f.to_schema_org() for f in self.funder]
        if self.temporal_coverage:
            dataset["temporalCoverage"] = self.temporal_coverage
        if self.spatial_coverage:
            dataset["spatialCoverage"] = self.spatial_coverage
        if self.measurement_technique:
            dataset["measurementTechnique"] = self.measurement_technique
        if self.citation:
            dataset["citation"] = self.citation

        # variableMeasured: explicit + inferred (if df provided) with dedup; else required-from-spec fallback.
        explicit_vm: List[Dict[str, Any]] = [
            v.to_schema_org() if hasattr(v, "to_schema_org") else {"@type": "PropertyValue", "name": str(v)}
            for v in (self.variable_measured or [])
        ]
        inferred_vm: List[Dict[str, Any]] = _variable_measured_from_df(df) if (df is not None) else []

        if merge_variables:
            all_vm = explicit_vm + inferred_vm
            # dedup by (name, propertyID)
            seen = set()
            dedup_vm: List[Dict[str, Any]] = []
            for it in all_vm:
                key = (it.get("name"), it.get("propertyID"))
                if key not in seen:
                    seen.add(key)
                    dedup_vm.append(it)
            vm_final = dedup_vm
        else:
            vm_final = explicit_vm or inferred_vm

        if not vm_final:
            vm_final = _required_pvs_from_spec()
        if not vm_final:  # last-resort emergency fallback
            vm_final = [
                PropertyValue(name="Voltage", property_id=None, unit_text="V").to_schema_org(),
                PropertyValue(name="Current", property_id=None, unit_text="A").to_schema_org(),
                PropertyValue(name="Test Time", property_id=None, unit_text="s").to_schema_org(),
            ]
        dataset["variableMeasured"] = vm_final

        # Distributions
        dd_nodes: List[Dict[str, Any]] = [d.to_schema_org() for d in distributions] if distributions else []
        if dd_nodes:
            dataset["distribution"] = dd_nodes

        # Inline CSVW Table(s) under mainEntity
        csvw_embedded: List[Dict[str, Any]] = []
        for d in distributions:
            t = d.to_csvw_table_embedded()
            if t:
                csvw_embedded.append(t)
        if csvw_embedded:
            dataset["mainEntity"] = csvw_embedded

        if self.included_in_data_catalog:
            dataset["includedInDataCatalog"] = self.included_in_data_catalog

        if extra_fields:
            dataset.update(extra_fields)

        ctx = list(context) if isinstance(context, (list, tuple)) else [context]
        return {"@context": ctx, **dataset}
    
    def save_jsonld(
        self,
        out_path: Union[str, Path],
        *,
        # anything accepted by to_schemaorg_dataset:
        dataset_uri: Optional[str] = None,
        identifier: Optional[str] = None,
        distributions: List[DataDownload] = (),
        context: Union[str, List[Any]] = ("https://schema.org/", "http://www.w3.org/ns/csvw"),
        extra_fields: Optional[Dict[str, Any]] = None,
        df: Optional[pd.DataFrame] = None,
        merge_variables: bool = True,
        enforce_desc_bounds: bool = True,
        max_description_len: int = 5000,
        indent: int = 2,
    ) -> Path:
        """Build and save Schema.org + CSVW JSON-LD to a file."""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        obj = self.to_schemaorg_dataset(
            dataset_uri=dataset_uri,
            identifier=identifier,
            distributions=distributions,
            context=context,
            extra_fields=extra_fields,
            df=df,
            merge_variables=merge_variables,
            enforce_desc_bounds=enforce_desc_bounds,
            max_description_len=max_description_len,
        )
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
        return out_path

    def save_rich_results_html(
        self,
        out_path: Union[str, Path],
        *,
        title: Optional[str] = None,
        graphify: bool = False,
        # anything accepted by to_schemaorg_dataset:
        dataset_uri: Optional[str] = None,
        identifier: Optional[str] = None,
        distributions: List[DataDownload] = (),
        context: Union[str, List[Any]] = ("https://schema.org/", "http://www.w3.org/ns/csvw"),
        extra_fields: Optional[Dict[str, Any]] = None,
        df: Optional[pd.DataFrame] = None,
        merge_variables: bool = True,
        enforce_desc_bounds: bool = True,
        max_description_len: int = 5000,
        indent: int = 2,
    ) -> Path:
        """
        Write a minimal HTML file embedding the Dataset JSON-LD in a
        <script type="application/ld+json">. Set graphify=True to wrap the
        dataset in @graph (matches many rich-result examples).
        """
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        obj = self.to_schemaorg_dataset(
            dataset_uri=dataset_uri,
            identifier=identifier,
            distributions=distributions,
            context=context,
            extra_fields=extra_fields,
            df=df,
            merge_variables=merge_variables,
            enforce_desc_bounds=enforce_desc_bounds,
            max_description_len=max_description_len,
        )

        if graphify:
            ctx = obj.get("@context", [])
            dataset_node = {k: v for k, v in obj.items() if k != "@context"}
            obj = {"@context": ctx, "@graph": [dataset_node]}

        html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>{(title or self.title)}</title>
    <script type="application/ld+json">
{json.dumps(obj, ensure_ascii=False, indent=indent)}
    </script>
  </head>
  <body></body>
</html>
"""
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        return out_path



# ---------------------------
# IO helpers
# ---------------------------

def save_schemaorg_dataset(
    meta: BDFMetadata,
    *,
    dataset_uri: Optional[str],
    identifier: Optional[str],
    distributions: List[DataDownload],
    out_path: Union[str, Path],
    context: Union[str, List[Any]] = ("https://schema.org/", "http://www.w3.org/ns/csvw"),
    extra_fields: Optional[Dict[str, Any]] = None,
    indent: int = 2,
    df: Optional[pd.DataFrame] = None,
    merge_variables: bool = True,
    enforce_desc_bounds: bool = True,
    max_description_len: int = 5000,
) -> Path:
    """
    Serialize a schema.org Dataset (with distributions), embedding CSVW Table(s) in mainEntity.
    If `df` is provided, variableMeasured is auto-populated from DataFrame headers/attrs.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    obj = meta.to_schemaorg_dataset(
        dataset_uri=dataset_uri,
        identifier=identifier,
        distributions=distributions,
        context=context,
        extra_fields=extra_fields,
        df=df,
        merge_variables=merge_variables,
        enforce_desc_bounds=enforce_desc_bounds,
        max_description_len=max_description_len,
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    return out_path


# CLI-facing convenience: build JSON-LD sidecar for a single data file
def save_jsonld(
    meta: BDFMetadata,
    data_path: Union[str, Path],
    *,
    out_path: Union[str, Path],
    dataset_uri: Optional[str] = None,
    identifier: Optional[str] = None,
    distributions: Optional[List[DataDownload]] = None,
    context: Union[str, List[Any]] = ("https://schema.org/", "http://www.w3.org/ns/csvw"),
    extra_fields: Optional[Dict[str, Any]] = None,
    df: Optional[pd.DataFrame] = None,
    merge_variables: bool = True,
    enforce_desc_bounds: bool = True,
    max_description_len: int = 5000,
    indent: int = 2,
    csvw_schema_url: Optional[str] = None,
) -> Path:
    data_path = Path(data_path)
    dist = DataDownload.for_file(data_path)
    dists = distributions or [dist]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    obj = meta.to_schemaorg_dataset(
        dataset_uri=dataset_uri,
        identifier=identifier,
        distributions=dists,
        context=context,
        extra_fields=extra_fields,
        df=df,
        merge_variables=merge_variables,
        enforce_desc_bounds=enforce_desc_bounds,
        max_description_len=max_description_len,
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    return out_path


# Exported symbols
__all__ = [
    "BDFMetadata",
    "Creator",
    "PropertyValue",
    "RelatedIdentifier",
    "DataDownload",
    "save_schemaorg_dataset",
    "save_jsonld",
]
