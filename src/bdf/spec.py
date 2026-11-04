"""Single source of truth for BDF canonical columns.

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

from __future__ import annotations

import contextlib
import importlib.resources
import re
import shutil
import uuid
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, field_validator, model_validator
from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, SKOS

from bdf._definitions import DefinitionSource
from bdf._df_compat import coerce_dataframe
from bdf._errors import BDFDefinitionError

if TYPE_CHECKING:
    import pint
    import polars as pl

    ureg: pint.UnitRegistry

# --------- Constants ----------

_SLUG = re.compile(r"[^a-z0-9]+")
_REQUIRED_DEFAULT = {"test_time_second", "voltage_volt", "current_ampere"}

# Generic-recognition disambiguation for vendor headers whose naive
# label/notation slug resolves to the wrong quantity. Applied last (winning)
# in base_synonym_index. Rationale, per the ontology's own definitions:
#   - A raw column literally named "Step Index" / "Step_Index" is the program
#     step identifier (Arbin Step_Index, Digatron Step, BioLogic Ns) => step_id,
#     NOT the per-step record counter. Without this override the deprecated
#     step_index synonym redirects the slug to step_record_index via
#     isReplacedBy, silently relabeling vendor step ids. Canonically labelled
#     data ("Step Record Index / 1") still resolves via exact-label paths.
#   - "Step", "Ns", and "Cycle Index" were previously unmapped; map them here.
_VENDOR_SYNONYM_OVERRIDES: dict[str, str] = {
    "step-index": "step_id",
    "step": "step_id",
    "ns": "step_id",
    "cycle-index": "cycle_count",
}
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
    "Ω": "ohm",  # 'ohm' character, 'Omega' character already understood by pint
}

# Bare "C" or "c" is ambiguous (Celsius vs Coulombs). This set lists the BDF
# destination units that unambiguously identify a temperature quantity, allowing
# bare "C"/"c" to be treated as Celsius in get_unit_conversion.
_TEMPERATURE_DST_UNITS: frozenset[str] = frozenset({"degC", "°C", "K", "degF"})
_SLASH_RE = re.compile(r"^\s*(.+?)\s*/\s*(.+)\s*$")
_SCHEMA_UNIT_CODE = URIRef("https://schema.org/unitCode")
# The ontology's persistent w3id URIs. A release's URI is its owl:versionIRI,
# and serves the TTL of that release's git tag. w3id routes X.Y.Z names only.
_BDF_W3ID = "https://w3id.org/battery-data-alliance/ontology/battery-data-format"
_BDF_RELEASE_URL_TMPL = f"{_BDF_W3ID}/{{version}}/battery-data-format"
# Serves the latest ontology release; its owl:versionInfo names that release.
_BDF_LATEST_URL = f"{_BDF_W3ID}/latest"

_ureg: pint.UnitRegistry | None = None


def _pint_understands(reg: pint.UnitRegistry, alias: str, canonical: str) -> bool:
    """True if pint already parses `alias` to the same value as `canonical`.

    Args:
        reg: Registry to resolve both unit strings against.
        alias: Candidate unit string (e.g. a UCUM code).
        canonical: Known-good pint unit string to compare against.

    Returns:
        True if pint resolves `alias` to the same quantity as `canonical`
        both at 0 and 1 (catching offset units, not just dimensionality).
    """
    import pint

    try:
        a0, c0 = reg.Quantity(0, alias), reg.Quantity(0, canonical)
        a1, c1 = reg.Quantity(1, alias), reg.Quantity(1, canonical)
        return (
            abs(a0.to(c0.units).magnitude - c0.magnitude) < 1e-9
            and abs(a1.to(c1.units).magnitude - c1.magnitude) < 1e-9
        )
    except (pint.errors.PintError, AssertionError):
        # PintError covers undefined/incompatible units; AssertionError covers
        # strings pint's parser chokes on internally (e.g. "℃" / ℃), which
        # would otherwise escape and crash registry construction on pint >= 0.25.
        return False


def _get_ureg() -> pint.UnitRegistry:
    """Return the shared unit registry, build on first use then cache.

    Returns:
        UnitRegistry with the BDF unit aliases defined.
    """
    global _ureg
    if _ureg is None:
        import pint

        reg: pint.UnitRegistry = pint.UnitRegistry()
        for alias, canonical in _UNIT_ALIAS.items():
            if _pint_understands(reg, alias, canonical):
                continue
            # @alias adds another name to the existing unit (preserving offset
            # behavior for affine units like degC), unlike `name = degC`, which
            # would silently define a new non-offset unit.
            with contextlib.suppress(Exception):
                reg.define(f"@alias {canonical} = {alias}")
        _ureg = reg
    return _ureg


def __getattr__(name: str) -> Any:
    """Get lazily built module attributes."""
    if name == "ureg":
        return _get_ureg()
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


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


_UNIT_CAPTURE = r"([A-Za-z0-9.·/*^%°℃ΩΩµμ⁰¹²³⁴⁵⁶⁷⁸⁹⁻ \-]+)"  # omega/ohm and mu/micro are different characters


def get_unit_conversion(src_unit: str | None, dst_unit: str | None) -> tuple[float, float] | None:
    """Return (scale, offset) for src→dst unit conversion, None if incompatible.

    Args:
        src_unit: Source unit string (or None for dimensionless).
        dst_unit: Destination unit string (or None for dimensionless).

    Returns:
        Tuple of (scale, offset) for conversion, or None if incompatible.
    """
    src_bare = _normalize_unit((src_unit or "").strip())
    dst_bare = _normalize_unit((dst_unit or "").strip())
    src_is_dim = src_bare in ("", "1")
    dst_is_dim = dst_bare in ("1", "")
    if src_is_dim or dst_is_dim:
        return (1.0, 0.0) if src_is_dim and dst_is_dim else None
    if src_bare.lower() == dst_bare.lower():
        return (1.0, 0.0)
    if src_bare in ("C", "c") and dst_bare in _TEMPERATURE_DST_UNITS:
        src_bare = "degC"

    import pint

    reg = _get_ureg()
    try:
        qty_dst = reg.Quantity(1, dst_bare)
        tgt_units = qty_dst.units
        if reg.Quantity(1, src_bare).dimensionality != qty_dst.dimensionality:
            return None
        at_zero = float(reg.Quantity(0, src_bare).to(tgt_units).magnitude)
        at_one = float(reg.Quantity(1, src_bare).to(tgt_units).magnitude)
        scale = round(at_one - at_zero, 15)
        offset = round(at_zero, 15)
        return (scale, offset)
    except (pint.errors.PintError, AssertionError):
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


def _declared_version(g: Graph) -> str:
    """Return the owl:versionInfo of the graph's ontology, or "" if it states none."""
    return next(
        (str(o) for s in g.subjects(RDF.type, OWL.Ontology) for o in g.objects(s, OWL.versionInfo)),
        "",
    )


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
    replaced_by: str = ""
    """mr_name of the non-deprecated replacement (dcterms:isReplacedBy); empty when
    not deprecated or the ontology carries no link."""
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

        # dcterms:isReplacedBy links a deprecated term to its preferred replacement;
        # the IRI fragment is the replacement's mr_name. Left empty when absent so
        # consumers fall back to the label base-name heuristic.
        replaced_by = next(
            (
                str(obj).rsplit("#", 1)[-1]
                for obj in g.objects(subject, URIRef("http://purl.org/dc/terms/isReplacedBy"))
                if isinstance(obj, URIRef) and str(obj).startswith(ns)
            ),
            "",
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
            replaced_by=replaced_by,
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
        # Apply vendor-header disambiguation last so it wins over the naive
        # label/notation slugs (see _VENDOR_SYNONYM_OVERRIDES).
        for slug, q_name in _VENDOR_SYNONYM_OVERRIDES.items():
            if q_name in self._quantities:
                idx[slug] = q_name
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
    def validate_df(self, df: pl.LazyFrame, *, raise_on_error: bool = True) -> pl.LazyFrame:
        """Check ``df`` column names against BDF canonical labels.

        Accepts pandas DataFrame, polars DataFrame, or polars LazyFrame.
        Warns (``UserWarning``) if extra non-BDF columns, deprecated/legacy BDF labels, or
        non-canonical-unit BDF labels are present. Missing required columns raise
        ``BDFValidationError`` by default; pass ``raise_on_error=False`` to warn instead. A
        column counts as satisfying a required quantity if it matches that quantity's
        preferred label, machine-readable name, a deprecated/legacy label that replaces it
        (e.g. ``"Test Time / ms"`` for ``"Test Time / s"``), or its label with a
        unit-compatible-but-non-canonical unit (e.g. ``"Voltage / mV"`` for ``"Voltage / V"``).

        Args:
            df: DataFrame to validate (pandas or polars).
            raise_on_error: Raise ``BDFValidationError`` on missing required columns (default
                True); False emits a ``UserWarning`` instead.

        Returns:
            Validated DataFrame coerced back to the original input type.
        """
        cols = set(df.collect_schema().names())

        canonical: set[str] = set()
        required_by_label: dict[str, str] = {}  # formatted_label -> mr_name
        for mr_name, q in self:
            if q.deprecated:
                continue
            canonical.add(q.formatted_label)
            canonical.add(q.effective_notation)
            if q.required:
                required_by_label[q.formatted_label] = mr_name

        missing = {
            lbl
            for lbl, mr_name in required_by_label.items()
            if lbl not in cols and self[mr_name].effective_notation not in cols
        }

        legacy_pairs: list[tuple[str, str]] = []
        handled: set[str] = set()
        for _, q in self:
            if not q.deprecated or not q.replaced_by:
                continue
            hit = next((lbl for lbl in (q.formatted_label, q.effective_notation) if lbl in cols), None)
            if hit is None:
                continue
            replacement = self[q.replaced_by].formatted_label if q.replaced_by in self else None
            if replacement is None:
                continue
            legacy_pairs.append((hit, replacement))
            missing.discard(replacement)
            handled.add(hit)

        if legacy_pairs:
            detail = ", ".join(f"{old!r} -> {new!r}" for old, new in legacy_pairs)
            warnings.warn(
                f"Legacy BDF column labels detected: {detail}. Update to preferred labels.",
                UserWarning,
                stacklevel=2,
            )

        non_canonical_units: list[tuple[str, str]] = []
        for _, q in self:
            if q.deprecated or q.unit is None or "{unit}" not in q.label_template:
                continue
            parts = q.label_template.split("{unit}")
            pattern = _UNIT_CAPTURE.join(re.escape(p) for p in parts)
            for c in cols - canonical - handled:
                m = re.fullmatch(pattern, c)
                if m is None or get_unit_conversion(m.group(1), q.unit) is None:
                    continue
                non_canonical_units.append((c, q.formatted_label))
                handled.add(c)
                missing.discard(q.formatted_label)

        if non_canonical_units:
            detail = ", ".join(f"{c!r} (canonical: {canon!r})" for c, canon in non_canonical_units)
            warnings.warn(
                f"Columns not using the canonical BDF unit: {detail}",
                UserWarning,
                stacklevel=2,
            )

        extra = cols - canonical - handled

        if missing:
            detail = f"Missing required BDF columns: {sorted(missing)}"
            if extra:
                detail += f"; unrecognized columns present: {sorted(extra)}"
            if raise_on_error:
                from ._errors import BDFValidationError

                raise BDFValidationError(detail)
            warnings.warn(detail, UserWarning, stacklevel=2)
        elif extra:
            warnings.warn(f"Non-BDF columns present: {sorted(extra)}", UserWarning, stacklevel=2)

        return df

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

    @coerce_dataframe
    def rename_labels(self, df: pl.LazyFrame, mode: Literal["preferred", "machine", "unchanged"]) -> pl.LazyFrame:
        """Rename BDF columns between preferred-label and machine-readable notation.

        E.g. ``"Voltage / V"`` <-> ``"voltage_volt"``.
        Columns outside the BDF spec are left as-is and warn.

        Args:
            df: DataFrame (pandas|polars|lazy) with BDF columns.
            mode: Target column style.
                "preferred": Rename to BDF preferred label, e.g. "Voltage / V".
                "machine": Rename to BDF machine-readable notation, e.g. "voltage_volt".
                "unchanged": Leave columns as-is.

        Returns:
            DataFrame of the same type, with matched columns renamed.
        """
        if mode == "unchanged":
            return df
        if mode == "preferred":
            mapping_source = {q.effective_notation: q.formatted_label for _, q in self if not q.deprecated}
            already_target = {q.formatted_label for _, q in self if not q.deprecated}
        elif mode == "machine":
            mapping_source = {q.formatted_label: q.effective_notation for _, q in self if not q.deprecated}
            already_target = {q.effective_notation for _, q in self if not q.deprecated}
        else:
            msg = f"Mode '{mode}' not understood. Use 'preferred', 'machine', or 'unchanged'."
            raise ValueError(msg)
        cols = df.collect_schema().names()
        mapping = {c: mapping_source[c] for c in cols if c in mapping_source}
        unmatched = [c for c in cols if c not in mapping_source and c not in already_target]
        if unmatched:
            warnings.warn(
                f"The following columns could not be converted to '{mode}' and were left as-is: {unmatched}",
                UserWarning,
                stacklevel=2,
            )
        return df.rename(mapping) if mapping else df

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

        return cls(quantities, ontology_version=_declared_version(g))

    @classmethod
    def get_snapshot(cls, dest: Path | None = None, version: str | None = None) -> Path:
        """Fetch an ontology release and write it as a snapshot.

        Args:
            dest: TTL file to write. Defaults to the bundled ontology file, and
                then the bundled version directory is replaced.
            version: Ontology release to fetch (e.g. '1.1.0'). The fetched
                owl:versionInfo must match. When None, upstream's latest
                GitHub release is fetched, not the live deployment.

        Returns:
            Path to the written TTL file.

        Raises:
            BDFDefinitionError: If the latest release cannot be named or
                requested, or the fetch, the parse, or the versionInfo check
                fails. ``dest`` (or the bundle) is unchanged on failure.
        """
        if version is None:
            try:
                version = ONTOLOGY_SOURCE.latest()
            except Exception as exc:
                raise BDFDefinitionError(f"Could not find the latest {ONTOLOGY_SOURCE.name} release: {exc}") from exc
        if dest is None:
            ONTOLOGY_SOURCE.write(ONTOLOGY_SOURCE.bundled, version)
            return ONTOLOGY_SOURCE.bundled / _ONTOLOGY_TTL
        # Stage beside dest, so the last rename is atomic.
        staging = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}")
        try:
            release = ONTOLOGY_SOURCE.write(staging, version)
            (release / _ONTOLOGY_TTL).replace(dest)
        except OSError as exc:
            raise BDFDefinitionError(f"Could not write {dest}: {exc}") from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return dest

    @classmethod
    def build(cls) -> "ColumnOntology":
        """Load the bundled ontology release and return a new ColumnOntology.

        Returns:
            New ColumnOntology instance holding the bundled release.

        Raises:
            BDFDefinitionError: If the bundled release is missing or cannot be
                parsed.
        """
        try:
            return ONTOLOGY_SOURCE.get(ONTOLOGY_SOURCE.bundled_version() or "", fetch=False)
        except Exception as exc:
            raise BDFDefinitionError(
                f"Bundled ontology release missing or unreadable: {exc}. Run bdf-update-snapshot to regenerate it."
            ) from exc

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
        """Load the latest ontology release, which the ontology's ``/latest`` w3id URI names.

        Falls back to the newest local final release, with a
        ``BDFDefinitionWarning``, when upstream cannot be asked or the release
        cannot be fetched.

        Args:
            refresh: If True, ask upstream and fetch the release again.

        Raises:
            BDFDefinitionError: If the release cannot be loaded and no local
                release exists. ``self`` is unchanged on failure.
        """
        self._adopt(ONTOLOGY_SOURCE.get_latest(refresh=refresh))

    def load_version(self, version: str, *, refresh: bool = False) -> None:
        """Load a specific ontology release, fetching it if not local.

        Args:
            version: Release to load (e.g. '1.0.0').
            refresh: If True, ignore the cache and fetch the release again.

        Raises:
            BDFDefinitionError: If the release cannot be fetched or parsed, or
                its versionInfo does not match. ``self`` is unchanged on
                failure.
        """
        self._adopt(ONTOLOGY_SOURCE.get(version, refresh=refresh))


