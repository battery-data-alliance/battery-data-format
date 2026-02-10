from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from .base import CyclerPlugin, SniffResult


class ExcelXlsx(CyclerPlugin):
    """
    Excel reader with optional per-file or per-dataset config.

    Config search order:
      1) env BDF_EXCEL_CONFIG (absolute path)
      2) <file>.excel.json
      3) bdf.excel.json
      4) excel.json
      5) contribution.json / collection.json with an "excel" object (search parents)

    Config keys (all optional):
      - sheet / sheet_name / worksheet: sheet name or 0-based index
      - sheet_index: 1-based index (if provided)
      - header_row: 1-based header row
      - header: pandas header argument (overrides header_row)
      - usecols: pandas usecols (e.g., "A:G")
      - skiprows: pandas skiprows
      - nrows: pandas nrows
      - engine: pandas engine
      - rename: {old: new} column rename mapping
      - drop_empty_rows: bool
    """

    id = "excel-xlsx"
    exts = (".xlsx", ".xlsm", ".xls")

    def sniff(self, path: Path, head: bytes) -> SniffResult:
        score, reasons = 0.0, []
        suffix = path.suffix.lower()
        if suffix in self.exts:
            score += 0.5
            reasons.append("ext")
        if head.startswith(b"PK"):
            score += 0.4
            reasons.append("zip")
        if head.startswith(b"\xD0\xCF\x11\xE0"):
            score += 0.4
            reasons.append("ole")
        return SniffResult(self.id, min(score, 1.0), "+".join(reasons), {})

    def parse(self, path: Path) -> pd.DataFrame:
        cfg = _load_excel_config(path)
        sheet = _resolve_sheet(cfg)
        header = _resolve_header(cfg)
        usecols = cfg.get("usecols")
        skiprows = cfg.get("skiprows")
        nrows = cfg.get("nrows")
        engine = cfg.get("engine") or _default_engine_for(path)

        try:
            df = pd.read_excel(
                path,
                sheet_name=sheet,
                header=header,
                usecols=usecols,
                skiprows=skiprows,
                nrows=nrows,
                engine=engine,
            )
        except ImportError as exc:
            raise RuntimeError(
                "Reading Excel files requires openpyxl (for .xlsx/.xlsm) or xlrd (for .xls). "
                "Install with `pip install openpyxl`."
            ) from exc

        if isinstance(df, dict):
            raise ValueError(
                "Excel reader expects a single sheet. "
                "Specify a sheet name or index in the excel config."
            )

        rename = cfg.get("rename")
        if isinstance(rename, dict) and rename:
            df = df.rename(columns=rename)

        if cfg.get("drop_empty_rows"):
            df = df.dropna(how="all")

        if cfg.get("strip_headers", True):
            df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]

        return df


def _default_engine_for(path: Path) -> str:
    if path.suffix.lower() == ".xls":
        return "xlrd"
    return "openpyxl"


def _strip_all_suffixes(path: Path) -> str:
    name = path.name
    while True:
        suffix = Path(name).suffix
        if not suffix:
            break
        name = Path(name).stem
    return name


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"Excel config must be a JSON object: {path}")
    return raw


def _load_excel_config(path: Path) -> dict[str, Any]:
    env = os.environ.get("BDF_EXCEL_CONFIG")
    if env:
        return _load_json(Path(env))

    base = _strip_all_suffixes(path)
    candidates = [
        path.with_name(f"{base}.excel.json"),
        path.with_name("bdf.excel.json"),
        path.with_name("excel.json"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return _load_json(candidate)

    parent_cfg = _find_contribution_excel_config(path.parent)
    if parent_cfg:
        return parent_cfg

    return {}


def _find_contribution_excel_config(start: Path) -> Optional[dict[str, Any]]:
    current = start.resolve()
    while True:
        for name in ("contribution.json", "collection.json"):
            candidate = current / name
            if candidate.exists():
                raw = _load_json(candidate)
                excel_cfg = raw.get("excel")
                if isinstance(excel_cfg, dict):
                    return excel_cfg
        if current.parent == current:
            break
        current = current.parent
    return None


def _resolve_sheet(cfg: dict[str, Any]) -> Any:
    if "sheet" in cfg:
        return cfg["sheet"]
    if "sheet_name" in cfg:
        return cfg["sheet_name"]
    if "worksheet" in cfg:
        return cfg["worksheet"]
    if "sheet_index" in cfg:
        try:
            idx = int(cfg["sheet_index"])
            return max(0, idx - 1)
        except Exception:
            return 0
    return 0


def _resolve_header(cfg: dict[str, Any]) -> Any:
    if "header" in cfg:
        return cfg["header"]
    if "header_row" in cfg:
        hr = cfg["header_row"]
        if isinstance(hr, list):
            out = []
            for item in hr:
                try:
                    out.append(max(0, int(item) - 1))
                except Exception:
                    out.append(item)
            return out
        try:
            return max(0, int(hr) - 1)
        except Exception:
            return 0
    return 0
