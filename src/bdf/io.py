# src/bdf/io.py
from __future__ import annotations
from pathlib import Path
from typing import Tuple
import json, csv
import pandas as pd

_FMT_EXTS = {
    "csv": {".csv", ".bdf.csv"},
    "parquet": {".parquet", ".bdf.parquet"},
    "feather": {".feather", ".bdf.feather"},
    "json": {".json", ".bdf.json"},
}
_COMPRESS = {".gz":"gzip", ".bz2":"bz2", ".xz":"xz", ".zst":"zstd"}

def _detect_format(path: Path) -> str:
    sfx = "".join(path.suffixes).lower()
    for fmt, exts in _FMT_EXTS.items():
        if any(sfx.endswith(e) for e in exts):
            return fmt
    last = path.suffix.lower()
    if last in (".csv", ".parquet", ".feather", ".json"):
        return last.lstrip(".")
    raise ValueError(f"Unknown BDF artifact format: {path.name}")

def _detect_compression(path: Path) -> str | None:
    s = str(path).lower()
    for ext, comp in _COMPRESS.items():
        if s.endswith(ext):
            return comp
    return None

def _meta_sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".metadata.json")

def load(pathlike) -> pd.DataFrame:
    p = Path(pathlike)
    if not p.exists():
        raise FileNotFoundError(p.name)
    fmt = _detect_format(p)
    comp = _detect_compression(p)

    try:
        if fmt == "csv":
            # strict CSV: no banner rows, uniform columns
            return pd.read_csv(
                p,
                engine="python",   # better error messages for malformed rows
                sep=",",
                quoting=csv.QUOTE_MINIMAL,
                on_bad_lines="error",
                skip_blank_lines=True,
                compression=comp,
            )
        if fmt == "parquet":
            return pd.read_parquet(p)
        if fmt == "feather":
            return pd.read_feather(p)
        if fmt == "json":
            return pd.read_json(p, lines=True, compression=comp)
    except Exception as e:
        # Re-raise with a short, path-sanitized message
        emsg = str(e)
        raise ValueError(f"Failed to parse BDF {fmt.upper()} file: {p.name}: {emsg}")

    raise ValueError(f"Unsupported format: {fmt}")

def save(df: pd.DataFrame, pathlike, *, metadata: dict | None = None, index: bool = False, **opts) -> None:
    p = Path(pathlike)
    p.parent.mkdir(parents=True, exist_ok=True)
    fmt = _detect_format(p)
    comp = _detect_compression(p)

    if fmt == "csv":
        df.to_csv(p, index=index, compression=comp, **opts)
    elif fmt == "parquet":
        df.to_parquet(p, index=index, **opts)
    elif fmt == "feather":
        df.to_feather(p, **opts)
    elif fmt == "json":
        df.to_json(p, orient="records", lines=True, compression=comp, **opts)
    else:
        raise ValueError(f"Unsupported format: {fmt}")

    if metadata:
        _meta_sidecar(p).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
