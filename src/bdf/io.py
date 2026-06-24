# src/bdf/io.py
from __future__ import annotations

import csv
import json
import warnings
from pathlib import Path

import pandas as pd
import polars as pl

from bdf.plugins import PLUGINS, Plugin, detect


def read(
    path: str | Path,
    *,
    plugin: Plugin | str | None = None,
    normalize: bool = True,
    validate: bool = True,
    include_optional: bool = True,
    extra_columns: dict[str, str] | None = None,
    lazy: bool = True,
    tz: str = "UTC",
) -> tuple[pl.DataFrame | pl.LazyFrame, dict]:
    """Read ``path`` (local file or URL) to BDF-canonical form, returning ``(df, metadata)``.

    An explicit ``plugin`` bypasses auto-detection. When ``normalize=False``, the raw
    parser frame is returned with source column names unchanged. ``validate`` defaults to
    True: column names are checked against the BDF ontology after reading; pass
    ``validate=False`` to skip the check and only warn. ``tz`` (default ``"UTC"``) is the
    IANA timezone applied to naive ``unix_time_second`` datetime formats; a ``UserWarning``
    is emitted when a naive format is in play and ``tz`` is left at its default.
    """
    plugin_id: str | None = None
    resolved_plugin: Plugin
    if plugin is None:
        plugin_id, resolved_plugin = detect(path)
    elif isinstance(plugin, str):
        plugin_id = plugin
        resolved_plugin = PLUGINS[plugin]
    elif isinstance(plugin, Plugin):
        resolved_plugin = plugin
    else:
        raise ValueError(f"invalid plugin argument: {plugin!r}")

    bdf_df = resolved_plugin.table_parser.read(
        path,
        normalize=normalize,
        validate=validate,
        include_optional=include_optional,
        extra_columns=extra_columns,
        lazy=lazy,
        tz=tz,
    )

    metadata: dict = {
        "source": plugin_id or "custom",
        **resolved_plugin.metadata_parser.parse(path),
    }

    return bdf_df, metadata


_FMT_EXTS = {
    "csv": {".csv", ".bdf.csv"},
    "parquet": {".parquet", ".bdf.parquet"},
    "feather": {".feather", ".bdf.feather"},
    "json": {".json", ".bdf.json"},
}
_COMPRESS = {".gz": "gzip", ".bz2": "bz2", ".xz": "xz", ".zst": "zstd"}


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


def _coalesce_into(target: pd.Series, incoming: pd.Series) -> pd.Series:
    return target.where(target.notna(), incoming)


def _label_maps() -> tuple[dict[str, str], dict[str, str]]:
    """
    Build two maps:
      - pref_label -> machine label (notation), using non-deprecated canonical targets.
      - machine label (notation) -> human pref_label, using non-deprecated canonical targets.
    """
    from . import spec

    base_preferred: dict[str, str] = {}
    for q, s in spec.COLUMN_ONTOLOGY:
        if s.deprecated:
            continue
        base = s.label_template.split(" / ", 1)[0].strip().lower()
        base_preferred.setdefault(base, q)

    pref_to_machine: dict[str, str] = {}
    machine_to_pref: dict[str, str] = {}

    for q, s in spec.COLUMN_ONTOLOGY:
        source_pref = s.formatted_label
        source_notation = s.effective_notation

        target_q = q
        if s.deprecated:
            base = source_pref.split(" / ", 1)[0].strip().lower()
            target_q = base_preferred.get(base, q)

        target = getattr(spec.COLUMN_ONTOLOGY, target_q)
        target_pref = target.formatted_label
        target_notation = target.effective_notation

        pref_to_machine.setdefault(source_pref, target_notation)
        machine_to_pref.setdefault(source_notation, target_pref)

    return pref_to_machine, machine_to_pref


def _serialize_labels(df: pd.DataFrame, *, human: bool) -> pd.DataFrame:
    out = df.copy()
    pref_to_machine, machine_to_pref = _label_maps()

    if human:
        for source, target in machine_to_pref.items():
            if source not in out.columns:
                continue
            if source == target:
                continue
            if target in out.columns:
                out[target] = _coalesce_into(out[target], out[source])
                out.drop(columns=[source], inplace=True)
            else:
                out.rename(columns={source: target}, inplace=True)
        return out

    for source, target in pref_to_machine.items():
        if source not in out.columns:
            continue
        if source == target:
            continue
        if target in out.columns:
            out[target] = _coalesce_into(out[target], out[source])
            out.drop(columns=[source], inplace=True)
        else:
            out.rename(columns={source: target}, inplace=True)
    return out


def load(pathlike) -> pd.DataFrame:
    p = Path(pathlike)
    if not p.exists():
        raise FileNotFoundError(p.name)
    fmt = _detect_format(p)
    comp = _detect_compression(p)

    try:
        df = None
        if fmt == "csv":
            # strict CSV: no banner rows, uniform columns
            df = pd.read_csv(
                p,
                engine="python",  # better error messages for malformed rows
                sep=",",
                quoting=csv.QUOTE_MINIMAL,
                on_bad_lines="error",
                skip_blank_lines=True,
                compression=comp,
            )
        elif fmt == "parquet":
            df = pd.read_parquet(p)
        elif fmt == "feather":
            df = pd.read_feather(p)
        elif fmt == "json":
            df = pd.read_json(p, lines=True, compression=comp)
        else:
            raise ValueError(f"Unsupported format: {fmt}")

        # Always expose human canonical labels in-memory.
        from .normalize import canonicalize_legacy_labels

        df, legacy = canonicalize_legacy_labels(df)
        if legacy:
            warnings.warn(
                "Legacy BDF column labels detected (skos:altLabel/notation). They were normalized to preferred labels.",
                stacklevel=2,
            )
        return _serialize_labels(df, human=True)
    except Exception as e:
        # Re-raise with a short, path-sanitized message
        emsg = str(e)
        raise ValueError(f"Failed to parse BDF {fmt.upper()} file: {p.name}: {emsg}") from e


def save(
    df: pd.DataFrame,
    pathlike,
    *,
    metadata: dict | None = None,
    index: bool = False,
    human: bool = False,
    **opts,
) -> None:
    p = Path(pathlike)
    p.parent.mkdir(parents=True, exist_ok=True)
    fmt = _detect_format(p)
    comp = _detect_compression(p)

    try:
        from .normalize import canonicalize_legacy_labels

        df, _legacy = canonicalize_legacy_labels(df)
    except Exception:
        pass
    df = _serialize_labels(df, human=human)

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
