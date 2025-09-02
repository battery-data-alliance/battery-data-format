# --- keep your imports / constants at top ---
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import json
import uuid
import hashlib

try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None  # type: ignore

BDF_CSVW_SCHEMA_URL = (
    "https://w3id.org/battery-data-alliance/ontology/battery-data-format/schema"
)
DEFAULT_BASE_IRI = "https://w3id.org/battery-data-alliance/datasets/"

UNIT_CODE = {
    "Test Time / s": "SEC",
    "Step Time / s": "SEC",
    "Voltage / V": "VLT",
    "Current / A": "AMP",
    "Ambient Temperature / degC": "CEL",
}

# ---------- small utils ----------
def _license_to_uri(lic: str) -> str | None:
    m = {
        "CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/",
        "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
        "ODC-By-1.0": "https://opendatacommons.org/licenses/by/1-0/",
        "MIT": "https://opensource.org/license/mit/",
        "Apache-2.0": "https://www.apache.org/licenses/LICENSE-2.0",
    }
    return m.get(lic)

def _media_type_from_suffix(p: Path) -> str:
    s = p.suffix.lower()
    if s == ".csv": return "text/csv"
    if s == ".tsv": return "text/tab-separated-values"
    if s == ".parquet": return "application/vnd.apache.parquet"
    if s == ".json": return "application/json"
    return "application/octet-stream"

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1<<20), b""):
            h.update(chunk)
    return h.hexdigest()

def _deterministic_slug_v5(*parts: str) -> str:
    """
    Deterministic UUIDv5 over a normalized string of key metadata.
    Ensures the same dataset re-mints the same slug across runs.
    """
    norm = "|".join(p.strip().lower() for p in parts if p and p.strip())
    return str(uuid.uuid5(uuid.NAMESPACE_URL, norm))

# ---------- dataclasses ----------
@dataclass
class Creator:
    name: str
    orcid: Optional[str] = None
    affiliation: Optional[str] = None
    type: str = "Person"  # "Person" | "Organization"
    def to_schema_org(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "@type": f"schema:{'Person' if self.type.lower()=='person' else 'Organization'}",
            "schema:name": self.name,
        }
        if self.affiliation and self.type.lower() == "person":
            out["schema:affiliation"] = {"@type": "schema:Organization", "schema:name": self.affiliation}
        if self.orcid:
            orcid_url = self.orcid if self.orcid.startswith("http") else f"https://orcid.org/{self.orcid}"
            out["schema:identifier"] = orcid_url
        return out
    def to_zenodo(self) -> Dict[str, Any]:
        z = {"name": self.name}
        if self.affiliation: z["affiliation"] = self.affiliation
        if self.orcid: z["orcid"] = self.orcid if self.orcid.startswith("0000-") else self.orcid.rsplit("/", 1)[-1]
        return z

@dataclass
class RelatedIdentifier:
    identifier: str
    relation: str = "isSupplementTo"
    scheme: Optional[str] = None
    def to_zenodo(self) -> Dict[str, Any]:
        d = {"identifier": self.identifier, "relation": self.relation}
        if self.scheme: d["scheme"] = self.scheme
        return d

