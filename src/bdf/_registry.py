from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# -------------------------------
# Registry loading (accepts either {"datasets":[...]} or {"entries":[...]})
# -------------------------------

def _find_repo_root(markers=("pyproject.toml", ".git"), max_up: int = 8) -> Path:
    p = Path.cwd().resolve()
    for _ in range(max_up):
        if any((p / m).exists() for m in markers):
            return p
        p = p.parent
    return Path.cwd().resolve()

def load_registry(path: str | Path | None = None) -> dict[str, Any]:
    """
    Load datasets registry.

    Accepted shapes:
      { "schema_version": "0.2", "datasets": [ { ... } ] }
      { "schema_version": "0.3", "entries":  [ { ... } ] }

    Default lookup order:
      1) explicit 'path'
      2) env BDF_DATASETS
      3) <repo-root>/data/datasets.json
    """
    if path:
        p = Path(path)
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    env = os.getenv("BDF_DATASETS")
    if env:
        p = Path(env)
        if p.exists():
            with open(p, encoding="utf-8") as f:
                return json.load(f)

    root = _find_repo_root()
    default_path = root / "data" / "datasets.json"
    if default_path.exists():
        with open(default_path, encoding="utf-8") as f:
            return json.load(f)

    raise FileNotFoundError("datasets.json not found. Provide path or set BDF_DATASETS.")

# -------------------------------
# Helpers
# -------------------------------

def _iter_dataset_dicts(reg: dict[str, Any]) -> Iterable[dict[str, Any]]:
    if isinstance(reg, dict):
        if isinstance(reg.get("datasets"), list):
            yield from (d for d in reg["datasets"] if isinstance(d, dict))
            return
        if isinstance(reg.get("entries"), list):
            yield from (d for d in reg["entries"] if isinstance(d, dict))
            return
    if isinstance(reg, list):
        for d in reg:
            if isinstance(d, dict):
                yield d

def list_datasets(path: str | Path | None = None) -> list[str]:
    """Return list of dataset IDs from the registry."""
    reg = load_registry(path)
    out: list[str] = []
    for d in _iter_dataset_dicts(reg):
        if "id" in d and d["id"] is not None:
            out.append(str(d["id"]))
    return out

def get_entry(registry: dict[str, Any], entry_id: str) -> dict[str, Any]:
    """Return the entry dict with matching id (case-insensitive)."""
    key = (entry_id or "").lower()
    for d in _iter_dataset_dicts(registry):
        if str(d.get("id", "")).lower() == key:
            return d
    raise KeyError(f"Dataset id not found: {entry_id}")

__all__ = ["load_registry", "list_datasets", "get_entry"]
