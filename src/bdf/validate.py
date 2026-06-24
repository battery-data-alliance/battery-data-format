from __future__ import annotations

import re
import warnings
from typing import Any, Dict, List

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

    if report:
        _print_report(rep)

    if raise_on_error and not rep["ok"]:
        raise BDFValidationError(f"Missing required columns: {rep['missing']}")

    return rep
