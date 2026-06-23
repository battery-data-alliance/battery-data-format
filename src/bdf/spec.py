# src/bdf/spec.py
from __future__ import annotations

import contextlib
import hashlib
import importlib.resources
import re
import tempfile
import warnings
from pathlib import Path
from typing import Any, cast

import pint
import polars as pl
from pydantic import BaseModel, field_validator, model_validator
from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, SKOS

from bdf._df_compat import coerce_dataframe

"""
Single source of truth for BDF canonical columns.

Each entry defines:
- unit: pint-compatible canonical unit
- label_template: preferred label, with "{unit}" placeholder
- required: bool (True for core required, False otherwise)
- mr_name: machine-readable snake name (official)
- iri: canonical IRI (official)
- synonyms: list[str] of base-name slugs mapping vendor headers to this quantity

Notes:
- Slugs are lowercase with non-alnum -> "-" (same slugger as normalizer).
- Synonyms are unit-agnostic ("voltage" not "voltage#v"); the normalizer parses units.
"""

# --------- Constants ----------

_SLUG = re.compile(r"[^a-z0-9]+")
_REQUIRED_DEFAULT = {"test_time_second", "voltage_volt", "current_ampere"}
_UNIT_ALIAS = {
    "celsius": "degC",
    "degree_celsius": "degC",
    "℃": "degC",
    "degc": "degC",
    "degreec": "degC",
    "\xf8c": "degC",
    "\xf8C": "degC",
    "\xb0c": "degC",
    "\xb0C": "degC",
    "Sec": "second",
    # UCUM codes used in schema:unitCode annotations (case-sensitive, exact
    # casing as it appears in the ontology's schema:unitCode literals)
    "Cel": "degC",
    "A.h": "Ah",
    "W.h": "Wh",
    "Ohm": "ohm",
}

# Bare "C" or "c" is ambiguous (Celsius vs Coulombs). This set lists the BDF
# destination units that unambiguously identify a temperature quantity, allowing
# bare "C"/"c" to be treated as Celsius in get_unit_conversion.
_TEMPERATURE_DST_UNITS: frozenset[str] = frozenset({"degC", "°C", "K", "degF"})
_SLASH_RE = re.compile(r"^\s*(.+?)\s*/\s*(.+)\s*$")
_BDF_LIVE_URL = "https://w3id.org/battery-data-alliance/ontology/battery-data-format"
_SCHEMA_UNIT_CODE = URIRef("https://schema.org/unitCode")
_BDF_RELEASE_URL_TMPL = (
    "https://raw.githubusercontent.com/battery-data-alliance/"
    "battery-data-format-ontology/{version}/battery-data-format.ttl"
)

ureg: pint.UnitRegistry = pint.UnitRegistry()


def _pint_understands(alias: str, canonical: str) -> bool:
    """True if pint already parses `alias` to the same value as `canonical`.

    Args:
        alias: Candidate unit string (e.g. a UCUM code).
        canonical: Known-good pint unit string to compare against.

    Returns:
        True if pint resolves `alias` to the same quantity as `canonical`
        both at 0 and 1 (catching offset units, not just dimensionality).
    """
    try:
        a0, c0 = ureg.Quantity(0, alias), ureg.Quantity(0, canonical)
        a1, c1 = ureg.Quantity(1, alias), ureg.Quantity(1, canonical)
        return (
            abs(a0.to(c0.units).magnitude - c0.magnitude) < 1e-9
            and abs(a1.to(c1.units).magnitude - c1.magnitude) < 1e-9
        )
    except (pint.errors.PintError, AssertionError):
        # PintError covers undefined/incompatible units; AssertionError covers
        # strings pint's parser chokes on internally (e.g. "℃" / ℃), which
        # would otherwise escape and crash module import on pint >= 0.25.
        return False


for _alias, _canonical in _UNIT_ALIAS.items():
    if _pint_understands(_alias, _canonical):
        continue
    # @alias adds another name to the existing unit (preserving offset
    # behavior for affine units like degC), unlike `name = degC`, which
    # would silently define a new non-offset unit.
    with contextlib.suppress(Exception):
        ureg.define(f"@alias {_canonical} = {_alias}")


# --------- Helper functions ----------


