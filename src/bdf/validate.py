from __future__ import annotations

import re
import warnings
from typing import Any, Dict, List

import pandas as pd

from .normalize import OPTIONAL, REQUIRED, spec
from .ontology_labels import load_alias_index
from .repair import _compute_eps_from_diffs  # reuse your epsilon heuristic
from .units import parse_from_header

__all__ = ["BDFValidationError", "validate_df", "check_term_rules"]

class BDFValidationError(Exception):
    """Raised when a DataFrame fails BDF validation."""


_SLUG = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")


# --- Semantic term-rule validation ------------------------------------------
# Each canonical quantity carries definitional rules (see the ontology). Data
# that violates them is almost always an ingestion bug — a wrong sign
# convention, an unscaled or resetting accumulator, or a unit mix-up. These
# checks are warning-level (they do not fail validation) because real
# instruments occasionally violate them (e.g. schedule-driven counter resets),
# but surfacing the violations makes such issues auditable rather than silent.

# Accumulate from test start and never reset -> must be monotonic non-decreasing.
# ('Test Time / s' is checked separately with a dedicated epsilon heuristic.)
_RULE_MONOTONIC: tuple[str, ...] = (
    "Charging Capacity / Ah",
    "Discharging Capacity / Ah",
    "Cumulative Capacity / Ah",
    "Charging Energy / Wh",
    "Discharging Energy / Wh",
    "Cumulative Energy / Wh",
    "Cycle Count / 1",
    "Step Count / 1",
)

# Quantities defined as magnitudes / throughput -> must be non-negative.
_RULE_NON_NEGATIVE: tuple[str, ...] = (
    "Charging Capacity / Ah",
    "Discharging Capacity / Ah",
    "Cumulative Capacity / Ah",
    "Charging Energy / Wh",
    "Discharging Energy / Wh",
    "Cumulative Energy / Wh",
    "Step Capacity / Ah",
    "Step Energy / Wh",
    "Cycle Count / 1",
    "Step Count / 1",
)

# Signed running integrals whose magnitude cannot exceed the throughput.
_RULE_BOUNDED_BY: dict[str, str] = {
    "Net Capacity / Ah": "Cumulative Capacity / Ah",
    "Net Energy / Wh": "Cumulative Energy / Wh",
}


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def check_term_rules(df: pd.DataFrame, *, rtol: float = 1e-6) -> List[Dict[str, Any]]:
    """Check that present canonical columns obey their term definitions.

    Returns a list of violation records (empty when the data conforms). Each
    record has ``column``, ``rule``, ``violations`` (row count) and a rule-
    specific detail field. Tolerances are scaled by each column's magnitude so
    floating-point noise is not reported.
    """
    violations: List[Dict[str, Any]] = []

    for col in _RULE_MONOTONIC:
        if col not in df.columns:
            continue
        s = _num(df, col).dropna()
        if s.empty:
            continue
        d = s.diff().dropna()
        tol = max(1e-9, rtol * float(s.abs().max()))
        n_bad = int((d < -tol).sum())
        if n_bad:
            violations.append({
                "column": col, "rule": "monotonic non-decreasing",
                "violations": n_bad, "min_delta": float(d.min()),
            })

    for col in _RULE_NON_NEGATIVE:
        if col not in df.columns:
            continue
        s = _num(df, col).dropna()
        if s.empty:
            continue
        tol = max(1e-9, rtol * float(s.abs().max()))
        n_bad = int((s < -tol).sum())
        if n_bad:
            violations.append({
                "column": col, "rule": "non-negative",
                "violations": n_bad, "min_value": float(s.min()),
            })

    for col, ref in _RULE_BOUNDED_BY.items():
        if col not in df.columns or ref not in df.columns:
            continue
        s = _num(df, col)
        r = _num(df, ref)
        mask = s.notna() & r.notna()
        if not mask.any():
            continue
        tol = max(1e-9, rtol * float(r[mask].abs().max()))
        n_bad = int((s[mask].abs() > r[mask] + tol).sum())
        if n_bad:
            violations.append({
                "column": col, "rule": f"|value| <= {ref}",
                "violations": n_bad,
            })

    return violations


