from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, List, Optional, Tuple

import numpy as np
import pandas as pd
from pint import UnitRegistry

# ---------- Spec (single source of truth) ----------
from bdf import spec

# ---------- Pint registry (single place) ----------
try:
    import pint  # type: ignore

    _OK = True
except Exception:
    pint = None  # type: ignore
    _OK = False

has_pint: bool = _OK
if _OK:
    ureg = pint.UnitRegistry()
    # Friendly aliases (idempotent)
    try:
        ureg.define("ampere_hour = ampere * hour = Ah = A*h")
        ureg.define("milliampere_hour = milli * ampere_hour = mAh = mA*h")
        ureg.define("watt_hour = watt * hour = Wh = W*h")
    except Exception:
        pass
else:
    ureg = None  # type: ignore

# ---------- Header parser (very small, Pint-friendly) ----------
_UNIT_ALIAS = {
    "v": "V",
    "volt": "V",
    "volts": "V",
    "a": "A",
    "amp": "A",
    "amps": "A",
    "ampere": "A",
    "amperes": "A",
    "w": "W",
    "watt": "W",
    "watts": "W",
    "s": "s",
    "sec": "s",
    "second": "s",
    "seconds": "s",
    "h": "h",
    "hr": "h",
    "hrs": "h",
    "hour": "h",
    "hours": "h",
    "degc": "degC",
    "°c": "degC",
    "celsius": "degC",
    "degree_celsius": "degC",
    "wh": "W*h",
    "watt_hour": "W*h",
    "ah": "A*h",
    "ampere_hour": "A*h",
    "pa": "Pa",
    "pascal": "Pa",
    "ohm": "ohm",
    "kg": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "g": "g",
    "gram": "g",
    "grams": "g",
    "per": "/",
}


_SPLIT = re.compile(r"[ _\-]+")
_HASH = re.compile(r"^(?P<name>.+?)#(?P<unit>.+)$")
_PAREN = re.compile(r"^(?P<name>.+?)\s*[\(\[]\s*(?P<unit>.+?)\s*[\)\]]\s*$")
_LABEL_SPLIT = re.compile(r"\s*/\s*")
_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(s: str) -> str:
    return _SLUG.sub("-", s.lower()).strip("-")


def _norm_tokens(tokens: List[str]) -> Optional[str]:
    if not tokens:
        return None
    norm = [_UNIT_ALIAS.get(t.lower(), t) for t in tokens]
    expr: List[str] = []
    for i, tok in enumerate(norm):
        if tok == "/":
            expr.append("/")
        else:
            if i and expr and expr[-1] not in ("", "/"):
                expr.append("*")
            expr.append(tok)
    s = "".join(expr)
    return s if re.search(r"[A-Za-z]", s) else None


def parse_from_header(header: str) -> Tuple[str, Optional[str], str]:
    """
    Return (base_name, pint_unit_expr_or_None, source) for headers like:
      - 'Voltage#V'
      - 'Current (A)' / 'Ambient Temperature [degC]'
      - 'specific_energy_watt_hour_per_kilogram'
    """
    h = header.strip()

    m = _HASH.match(h)
    if m:
        base = m.group("name").strip()
        unit = _norm_tokens(_SPLIT.split(m.group("unit").strip()))
        return base, unit, "hash"

    m = _PAREN.match(h)
    if m:
        base = m.group("name").strip()
        unit = _norm_tokens(_SPLIT.split(m.group("unit").strip()))
        return base, unit, "paren"

    tokens = [t for t in _SPLIT.split(h.lower()) if t]
    # try longest -> shortest suffix; accept only if Pint can parse the unit
    for i in range(min(6, len(tokens)), 0, -1):
        unit = _norm_tokens(tokens[-i:])
        if not unit:
            continue
        try:
            if has_pint:
                ureg.Unit(unit)  # validate with Pint
            base = " ".join(tokens[:-i]) or h
            return base, unit, "snake"
        except Exception:
            continue

    return h, None, "none"


