# src/bdf/normalize/normalize.py
from __future__ import annotations
import re
from typing import Dict, Mapping
import pandas as pd

from bdf.units import parse_from_header, convert_series
from . import spec

_SLUG = re.compile(r"[^a-z0-9]+")
def _slugify(s: str) -> str:
    return _SLUG.sub("-", s.lower()).strip("-")

# Public constants
REQUIRED = spec.required_labels()
OPTIONAL = spec.optional_labels()

# Force these three to be the first columns when present
ORDERED_REQUIRED = ("Test Time / s", "Voltage / V", "Current / A")

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

def _is_numeric_series(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s)

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
        try:
            incoming = pd.to_numeric(incoming, errors="coerce")
        except Exception:
            pass
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
    meta: Dict[str, Dict[str, str]] = {}

    base_idx = dict(_base_index())                   # slug → quantity (official MR name)
    plugin_direct = _merge_plugin_column_synonyms(plugin)  # slug → canonical label

    recognized: list[str] = []
    produced: set[str] = set()  # canonical labels we've established in 'out'

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

        # 1) plugin can force a direct canonical label
        canon = plugin_direct.get(base_slug)
        quantity = None
        target_unit = None

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
        else:
            # 2) base-name → quantity from spec
            quantity = base_idx.get(base_slug)
            if not quantity:
                continue
            target_unit = spec.unit_for(quantity)
            canon = _canon_label(quantity)

        # Special guard: If mapping to unix time from a non-numeric string (e.g., 'Timestamp'),
        # but a canonical Unix Time already exists, skip to avoid duplicates.
        if quantity == "unix_time_second" and "Unix Time / s" in out.columns and not _is_numeric_series(out[col]):
            continue

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
        if canon in produced and col != canon:
            if canon in out.columns:
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
    already_canon = [c for c in out.columns if c in REQUIRED or c in OPTIONAL]
    have = set(out.columns)  # after coalescing/renaming

    if strict:
        missing = [c for c in REQUIRED if c not in have]
        if missing:
            raise ValueError(f"Missing required BDF columns after normalization: {missing}")

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
    return out

