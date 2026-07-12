from __future__ import annotations

import re
import warnings
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from . import spec
from .repair import _compute_eps_from_diffs  # reuse your epsilon heuristic

__all__ = ["BDFValidationError", "validate_df"]

REQUIRED = spec.COLUMN_ONTOLOGY.required_labels()
OPTIONAL = spec.COLUMN_ONTOLOGY.optional_labels()


class BDFValidationError(Exception):
    """Raised when a DataFrame fails BDF validation."""


_SLUG = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")


# Algebraic identities the ontology defines via prov:wasDerivedFrom:
#   cumulative_* = charging_* + discharging_*   (monotonically non-decreasing)
#   net_*        = charging_* - discharging_*
# Each entry: (target_mr, op, left_mr, right_mr).
_DERIVED_IDENTITIES: tuple[tuple[str, str, str, str], ...] = (
    ("cumulative_capacity_ah", "+", "charging_capacity_ah", "discharging_capacity_ah"),
    ("net_capacity_ah", "-", "charging_capacity_ah", "discharging_capacity_ah"),
    ("cumulative_energy_wh", "+", "charging_energy_wh", "discharging_energy_wh"),
    ("net_energy_wh", "-", "charging_energy_wh", "discharging_energy_wh"),
)

# Quantities the ontology requires to be monotonically non-decreasing over a test.
_MONOTONIC_NONDECREASING: tuple[str, ...] = (
    "cumulative_capacity_ah",
    "cumulative_energy_wh",
    "charging_capacity_ah",
    "discharging_capacity_ah",
    "charging_energy_wh",
    "discharging_energy_wh",
)


def _canonical_series(df: pd.DataFrame) -> Dict[str, "pd.Series"]:
    """Map canonical mr_name -> numeric Series for every recognised column.

    Resolves preferred labels ("Cumulative Capacity / Ah"), machine-readable
    notations ("cumulative_capacity_ah") and known vendor synonyms to the
    canonical quantity name, so derived checks work regardless of header style.

    Args:
        df: DataFrame whose columns may use any accepted BDF header style.

    Returns:
        Mapping from canonical mr_name to a numeric-coerced Series.
    """
    onto = spec.COLUMN_ONTOLOGY
    label_to_mr: Dict[str, str] = {}
    for q, s in onto:
        label_to_mr.setdefault(s.formatted_label, q)
        label_to_mr.setdefault(s.effective_notation, q)
    synonym_idx = onto.base_synonym_index()

    out: Dict[str, pd.Series] = {}
    for col in df.columns:
        mr = label_to_mr.get(str(col)) or synonym_idx.get(_slugify(str(col)))
        if mr and mr not in out:
            out[mr] = pd.to_numeric(df[col], errors="coerce")
    return out