_ONTOLOGY_TTL = "bdf-ontology.ttl"


def _download_ontology(tag: str, dest: Path, timeout: float) -> None:
    """Download one ontology release into the empty directory ``dest``."""
    import requests

    resp = requests.get(_BDF_RELEASE_URL_TMPL.format(version=tag), headers={"Accept": "text/turtle"}, timeout=timeout)
    resp.raise_for_status()
    declared = _declared_version(_graph_from_bytes(resp.content, format="turtle"))
    if DefinitionSource.normalise(declared) != DefinitionSource.normalise(tag):
        raise BDFDefinitionError(
            f"Requested ontology release {tag!r} but fetched TTL declares "
            f"owl:versionInfo {declared!r}; tag and content disagree."
        )
    (dest / _ONTOLOGY_TTL).write_bytes(resp.content)


def _latest_ontology_version(timeout: float) -> str:
    """Return the owl:versionInfo of the ontology that the latest URI serves."""
    import requests

    resp = requests.get(_BDF_LATEST_URL, headers={"Accept": "text/turtle"}, timeout=timeout)
    resp.raise_for_status()
    declared = _declared_version(_graph_from_bytes(resp.content, format="turtle"))
    if not declared:
        raise BDFDefinitionError(f"The ontology at {_BDF_LATEST_URL} states no owl:versionInfo")
    return declared


def _open_ontology(path: Path) -> ColumnOntology:
    """Parse the ontology version directory ``path``."""
    g = _graph_from_bytes((path / _ONTOLOGY_TTL).read_bytes(), format="turtle")
    return ColumnOntology.from_graph(g)


ONTOLOGY_SOURCE: DefinitionSource[ColumnOntology] = DefinitionSource(
    "bdf-ontology",
    bundled=Path(str(importlib.resources.files("bdf.data") / "bdf-ontology")),
    tag_prefix="",
    find_latest=_latest_ontology_version,
    download=_download_ontology,
    open=_open_ontology,
)


def _update_snapshot_cli() -> None:
    """Entry point: replace the bundled ontology release.

    Usage: bdf-update-snapshot [VERSION]

    With VERSION (e.g. '1.1.0'), fetches that release and verifies the
    fetched owl:versionInfo matches. Without, fetches upstream's latest
    GitHub release.
    """
    ONTOLOGY_SOURCE.cli("bdf-update-snapshot")


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