def _slugify(text: str) -> str:
    """Lowercase and collapse non-alnum runs to '-'.

    Args:
        text: Text to slugify.

    Returns:
        Lowercased text with non-alphanumeric runs replaced by hyphens.
    """
    return _SLUG.sub("-", text.lower()).strip("-")


def _normalize_unit(unit: str) -> str:
    """Map known unit aliases (e.g. 'celsius') to canonical pint strings.

    Args:
        unit: Unit string to normalize.

    Returns:
        Canonical pint unit string.
    """
    key = unit.strip()
    if not key:
        return key
    return _UNIT_ALIAS.get(key, key)


def parse_label(label: str) -> tuple[str, str] | None:
    """Split 'Base / unit' into (base, normalised_unit), or None if not parseable.

    Args:
        label: BDF label string in format 'Base / unit'.

    Returns:
        Tuple of (base, normalized_unit) or None if not parseable.
    """
    m = _SLASH_RE.match(str(label))
    if m is None:
        return None
    base = m.group(1).strip()
    unit = _normalize_unit(m.group(2).strip())
    if not base or not unit:
        return None
    return base, unit


def get_unit_conversion(src_unit: str | None, dst_unit: str | None) -> tuple[float, float] | None:
    """Return (scale, offset) for src→dst unit conversion, None if incompatible.

    Args:
        src_unit: Source unit string (or None for dimensionless).
        dst_unit: Destination unit string (or None for dimensionless).

    Returns:
        Tuple of (scale, offset) for conversion, or None if incompatible.
    """
    src_bare = (src_unit or "").strip()
    dst_bare = (dst_unit or "").strip()
    src_is_dim = src_bare in ("", "1")
    dst_is_dim = dst_bare in ("1", "")
    if src_is_dim or dst_is_dim:
        return (1.0, 0.0) if src_is_dim and dst_is_dim else None
    if src_bare.lower() == dst_bare.lower():
        return (1.0, 0.0)
    if src_bare in ("C", "c") and dst_bare in _TEMPERATURE_DST_UNITS:
        src_bare = "degC"
    try:
        qty_dst = ureg.Quantity(1, dst_bare)
        tgt_units = qty_dst.units
        if ureg.Quantity(1, src_bare).dimensionality != qty_dst.dimensionality:
            return None
        at_zero = float(ureg.Quantity(0, src_bare).to(tgt_units).magnitude)
        at_one = float(ureg.Quantity(1, src_bare).to(tgt_units).magnitude)
        scale = round(at_one - at_zero, 15)
        offset = round(at_zero, 15)
        return (scale, offset)
    except pint.errors.PintError:
        return None


def unit_from_label(label: str) -> str | None:
    """Return the unit portion of a 'Base / unit' label, or None.

    Args:
        label: BDF label string in format 'Base / unit'.

    Returns:
        Unit string, or None if label is not parseable.
    """
    parsed = parse_label(label)
    return parsed[1] if parsed else None


# --------- Ontology loading helpers ----------


def _graph_from_bytes(data: bytes, format: str | None = None) -> Graph:
    """Parse bytes into an rdflib Graph; return None on failure.

    Args:
        data: Bytes to parse as RDF.
        format: RDF format (e.g. 'turtle', 'xml'). Auto-detected if None.

    Returns:
        Parsed RDFlib Graph.
    """
    g = Graph()
    return g.parse(data=data, format=format)


def _ontology_cache_dir() -> Path:
    """Return the cache directory for bdf ontology files.

    Honours ``BDF_CACHE_DIR`` (the same env var ``bdf.fetch`` uses) so CI can
    warm versioned ontology releases into the same actions/cache-backed
    directory it restores for other network fixtures, instead of a
    platformdirs path that never survives across CI jobs/runners.

    Returns:
        Path to the cache directory.
    """
    from .fetch import cache_dir

    return cache_dir("bdf")


def _ontology_version_slug(g: Any, raw_bytes: bytes) -> str:
    """Extract version slug from graph owl:versionInfo, falling back to sha256.

    Args:
        g: Parsed RDFlib graph object.
        raw_bytes: Raw bytes of the ontology file.

    Returns:
        Version slug string.
    """
    try:
        from rdflib.namespace import OWL as _OWL

        for lit in g.objects(None, _OWL.versionInfo):
            text = str(lit).strip()
            if text:
                return _slugify(text)
    except Exception:
        pass
    return hashlib.sha256(raw_bytes).hexdigest()[:16]