def _collect_report(df: pd.DataFrame) -> Dict[str, Any]:
    allowed = set(REQUIRED + OPTIONAL)
    alias_idx = load_alias_index()
    legacy_cols: List[str] = []
    notation_cols: List[str] = []
    deprecated_pref_cols: List[str] = []
    canonical_present: set[str] = set()
    notation_to_canonical: dict[str, str] = {}
    deprecated_pref_to_canonical: dict[str, str] = {}
    base_preferred: dict[str, str] = {}
    for q, s in spec.COLUMNS.items():
        if bool(s.get("deprecated")):
            continue
        base = spec._label_for(q).split(" / ", 1)[0].strip().lower()
        base_preferred.setdefault(base, q)
    for q in spec.COLUMNS:
        s = spec.COLUMNS[q]
        pref = spec._label_for(q)
        target_q = q
        if bool(s.get("deprecated")):
            base = pref.split(" / ", 1)[0].strip().lower()
            target_q = base_preferred.get(base, q)
            deprecated_pref_to_canonical[pref] = spec._label_for(target_q)
        notation_to_canonical[spec.notation_for(q)] = spec._label_for(target_q)

    for col in df.columns:
        if col in allowed:
            canonical_present.add(col)
            continue
        canonical_from_deprecated_pref = deprecated_pref_to_canonical.get(str(col))
        if canonical_from_deprecated_pref:
            deprecated_pref_cols.append(col)
            canonical_present.add(canonical_from_deprecated_pref)
            continue
        canonical_from_notation = notation_to_canonical.get(str(col))
        if canonical_from_notation:
            notation_cols.append(col)
            canonical_present.add(canonical_from_notation)
            continue
        base, _unit, _src = parse_from_header(str(col))
        base_slug = _slugify(base.replace("/", " ").replace("#", " "))
        full_slug = _slugify(str(col).replace("/", " ").replace("#", " "))
        alias = alias_idx.get(base_slug) or alias_idx.get(full_slug)
        if alias:
            legacy_cols.append(col)
            canonical_present.add(alias.label)

    extras: List[str] = [
        c for c in df.columns
        if c not in allowed and c not in legacy_cols and c not in notation_cols and c not in deprecated_pref_cols
    ]
    missing: List[str] = [c for c in REQUIRED if c not in canonical_present]

    # --- time monotonicity (warning-level) ---
    time_stats = {"present": False, "monotonic": True, "violations": 0, "min_drop": 0.0}
    if "Test Time / s" in df.columns:
        s = pd.to_numeric(df["Test Time / s"], errors="coerce")
        d = s.diff()
        # robust threshold (same idea as clean.py)
        eps = _compute_eps_from_diffs(d.fillna(0.0).to_numpy())
        bad = d < -eps
        n_bad = int(bad.sum())
        time_stats = {
            "present": True,
            "monotonic": (n_bad == 0),
            "violations": n_bad,
            "min_drop": float(d[bad].min()) if n_bad else 0.0,
            "first_bad_index": int(bad[bad].index[0]) if n_bad else None,
            "epsilon": float(eps),
        }

    ok = len(missing) == 0
    return {
        "ok": ok,
        "missing": missing,
        "extras": extras,
        "required": REQUIRED,
        "optional": OPTIONAL,
        "legacy_labels": legacy_cols,
        "n_rows": len(df),
        "n_cols": len(df.columns),
        "time_stats": time_stats,
        "rule_violations": check_term_rules(df),
    }


def _print_report(rep: Dict[str, Any]) -> None:
    check = "✅" if rep["ok"] else "❌"
    print(f"{check} BDF validation {'passed' if rep['ok'] else 'failed'}")
    print(f"   rows: {rep['n_rows']:,}   cols: {rep['n_cols']}")
    if rep["missing"]:
        print("   Missing required columns:")
        for c in rep["missing"]:
            print(f"     - {c}")
    if rep["extras"]:
        print("   Non-canonical columns (ignored by BDF):")
        for c in rep["extras"]:
            print(f"     - {c}")

    ts = rep.get("time_stats", {})
    if ts.get("present") and not ts.get("monotonic", True):
        print(
            f"   ⚠️ Non-monotonic 'Test Time / s': "
            f"{ts['violations']} drops (min Δ = {ts['min_drop']:.6g} s, eps≈{ts['epsilon']:.6g})."
        )
        print("      Suggestion: bdf.clean(df, time_fix='segment') or bdf.repair.fix_time(df, method='auto').")

    for v in rep.get("rule_violations", []):
        print(f"   ⚠️ '{v['column']}' violates rule '{v['rule']}' in {v['violations']} row(s).")


def validate_df(
    df: pd.DataFrame,
    *,
    report: bool = False,
    raise_on_error: bool = True,
) -> Dict[str, Any]:
    rep = _collect_report(df)

    # Warning, not an error
    ts = rep.get("time_stats", {})
    if ts.get("present") and not ts.get("monotonic", True):
        warnings.warn(
            f"Non-monotonic 'Test Time / s' detected: {ts['violations']} drops "
            f"(min Δ = {ts['min_drop']:.6g} s). Consider bdf.repair.fix_time(...).",
            RuntimeWarning,
            stacklevel=2,
        )

    legacy = rep.get("legacy_labels") or []
    if legacy:
        warnings.warn(
            "Legacy BDF column labels detected (skos:altLabel/notation). "
            "They are accepted for compatibility but should be updated to preferred labels.",
            UserWarning,
            stacklevel=2,
        )

    for v in rep.get("rule_violations", []):
        warnings.warn(
            f"Term-rule violation: '{v['column']}' is not {v['rule']} "
            f"({v['violations']} row(s)). This usually indicates a sign, scaling, "
            "or accumulator-reset issue in ingestion.",
            UserWarning,
            stacklevel=2,
        )

    if report:
        _print_report(rep)

    if raise_on_error and not rep["ok"]:
        raise BDFValidationError(f"Missing required columns: {rep['missing']}")

    return rep
