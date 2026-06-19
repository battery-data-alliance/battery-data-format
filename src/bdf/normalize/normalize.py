# src/bdf/normalize/normalize.py
from __future__ import annotations

import contextlib
import re
import warnings
from collections.abc import Mapping

import pandas as pd

from bdf import spec
from bdf.units import convert_series, parse_from_header

_SLUG = re.compile(r"[^a-z0-9]+")


def _slugify(s: str) -> str:
    return _SLUG.sub("-", s.lower()).strip("-")


# Public constants
REQUIRED = spec.COLUMN_ONTOLOGY.required_labels()
OPTIONAL = spec.COLUMN_ONTOLOGY.optional_labels()

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
    return spec.COLUMN_ONTOLOGY[quantity].formatted_label


def _quantity_unit(quantity: str) -> str:
    return spec.COLUMN_ONTOLOGY[quantity].unit


def _base_index() -> Mapping[str, str]:
    return spec.COLUMN_ONTOLOGY.base_synonym_index()


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
                # normalize plugin label to our canon (match left side, skip deprecated)
                left = canon_label.split(" / ", 1)[0].strip().lower()
                target = None
                for _q, qty in spec.COLUMN_ONTOLOGY:
                    if qty.deprecated:
                        continue
                    if qty.formatted_label.split(" / ", 1)[0].strip().lower() == left:
                        target = qty.formatted_label
                        break
                final_label = target or canon_label
                for p in patterns or []:
                    base, _unit, _src = parse_from_header(str(p))
                    idx[_slugify(base)] = final_label
    except Exception:
        pass
    return idx


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

    base_idx = dict(_base_index())  # slug -> quantity (official MR name)
    plugin_direct = _merge_plugin_column_synonyms(plugin)  # slug -> canonical label
    decimal_hint = getattr(plugin, "decimal", None)

    recognized: list[str] = []
    produced: set[str] = set()  # canonical labels we've established in 'out'
    legacy_headers: list[str] = []

    # ---- Derive Unix Time / s from Timestamp if present (prefer derived) ----
    if "Timestamp" in out.columns and "Unix Time / s" not in out.columns:
        try:
            ts = pd.to_datetime(out["Timestamp"], utc=True, errors="coerce")
            out["Unix Time / s"] = ts.view("int64") / 1e9
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

        # 1) plugin can force a direct canonical label
        canon = plugin_direct.get(base_slug)
        quantity = None
        target_unit = None

        if canon:
            # find spec quantity by left label part (skip deprecated)
            left = canon.split(" / ", 1)[0].strip().lower()
            for q, qty in spec.COLUMN_ONTOLOGY:
                if qty.deprecated:
                    continue
                if qty.formatted_label.split(" / ", 1)[0].strip().lower() == left:
                    quantity = q
                    target_unit = qty.unit
                    canon = qty.formatted_label
                    break
            if target_unit is None:
                # Fallback to the unit part of the plugin label
                target_unit = canon.split(" / ", 1)[-1]
        else:
            # 2) base-name -> quantity from spec
            quantity = base_idx.get(base_slug)
            if not quantity:
                continue
            target_unit = _quantity_unit(quantity)
            canon = _canon_label(quantity)

        # Special guard: If mapping to unix time from a non-numeric string (e.g., 'Timestamp'),
        # but a canonical Unix Time already exists, skip to avoid duplicates.
        if quantity == "unix_time_second" and "Unix Time / s" in out.columns and not _is_numeric_series(out[col]):
            continue

        # Locale-aware coercion when numbers carry comma decimals
        out[col] = _coerce_with_decimal(out[col], decimal_hint)

        # Unit conversion if we can
        unit_src = ""
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
            meta.setdefault(
                canon,
                {
                    "quantity": quantity or "",
                    "sourceHeader": "",
                    "sourceUnit": "",
                    "unit": target_unit,
                    "parsedFrom": source or "",
                },
            )
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
    # ---- Selection logic: keep all canonical columns we recognized (no dup) ----
    have = set(out.columns)  # after coalescing/renaming

    if strict:
        missing = [c for c in REQUIRED if c not in have]
        if missing:
            sample_cols = list(out.columns)[:25]
            raise ValueError(
                f"Missing required BDF columns after normalization: {missing}. Seen columns: {sample_cols}"
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
    tail = [c for c in candidate if c not in ORDERED_REQUIRED]
    out = out[front + tail].copy()

    out.attrs["bdf:columns"] = meta
    if legacy_headers:
        warnings.warn(
            "Legacy BDF column labels detected (skos:altLabel/notation). They were normalized to preferred labels.",
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
    out = df.copy()
    legacy_headers: list[str] = []

    # Build preferred non-deprecated base -> mr_name
    base_preferred: dict[str, str] = {}
    for q, s in spec.COLUMN_ONTOLOGY:
        if s.deprecated:
            continue
        base = s.formatted_label.split(" / ", 1)[0].strip().lower()
        base_preferred.setdefault(base, q)

    # Build lookup: pref_label/notation -> (target_canon, target_unit, source_unit, is_legacy)
    # source_unit is non-empty only when deprecated unit differs from target unit
    notation_to_canon: dict[str, tuple[str, str, str, bool]] = {}
    pref_to_canon: dict[str, tuple[str, str, str, bool]] = {}
    for q, s in spec.COLUMN_ONTOLOGY:
        pref = s.formatted_label
        notation = s.effective_notation
        target_q = q
        is_deprecated = s.deprecated
        if s.deprecated:
            base = pref.split(" / ", 1)[0].strip().lower()
            target_q = base_preferred.get(base, q)
        target_canon = spec.COLUMN_ONTOLOGY[target_q].formatted_label
        target_unit = spec.COLUMN_ONTOLOGY[target_q].unit
        src_unit = s.unit if is_deprecated and s.unit != target_unit else ""
        notation_to_canon[notation] = (target_canon, target_unit, src_unit, is_deprecated)
        pref_to_canon[pref] = (target_canon, target_unit, src_unit, is_deprecated)

    synonym_idx = spec.COLUMN_ONTOLOGY.base_synonym_index()

    def _apply(col: str, canon: str, target_unit: str, src_unit: str, is_legacy: bool) -> None:
        if is_legacy and canon != col:
            legacy_headers.append(col)
        if src_unit and src_unit != target_unit and _is_numeric_series(out[col]):
            with contextlib.suppress(Exception):
                out[col] = convert_series(out[col], src_unit, target_unit)
        if canon in out.columns and col != canon:
            out[canon] = _coalesce_into(out[canon], out[col])
            out.drop(columns=[col], inplace=True)
        elif canon != col:
            out.rename(columns={col: canon}, inplace=True)

    for col in list(out.columns):
        pref_hit = pref_to_canon.get(str(col))
        if pref_hit:
            _apply(col, *pref_hit)
            continue

        notation_hit = notation_to_canon.get(str(col))
        if notation_hit:
            _apply(col, *notation_hit)
            continue

        # Synonym fallback: altLabel/hiddenLabel slugs (e.g. "cycle_dimensionless")
        col_slug = _slugify(str(col))
        mr = synonym_idx.get(col_slug)
        if mr:
            qty = spec.COLUMN_ONTOLOGY[mr]
            _apply(col, qty.formatted_label, qty.unit, "", True)

    if keep_unmapped:
        return out, legacy_headers
    canonical_all = set(REQUIRED) | set(OPTIONAL)
    out = out[[c for c in out.columns if c in canonical_all]].copy()
    return out, legacy_headers
