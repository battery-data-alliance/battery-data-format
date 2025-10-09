# src/bdf/data_sources/base.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple, Type
import re
import numpy as np
import pandas as pd

@dataclass
class SniffResult:
    id: str
    confidence: float
    reason: str
    meta: Dict[str, Any] = field(default_factory=dict)

class _CyclerRegistry:
    _by_id: Dict[str, Type["CyclerPlugin"]] = {}
    def register(self, cls: Type["CyclerPlugin"]):
        if not getattr(cls, "id", None):
            raise ValueError(f"{cls.__name__} missing 'id'")
        if cls.id in self._by_id:
            raise ValueError(f"Duplicate plugin id: {cls.id}")
        self._by_id[cls.id] = cls
    def get(self, id_: str) -> Type["CyclerPlugin"] | None:
        return self._by_id.get(id_)
    def all(self) -> Iterable[Type["CyclerPlugin"]]:
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
        if col_out in df.columns:
            return df

        # find a timestamp-like column
        pat = re.compile("|".join(self.timestamp_candidate_patterns), re.IGNORECASE)
        cand = next((c for c in df.columns if pat.search(str(c).strip().lower())), None)
        if cand is None:
            return df

        s = df[cand]

        # numeric epoch? (auto-detect s/ms/us/ns)
        if pd.api.types.is_numeric_dtype(s):
            x = pd.to_numeric(s, errors="coerce").astype("float64")
            med_abs = float(np.nanmedian(np.abs(x))) if len(x) else np.nan
            if np.isfinite(med_abs):
                if med_abs >= 1e17: unit = "ns"
                elif med_abs >= 1e14: unit = "us"
                elif med_abs >= 1e11: unit = "ms"
                else: unit = "s"
            else:
                unit = "s"
            try:
                dt = pd.to_datetime(x, unit=unit, utc=True, errors="coerce")
            except Exception:
                dt = pd.to_datetime(x, utc=True, errors="coerce")
        else:
            # string-like
            dt0 = pd.to_datetime(s, utc=False, errors="coerce")
            if getattr(dt0.dtype, "tz", None) is None:
                if self.assume_naive_tz:
                    try:
                        dt = (
                            pd.to_datetime(s, errors="coerce")
                            .dt.tz_localize(self.assume_naive_tz)
                            .dt.tz_convert("UTC")
                        )
                    except Exception:
                        dt = pd.to_datetime(s, utc=True, errors="coerce")
                else:
                    dt = pd.to_datetime(s, utc=True, errors="coerce")
            else:
                try:
                    dt = dt0.dt.tz_convert("UTC")
                except Exception:
                    dt = dt0

        # keep only if it parsed for a reasonable fraction of rows
        if len(df) == 0 or float(dt.notna().mean()) < 0.5:
            return df

        # --- FIX: mask NaT before int conversion to avoid huge negative values ---
        valid = dt.notna().to_numpy()
        epoch_s = np.full(len(dt), np.nan, dtype="float64")
        try:
            # pandas ≥ 2.x preferred
            epoch_ns_valid = dt.astype("int64")[valid]
        except TypeError:
            # older pandas fallback
            epoch_ns_valid = dt.view("int64")[valid]
        epoch_s[valid] = epoch_ns_valid.astype("float64") / 1_000_000_000.0

        out = df.copy()
        out[col_out] = epoch_s
        return out
