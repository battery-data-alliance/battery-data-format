# --- add/keep imports at top ---
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import json
import datetime as _dt

# Optional pandas typing without hard dependency at import time
try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None  # type: ignore

BDF_CSVW_SCHEMA_URL = "https://w3id.org/battery-data-alliance/ontology/battery-data-format/schema"

UNIT_CODE = {
    "Test Time / s": "SEC",
    "Step Time / s": "SEC",
    "Voltage / V": "VLT",
    "Current / A": "AMP",
    "Ambient Temperature / degC": "CEL",
}

# ---------------------------
# Dataclasses
# ---------------------------

@dataclass
class Creator:
    name: str
    orcid: Optional[str] = None
    affiliation: Optional[str] = None
    type: str = "Person"

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
        if self.affiliation:
            z["affiliation"] = self.affiliation
        if self.orcid:
            z["orcid"] = self.orcid if self.orcid.startswith("0000-") else self.orcid.rsplit("/", 1)[-1]
        return z


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
class BDFMetadata:
    title: str
    creators: List[Creator]
    description: str

    keywords: List[str] = field(default_factory=list)
    license: str = "CC-BY-4.0"
    access_right: str = "open"
    version: Optional[str] = None
    publication_date: Optional[str] = None
    doi: Optional[str] = None
    language: Optional[str] = "en"
    communities: List[str] = field(default_factory=list)
    related_identifiers: List[RelatedIdentifier] = field(default_factory=list)
    contributors: List[Creator] = field(default_factory=list)

    csvw_schema_url: str = BDF_CSVW_SCHEMA_URL

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
        if self.communities:
            payload["communities"] = [{"identifier": cid} for cid in self.communities]
        if self.contributors:
            payload["contributors"] = [c.to_zenodo() for c in self.contributors]
        if self.doi: payload["doi"] = self.doi
        return payload

    # ---- helpers for CSVW columns ----

    def _csvw_columns_from_names_and_sample(
        self, names: List[str], sample_df: Optional["pd.DataFrame"] = None
    ) -> List[Dict[str, Any]]:
        cols: List[Dict[str, Any]] = []
        for col in names:
            item: Dict[str, Any] = {"name": col, "titles": col}
            # datatype guess (prefer sample_df dtype if available)
            if sample_df is not None and pd is not None and col in sample_df.columns:
                dt = "number" if pd.api.types.is_numeric_dtype(sample_df[col]) else "string"
            else:
                dt = "string"
            item["datatype"] = dt
            if col in UNIT_CODE:
                item["unitCode"] = UNIT_CODE[col]
            cols.append(item)
        return cols

    def _peek_columns(self, path: Path) -> tuple[List[str], Optional["pd.DataFrame"]]:
        """
        Try to infer column names (and a tiny sample df for dtype inference) without
        loading the entire dataset. Returns (names, sample_df_or_None).
        """
        suffix = path.suffix.lower()
        if pd is None:
            return [], None

        try:
            if suffix in {".csv", ".tsv", ".txt"}:
                # read header + a tiny sample
                sep = None if suffix == ".csv" else ("\t" if suffix == ".tsv" else None)
                df0 = pd.read_csv(path, nrows=200, sep=sep, engine="python", on_bad_lines="skip")
                return list(df0.columns), df0
            if suffix in {".parquet", ".pq"}:
                df0 = pd.read_parquet(path, columns=None)  # engine=pyarrow via pandas
                # Take a small head for dtype checks
                return list(df0.columns), df0.head(200)
        except Exception:
            pass
        return [], None

    def to_jsonld(
        self,
        data_path: Union[str, Path],
        *,
        csvw_schema_url: Optional[str] = None,
        include_columns: Union[bool, str] = "auto",  # True|False|"auto"
    ) -> Dict[str, Any]:
        """
        Build JSON-LD:
          - schema.org Dataset node
          - csvw Table node pointing to the data and to the CSVW schema
          - optionally embed a CSVW 'columns' array (lightweight, inferred)
        """
        data_path = Path(data_path)
        schema_url = csvw_schema_url or self.csvw_schema_url

        creators = [c.to_schema_org() for c in self.creators]
        dataset_node: Dict[str, Any] = {
            "@id": "#dataset",
            "@type": "schema:Dataset",
            "schema:name": self.title,
            "schema:description": self.description,
            "schema:creator": creators,
            "schema:keywords": self.keywords,
            "schema:license": self.license,
            "schema:inLanguage": self.language or "en",
        }
        if self.version: dataset_node["schema:version"] = self.version
        if self.publication_date: dataset_node["schema:datePublished"] = self.publication_date
        if self.doi: dataset_node["schema:identifier"] = self.doi
        if self.related_identifiers:
            dataset_node["schema:relatedLink"] = [ri.identifier for ri in self.related_identifiers]

        table_node: Dict[str, Any] = {
            "@id": "#table",
            "@type": "Table",
            "url": data_path.name,
            "tableSchema": schema_url,
        }

        # Optionally embed column metadata
        if include_columns:
            names, sample = self._peek_columns(data_path) if include_columns == "auto" else ([], None)
            if include_columns is True or names:
                columns = self._csvw_columns_from_names_and_sample(names, sample)
                if columns:
                    table_node["columns"] = columns

        dataset_node["schema:distribution"] = {"@id": "#table"}

        jsonld = {
            "@context": [
                "http://www.w3.org/ns/csvw",
                "https://schema.org/",
                {"dc": "http://purl.org/dc/terms/"},
            ],
            "@graph": [dataset_node, table_node],
        }
        return jsonld


# ---------------------------
# IO helper
# ---------------------------

def save_jsonld(
    meta: BDFMetadata,
    data_path: Union[str, Path],
    out_path: Optional[Union[str, Path]] = None,
    *,
    csvw_schema_url: Optional[str] = None,
    indent: int = 2,
    include_columns: Union[bool, str] = "auto",  # True|False|"auto"
) -> Path:
    """
    Serialize JSON-LD next to a BDF data file.

    - No DataFrame is required.
    - If include_columns is True (or "auto" and columns can be peeked),
      a lightweight CSVW 'columns' array is embedded.
    """
    data_path = Path(data_path)
    out_path = Path(out_path) if out_path else data_path.with_suffix(data_path.suffix + ".metadata.jsonld")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    jsonld = meta.to_jsonld(
        data_path,
        csvw_schema_url=csvw_schema_url,
        include_columns=include_columns,
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(jsonld, f, ensure_ascii=False, indent=indent)
    return out_path
