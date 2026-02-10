# src/bdf/normalize/normalize.py
from __future__ import annotations

import contextlib
import re
import warnings
from collections.abc import Mapping

import pandas as pd

from bdf.ontology_labels import load_alias_index
from bdf.units import convert_series, parse_from_header

from . import spec

_SLUG = re.compile(r"[^a-z0-9]+")
def _slugify(s: str) -> str:
    return _SLUG.sub("-", s.lower()).strip("-")

# Public constants
REQUIRED = spec.required_labels()
OPTIONAL = spec.optional_labels()

# Force these three to be the first columns when present
ORDERED_REQUIRED = ("Test Time / s", "Voltage / V", "Current / A")
_REQUIRED_SLUGS = {_slugify(s.split(" / ", 1)[0]) for s in ORDERED_REQUIRED}

def guess_plugin_by_columns(df: pd.DataFrame, *, current_id: str | None = None):
    """
    Best-effort plugin guesser based on column headers only (no re-parse).
    Returns an instantiated plugin or None if no reasonable match is found.
    """
    try:
        from bdf.data_sources import all_plugins
    except Exception:
        return None

    header_slugs = set()
    for col in df.columns:
        base, _unit, _source = parse_from_header(str(col))
        base_slug = _slugify(base.replace("/", " ").replace("#", " "))
        if base_slug:
            header_slugs.add(base_slug)

    if not header_slugs:
        return None

    best = None
    best_score = 0.0

    for cls in all_plugins():
        pid = getattr(cls, "id", None)
        if current_id and pid == current_id:
            continue
        try:
            plugin = cls()
        except Exception:
            continue

        plugin_idx = _merge_plugin_column_synonyms(plugin)
        if not plugin_idx:
            continue

        keys = set(plugin_idx.keys())
        hits = header_slugs & keys
        if not hits:
            continue

        req_hits = hits & _REQUIRED_SLUGS
        score = len(hits) + 2.0 * len(req_hits)
        if score > best_score:
            best_score = score
            best = plugin

    # Require at least a couple of matched columns to avoid wild guesses
    if best_score >= 3.0:
        return best
    return None

def _canon_label(quantity: str) -> str:
    return spec._label_for(quantity)

def _quantity_unit(quantity: str) -> str:
    return spec.unit_for(quantity)

def _base_index() -> Mapping[str, str]:
    return spec.base_synonym_index()

def _merge_plugin_column_synonyms(plugin) -> dict[str, str]:
    idx: dict[str, str] = {}
    if plugin is None:
        return idx
    try:
        raw = getattr(plugin, "column_synonyms", None)
        if callable(raw):
            raw = raw()
        if isinstance(raw, Mapping):
            for canon_label, patterns in raw.items():
                # normalize plugin label to our canon (match left side)
                left = canon_label.split(" / ", 1)[0].strip().lower()
                target = None
                for q in spec.COLUMNS:
                    if spec._label_for(q).split(" / ", 1)[0].strip().lower() == left:
                        target = spec._label_for(q)
                        break
                final_label = target or canon_label
                for p in patterns or []:
                    base, _unit, _src = parse_from_header(str(p))
                    idx[_slugify(base)] = final_label
    except Exception:
        pass
    return idx

def _legacy_alias_index() -> dict[str, object]:
    return load_alias_index()

def _is_numeric_series(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s)

def _coerce_with_decimal(s: pd.Series, decimal_hint: str | None) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        return s
    try:
        s_str = s.astype("string")
    except Exception:
        return s

    if decimal_hint and decimal_hint != ".":
        with contextlib.suppress(Exception):
            s_num = pd.to_numeric(s_str.str.replace(decimal_hint, ".", regex=False), errors="coerce")
            if s_num.notna().any():
                return s_num

    with contextlib.suppress(Exception):
        if s_str.str.contains(r"[0-9],[0-9]", regex=True).any():
            s_num = pd.to_numeric(s_str.str.replace(",", ".", regex=False), errors="coerce")
            if s_num.notna().any():
                return s_num

    return s

def _coalesce_into(target: pd.Series, incoming: pd.Series) -> pd.Series:
    """
    Merge two series into 'target':
    - Prefer numeric over non-numeric when target is non-numeric
    - Otherwise fill missing values in target with incoming
    """
    # numeric preference
    tnum, inum = _is_numeric_series(target), _is_numeric_series(incoming)
    if inum and not tnum:
        # incoming is better-typed numeric -> replace target where possible
        with contextlib.suppress(Exception):
            incoming = pd.to_numeric(incoming, errors="coerce")
        return incoming

    # general 'fill holes'
    return target.where(target.notna(), incoming)