# ---------- Unit resolution (spec -> label -> IRI -> header -> heuristic) ----------
def _to_pint(unit_str: str, as_string: bool):
    if as_string or not has_pint:
        return unit_str
    if unit_str == "1":
        return ureg.dimensionless
    return ureg.Unit(unit_str)


def _label_unit(label: str) -> Optional[str]:
    if " / " in label:
        return _LABEL_SPLIT.split(label, maxsplit=1)[1]
    return None


def _heuristic_from_mr_suffix(mr_name: str) -> Optional[str]:
    """Try suffixes of MR names via Pint (e.g., ampere_hour -> A*h)."""
    alias = {
        "celsius": "degC",
        "degree_celsius": "degC",
        "pascal": "Pa",
        "ampere_hour": "A*h",
        "watt_hour": "W*h",
    }
    parts = mr_name.lower().split("_")
    for span in (4, 3, 2, 1):
        if len(parts) < span:
            continue
        raw = "_".join(parts[-span:])
        canon = alias.get(raw, raw)
        expr = canon.replace("_per_", "/").replace("_", "*")
        try:
            if has_pint:
                ureg.Unit(expr)
            else:
                if not re.search(r"[A-Za-z]", expr):
                    continue
            return expr
        except Exception:
            continue
    return None


def resolve_pint_unit(
    *,
    mr_name: Optional[str] = None,
    iri: Optional[str] = None,
    label: Optional[str] = None,
    as_string: bool = False,
):
    """Explicit resolver: prefer spec; fall back to label/IRI; then MR suffix heuristic."""
    # 1) spec by MR name
    if mr_name and hasattr(spec.COLUMN_ONTOLOGY, mr_name):
        return _to_pint(getattr(spec.COLUMN_ONTOLOGY, mr_name).unit, as_string)

    # 2) IRI (from spec)
    if iri:
        u = next((s.unit for _, s in spec.COLUMN_ONTOLOGY if s.iri == iri), None)
        if u:
            return _to_pint(u, as_string)

    # 3) canonical label
    if label:
        u = _label_unit(label)
        if u:
            return _to_pint(u, as_string)

    # 4) heuristic from MR suffix
    if mr_name:
        u = _heuristic_from_mr_suffix(mr_name)
        if u:
            return _to_pint(u, as_string)

    raise KeyError("Could not resolve unit with the provided parameters.")


def resolve_unit(value: Any, *, as_string: bool = False):
    """
    One-shot resolver. Pass a Series, IRI, canonical label, MR name, or vendor header.
    Order:
      A) Series -> df.attrs['bdf:columns'][name]['unit']
      B) MR name (spec)     C) Canonical label (spec)     D) IRI (spec)
      E) Heuristic from MR suffix (Pint-validated)
      F) Vendor header parse ('#','()','snake') -> unit
      G) Base-name synonym -> spec quantity -> unit
      H) Last-chance: 'X / UNIT'
    """
    # Optional pandas support without hard dependency
    try:
        import pandas as pd  # type: ignore

        _HAS_PD = True
    except Exception:
        _HAS_PD = False

    if _HAS_PD and isinstance(value, pd.Series):
        name = value.name if value.name is not None else ""
        try:
            df = value.to_frame()
            if hasattr(df, "attrs"):
                meta = df.attrs.get("bdf:columns", {})
                if isinstance(meta, Mapping) and name in meta and "unit" in meta[name]:
                    return _to_pint(meta[name]["unit"], as_string)
        except Exception:
            pass
        value = str(name)

    if not isinstance(value, str):
        raise TypeError("resolve_unit(value) expects a string or a pandas Series.")

    s = value.strip()

    # MR name (spec)
    if hasattr(spec.COLUMN_ONTOLOGY, s):
        return _to_pint(getattr(spec.COLUMN_ONTOLOGY, s).unit, as_string)

    # Canonical label (exact)
    for _, sc in spec.COLUMN_ONTOLOGY:
        if s == sc.label:
            return _to_pint(sc.unit, as_string)

    # IRI (from spec)
    if s.startswith(("http://", "https://", "urn:")):
        u = next((sc.unit for _, sc in spec.COLUMN_ONTOLOGY if sc.iri == s), None)
        if u:
            return _to_pint(u, as_string)

    # Heuristic from MR suffix (prefer this for machine-readable names)
    guess = _heuristic_from_mr_suffix(s)
    if guess:
        return _to_pint(guess, as_string)

    # Vendor header parsing
    base, unit_expr, _src = parse_from_header(s)
    if unit_expr:
        return _to_pint(unit_expr, as_string)

    # Base-name synonym -> spec quantity -> unit
    base_slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    syn_idx: Mapping[str, str] = spec.COLUMN_ONTOLOGY.base_synonym_index()  # slug -> MR name
    q = syn_idx.get(base_slug)
    if q:
        return _to_pint(getattr(spec.COLUMN_ONTOLOGY, q).unit, as_string)

    # Last-chance: 'X / UNIT'
    u2 = _label_unit(s)
    if u2:
        return _to_pint(u2, as_string)

    raise KeyError(f"Could not resolve unit for: {value!r}")