def _check_derived(df: pd.DataFrame) -> Dict[str, Any]:
    """Check ontology-defined derived-column identities and monotonicity.

    All findings are warning-level: derived columns are optional, but when
    present they must satisfy the algebra the ontology defines. Checks run
    only for the columns actually present.

    Args:
        df: DataFrame to check.

    Returns:
        Dict with ``issues`` (list of human-readable strings) and ``details``
        (list of structured findings).
    """
    cols = _canonical_series(df)
    issues: List[str] = []
    details: List[Dict[str, Any]] = []

    # 1) algebraic identities: cumulative = a + b, net = a - b
    for target, op, a, b in _DERIVED_IDENTITIES:
        if not (target in cols and a in cols and b in cols):
            continue
        got = cols[target].to_numpy(dtype=float)
        exp = (cols[a] + cols[b] if op == "+" else cols[a] - cols[b]).to_numpy(dtype=float)
        valid = np.isfinite(got) & np.isfinite(exp)
        mismatch = valid & ~np.isclose(got, exp, rtol=1e-6, atol=1e-9)
        n_bad = int(mismatch.sum())
        if n_bad:
            worst = float(np.abs(got[mismatch] - exp[mismatch]).max())
            issues.append(f"'{target}' != {a} {op} {b} in {n_bad}/{len(df)} rows (worst |Δ| = {worst:.4g}).")
            details.append({"check": "identity", "column": target, "violations": n_bad, "worst_abs_diff": worst})

    # 2) monotonic non-decreasing quantities
    for name in _MONOTONIC_NONDECREASING:
        if name not in cols:
            continue
        v = cols[name].to_numpy(dtype=float)
        if v.size < 2:
            continue
        scale = float(np.nanmax(np.abs(v))) if np.isfinite(v).any() else 0.0
        eps = 1e-9 + 1e-6 * scale
        drops = int(np.nansum(np.diff(v) < -eps))
        if drops:
            issues.append(f"'{name}' is not monotonically non-decreasing ({drops} drops).")
            details.append({"check": "monotonic", "column": name, "violations": drops})

    # 3) cycle_count: non-negative, integer-valued, monotonic non-decreasing
    if "cycle_count" in cols:
        v = cols["cycle_count"].to_numpy(dtype=float)
        finite = v[np.isfinite(v)]
        if finite.size:
            n_neg = int((finite < 0).sum())
            if n_neg:
                issues.append(f"'cycle_count' contains {n_neg} negative values.")
                details.append({"check": "cycle_count_negative", "column": "cycle_count", "violations": n_neg})
            if not np.allclose(finite, np.round(finite)):
                issues.append("'cycle_count' contains non-integer values.")
                details.append({"check": "cycle_count_noninteger", "column": "cycle_count"})
            drops = int(np.nansum(np.diff(v) < 0))
            if drops:
                issues.append(f"'cycle_count' is not monotonically non-decreasing ({drops} drops).")
                details.append({"check": "monotonic", "column": "cycle_count", "violations": drops})

    # 4) step_index: 1-based within-step point counter (resets to 1, else +1)
    if "step_index" in cols:
        v = cols["step_index"].to_numpy(dtype=float)
        finite = v[np.isfinite(v)]
        if finite.size:
            mn = float(finite.min())
            if mn != 1.0:
                issues.append(
                    f"'step_index' never equals 1 (min={mn:g}); it looks like a program step "
                    f"identifier (Step ID / Arbin Step_Index / Digatron Step), not the 1-based "
                    f"within-step point counter."
                )
                details.append({"check": "step_index_min", "column": "step_index", "min": mn})
            elif v.size >= 2:
                d = np.diff(v)
                bad = int(np.nansum((d != 1.0) & (v[1:] != 1.0)))
                if bad:
                    issues.append(f"'step_index' has {bad} transitions that neither increment by 1 nor reset to 1.")
                    details.append({"check": "step_index_seq", "column": "step_index", "violations": bad})

    return {"issues": issues, "details": details}


def _collect_report(df: pd.DataFrame) -> Dict[str, Any]:
    allowed = set(REQUIRED + OPTIONAL)
    synonym_idx = spec.COLUMN_ONTOLOGY.base_synonym_index()
    legacy_cols: List[str] = []
    notation_cols: List[str] = []
    deprecated_pref_cols: List[str] = []
    canonical_present: set[str] = set()
    notation_to_canonical: dict[str, str] = {}
    deprecated_pref_to_canonical: dict[str, str] = {}
    base_preferred: dict[str, str] = {}
    for q, s in spec.COLUMN_ONTOLOGY:
        if s.deprecated:
            continue
        base = s.formatted_label.split(" / ", 1)[0].strip().lower()
        base_preferred.setdefault(base, q)
    for q, s in spec.COLUMN_ONTOLOGY:
        pref = s.formatted_label
        target_q = q
        if s.deprecated:
            base = pref.split(" / ", 1)[0].strip().lower()
            target_q = base_preferred.get(base, q)
            deprecated_pref_to_canonical[pref] = spec.COLUMN_ONTOLOGY[target_q].formatted_label
        notation_to_canonical[s.effective_notation] = spec.COLUMN_ONTOLOGY[target_q].formatted_label

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
        col_slug = _slugify(str(col))
        mr = synonym_idx.get(col_slug)
        if mr:
            legacy_cols.append(col)
            canonical_present.add(spec.COLUMN_ONTOLOGY[mr].formatted_label)

    extras: List[str] = [
        c
        for c in df.columns
        if c not in allowed and c not in legacy_cols and c not in notation_cols and c not in deprecated_pref_cols
    ]
    missing: List[str] = [c for c in REQUIRED if c not in canonical_present]

    # --- time monotonicity (warning-level) ---
    time_stats: Dict[str, Any] = {"present": False, "monotonic": True, "violations": 0, "min_drop": 0.0}
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
        "derived": _check_derived(df),
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

    derived = rep.get("derived", {})
    for issue in derived.get("issues", []):
        print(f"   ⚠️ {issue}")


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

    derived_issues = rep.get("derived", {}).get("issues", [])
    if derived_issues:
        warnings.warn(
            "Derived-column inconsistencies detected (values do not match their "
            "ontology definitions):\n  - " + "\n  - ".join(derived_issues),
            RuntimeWarning,
            stacklevel=2,
        )

    if report:
        _print_report(rep)

    if raise_on_error and not rep["ok"]:
        raise BDFValidationError(f"Missing required columns: {rep['missing']}")

    return rep
