# src/bdf/validate.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List
import numpy as np
import pandas as pd
from . import io  # uses io.REQUIRED_COLUMNS & helpers

@dataclass
class ValidationReport:
    ok: bool
    errors: List[str]
    warnings: List[str]
    def __bool__(self) -> bool: return self.ok
    def __str__(self) -> str:
        lines = []
        if self.errors:
            lines.append("Errors:"); lines += [f"  - {e}" for e in self.errors]
        if self.warnings:
            lines.append("Warnings:"); lines += [f"  - {w}" for w in self.warnings]
        if not lines:
            lines.append("OK: BDF validation passed.")
        return "\n".join(lines)

class BDFValidationError(ValueError):
    """Raised when strict validation fails."""

def _is_numeric(col: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(col)

def _finite_stats(col: pd.Series) -> tuple[bool, int]:
    """Return (all_finite, n_nonfinite) for a column."""
    arr = pd.to_numeric(col, errors="coerce").to_numpy(dtype="float64", na_value=np.nan)
    mask = np.isfinite(arr)
    nbad = int((~mask).sum())
    return bool(mask.all()), nbad

def _robust_outlier_mask(
    col: pd.Series,
    *,
    z_thresh: float = 8.0,
    min_n: int = 30,
) -> pd.Series:
    """
    Flag statistical outliers via robust z-score using MAD (fallback to IQR).
    Returns a boolean mask aligned to 'col'.
    """
    s = pd.to_numeric(col, errors="coerce")
    mask_valid = s.notna()
    x = s[mask_valid].to_numpy(dtype="float64", na_value=np.nan)

    if x.size < min_n:
        return pd.Series(False, index=col.index)

    med = np.nanmedian(x)
    mad = np.nanmedian(np.abs(x - med))
    if mad and mad > 0:
        # robust z-score; 1.4826 makes MAD consistent with std for normal data
        rz = np.abs((x - med) / (1.4826 * mad))
        flagged = rz > z_thresh
    else:
        # Fallback: IQR method, but lenient (3×IQR to avoid false positives)
        q1, q3 = np.nanpercentile(x, [25, 75])
        iqr = q3 - q1
        if iqr == 0:
            return pd.Series(False, index=col.index)
        lo, hi = q1 - 3.0 * iqr, q3 + 3.0 * iqr
        flagged = (x < lo) | (x > hi)

    # Build mask on original index
    out = pd.Series(False, index=col.index)
    out.loc[mask_valid.index] = False  # init (no-op but explicit)
    out.loc[mask_valid[mask_valid].index] = flagged
    return out

def validate_df(df: pd.DataFrame, *, strict: bool = False) -> ValidationReport:
    errors: List[str] = []
    warnings: List[str] = []

    # 1) Required columns present
    missing = [c for c in io.REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        errors.append(f"Missing required columns: {missing}")
        if strict: raise BDFValidationError("\n".join(errors))
        return ValidationReport(False, errors, warnings)

    # 2) Numeric types
    for c in io.REQUIRED_COLUMNS:
        if not _is_numeric(df[c]):
            try_coerced = pd.to_numeric(df[c], errors="coerce")
            if try_coerced.isna().all():
                errors.append(f"Column not numeric: {c}")
            else:
                warnings.append(f"Column {c!r} was not numeric; coercion would produce NaNs.")
    if errors:
        if strict: raise BDFValidationError("\n".join(errors))
        return ValidationReport(False, errors, warnings)

    # 3) Finite values (use NumPy)
    for c in io.REQUIRED_COLUMNS:
        all_finite, nbad = _finite_stats(df[c])
        if not all_finite:
            errors.append(f"Non-finite values in {c} (found {nbad}).")

    # 4) Time sanity: non-negative, mostly non-decreasing
    t = pd.to_numeric(df["Test Time / s"], errors="coerce")
    if (t < 0).any():
        errors.append("Test Time / s has negative values.")
    drops = (t.diff() < 0).sum()
    if drops > 0:
        frac = drops / max(len(t) - 1, 1)
        if frac > 0.01:
            warnings.append(f"Test Time / s has {drops} decreases ({frac:.1%}); data may be unsorted.")
        else:
            warnings.append(f"Minor non-monotonic time detected ({drops} drops).")

    # 5) Outlier warnings for Voltage & Current (robust)
    for col, z in (("Voltage / V", 8.0), ("Current / A", 8.0)):
        if col in df.columns:
            m = _robust_outlier_mask(df[col], z_thresh=z, min_n=30)
            n = int(m.sum())
            if n > 0:
                frac = n / max(len(df), 1)
                examples = ", ".join(map(lambda v: f"{v:.6g}", df.loc[m, col].head(3)))
                warnings.append(
                    f"Potential outliers in {col}: {n} points ({frac:.2%}) flagged by robust MAD z>{z}. "
                    f"Examples: {examples}"
                )

    ok = not errors
    if strict and not ok:
        raise BDFValidationError(str(ValidationReport(ok, errors, warnings)))
    return ValidationReport(ok, errors, warnings)

def validate_path(path: str | Path, *, strict: bool = False) -> ValidationReport:
    df = io._read_any(Path(path))  # robust CSV/Parquet read
    return validate_df(df, strict=strict)