def _write_ontology_cache(cache_dir: Path, slug: str, content: bytes) -> None:
    """Atomically write ontology bytes to the cache directory.

    Args:
        cache_dir: Directory to write to.
        slug: Version slug used as filename suffix.
        content: Ontology bytes to write.
    """
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        dest = cache_dir / f"bdf-ontology-v{slug}.ttl"
        with tempfile.NamedTemporaryFile(dir=cache_dir, delete=False, suffix=".tmp") as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(content)
        tmp_path.replace(dest)
    except OSError:
        pass


def _read_ontology_cache_latest(cache_dir: Path) -> bytes | None:
    """Return bytes of the newest versioned ontology file in cache, or None.

    Args:
        cache_dir: Directory to search.

    Returns:
        Bytes of the newest cached ontology, or None if none found.
    """
    candidates = sorted(cache_dir.glob("bdf-ontology-v*.ttl"))
    for path in reversed(candidates):
        try:
            return path.read_bytes()
        except OSError:
            continue
    return None


def _english_literals(g: Any, subject: Any, predicate: Any) -> list[str]:
    """Collect non-empty literal values for *predicate*, keeping untagged or English ones."""
    values: list[str] = []
    for lit in g.objects(subject, predicate):
        try:
            text = str(lit)
        except Exception:
            continue
        if getattr(lit, "language", None) not in (None, "en"):
            continue
        if text:
            values.append(text)
    return values


# --------- Pydantic models ----------


