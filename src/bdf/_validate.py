from __future__ import annotations

import warnings

# mypy: ignore-errors
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import polars as pl

import bdf.spec as spec

from ._df_compat import _classify_df, _to_polars_lazy
from ._errors import BDFValidationError
from ._time_scale import detect_scale_mismatch
from .file_utils import is_url
from .plugins import detect  # spec-driven detection -> (plugin_id, Plugin)
from .repair import _compute_eps_from_diffs  # reuse your epsilon heuristic
from .spec import _slugify

REQUIRED = spec.COLUMN_ONTOLOGY.required_labels()
OPTIONAL = spec.COLUMN_ONTOLOGY.optional_labels()


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


def _canonical_series(df: pl.DataFrame) -> Dict[str, np.ndarray]:
    """Map canonical mr_name -> float64 numpy array for every recognised column.

    Resolves preferred labels ("Cumulative Capacity / Ah"), machine-readable
    notations ("cumulative_capacity_ah") and known vendor synonyms to the
    canonical quantity name, so derived checks work regardless of header style.

    Args:
        df: Table whose columns may use any accepted BDF header style.

    Returns:
        Mapping from canonical mr_name to a numeric-coerced float64 array
        (non-numeric values become NaN).
    """
    onto = spec.COLUMN_ONTOLOGY
    label_to_mr: Dict[str, str] = {}
    for q, s in onto:
        label_to_mr.setdefault(s.formatted_label, q)
        label_to_mr.setdefault(s.effective_notation, q)
    synonym_idx = onto.base_synonym_index()

    out: Dict[str, np.ndarray] = {}
    for col in df.columns:
        mr = label_to_mr.get(str(col)) or synonym_idx.get(_slugify(str(col)))
        if mr and mr not in out:
            series = df[col]
            series = series.cast(pl.Float64, strict=False) if series.dtype == pl.Utf8 else series.cast(pl.Float64)
            out[mr] = series.fill_null(float("nan")).to_numpy()
    return out


def _resolve_source(
    source: str | Path,
    *,
    registry_path: str | Path | None = None,
) -> tuple[Path, str | None]:
    """
    Return a local Path for the source and an optional plugin hint.
    Source may be: local path, http(s) URL, or dataset id from the registry.
    """
    s = str(source)

    # 1) existing file path
    p = Path(s)
    if p.exists():
        return p, None

    # 2) URL -> cache it
    if is_url(s):
        from .fetch import fetch_url  # lazy

        path = fetch_url(s)
        return path, None

    # 3) dataset id from registry
    from ._registry import get_entry as _get_entry, load_registry as _load_registry  # lazy

    reg = _load_registry(registry_path)
    entry = _get_entry(reg, s)  # raises if not found/ambiguous
    url = entry["url"]
    plugin_hint = entry.get("plugin")
    sha256 = entry.get("sha256")
    filename = entry.get("filename")

    from .fetch import fetch_url  # lazy

    path = fetch_url(url, sha256=sha256, filename=filename)
    return path, plugin_hint


def _check_derived(df: pl.DataFrame) -> Dict[str, Any]:
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
        got = cols[target]
        exp = cols[a] + cols[b] if op == "+" else cols[a] - cols[b]
        valid = np.isfinite(got) & np.isfinite(exp)
        # scale-aware atol: 8-significant-digit CSV round-trips leave ~1e-8-of-scale
        # residue near zero-crossings, which a fixed atol=1e-9 misreads as violations.
        scale = float(np.nanmax(np.abs(exp[valid]))) if valid.any() else 0.0
        mismatch = valid & ~np.isclose(got, exp, rtol=1e-6, atol=1e-9 + 1e-7 * scale)
        n_bad = int(mismatch.sum())
        if n_bad:
            worst = float(np.abs(got[mismatch] - exp[mismatch]).max())
            issues.append(f"'{target}' != {a} {op} {b} in {n_bad}/{len(df)} rows (worst |Δ| = {worst:.4g}).")
            details.append({"check": "identity", "column": target, "violations": n_bad, "worst_abs_diff": worst})

    # 2) monotonic non-decreasing quantities
    for name in _MONOTONIC_NONDECREASING:
        if name not in cols:
            continue
        v = cols[name]
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
        v = cols["cycle_count"]
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

    # 4) step_record_index (ex step_index, deprecated in ontology 1.3.0):
    # 1-based within-step point counter (resets to 1, else +1). Data using the
    # deprecated header still resolves via the deprecated term's mr name.
    counter_name = next((n for n in ("step_record_index", "step_index") if n in cols), None)
    if counter_name:
        v = cols[counter_name]
        finite = v[np.isfinite(v)]
        if finite.size:
            mn = float(finite.min())
            if mn != 1.0:
                issues.append(
                    f"'{counter_name}' never equals 1 (min={mn:g}); it looks like a program step "
                    f"identifier (Step ID / Arbin Step_Index / Digatron Step), not the 1-based "
                    f"within-step point counter."
                )
                details.append({"check": "step_index_min", "column": counter_name, "min": mn})
            elif v.size >= 2:
                d = np.diff(v)
                bad = int(np.nansum((d != 1.0) & (v[1:] != 1.0)))
                if bad:
                    issues.append(f"'{counter_name}' has {bad} transitions that neither increment by 1 nor reset to 1.")
                    details.append({"check": "step_index_seq", "column": counter_name, "violations": bad})

    # 5) elapsed-time vs wall-clock scale cross-check (GH #65): a column whose
    # values are in the wrong unit is self-consistent, so only the comparison
    # with the independently recorded wall clock reveals it.
    if "unix_time_second" in cols:
        wall = cols["unix_time_second"]
        for name in ("test_time_second", "step_time_second"):
            if name not in cols:
                continue
            mismatch = detect_scale_mismatch(cols[name], wall)
            if mismatch is None:
                continue
            if mismatch.unit_name:
                issues.append(
                    f"'{name}' increments disagree with wall-clock ('unix_time_second') increments "
                    f"by ~{mismatch.ratio:g}x: values appear to be {mismatch.unit_name}, not seconds."
                )
            else:
                issues.append(
                    f"'{name}' increments disagree with wall-clock ('unix_time_second') increments "
                    f"by ~{mismatch.ratio:g}x (no known unit matches this ratio)."
                )
            details.append(
                {
                    "check": "time_scale",
                    "column": name,
                    "ratio": mismatch.ratio,
                    "actual_unit": mismatch.unit_name,
                    "n_samples": mismatch.n_samples,
                }
            )

    return {"issues": issues, "details": details}