# ---------- Conversions (Pint-backed) ----------
ureg = UnitRegistry()
Q_ = ureg.Quantity


def convert(x, to_unit: str, from_unit: str | None = None, *, strict: bool = False):
    """
    Convert numbers/Series to `to_unit`. If `from_unit` is None, try to infer via resolve_unit().
    Accepts pandas Series with numpy/pyarrow dtypes.
    """
    # ---- Normalize inputs ----
    name = getattr(x, "name", None)
    index = getattr(x, "index", None)
    is_series = isinstance(x, pd.Series)

    # Force a float array (handles pyarrow dtypes safely)
    try:
        # to_numpy() avoids object dtype and works with ArrowDtype
        arr = x.to_numpy(dtype="float64") if is_series else np.asarray(x, dtype="float64")
    except Exception:
        if strict:
            raise
        # best effort: coerce via pandas if available
        arr = (
            pd.to_numeric(x, errors="coerce").to_numpy(dtype="float64") if is_series else np.asarray(x, dtype="float64")
        )

    # Infer from_unit if needed (use your existing resolver)
    if from_unit is None:
        try:
            from bdf.units.core import resolve_unit  # or correct import path

            # Prefer the column label if x is a Series; else we can't infer
            if is_series and name:
                from_unit = resolve_unit(str(name), as_string=True)
        except Exception:
            from_unit = None

    # If we still don't know the source unit or to_unit is falsy, bail early
    if not to_unit or not from_unit:
        return x if is_series else arr

    # ---- Do the conversion with Pint ----
    try:
        y = Q_(arr, from_unit).to(to_unit).magnitude
    except Exception:
        if strict:
            raise
        # fall back: return original
        y = arr

    # Restore pandas Series shape/metadata
    if is_series:
        return pd.Series(y, index=index, name=name)
    return y


def convert_series(series, from_unit: str, to_unit: str):
    """Shorthand for converting a pandas Series (no inference)."""
    if not has_pint:
        return getattr(series, "values", series)
    return convert(series, to_unit, from_unit=from_unit)


def convert_dataframe_for_plot(
    df,
    *,
    x: Optional[str] = None,
    xunit: Optional[str] = None,
    y: Optional[str] = None,
    yunit: Optional[str] = None,
    yy: Optional[str] = None,
    yyunit: Optional[str] = None,
):
    """
    Tiny helper for plotting: returns dict of Series (converted if a target unit is given).
    """
    try:
        pass  # type: ignore
    except Exception:
        raise RuntimeError("convert_dataframe_for_plot requires pandas.") from None

    out: dict[str, pd.Series] = {}

    def _maybe(col: Optional[str], unit: Optional[str]):
        if not col or col not in df.columns:
            return None
        s = df[col]
        if unit:
            try:
                return convert(s, unit, from_unit=None, strict=False)
            except Exception:
                return s
        return s

    out["x"] = _maybe(x, xunit)
    out["y"] = _maybe(y, yunit)
    out["yy"] = _maybe(yy, yyunit)
    return out
