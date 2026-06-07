from __future__ import annotations

import json
import os
import re
import warnings
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from .base import CyclerPlugin, SniffResult


class MatlabMat(CyclerPlugin):
    """
    MATLAB .mat reader with explicit sidecar mapping.

    Mapping file (JSON) should define target BDF columns -> source variable names:
      {
        "fields": {
          "test_time_second": {"source": "t", "source_unit": "h"},
          "voltage_volt": {"source": "U"},
          "current_ampere": {"source": "I", "source_unit": "mA"}
        },
        "scale": {"voltage_volt": 0.001},
        "offset": {"voltage_volt": 0.0},
        "data_path": "data"   # optional nested struct path
      }
    """

    id = "matlab-mat"
    exts = (".mat",)

    def sniff(self, path: Path, head: bytes) -> SniffResult:
        score, reasons = 0.0, []
        if path.suffix.lower() == ".mat":
            score += 0.4
            reasons.append("ext")
        head_upper = head[:128]
        if b"MATLAB" in head_upper:
            score += 0.5
            reasons.append("matlab")
        if head_upper.startswith(b"\x89HDF") or b"HDF" in head_upper:
            score += 0.3
            reasons.append("hdf5")
        return SniffResult(self.id, min(score, 1.0), "+".join(reasons), {})

    def parse(self, path: Path) -> pd.DataFrame:
        mapping = _load_mapping(path)
        allow_infer = os.environ.get("BDF_MAT_INFER", "").strip() in {"1", "true", "True", "yes"}

        data = _load_mat_data(path, mapping.get("data_path") if mapping else None)

        if mapping is None and allow_infer:
            mapping = {"fields": _infer_mapping(list(data.keys()))}
            warnings.warn(
                "Inferred .mat mapping (BDF_MAT_INFER=1). "
                "Provide a .map.json file to lock this down.",
                stacklevel=2,
            )

        if mapping is None:
            raise ValueError(
                "MATLAB .mat files require a mapping file. "
                "Create a sidecar <name>.map.json or bdf.mapping.json."
            )

        variables, field_units = _mapping_variables_and_units(mapping)
        if not isinstance(variables, dict) or not variables:
            raise ValueError(
                "Mapping file must include target-keyed 'fields' entries "
                "or a legacy 'variables'/'columns'/'map' object."
            )

        variables = _normalize_mapping(variables)
        units: dict[str, Any] = {}
        source_units = mapping.get("source_units")
        if isinstance(source_units, dict):
            units.update(source_units)
        legacy_units = mapping.get("units")
        if isinstance(legacy_units, dict):
            units.update(legacy_units)
        units.update(field_units)
        scale = mapping.get("scale") or {}
        offset = mapping.get("offset") or {}

        dt_cfg = _datetime_config(mapping, variables, units)

        cols: dict[str, Any] = {}
        for target, source in variables.items():
            src = _select_source_name(source, data)
            if not src:
                raise KeyError(f"Variable not found for target '{target}'.")
            arr = _extract_array(data, src)
            cols[target] = arr

        df = pd.DataFrame(cols)

        if dt_cfg:
            dt_source = dt_cfg.get("source")
            dt_target = dt_cfg.get("target") or "Unix Time / s"
            if dt_source and dt_target not in df.columns:
                src = _select_source_name(dt_source, data)
                if src:
                    df[dt_target] = _extract_array(data, src)
            if "bdf:timestamp" not in df.attrs:
                df.attrs["bdf:timestamp"] = {
                    "column": dt_target,
                    "format": dt_cfg.get("format"),
                    "timezone": dt_cfg.get("timezone"),
                }
            if dt_target in units and _looks_like_datetime_format(str(units[dt_target])):
                units = dict(units)
                units.pop(dt_target, None)

        for col, factor in scale.items():
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce") * float(factor)

        for col, delta in offset.items():
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce") + float(delta)

        for col, unit in units.items():
            if col not in df.columns:
                continue
            try:
                from bdf.units.core import convert_series, resolve_unit
                to_unit = resolve_unit(col, as_string=True)
                df[col] = convert_series(df[col], str(unit), str(to_unit))
            except Exception:
                continue

        return df