def _collect_report(df: pl.DataFrame) -> Dict[str, Any]:
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
    time_label = spec.COLUMN_ONTOLOGY.test_time_second.formatted_label
    time_stats: Dict[str, Any] = {"present": False, "monotonic": True, "violations": 0, "min_drop": 0.0}
    if time_label in df.columns:
        series = df[time_label]
        series = series.cast(pl.Float64, strict=False) if series.dtype == pl.Utf8 else series.cast(pl.Float64)
        t = series.fill_null(float("nan")).to_numpy()
        d = np.diff(t, prepend=np.nan)
        # robust threshold (same idea as clean.py)
        eps = _compute_eps_from_diffs(np.nan_to_num(d, nan=0.0))
        bad = d < -eps
        n_bad = int(bad.sum())
        time_stats = {
            "present": True,
            "monotonic": (n_bad == 0),
            "violations": n_bad,
            "min_drop": float(d[bad].min()) if n_bad else 0.0,
            "first_bad_index": int(np.nonzero(bad)[0][0]) if n_bad else None,
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
            f"   ⚠️ Non-monotonic '{spec.COLUMN_ONTOLOGY.test_time_second.formatted_label}': "
            f"{ts['violations']} drops (min Δ = {ts['min_drop']:.6g} s, eps≈{ts['epsilon']:.6g})."
        )
        print("      Suggestion: bdf.clean(df, time_fix='segment') or bdf.repair.fix_time(df, method='auto').")

    derived = rep.get("derived", {})
    for issue in derived.get("issues", []):
        print(f"   ⚠️ {issue}")


def validate_df(
    df,
    *,
    report: bool = False,
    raise_on_error: bool = True,
) -> Dict[str, Any]:
    """Validate a BDF table; accepts polars (eager or lazy) or pandas frames."""
    _classify_df(df)  # raise early on unsupported types
    rep = _collect_report(_to_polars_lazy(df).collect())

    # Warning, not an error
    ts = rep.get("time_stats", {})
    if ts.get("present") and not ts.get("monotonic", True):
        warnings.warn(
            f"Non-monotonic '{spec.COLUMN_ONTOLOGY.test_time_second.formatted_label}' detected: "
            f"{ts['violations']} drops "
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


def validate(
    obj,
    *,
    report: bool = False,
    raise_on_error: bool = False,  # <- default False so notebooks don’t crash
    registry_path: str | Path | None = None,
):
    """
    Validate a BDF DataFrame, a local file path, an HTTP/HTTPS URL, or a dataset id.

    Behavior:
      - DataFrame: validate as-is (no transformations).
      - Path/URL/id: only treated as a *BDF artifact* (strict). We do NOT vendor-parse
        or normalize here. If it doesn’t look like BDF, you’ll get an 'ok=False' report.

    Returns:
      dict report with at least:
        {"ok": True, "issues": [...]}   or   {"ok": False, "kind": "...", "detail": "..."}
    """

    # small local helpers (kept inside to avoid extra imports at module load time)
    def _bad_report(kind: str, detail: str, **extra):
        r = {"ok": False, "kind": kind, "detail": detail}
        if extra:
            r.update(extra)
        if report:
            print(f"Validation failed: {detail}")
        if raise_on_error:
            raise BDFValidationError(detail)
        return r

    # Direct DataFrame path
    import pandas as pd

    if isinstance(obj, pd.DataFrame):
        return validate_df(obj, report=report, raise_on_error=raise_on_error)

    # Resolve path/URL/registry id to a local path
    if isinstance(obj, (str, Path)):
        local_path, _ = _resolve_source(obj, registry_path=registry_path)
        p = Path(local_path)
        fname = p.name

        # Check if file looks like bdf
        try:
            plugin_name, _plugin = detect(p)
        except ValueError:
            plugin_name = "None"
            message = "Did not match any existing plugin"
        else:
            message = f"Matched plugin '{plugin_name}'"
        if not plugin_name.startswith("bdf_"):
            return _bad_report(
                kind="not_bdf_artifact",
                detail=f"{fname} does not look like a BDF artifact. {message}.",
                file=fname,
            )

        # Try to read the file
        try:
            from .io import read

            df, _metadata = read(p)
        except Exception as e:
            return _bad_report(
                kind="io_error",
                detail=f"Failed to load BDF artifact {fname}: {e}",
                file=fname,
            )

        # Validate columns/units only; do NOT normalize or modify
        return validate_df(df, report=report, raise_on_error=raise_on_error)

    # Anything else: wrong type
    return _bad_report(
        kind="type_error",
        detail="validate() expects a pandas DataFrame, a file path (str/Path), a URL, or a dataset id.",
    )
