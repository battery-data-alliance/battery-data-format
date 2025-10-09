# src/bdf/io.py
from __future__ import annotations
from pathlib import Path
from typing import Iterable, Tuple
import pandas as pd

# Canonical BDF headers (exact, SI/IUPAC labels)
REQUIRED_COLUMNS = ["Test Time / s", "Voltage / V", "Current / A"]
RECOMMENDED_COLUMNS = ["Ambient Temperature / degC", "Step Number / 1"]

def _normalize_colnames(df: pd.DataFrame) -> pd.DataFrame:
    # strip whitespace/BOM, but keep original case
    df = df.copy()
    df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]
    return df

def _read_csv_robust(path: Path) -> pd.DataFrame:
    # try utf-8-sig (handles BOM), then latin-1; try default comma, then auto-sep
    try:
        return pd.read_csv(path, dtype_backend="pyarrow", encoding="utf-8-sig")
    except UnicodeDecodeError:
        pass
    # Fallback encoding
    try:
        return pd.read_csv(path, dtype_backend="pyarrow", encoding="latin-1")
    except Exception:
        # Last resort: sniff delimiter
        return pd.read_csv(path, dtype_backend="pyarrow", encoding="latin-1",
                           sep=None, engine="python")

def _read_any(path: Path) -> pd.DataFrame:
    p = Path(path)
    ext = p.suffix.lower()
    if ext in {".parquet", ".pq"}:
        df = pd.read_parquet(p)
    else:
        df = _read_csv_robust(p)
    return _normalize_colnames(df)

def _looks_like_bdf(df: pd.DataFrame) -> Tuple[bool, list[str]]:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    return (len(missing) == 0, missing)

def _looks_like_vendor(df: pd.DataFrame) -> bool:
    # A few common vendor headers: helps produce a helpful error message
    vendor_hints = {"time/s", "ewe/v", "ecell/v", "i/ma", "current(a)", "voltage(v)", "step", "cycle"}
    cols_lc = {c.lower() for c in df.columns}
    return any(h in cols_lc for h in vendor_hints)

def load(path: str | Path, validate: bool = True) -> pd.DataFrame:
    """
    Load a BDF CSV/Parquet. If validate=True (default), ensure required canonical
    columns exist and raise a helpful error if the file looks like a vendor export.
    """
    df = _read_any(Path(path))
    if not validate:
        return df

    ok, missing = _looks_like_bdf(df)
    if not ok:
        if _looks_like_vendor(df):
            raise ValueError(
                "This file looks like a vendor/raw export, not a BDF table. "
                f"Missing BDF columns: {missing}. "
                "Try: bdf read_raw_to_bdf(...) or CLI: `bdf convert <raw> --to bdf.csv`."
            )
        raise ValueError(f"Missing required BDF columns: {missing}")
    return df

def save_csv(df: pd.DataFrame, path: str | Path, index: bool = False) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # Write with UTF-8 BOM to be friendlier to Excel; remove if you prefer plain UTF-8
    df.to_csv(path, index=index, encoding="utf-8-sig")

def save_parquet(df: pd.DataFrame, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)

def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Warn (stdout) about missing recommended columns; return df unchanged."""
    for col in RECOMMENDED_COLUMNS:
        if col not in df.columns:
            print(f"[bdf] warning: recommended column missing → {col}")
    return df

def summarize(df: pd.DataFrame) -> str:
    rows = len(df)
    cols = ", ".join(df.columns.tolist())
    return f"BDF table: {rows} rows\nColumns: {cols}"

def is_bdf(df: pd.DataFrame) -> bool:
    """Quick boolean: does this DataFrame meet BDF required columns?"""
    return _looks_like_bdf(df)[0]