def normalize_columns(
    df: pd.DataFrame,
    *,
    plugin=None,
    strict: bool = True,
    include_optional: bool = True,
    keep_unmapped: bool = False,
) -> pd.DataFrame:
    out = df.copy()
    meta: dict[str, dict[str, str]] = {}

    base_idx = dict(_base_index())                   # slug -> quantity (official MR name)
    plugin_direct = _merge_plugin_column_synonyms(plugin)  # slug -> canonical label
    alias_idx = _legacy_alias_index()
    decimal_hint = getattr(plugin, "decimal", None)

    recognized: list[str] = []
    produced: set[str] = set()  # canonical labels we've established in 'out'
    legacy_headers: list[str] = []

    # ---- Derive Unix Time / s from Timestamp if present (prefer derived) ----
    if "Timestamp" in out.columns and "Unix Time / s" not in out.columns:
        try:
            ts = pd.to_datetime(out["Timestamp"], utc=True, errors="coerce")
            out["Unix Time / s"] = (ts.view("int64") / 1e9)
            recognized.append("Unix Time / s")
            produced.add("Unix Time / s")
            meta["Unix Time / s"] = {
                "quantity": "unix_time_second",
                "sourceHeader": "Timestamp",
                "sourceUnit": "",
                "unit": "s",
                "parsedFrom": "derived",
            }
        except Exception:
            pass

    # ---- Map every column we can to canon (with dedup + coalesce) ----
    for col in list(out.columns):
        # Skip the derived source to avoid remapping the original Timestamp column
        if col == "Timestamp" and "Unix Time / s" in out.columns:
            continue

        base, unit_expr, source = parse_from_header(str(col))
        base_slug = _slugify(base.replace("/", " ").replace("#", " "))
        full_slug = _slugify(str(col).replace("/", " ").replace("#", " "))

        # 1) plugin can force a direct canonical label
        canon = plugin_direct.get(base_slug)
        quantity = None
        target_unit = None
        alias = alias_idx.get(base_slug) or alias_idx.get(full_slug)

        if canon:
            # find spec quantity by left label part
            left = canon.split(" / ", 1)[0].strip().lower()
            for q in spec.COLUMNS:
                if spec._label_for(q).split(" / ", 1)[0].strip().lower() == left:
                    quantity = q
                    target_unit = spec.unit_for(q)
                    canon = spec._label_for(q)  # normalize to exact canon
                    break
            if target_unit is None:
                # Fallback to the unit part of the plugin label
                target_unit = canon.split(" / ", 1)[-1]
        elif alias:
            quantity = alias.quantity
            target_unit = alias.unit
            canon = alias.label
        else:
            # 2) base-name -> quantity from spec
            quantity = base_idx.get(base_slug)
            if not quantity:
                continue
            target_unit = spec.unit_for(quantity)
            canon = _canon_label(quantity)

        # Special guard: If mapping to unix time from a non-numeric string (e.g., 'Timestamp'),
        # but a canonical Unix Time already exists, skip to avoid duplicates.
        if quantity == "unix_time_second" and "Unix Time / s" in out.columns and not _is_numeric_series(out[col]):
            continue

        # Locale-aware coercion when numbers carry comma decimals
        out[col] = _coerce_with_decimal(out[col], decimal_hint)

        # Unit conversion if we can (legacy alias may imply the source unit)
        unit_src = ""
        if alias and alias.source_unit and not unit_expr:
            unit_expr = alias.source_unit
        if _is_numeric_series(out[col]) and unit_expr:
            try:
                out[col] = convert_series(out[col], unit_expr, target_unit)
                unit_src = unit_expr
            except Exception:
                unit_src = ""

        # If canonical already exists in DataFrame, coalesce then drop this column
        if canon in out.columns and col != canon:
            out[canon] = _coalesce_into(out[canon], out[col])
            # record meta (append note)
            meta.setdefault(canon, {
                "quantity": quantity or "",
                "sourceHeader": "",
                "sourceUnit": "",
                "unit": target_unit,
                "parsedFrom": source or "",
            })
            # Append provenance (multiple sources)
            prev = meta[canon].get("sourceHeader", "")
            meta[canon]["sourceHeader"] = (prev + "|" if prev else "") + str(col)
            # drop duplicate source
            out.drop(columns=[col], inplace=True)
            recognized.append(canon)
            produced.add(canon)
            continue

        # If we produced this canon earlier (via another vendor column), coalesce and drop
        if canon in produced and col != canon and canon in out.columns:
            out[canon] = _coalesce_into(out[canon], out[col])
            out.drop(columns=[col], inplace=True)
            recognized.append(canon)
            continue

        # Normal path: rename (if needed), record meta
        if canon != col:
            out.rename(columns={col: canon}, inplace=True)
        recognized.append(canon)
        produced.add(canon)
        meta[canon] = {
            "quantity": quantity or "",
            "sourceHeader": str(col),
            "sourceUnit": unit_src,
            "unit": target_unit,
            "parsedFrom": source if quantity else (source or "plugin-canonical"),
        }
        if alias:
            legacy_headers.append(str(col))

    # ---- Selection logic: keep all canonical columns we recognized (no dup) ----
    have = set(out.columns)  # after coalescing/renaming

    if strict:
        missing = [c for c in REQUIRED if c not in have]
        if missing:
            sample_cols = list(out.columns)[:25]
            raise ValueError(
                "Missing required BDF columns after normalization: "
                f"{missing}. Seen columns: {sample_cols}"
            )


    if not include_optional and not keep_unmapped:
        # only required, in the forced order
        keep_set = set(REQUIRED)
        candidate = [c for c in out.columns if c in keep_set]
    elif not keep_unmapped:
        # keep all canonical (REQUIRED + OPTIONAL we actually have)
        canonical_all = set(REQUIRED) | set(OPTIONAL)
        candidate = [c for c in out.columns if c in canonical_all]
    else:
        # keeping vendor columns too; just reorder, don't drop
        candidate = list(out.columns)

    # Always move required trio to the very front in the specified order
    front = [c for c in ORDERED_REQUIRED if c in candidate]
    tail  = [c for c in candidate if c not in ORDERED_REQUIRED]
    out = out[front + tail].copy()

    out.attrs["bdf:columns"] = meta
    if legacy_headers:
        warnings.warn(
            "Legacy BDF column labels detected (skos:altLabel/notation). "
            "They were normalized to preferred labels.",
            stacklevel=2,
        )
    return out