class Quantity(BaseModel):
    """One BDF physical quantity: unit, human label, and lookup metadata."""

    unit: str | None
    label_template: str
    dtype: str = "float"
    mr_name: str
    iri: str
    synonyms: list[str]
    deprecated: bool = False
    notation: str = ""
    obligation: str = ""
    definition: str = ""
    description: str = ""
    latex_symbol: str = ""
    latex_formula: str = ""
    derived_from: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _resolve_label_and_dtype(cls, data: Any) -> Any:
        if isinstance(data, dict):
            unit = data.get("unit")
            label_template = data.get("label_template", "")
            if unit is not None and unit != "1" and "{unit}" not in label_template and " / " in label_template:
                base = label_template.split(" / ", 1)[0]
                data["label_template"] = f"{base} / {{unit}}"
            elif unit == "1" and "{unit}" in label_template:
                data["label_template"] = label_template.replace("{unit}", "1")
            if "dtype" not in data:
                if unit == "1":
                    data["dtype"] = "int"
                elif unit is None:
                    desc = (data.get("description") or "").lower()
                    data["dtype"] = "str" if "string" in desc else "int"
                else:
                    data["dtype"] = "float"
        return data

    @field_validator("dtype")
    @classmethod
    def _validate_dtype(cls, v: str) -> str:
        if v not in ("int", "float", "str"):
            raise ValueError(f"dtype must be 'int', 'float', or 'str', got {v!r}")
        return v

    @property
    def required(self) -> bool:
        """True when the term's obligation level is 'required'.

        Derived from `obligation` rather than stored, so the two can never
        disagree. For pre-1.1.0 ontologies without obligation annotations,
        `from_graph_subject` synthesizes the obligation from the static
        fallback set, which this property then reflects.
        """
        return self.obligation == "required"

    @property
    def formatted_label(self) -> str:
        """Return the label with {unit} replaced by the actual unit string."""
        if "{unit}" in self.label_template and self.unit is not None:
            return self.label_template.format(unit=self.unit)
        return self.label_template

    def convert_to(self, dst_unit: str | None) -> tuple[float, float] | None:
        """Return (scale, offset) to convert self.unit → dst_unit, or None.

        Args:
            dst_unit: Destination unit string, or None for dimensionless.

        Returns:
            Tuple of (scale, offset) for conversion, or None if incompatible.
        """
        return get_unit_conversion(self.unit, dst_unit)

    def convert_from(self, src_unit: str | None) -> tuple[float, float] | None:
        """Return (scale, offset) to convert src_unit → self.unit, or None if incompatible.

        Args:
            src_unit: Source unit string, or None for dimensionless.

        Returns:
            Tuple of (scale, offset) for conversion, or None if incompatible.
        """
        return get_unit_conversion(src_unit, self.unit)

    @property
    def effective_notation(self) -> str:
        """notation field if set, otherwise mr_name."""
        return (self.notation or self.mr_name).strip() or self.mr_name

    @classmethod
    def from_graph_subject(cls, g: Any, subject: Any, skos: Any, owl_ns: Any) -> "Quantity | None":
        """Parse one Quantity from an RDF subject. Returns None if malformed.

        Args:
            g: RDFlib graph object.
            subject: RDF subject node.
            skos: SKOS namespace.
            owl_ns: OWL namespace.

        Returns:
            Quantity instance, or None if subject cannot be parsed.
        """
        iri = str(subject)
        if "#" not in iri:
            return None
        mr_name = iri.rsplit("#", 1)[-1]
        ns = iri.rsplit("#", 1)[0] + "#"

        pref_labels = _english_literals(g, subject, skos.prefLabel)
        if not pref_labels:
            return None

        # Read unit from schema:unitCode (authoritative) rather than parsing skos:prefLabel
        unit_codes = _english_literals(g, subject, _SCHEMA_UNIT_CODE)
        if unit_codes:
            unit: str | None = _normalize_unit(unit_codes[0])
        else:
            unit = None

        if unit is not None:
            parsed = parse_label(pref_labels[0])
            base = parsed[0] if parsed else pref_labels[0]
            label_template = f"{base} / {{unit}}"
        else:
            base = pref_labels[0]
            label_template = base

        deprecated = next(
            (str(lit).lower() == "true" for lit in g.objects(subject, owl_ns.deprecated)),
            False,
        )

        alt_labels = _english_literals(g, subject, skos.altLabel)
        notations = _english_literals(g, subject, skos.notation)
        notation = next((s for n in notations if (s := str(n).strip())), mr_name)

        syns = {_slugify(base), _slugify(mr_name)}
        for label in alt_labels + notations:
            base_part = label.split(" / ", 1)[0].strip()
            slug = _slugify(base_part.replace("/", " ").replace("#", " ").replace("_", " "))
            if slug:
                syns.add(slug)
        synonyms = sorted(s for s in syns if s)

        # Ontology-sourced documentation/conformance metadata (absent in pre-1.1.0
        # snapshots, in which case these stay empty and `required` falls back to
        # the static default in from_graph()).
        def _first(predicate: Any) -> str:
            vals = _english_literals(g, subject, predicate)
            return vals[0].strip() if vals else ""

        obligation = _first(URIRef(ns + "obligation")).lower()
        if not obligation and not deprecated:
            # Pre-1.1.0 ontologies carry no :obligation annotations; synthesize
            # the level from the static fallback set so requiredness survives
            # on old snapshots. Deprecated terms never carry an obligation.
            obligation = "required" if mr_name in _REQUIRED_DEFAULT else "optional"
        definition = _first(skos.definition)
        description = _first(URIRef("https://schema.org/description"))
        latex_symbol = _first(URIRef(ns + "latexSymbol"))
        latex_formula = _first(URIRef(ns + "latexFormula"))

        derived = {
            str(obj).rsplit("#", 1)[-1]
            for obj in g.objects(subject, URIRef("http://www.w3.org/ns/prov#wasDerivedFrom"))
            if isinstance(obj, URIRef) and str(obj).startswith(ns)
        }
        derived_from = tuple(sorted(derived))

        return cls(
            unit=unit,
            label_template=label_template,
            mr_name=mr_name,
            notation=notation,
            iri=iri,
            deprecated=deprecated,
            synonyms=synonyms,
            obligation=obligation,
            definition=definition,
            description=description,
            latex_symbol=latex_symbol,
            latex_formula=latex_formula,
            derived_from=derived_from,
        )


