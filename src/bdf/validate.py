from __future__ import annotations
import warnings
import pandas as pd
from typing import Dict, Any, List
from .normalize import REQUIRED, OPTIONAL
from .repair import _compute_eps_from_diffs  # reuse your epsilon heuristic

__all__ = ["BDFValidationError", "validate_df"]

class BDFValidationError(Exception):
    """Raised when a DataFrame fails BDF validation."""


def _collect_report(df: pd.DataFrame) -> Dict[str, Any]:
    allowed = set(REQUIRED + OPTIONAL)
    extras: List[str] = [c for c in df.columns if c not in allowed]
    missing: List[str] = [c for c in REQUIRED if c not in df.columns]

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
        print("      Suggestion: bdf.fix_time(df, method='auto') or bdf.clean_bdf(df, time_fix='segment').")


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
            f"(min Δ = {ts['min_drop']:.6g} s). Consider bdf.fix_time(...).",
            RuntimeWarning,
        )

    if report:
        _print_report(rep)

    if raise_on_error and not rep["ok"]:
        raise BDFValidationError(f"Missing required columns: {rep['missing']}")

    return rep