@dataclass
class BDFMetadata:
    title: str
    creators: List[Creator]
    description: str

    keywords: List[str] = field(default_factory=list)
    license: str = "CC-BY-4.0"
    access_right: str = "open"
    version: Optional[str] = None
    publication_date: Optional[str] = None  # "YYYY-MM-DD"
    doi: Optional[str] = None               # version DOI (preferred canonical)
    concept_doi: Optional[str] = None       # concept DOI (family)
    language: Optional[str] = "en"
    communities: List[str] = field(default_factory=list)
    related_identifiers: List[RelatedIdentifier] = field(default_factory=list)
    contributors: List[Creator] = field(default_factory=list)

    # ID minting / naming
    dataset_id: Optional[str] = None        # explicit override for @id, if you have one
    base_iri: str = DEFAULT_BASE_IRI        # w3id base for pre-DOI ids
    id_strategy: str = "auto"               # 'auto'|'doi'|'w3id'|'custom'
    csvw_schema_url: str = BDF_CSVW_SCHEMA_URL

    # ---------- Zenodo payload ----------
    def to_zenodo_metadata(self) -> Dict[str, Any]:
        creators = [c.to_zenodo() for c in self.creators]
        payload: Dict[str, Any] = {
            "title": self.title,
            "upload_type": "dataset",
            "description": self.description,
            "creators": creators,
            "license": self.license,
            "access_right": self.access_right,
        }
        if self.keywords: payload["keywords"] = self.keywords
        if self.version: payload["version"] = self.version
        if self.publication_date: payload["publication_date"] = self.publication_date
        if self.language: payload["language"] = self.language
        if self.related_identifiers:
            payload["related_identifiers"] = [ri.to_zenodo() for ri in self.related_identifiers]
        if self.communities: payload["communities"] = [{"identifier": cid} for cid in self.communities]
        if self.contributors: payload["contributors"] = [c.to_zenodo() for c in self.contributors]
        if self.doi: payload["doi"] = self.doi
        return payload

    # ---------- CSVW columns (optional) ----------
    def _csvw_columns_from_df(self, df) -> List[Dict[str, Any]]:
        cols: List[Dict[str, Any]] = []
        for col in df.columns:
            item: Dict[str, Any] = {"name": col, "titles": col}
            try:
                import pandas as _pd
                dt = "number" if _pd.api.types.is_numeric_dtype(df[col]) else "string"
            except Exception:
                dt = "string"
            item["datatype"] = dt
            if col in UNIT_CODE: item["unitCode"] = UNIT_CODE[col]
            cols.append(item)
        return cols

    # ---------- ID minting ----------
    def _mint_ids(
        self, data_path: Path, *, df=None, file_sha256: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Decide canonical @id and part ids (distribution, table) using the rules:
          1) If self.dataset_id -> use it
          2) Else if self.doi   -> use https://doi.org/<doi>
          3) Else mint stable w3id using UUIDv5(base on: title | first-creator | pubdate | sha256?)
        Distribution and table become fragment IRIs off the dataset id.
        """
        if self.dataset_id:
            did = self.dataset_id
        elif self.id_strategy == "doi" and self.doi:
            did = f"https://doi.org/{self.doi}"
        elif self.id_strategy in ("auto", "w3id"):
            # prefer deterministic slug; include SHA256 to avoid collisions across different files with same title
            if not file_sha256 and data_path.exists():
                file_sha256 = _sha256_file(data_path)
            first_creator = (self.creators[0].orcid or self.creators[0].name) if self.creators else ""
            basis = "|".join([
                self.title or "",
                first_creator or "",
                self.publication_date or "",
                self.version or "",
                file_sha256 or "",
            ])
            slug = _deterministic_slug_v5(basis)
            did = self.base_iri.rstrip("/") + "/" + slug
        elif self.id_strategy == "custom" and self.dataset_id:
            did = self.dataset_id
        else:
            # safe fallback: w3id with random v4 (only if nothing else available)
            did = self.base_iri.rstrip("/") + "/" + str(uuid.uuid4())
        return {
            "dataset": did,
            "distribution": did + "#dist",
            "table": did + "#table",
        }

    # ---------- JSON-LD ----------
    def to_jsonld(
        self,
        data_path: Union[str, Path],
        *,
        df=None,
        csvw_schema_url: Optional[str] = None,
        file_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Build JSON-LD across schema.org, DCAT, DCTERMS, and CSVW.
        Uses DOI if present; otherwise mints a stable w3id-based URI.
        """
        data_path = Path(data_path)
        schema_url = csvw_schema_url or self.csvw_schema_url
        media_type = _media_type_from_suffix(data_path)
        lic_uri = _license_to_uri(self.license) or self.license

        ids = self._mint_ids(data_path, df=df, file_sha256=file_sha256)
        did, dist_id, table_id = ids["dataset"], ids["distribution"], ids["table"]

        creators = [c.to_schema_org() for c in self.creators]

        # Dataset node
        dataset_node: Dict[str, Any] = {
            "@id": did,
            "@type": ["schema:Dataset", "dcat:Dataset"],
            "schema:name": self.title,
            "schema:description": self.description,
            "schema:creator": creators,
            "dct:creator": creators,
            "schema:keywords": self.keywords,
            "dcat:keyword": self.keywords,
            "schema:license": lic_uri,
            "dct:license": lic_uri,
            "schema:inLanguage": self.language or "en",
            "dct:language": self.language or "en",
        }
        if self.version:
            dataset_node["schema:version"] = self.version
            dataset_node["dct:hasVersion"] = self.version
        if self.publication_date:
            dataset_node["schema:datePublished"] = self.publication_date
            dataset_node["dct:issued"] = self.publication_date
        if self.doi:
            doi_uri = f"https://doi.org/{self.doi}"
            dataset_node["schema:identifier"] = self.doi
            dataset_node["dct:identifier"] = self.doi
            dataset_node.setdefault("schema:sameAs", []).append(doi_uri)
            # If @id is w3id, point to DOI as sameAs
            if not did.startswith(doi_uri):
                dataset_node.setdefault("sameAs", []).append(doi_uri)
        if self.concept_doi:
            dataset_node["dct:isVersionOf"] = f"https://doi.org/{self.concept_doi}"
        if self.related_identifiers:
            dataset_node["dct:relation"] = [ri.identifier for ri in self.related_identifiers]

        # Distribution node (data file)
        dist_node: Dict[str, Any] = {
            "@id": dist_id,
            "@type": ["dcat:Distribution", "schema:DataDownload"],
            "dcat:downloadURL": data_path.name,
            "dcat:mediaType": media_type,
            "schema:contentUrl": data_path.name,
            "schema:encodingFormat": media_type,
            "dct:conformsTo": schema_url,
        }
        # include checksum to help dedupe
        if data_path.exists():
            dist_node["dct:identifier"] = f"sha256:{file_sha256 or _sha256_file(data_path)}"

        # CSVW Table node
        table_node: Dict[str, Any] = {
            "@id": table_id,
            "@type": "csvw:Table",
            "url": data_path.name,
            "tableSchema": schema_url,
        }
        if df is not None and pd is not None:
            table_node["columns"] = self._csvw_columns_from_df(df)

        # Links
        dataset_node["dcat:distribution"] = {"@id": dist_id}
        dataset_node["schema:distribution"] = {"@id": dist_id}
        dist_node["dct:references"] = {"@id": table_id}

        # Context
        jsonld = {
            "@context": {
                "schema": "https://schema.org/",
                "dcat": "http://www.w3.org/ns/dcat#",
                "dct": "http://purl.org/dc/terms/",
                "csvw": "http://www.w3.org/ns/csvw#",
                "url": "csvw:url",
                "tableSchema": {"@id": "csvw:tableSchema", "@type": "@id"},
                "sameAs": {"@id": "schema:sameAs", "@type": "@id"},
            },
            "@graph": [dataset_node, dist_node, table_node],
        }
        return jsonld

# ---------- IO ----------
def save_jsonld(
    meta: BDFMetadata,
    data_path: Union[str, Path],
    out_path: Optional[Union[str, Path]] = None,
    *,
    df=None,
    csvw_schema_url: Optional[str] = None,
    indent: int = 2,
) -> Path:
    data_path = Path(data_path)
    out_path = Path(out_path) if out_path else data_path.with_suffix(data_path.suffix + ".metadata.jsonld")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    jsonld = meta.to_jsonld(data_path, df=df, csvw_schema_url=csvw_schema_url)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(jsonld, f, ensure_ascii=False, indent=indent)
    return out_path
