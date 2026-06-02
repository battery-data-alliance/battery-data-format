# src/bdf/data_sources/base.py
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Tuple, Type

import pandas as pd


@dataclass
class SniffResult:
    id: str
    confidence: float
    reason: str
    meta: Dict[str, Any] = field(default_factory=dict)

class _CyclerRegistry:
    _by_id: Dict[str, Type[CyclerPlugin]] = {}
    def register(self, cls: Type[CyclerPlugin]):
        if not getattr(cls, "id", None):
            raise ValueError(f"{cls.__name__} missing 'id'")
        if cls.id in self._by_id:
            raise ValueError(f"Duplicate plugin id: {cls.id}")
        self._by_id[cls.id] = cls
    def get(self, id_: str) -> Type[CyclerPlugin] | None:
        return self._by_id.get(id_)
    def all(self) -> Iterable[Type[CyclerPlugin]]:
        return self._by_id.values()

REGISTRY = _CyclerRegistry()

# Metaclass must inherit from 'type'
class _AutoRegister(type):
    def __init__(cls, name, bases, ns):
        super().__init__(name, bases, ns)
        # avoid registering the abstract base itself or private helpers
        if name != "CyclerPlugin" and not name.startswith("_"):
            REGISTRY.register(cls)

class CyclerPlugin(metaclass=_AutoRegister):
    """
    Base class for cycler plugins.
    Subclasses override: id, exts, sniff(), parse().
    Optional hooks: augment(), fixup().
    """
    id: str = "abstract"
    exts: Tuple[str, ...] = ()
    column_synonyms: Dict[str, list[str]] = {}  # optional

    # ---- Auto "Unix Time / s" support ----
    unix_time_col_name: str = "Unix Time / s"
    timestamp_candidate_patterns: Tuple[str, ...] = (
        r"^timestamp$",
        r"^date[_\s/:-]*time$",
        r"^datetime$",
        r"^date\s*time\s*iso",
        r"^time\s*stamp$",
        r"^utc[_\s-]*time$",
    )
    # If naive timestamps found, treat as this tz and convert to UTC; set None to treat as UTC already
    assume_naive_tz: str | None = "UTC"
    # strftime format for timestamp candidate columns; None = let pandas infer (may warn)
    timestamp_format: str | None = None

    # ----- required API -----
    def sniff(self, path: Path, head: bytes) -> SniffResult: ...
    def parse(self, path: Path) -> pd.DataFrame: ...

    # ----- optional hooks -----
    def augment(self, df_raw: pd.DataFrame) -> pd.DataFrame:
        """
        Called right AFTER parse() and BEFORE normalization.
        Default: derive "Unix Time / s" from a timestamp-like column if present.
        """
        return self._ensure_unix_time(df_raw)

    def fixup(self, df: pd.DataFrame) -> pd.DataFrame:
        """Optional post-normalization tweaks (e.g., unit fixes)."""
        return df

    # ----- helpers -----
    def _ensure_unix_time(self, df: pd.DataFrame) -> pd.DataFrame:
        col_out = self.unix_time_col_name
        hint = _timestamp_hint(df)
        hinted_col = hint.get("column")
        hinted_fmt = hint.get("format")
        hinted_tz = hint.get("timezone") or self.assume_naive_tz

        from bdf.time import parse_unix_time

        if col_out in df.columns:
            s = df[col_out]
            if pd.api.types.is_numeric_dtype(s):
                return df
            try:
                unix = parse_unix_time(s, fmt=hinted_fmt, tz=hinted_tz, min_success=0.5)
                out = df.copy()
                out[col_out] = unix
                return out
            except Exception:
                return df

        if hinted_col and hinted_col in df.columns:
            try:
                unix = parse_unix_time(
                    df[hinted_col],
                    fmt=hinted_fmt,
                    tz=hinted_tz,
                    min_success=0.5,
                )
                out = df.copy()
                out[col_out] = unix
                return out
            except Exception:
                pass

        pat = re.compile("|".join(self.timestamp_candidate_patterns), re.IGNORECASE)
        cand = next((c for c in df.columns if pat.search(str(c).strip().lower())), None)
        if cand is None:
            return df

        try:
            unix = parse_unix_time(df[cand], fmt=self.timestamp_format, tz=self.assume_naive_tz, min_success=0.5)
        except Exception:
            return df

        out = df.copy()
        out[col_out] = unix
        return out


def _timestamp_hint(df: pd.DataFrame) -> Dict[str, Any]:
    meta = getattr(df, "attrs", {}).get("bdf:timestamp")
    if not isinstance(meta, dict):
        return {}
    column = meta.get("column") or meta.get("source") or meta.get("name")
    fmt = meta.get("format")
    timezone = meta.get("timezone") or meta.get("tz")
    return {"column": column, "format": fmt, "timezone": timezone}
