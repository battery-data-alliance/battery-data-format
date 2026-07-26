# src/bdf/io.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, cast

import pandas as pd
import polars as pl

from bdf.file_utils import open_compressed, strip_compression_suffix
from bdf.plugins import PLUGINS, Plugin, detect
from bdf.spec import COLUMN_ONTOLOGY


def _read(
    path: str | Path,
    *,
    plugin: Plugin | str | None = None,
    normalize: bool = True,
    validate: bool = True,
    include_unknown: bool = False,
    lazy: bool = True,
    tz: str = "UTC",
) -> tuple[pl.DataFrame | pl.LazyFrame, dict]:
    """Read ``path`` (local file or URL) to BDF-canonical form, returning ``(df, metadata)``.

    Private implementation behind the public `read` and `scan` functions.

    Raises:
        ValueError: If ``plugin`` is not None, a str, or a Plugin instance.
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
        include_unknown=include_unknown,
        lazy=lazy,
        tz=tz,
    )

    metadata: dict = {
        "source": plugin_id or "custom",
        **resolved_plugin.metadata_parser.parse(path),
    }

    return bdf_df, metadata


def read(
    path: str | Path,
    *,
    plugin: Plugin | str | None = None,
    normalize: bool = True,
    validate: bool = True,
    include_unknown: bool = False,
    tz: str = "UTC",
) -> tuple[pl.DataFrame, dict]:
    """Read ``path`` (local file or URL) to BDF-canonical form, returning ``(df, metadata)``.

    Collects to a :class:`polars.DataFrame`; use :func:`scan` for a :class:`polars.LazyFrame`.

    Args:
        path: Local file path or http(s) URL to read.
        plugin: Plugin instance or registry id. Auto-detects if not set (default).
        normalize: Map vendor columns to BDF canonical names (default True); False returns
            raw source columns unchanged.
        validate: Check columns against the BDF ontology, error if missing required columns
            (default True); set to False to only warn.
        include_unknown: Keep columns outside of the BDF spec in the dataframe (default False).
        tz: IANA timezone used to compute ``Unix Time / s`` if the source has naive datetime.
            Default is``"UTC"``, and will warn if source contains naive datetimes.

    Returns:
        Tuple of (df, metadata): the BDF table as a DataFrame, and a metadata dict with at
        least a ``"source"`` key naming the resolved plugin id (``"custom"`` for a
        directly-supplied ``Plugin``).

    Raises:
        ValueError: If ``plugin`` is not None, a str, or a Plugin instance.
    """
    bdf_df, metadata = _read(
        path,
        plugin=plugin,
        normalize=normalize,
        validate=validate,
        include_unknown=include_unknown,
        lazy=False,
        tz=tz,
    )
    return cast(pl.DataFrame, bdf_df), metadata


def scan(
    path: str | Path,
    *,
    plugin: Plugin | str | None = None,
    normalize: bool = True,
    validate: bool = True,
    include_unknown: bool = False,
    tz: str = "UTC",
) -> tuple[pl.LazyFrame, dict]:
    """Scan ``path`` (local file or URL) to BDF-canonical form, returning ``(df, metadata)``.

    Returns a :class:`polars.LazyFrame`; use :func:`read` for an eager :class:`polars.DataFrame`.

    Laziness depends on the plugin: CSV/Parquet parsers scan lazily with real pushdown; binary
    formats (.xlsx, .nda, .ndax, .mat, .mpr) read eagerly and just wrap the result in a
    LazyFrame — harmless, but no performance benefit.

    Args:
        path: Local file path or http(s) URL to read.
        plugin: Plugin instance or registry id; auto-detects via ``bdf.plugins.detect`` when
            None (default).
        normalize: Map vendor columns to BDF canonical names (default True); False returns
            raw source columns unchanged.
        validate: Check columns against the BDF ontology, raising on missing required ones
            (default True); False only warns.
        include_unknown: Keep columns outside of the BDF spec in the dataframe (default False).
        tz: IANA timezone used to compute ``Unix Time / s`` if the source has naive datetime.
            Default is``"UTC"``, and will warn if source contains naive datetimes.

    Returns:
        Tuple of (df, metadata): the BDF table as a LazyFrame, and a metadata dict with at
        least a ``"source"`` key naming the resolved plugin id (``"custom"`` for a
        directly-supplied ``Plugin``).

    Raises:
        ValueError: If ``plugin`` is not None, a str, or a Plugin instance.
    """
    bdf_df, metadata = _read(
        path,
        plugin=plugin,
        normalize=normalize,
        validate=validate,
        include_unknown=include_unknown,
        lazy=True,
        tz=tz,
    )
    return cast(pl.LazyFrame, bdf_df), metadata


_FMT_EXTS = {
    "csv": {".csv", ".bdf.csv"},
    "parquet": {".parquet", ".bdf.parquet", ".pq", ".bdf.pq"},
    "ipc": {".ipc", ".bdf.ipc", ".feather", ".bdf.feather", ".ftr", ".bdf.ftr", ".arrow", ".bdf.arrow"},
    "json": {".json", ".bdf.json"},
    "ndjson": {".ndjson", ".bdf.ndjson"},
    "xlsx": {".xlsx", ".bdf.xlsx"},
}


def _detect_format(path: Path) -> str:
    """Return the BDF artifact format ("csv"/"parquet"/"feather"/"json") for ``path``.

    Args:
        path: File path whose suffixes are inspected (e.g. ``.bdf.csv.gz``).

    Returns:
        Format name matched against :data:`_FMT_EXTS`, falling back to the final suffix.

    Raises:
        ValueError: If no known format extension is found in ``path``.
    """
    sfx = "".join(Path(strip_compression_suffix(path.name)).suffixes).lower()
    for fmt, exts in _FMT_EXTS.items():
        if any(sfx.endswith(e) for e in exts):
            return fmt
    raise ValueError(f"Unknown BDF artifact format: {path.name}")


def _meta_sidecar(path: Path) -> Path:
    """Return the metadata sidecar path for a BDF artifact path.

    Args:
        path: BDF artifact file path.

    Returns:
        Path with ``.metadata.json`` appended to the file name.
    """
    return path.with_name(path.name + ".metadata.json")


def save(
    df: pl.DataFrame | pl.LazyFrame | pd.DataFrame,
    pathlike: str | Path,
    *,
    metadata: dict | None = None,
    validate: bool = True,
    labels: Literal["preferred", "machine", "unchanged"] = "unchanged",
    **opts,
) -> None:
    """Save a BDF table to a CSV/parquet/IPC/JSON/ndjson/xlsx artifact.

    Detects format and compression from the file extension and creates parent
    directories as needed.

    Args:
        df: BDF table to write.
        pathlike: Output file path; format/compression are inferred from its extension.
        metadata: Optional metadata dict written alongside as a ``.metadata.json`` sidecar.
        validate: Check columns against the BDF ontology, raising on missing required ones
            (default True); False only warns.
        labels: Style of column names to use (default: "unchanged"):
            "preferred": BDF preferred label, e.g. "Voltage / V"
            "machine": BDF machine-readable label e.g. "voltage_volt"
            "unchanged": Keep column names as-is
        **opts: Additional keyword arguments forwarded to the polars writer
            (``write_csv``/``write_parquet``/``write_ipc``/``write_json``/``write_ndjson``/
            ``write_excel``).

    Raises:
        ValueError: If the format is unsupported, or compression is requested for xlsx output.
    """
    p = Path(pathlike)
    p.parent.mkdir(parents=True, exist_ok=True)
    fmt = _detect_format(p)

    if isinstance(df, pl.LazyFrame):
        df = df.collect()
    elif isinstance(df, pd.DataFrame):
        df = pl.from_pandas(df)

    COLUMN_ONTOLOGY.validate_df(df, raise_on_error=validate)

    df = COLUMN_ONTOLOGY.rename_labels(df, labels)

    assert isinstance(df, pl.DataFrame)

    target: Any = open_compressed(p)
    try:
        if fmt == "csv":
            df.write_csv(target, **opts)
        elif fmt == "parquet":
            df.write_parquet(target, **opts)
        elif fmt == "ipc":
            df.write_ipc(target, **opts)
        elif fmt == "json":
            df.write_json(target, **opts)
        elif fmt == "ndjson":
            df.write_ndjson(target, **opts)
        elif fmt == "xlsx":
            if not isinstance(target, Path):
                msg = "Compression is not supported for xlsx output"
                raise ValueError(msg)
            df.write_excel(target, **opts)
        else:
            raise ValueError(f"Unsupported format: {fmt}")
    finally:
        if not isinstance(target, Path):
            target.close()

    if metadata:
        _meta_sidecar(p).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