class ColumnOntology:
    """Registry of all BDF canonical quantities. Iterate as (mr_name, Quantity) pairs."""

    def __init__(self, quantities: dict[str, Quantity], ontology_version: str = "") -> None:
        """Initialize with a dictionary of quantities.

        Args:
            quantities: Mapping from mr_name to Quantity.
            ontology_version: owl:versionInfo of the source ontology, if known.
        """
        self._quantities = quantities
        self.ontology_version = ontology_version

    def _adopt(self, other: "ColumnOntology") -> None:
        """Take over quantities and version from another instance (in-place reload)."""
        self._quantities = other._quantities
        self.ontology_version = other.ontology_version

    def __iter__(self):
        return iter(self._quantities.items())

    def __getitem__(self, key: str) -> Quantity:
        return self._quantities[key]

    def __contains__(self, key: object) -> bool:
        return key in self._quantities

    def __getattr__(self, name: str) -> Quantity:
        try:
            return self._quantities[name]
        except KeyError:
            raise AttributeError(name) from None

    def get(self, key: str, default: Quantity | None = None) -> Quantity | None:
        """Return quantity by key, or default if absent.

        Args:
            key: Quantity mr_name.
            default: Value to return if key not found.

        Returns:
            Quantity or default.
        """
        return self._quantities.get(key, default)

    def base_synonym_index(self) -> dict[str, str]:
        """Build a mapping from base-name slug to quantity key (machine-readable name).

        Returns:
            Dictionary mapping base-name slugs to mr_name keys.
        """
        idx: dict[str, str] = {}
        for q_name, q in self:
            if q.deprecated:
                continue
            left = q.label_template.split(" / ", 1)[0]
            left_slug = _slugify(left)
            if left_slug:
                idx.setdefault(left_slug, q_name)
            notation_slug = _slugify(q.effective_notation)
            if notation_slug:
                idx.setdefault(notation_slug, q_name)
            for base in q.synonyms:
                slug = _slugify(str(base))
                if slug:
                    idx.setdefault(slug, q_name)
        return idx

    def required_labels(self) -> tuple[str, ...]:
        """Labels of all non-deprecated required quantities.

        Returns:
            Tuple of formatted labels for required quantities.
        """
        return tuple(q.formatted_label for _, q in self if q.required and not q.deprecated)

    def optional_labels(self) -> tuple[str, ...]:
        """Labels of all non-deprecated optional quantities.

        Returns:
            Tuple of formatted labels for optional quantities.
        """
        return tuple(q.formatted_label for _, q in self if not q.required and not q.deprecated)

    @coerce_dataframe
    def validate_df(self, df: pl.LazyFrame) -> pl.LazyFrame:
        """Check ``df`` column names against BDF canonical labels.

        Accepts pandas DataFrame, polars DataFrame, or polars LazyFrame.
        Raises ``BDFValidationError`` if required columns are absent.
        Warns (``UserWarning``) if extra non-BDF columns are present.

        Args:
            df: DataFrame to validate (pandas or polars).

        Returns:
            Validated DataFrame coerced back to the original input type.
        """
        lf = cast(pl.LazyFrame, df)  # guaranteed by @coerce_dataframe
        cols = set(lf.collect_schema().names())

        canonical: set[str] = set()
        required: set[str] = set()
        for _, q in self:
            if q.deprecated:
                continue
            lbl = q.formatted_label
            canonical.add(lbl)
            if q.required:
                required.add(lbl)

        missing = required - cols
        if missing:
            from bdf.validate import BDFValidationError  # lazy — validate imports spec

            raise BDFValidationError(f"Missing required BDF columns: {sorted(missing)}")

        extra = cols - canonical
        if extra:
            warnings.warn(f"Non-BDF columns present: {sorted(extra)}", UserWarning, stacklevel=2)

        return lf

    def quantity_from_label(self, label: str) -> tuple[Quantity, str | None] | None:
        """Return (Quantity, unit) for the given label, or None if not found.

        Parses the label once and returns both the matching non-deprecated Quantity
        and the unit string extracted from the label. Prefers non-deprecated quantities
        when multiple quantities share a base label.

        Args:
            label: BDF label in format 'Base / unit'.

        Returns:
            Tuple of (Quantity, unit_str) if found, None if label is unparseable or
            no matching quantity exists.
        """
        parsed = parse_label(label)
        first_deprecated: tuple[Quantity, str | None] | None = None
        if parsed is None:
            label_lower = label.lower()
            for _, q in self:
                if "{unit}" not in q.label_template and q.label_template.lower() == label_lower:
                    if not q.deprecated:
                        return (q, None)
                    if first_deprecated is None:
                        first_deprecated = (q, None)
            return first_deprecated
        query_base, unit = parsed
        query_base_lower = query_base.lower()
        for _, q in self:
            tmpl_base = q.label_template.split(" / ")[0].strip().lower()
            if tmpl_base == query_base_lower:
                if not q.deprecated:
                    return (q, unit)
                if first_deprecated is None:
                    first_deprecated = (q, unit)
        return first_deprecated

    def mr_name_from_label(self, label: str) -> str | None:
        """Return the mr_name whose label_template base matches label, or None.

        Prefers non-deprecated matches when multiple quantities share a base label.

        Args:
            label: BDF label in format 'Base / unit'.

        Returns:
            Machine-readable name (mr_name) if found, None otherwise.
        """
        parsed = parse_label(label)
        first_deprecated: str | None = None
        if parsed is None:
            label_lower = label.lower()
            for mr_name, q in self:
                if "{unit}" not in q.label_template and q.label_template.lower() == label_lower:
                    if not q.deprecated:
                        return mr_name
                    if first_deprecated is None:
                        first_deprecated = mr_name
            return first_deprecated
        query_base = parsed[0].lower()
        for mr_name, q in self:
            tmpl_base = q.label_template.split(" / ")[0].strip().lower()
            if tmpl_base == query_base:
                if not q.deprecated:
                    return mr_name
                if first_deprecated is None:
                    first_deprecated = mr_name
        return first_deprecated

    @classmethod
    def from_graph(cls, g: Any) -> "ColumnOntology":
        """Build ColumnOntology from an rdflib graph.

        Args:
            g: Parsed RDFlib graph object.

        Returns:
            New ColumnOntology instance.
        """
        quantities: dict[str, Quantity] = {}
        for subject in g.subjects(RDF.type, OWL.Class):
            q = Quantity.from_graph_subject(g, subject, SKOS, OWL)
            if q is not None:
                quantities[q.mr_name] = q

        version = next(
            (str(o) for s in g.subjects(RDF.type, OWL.Ontology) for o in g.objects(s, OWL.versionInfo)),
            "",
        )
        return cls(quantities, ontology_version=version)

    @classmethod
    def get_snapshot(cls, dest: Path | None = None, version: str | None = None) -> Path:
        """Fetch the ontology and write the bundled snapshot.

        Args:
            dest: Path to write snapshot. Defaults to bundled package data path.
            version: Ontology release tag to pin (e.g. '1.1.0'). When given, the
                TTL is fetched from that release tag and the fetched
                owl:versionInfo must match. When None, the live (latest
                deployed) ontology is fetched.

        Returns:
            Path to the written snapshot file.

        Raises:
            requests.HTTPError: If the URL request fails.
            RuntimeError: If the fetched ontology cannot be parsed, or its
                versionInfo does not match the requested release.
        """
        import requests

        if dest is None:
            ref = importlib.resources.files("bdf.data").joinpath("bdf-ontology-snapshot.ttl")
            dest = Path(str(ref))

        url = _BDF_RELEASE_URL_TMPL.format(version=version) if version else _BDF_LIVE_URL
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        raw = resp.content
        g = _graph_from_bytes(raw)
        if g is None:
            raise RuntimeError(f"Failed to parse ontology from {url}")
        if version is not None:
            fetched = cls.from_graph(g).ontology_version
            if fetched != version:
                raise RuntimeError(
                    f"Requested ontology release {version!r} but fetched TTL declares "
                    f"owl:versionInfo {fetched!r}; tag and content disagree."
                )

        serialized = g.serialize(format="turtle")
        content = serialized if isinstance(serialized, bytes) else serialized.encode("utf-8")

        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False, suffix=".tmp") as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(content)
        tmp_path.replace(dest)
        return dest

    @classmethod
    def build(cls) -> "ColumnOntology":
        """Load bundled ontology snapshot and return a new ColumnOntology.

        Returns:
            New ColumnOntology instance parsed from bundled snapshot.

        Raises:
            RuntimeError: If bundled snapshot is missing or cannot be parsed.
        """
        try:
            ref = importlib.resources.files("bdf.data").joinpath("bdf-ontology-snapshot.ttl")
            data = ref.read_bytes()
        except Exception as exc:
            raise RuntimeError(
                f"Bundled ontology snapshot missing or unreadable: {exc}. "
                "Run ColumnOntology.get_snapshot() to regenerate it."
            ) from exc
        g = _graph_from_bytes(data, format="turtle")
        if g is None:
            raise RuntimeError(
                "Bundled ontology snapshot could not be parsed. Run ColumnOntology.get_snapshot() to regenerate it."
            )
        return cls.from_graph(g)

    def load_ttl(self, path: Path | str) -> None:
        """Load ontology from a TTL file, updating quantities in place.

        Args:
            path: Path to TTL file.

        Raises:
            Exception: If file cannot be parsed.
        """
        if Graph is None:
            raise RuntimeError("rdflib is required for load_ttl")
        g = Graph()
        try:
            g.parse(str(path))
        except Exception as exc:
            raise ValueError(f"Failed to parse TTL file {path}: {exc}") from exc
        self._adopt(self.__class__.from_graph(g))

    def load_latest(self, *, refresh: bool = False) -> None:
        """Load the latest available ontology, using cache or fetching live.

        Args:
            refresh: If True, always fetch from the live URL, ignoring cache.

        Raises:
            requests.HTTPError: If the live URL request fails.
            RuntimeError: If the fetched ontology cannot be parsed.
        """
        cache_dir = _ontology_cache_dir()

        if not refresh:
            cached = _read_ontology_cache_latest(cache_dir)
            if cached is not None:
                g = _graph_from_bytes(cached, format="turtle")
                if g is not None:
                    self._adopt(self.__class__.from_graph(g))
                    return

        import requests

        resp = requests.get(_BDF_LIVE_URL, timeout=30)
        resp.raise_for_status()
        raw = resp.content
        g = _graph_from_bytes(raw)
        if g is None:
            raise RuntimeError("Failed to parse ontology from live URL")

        slug = _ontology_version_slug(g, raw)
        serialized = g.serialize(format="turtle")
        content = serialized if isinstance(serialized, bytes) else serialized.encode("utf-8")
        _write_ontology_cache(cache_dir, slug, content)
        self._adopt(self.__class__.from_graph(g))

    def load_version(self, version: str, *, refresh: bool = False) -> None:
        """Load a specific versioned ontology, fetching it if not cached.

        Args:
            version: Version string to load (e.g. '1.0.0').
            refresh: If True, ignore the cache and re-fetch the release.

        Raises:
            requests.HTTPError: If the release URL request fails.
            RuntimeError: If the fetched ontology cannot be parsed, or its
                versionInfo does not match the requested release.
        """
        cache_dir = _ontology_cache_dir()
        versioned = cache_dir / f"bdf-ontology-v{version}.ttl"

        if not refresh and versioned.exists():
            data = versioned.read_bytes()
            g = _graph_from_bytes(data, format="turtle")
            if g is not None:
                self._adopt(self.__class__.from_graph(g))
                return

        self.get_snapshot(dest=versioned, version=version)
        data = versioned.read_bytes()
        g = _graph_from_bytes(data, format="turtle")
        if g is None:
            raise RuntimeError(f"Failed to parse fetched ontology release {version!r}")
        self._adopt(self.__class__.from_graph(g))


def _update_snapshot_cli() -> None:
    """Entry point: fetch ontology and update bundled snapshot.

    Usage: bdf-update-snapshot [VERSION]

    With VERSION (e.g. '1.1.0'), fetches that release tag and verifies the
    fetched owl:versionInfo matches. Without, fetches the live ontology.
    """
    import sys

    version = sys.argv[1] if len(sys.argv) > 1 else None
    path = ColumnOntology.get_snapshot(version=version)
    pinned = f" (pinned to release {version})" if version else ""
    print(f"Snapshot updated: {path}{pinned}")


# --------- Module-level singleton ----------

COLUMN_ONTOLOGY: ColumnOntology = ColumnOntology.build()


__all__ = [
    "ColumnOntology",
    "Quantity",
    "COLUMN_ONTOLOGY",
    "ureg",
    "parse_label",
    "unit_from_label",
    "get_unit_conversion",
]