def _strip_all_suffixes(path: Path) -> str:
    base = path.name
    while True:
        suffix = Path(base).suffix
        if not suffix:
            break
        base = Path(base).stem
    return base


def _load_mapping(path: Path) -> Optional[dict[str, Any]]:
    base = _strip_all_suffixes(path)
    candidates = [
        path.with_name(f"{base}.map.json"),
        path.with_name(f"{base}.mapping.json"),
        path.with_name("bdf.mapping.json"),
        path.with_name("bdf.map.json"),
    ]
    env = os.environ.get("BDF_MAT_MAPPING")
    if env:
        candidates.insert(0, Path(env))

    for candidate in candidates:
        if candidate.exists():
            with open(candidate, encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError(f"Mapping file must be a JSON object: {candidate}")
            return raw
    return None


def _load_mat_data(path: Path, data_path: Optional[str]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    try:
        from scipy.io import loadmat  # type: ignore
        data = loadmat(path, squeeze_me=True, struct_as_record=False)
        data = {k: v for k, v in data.items() if not k.startswith("__")}
    except Exception as exc:
        if _is_hdf5_file(path):
            data = _load_mat_hdf5(path)
        else:
            raise RuntimeError(
                "Reading .mat requires scipy (and h5py for v7.3). "
                "Install with `pip install scipy h5py`."
            ) from exc

    if data_path:
        data = _resolve_data_path(data, data_path)
    return data


def _load_mat_hdf5(path: Path) -> dict[str, Any]:
    try:
        import h5py  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Reading .mat (v7.3) requires h5py. Install with `pip install h5py`."
        ) from exc
    data: dict[str, Any] = {}
    with h5py.File(path, "r") as f:
        for key in f:
            data[key] = f[key][()]
    return data


def _is_hdf5_file(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except Exception:
        return False
    return head.startswith(b"\x89HDF")


def _resolve_data_path(data: dict[str, Any], path_expr: str) -> dict[str, Any]:
    parts = [p for p in re.split(r"[./]", path_expr) if p]
    obj: Any = data
    for part in parts:
        obj = _get_child(obj, part)
    mapped = _to_mapping(obj)
    if not mapped:
        raise KeyError(f"data_path '{path_expr}' does not resolve to a mapping.")
    return mapped


def _get_child(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj[key]
    if hasattr(obj, "_fieldnames") and key in obj._fieldnames:
        return getattr(obj, key)
    if isinstance(obj, np.ndarray) and obj.dtype.names and key in obj.dtype.names:
        return obj[key]
    if hasattr(obj, key):
        return getattr(obj, key)
    raise KeyError(key)


def _to_mapping(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "_fieldnames"):
        return {name: getattr(obj, name) for name in obj._fieldnames}
    if isinstance(obj, np.ndarray) and obj.dtype.names:
        return {name: obj[name] for name in obj.dtype.names}
    return {}


def _select_source_name(source: Any, data: dict[str, Any]) -> Optional[str]:
    if isinstance(source, str):
        return source if source in data else _find_key_case_insensitive(source, data)
    if isinstance(source, list):
        for candidate in source:
            if isinstance(candidate, str):
                if candidate in data:
                    return candidate
                match = _find_key_case_insensitive(candidate, data)
                if match:
                    return match
    return None


def _find_key_case_insensitive(name: str, data: dict[str, Any]) -> Optional[str]:
    name_lower = name.lower()
    for key in data:
        if str(key).lower() == name_lower:
            return str(key)
    return None


def _extract_array(data: dict[str, Any], source: str) -> np.ndarray:
    obj = data[source]
    arr = np.asarray(obj)
    if arr.ndim > 1 and 1 in arr.shape:
        arr = arr.reshape(-1)
    return arr


def _infer_mapping(variables: list[str]) -> dict[str, str]:
    lower = {v.lower(): v for v in variables}

    def _pick(candidates: list[str]) -> Optional[str]:
        for cand in candidates:
            if cand in lower:
                return lower[cand]
        for cand in candidates:
            for key in lower:
                if cand in key:
                    return lower[key]
        return None

    time_var = _pick(["time", "t", "test time", "test_time", "time_s", "time_sec"])
    volt_var = _pick(["voltage", "v", "ewe", "u", "voltage_v", "cell_voltage"])
    curr_var = _pick(["current", "i", "current_a", "current_ma", "i_a", "i_ma"])

    mapping: dict[str, str] = {}
    if time_var:
        mapping["Test Time / s"] = time_var
    if volt_var:
        mapping["Voltage / V"] = volt_var
    if curr_var:
        mapping["Current / A"] = curr_var
    if not mapping:
        raise ValueError("Could not infer any standard BDF columns from .mat variables.")
    return mapping


def _normalize_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    def _looks_like_bdf(label: str) -> bool:
        text = label.strip()
        if " / " in text:
            return True
        if text.lower() in {
            "test time / s",
            "voltage / v",
            "current / a",
        }:
            return True
        try:
            from bdf import spec
            if text in spec.COLUMNS:
                return True
            for q in spec.COLUMNS:
                if text == spec._label_for(q):
                    return True
                if text == spec.notation_for(q):
                    return True
        except Exception:
            pass
        return False

    keys = [str(k) for k in mapping]
    values = [str(v) for v in mapping.values()]
    if any(_looks_like_bdf(k) for k in keys):
        return mapping
    if any(_looks_like_bdf(v) for v in values):
        return {str(v): k for k, v in mapping.items()}
    return mapping


def _datetime_config(
    mapping: dict[str, Any],
    variables: dict[str, Any],
    units: dict[str, Any],
) -> Optional[dict[str, Any]]:
    cfg_raw = mapping.get("datetime") or mapping.get("timestamp") or {}
    if cfg_raw is None:
        cfg_raw = {}
    if not isinstance(cfg_raw, dict):
        return None

    source = (
        cfg_raw.get("source")
        or cfg_raw.get("column")
        or mapping.get("datetime_source")
        or mapping.get("timestamp_source")
    )
    target = (
        cfg_raw.get("target")
        or mapping.get("datetime_target")
        or mapping.get("timestamp_target")
        or "Unix Time / s"
    )
    fmt = cfg_raw.get("format") or mapping.get("datetime_format") or mapping.get("timestamp_format")
    tz = (
        cfg_raw.get("timezone")
        or cfg_raw.get("tz")
        or mapping.get("datetime_timezone")
        or mapping.get("datetime_tz")
        or mapping.get("timestamp_timezone")
        or mapping.get("timestamp_tz")
    )

    if not source and target in variables:
        source = variables.get(target)

    if not fmt and isinstance(units, dict):
        unit_hint = units.get(target)
        if isinstance(unit_hint, str) and _looks_like_datetime_format(unit_hint):
            fmt = unit_hint

    if not any([source, fmt, tz]) and target not in variables:
        return None

    return {"source": source, "target": target, "format": fmt, "timezone": tz}


def _mapping_variables_and_units(mapping: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    fields = mapping.get("fields")
    units: dict[str, str] = {}
    if isinstance(fields, dict) and fields:
        variables: dict[str, Any] = {}
        for target, spec in fields.items():
            key = str(target)
            if isinstance(spec, (str, list)):
                variables[key] = spec
                continue
            if not isinstance(spec, dict):
                continue
            source = spec.get("source")
            if source is None:
                source = spec.get("column")
            if source is None:
                source = spec.get("variable")
            if source is None:
                continue
            variables[key] = source
            src_unit = spec.get("source_unit")
            if isinstance(src_unit, str) and src_unit:
                units[key] = src_unit
            else:
                unit = spec.get("unit")
                if isinstance(unit, str) and unit:
                    units[key] = unit
        if variables:
            return variables, units

    variables = (
        mapping.get("variables")
        or mapping.get("columns")
        or mapping.get("map")
        or {}
    )
    if isinstance(variables, dict):
        return variables, units
    return {}, units


def _looks_like_datetime_format(value: str) -> bool:
    text = value.strip()
    if "%" in text:
        return True
    for token in ("YYYY", "YY", "DD", "HH", "SS", "AM", "PM"):
        if token in text:
            return True
    return bool("/" in text and ":" in text)