def canonicalize_legacy_labels(
    df: pd.DataFrame,
    *,
    keep_unmapped: bool = True,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Rename legacy ontology labels to preferred labels, with unit conversion if needed.
    Returns (new_df, legacy_headers_used).
    """
    alias_idx = _legacy_alias_index()

    out = df.copy()
    legacy_headers: list[str] = []
    base_preferred: dict[str, str] = {}
    deprecated_pref_labels: set[str] = set()
    deprecated_notations: set[str] = set()
    for q, s in spec.COLUMNS.items():
        if bool(s.get("deprecated")):
            continue
        base = spec._label_for(q).split(" / ", 1)[0].strip().lower()
        base_preferred.setdefault(base, q)

    notation_to_canon: dict[str, tuple[str, str]] = {}
    pref_to_canon: dict[str, tuple[str, str]] = {}
    for q in spec.COLUMNS:
        s = spec.COLUMNS[q]
        pref = spec._label_for(q)
        notation = spec.notation_for(q)
        target_q = q
        if bool(s.get("deprecated")):
            base = pref.split(" / ", 1)[0].strip().lower()
            target_q = base_preferred.get(base, q)
            deprecated_pref_labels.add(pref)
            deprecated_notations.add(notation)
        target_canon = spec._label_for(target_q)
        target_unit = spec.unit_for(target_q)
        notation_to_canon[notation] = (target_canon, target_unit)
        pref_to_canon[pref] = (target_canon, target_unit)

    for col in list(out.columns):
        pref_hit = pref_to_canon.get(str(col))
        if pref_hit:
            canon, _target_unit = pref_hit
            if str(col) in deprecated_pref_labels and str(col) != canon:
                legacy_headers.append(str(col))
            if canon in out.columns and col != canon:
                out[canon] = _coalesce_into(out[canon], out[col])
                out.drop(columns=[col], inplace=True)
            elif canon != col:
                out.rename(columns={col: canon}, inplace=True)
            continue

        notation_hit = notation_to_canon.get(str(col))
        if notation_hit:
            canon, _target_unit = notation_hit
            if str(col) in deprecated_notations:
                legacy_headers.append(str(col))
            if canon in out.columns and col != canon:
                out[canon] = _coalesce_into(out[canon], out[col])
                out.drop(columns=[col], inplace=True)
            elif canon != col:
                out.rename(columns={col: canon}, inplace=True)
            continue

        base, unit_expr, _source = parse_from_header(str(col))
        base_slug = _slugify(base.replace("/", " ").replace("#", " "))
        full_slug = _slugify(str(col).replace("/", " ").replace("#", " "))
        alias = alias_idx.get(base_slug) or alias_idx.get(full_slug)
        if not alias:
            continue
        legacy_headers.append(str(col))

        canon = alias.label
        target_unit = alias.unit
        if alias.source_unit and not unit_expr:
            unit_expr = alias.source_unit

        if _is_numeric_series(out[col]) and unit_expr:
            with contextlib.suppress(Exception):
                out[col] = convert_series(out[col], unit_expr, target_unit)

        if canon in out.columns and col != canon:
            out[canon] = _coalesce_into(out[canon], out[col])
            out.drop(columns=[col], inplace=True)
        elif canon != col:
            out.rename(columns={col: canon}, inplace=True)

    if keep_unmapped:
        return out, legacy_headers
    # If dropping unmapped, keep only canonical columns
    canonical_all = set(REQUIRED) | set(OPTIONAL)
    out = out[[c for c in out.columns if c in canonical_all]].copy()
    return out, legacy_headers
