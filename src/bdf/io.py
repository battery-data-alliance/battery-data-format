# src/bdf/io.py
from __future__ import annotations

import contextlib
import csv
import json
import re
import warnings
from pathlib import Path

import pandas as pd
import polars as pl

from bdf import spec
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
) -> tuple[pl.DataFrame | pl.LazyFrame, dict]:
    """Read ``path`` (local file or URL) to BDF-canonical form, returning ``(df, metadata)``.

    An explicit ``plugin`` bypasses auto-detection. When ``normalize=False``, the raw
    parser frame is returned with source column names unchanged. ``validate`` defaults to
    True: column names are checked against the BDF ontology after reading; pass
    ``validate=False`` to skip the check and only warn.

    Args:
        path: Local file path or http(s) URL to read.
        plugin: Explicit Plugin instance or registry id to use, bypassing auto-detection
            when set. Defaults to None (auto-detect via ``bdf.plugins.detect``).
        normalize: Map vendor columns to BDF canonical names when True (default).
        validate: Validate column names against the BDF ontology when True (default;
            raises on missing required columns instead of warning).
        include_optional: Include optional BDF columns in the normalized output.
        extra_columns: Additional column rename mappings to apply during normalization.
        lazy: Return a LazyFrame when True (default); collect to a DataFrame when False.

    Returns:
        Tuple of (df, metadata): the BDF table (LazyFrame or DataFrame per ``lazy``) and
        a metadata dict with at least a ``"source"`` key naming the resolved plugin id
        (or ``"custom"`` for a directly-supplied ``Plugin``).

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
        include_optional=include_optional,
        extra_columns=extra_columns,
        lazy=lazy,
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
    """Return the BDF artifact format ("csv"/"parquet"/"feather"/"json") for ``path``.

    Args:
        path: File path whose suffixes are inspected (e.g. ``.bdf.csv.gz``).

    Returns:
        Format name matched against :data:`_FMT_EXTS`, falling back to the final suffix.

    Raises:
        ValueError: If no known format extension is found in ``path``.
    """
    sfx = "".join(path.suffixes).lower()
    for fmt, exts in _FMT_EXTS.items():
        if any(sfx.endswith(e) for e in exts):
            return fmt
    last = path.suffix.lower()
    if last in (".csv", ".parquet", ".feather", ".json"):
        return last.lstrip(".")
    raise ValueError(f"Unknown BDF artifact format: {path.name}")


def _detect_compression(path: Path) -> str | None:
    """Return the compression codec implied by ``path``'s trailing extension.

    Args:
        path: File path whose string form is checked against :data:`_COMPRESS` suffixes.

    Returns:
        Compression codec name (e.g. "gzip"), or None if no known compression suffix matches.
    """
    s = str(path).lower()
    for ext, comp in _COMPRESS.items():
        if s.endswith(ext):
            return comp
    return None


def _meta_sidecar(path: Path) -> Path:
    """Return the metadata sidecar path for a BDF artifact path.

    Args:
        path: BDF artifact file path.

    Returns:
        Path with ``.metadata.json`` appended to the file name.
    """
    return path.with_name(path.name + ".metadata.json")


def _coalesce_into(target: pd.Series, incoming: pd.Series) -> pd.Series:
    """Fill nulls in ``target`` from ``incoming`` at matching positions.

    Args:
        target: Series whose non-null values take priority.
        incoming: Series supplying values for positions where ``target`` is null.

    Returns:
        Series with ``target``'s values kept and nulls filled from ``incoming``.
    """
    return target.where(target.notna(), incoming)


def _label_maps() -> tuple[dict[str, str], dict[str, str]]:
    """Build two maps:
      - pref_label -> machine label (notation), using non-deprecated canonical targets.
      - machine label (notation) -> human pref_label, using non-deprecated canonical targets.

    Returns:
        Tuple of (pref_to_machine, machine_to_pref) label-mapping dicts.
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
    """Rewrite column labels between human pref_label and machine notation form.

    Args:
        df: BDF table whose columns carry either pref_label or notation labels.
        human: Convert to human pref_labels when True; convert to machine notation when False.

    Returns:
        New DataFrame with columns renamed (or coalesced into an existing target column).
    """
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


_LEGACY_SLUG = re.compile(r"[^a-z0-9]+")


def _legacy_slugify(s: str) -> str:
    """Lowercase ``s`` and collapse non-alphanumeric runs to a single hyphen.

    Args:
        s: String to slugify (e.g. a legacy column header).

    Returns:
        Slugified string with no leading/trailing hyphens.
    """
    return _LEGACY_SLUG.sub("-", s.lower()).strip("-")


def _legacy_is_numeric(s: pd.Series) -> bool:
    """Return True if ``s`` has a numeric dtype.

    Args:
        s: Series to check.

    Returns:
        True if the series dtype is numeric.
    """
    return pd.api.types.is_numeric_dtype(s)


def _legacy_coalesce(target: pd.Series, incoming: pd.Series) -> pd.Series:
    """Merge ``incoming`` into ``target``: prefer numeric typing, then fill holes.

    Args:
        target: Series whose non-null values take priority when both are non-numeric
            (or both numeric).
        incoming: Series to merge in; coerced to numeric and substituted wholesale
            when it is numeric and ``target`` is not.

    Returns:
        Merged series: ``incoming`` outright if it is numeric and ``target`` isn't,
        otherwise ``target`` with nulls filled from ``incoming``.
    """
    tnum, inum = _legacy_is_numeric(target), _legacy_is_numeric(incoming)
    if inum and not tnum:
        with contextlib.suppress(Exception):
            incoming = pd.to_numeric(incoming, errors="coerce")
        return incoming
    return target.where(target.notna(), incoming)


def canonicalize_legacy_labels(
    df: pd.DataFrame,
    *,
    keep_unmapped: bool = True,
) -> tuple[pd.DataFrame, list[str]]:
    """Rename deprecated on-disk BDF labels to current preferred labels.

    Turns legacy ontology labels (skos:altLabel / notation) into the preferred
    labels, converting units where the deprecated quantity used a different one.
    Distinct from the spec-driven vendor normalizer in :mod:`bdf.table_normalizers`,
    which maps raw vendor headers; this operates on already-BDF artifacts.

    Args:
        df: BDF table whose columns may carry deprecated labels.
        keep_unmapped: When False, drop columns that are not canonical BDF labels.

    Returns:
        Tuple of (new_df, legacy_headers_used).
    """
    out = df.copy()
    legacy_headers: list[str] = []

    # Preferred non-deprecated base name -> mr_name.
    base_preferred: dict[str, str] = {}
    for q, s in spec.COLUMN_ONTOLOGY:
        if s.deprecated:
            continue
        base = s.formatted_label.split(" / ", 1)[0].strip().lower()
        base_preferred.setdefault(base, q)

    # pref_label / notation -> (target_canon, target_unit, source_unit, is_legacy).
    # source_unit is non-empty only when a deprecated unit differs from the target.
    notation_to_canon: dict[str, tuple[str, str | None, str, bool]] = {}
    pref_to_canon: dict[str, tuple[str, str | None, str, bool]] = {}
    for q, s in spec.COLUMN_ONTOLOGY:
        pref = s.formatted_label
        notation = s.effective_notation
        target_q = q
        is_deprecated = s.deprecated
        if s.deprecated:
            base = pref.split(" / ", 1)[0].strip().lower()
            target_q = base_preferred.get(base, q)
        target_canon = spec.COLUMN_ONTOLOGY[target_q].formatted_label
        target_unit = spec.COLUMN_ONTOLOGY[target_q].unit
        src_unit = s.unit if is_deprecated and s.unit != target_unit else ""
        notation_to_canon[notation] = (target_canon, target_unit, src_unit, is_deprecated)
        pref_to_canon[pref] = (target_canon, target_unit, src_unit, is_deprecated)

    synonym_idx = spec.COLUMN_ONTOLOGY.base_synonym_index()

    def _apply(col: str, canon: str, target_unit: str | None, src_unit: str, is_legacy: bool) -> None:
        if is_legacy and canon != col:
            legacy_headers.append(col)
        if src_unit and src_unit != target_unit and _legacy_is_numeric(out[col]):
            conv = spec.get_unit_conversion(src_unit, target_unit)
            if conv:
                scale, offset = conv
                out[col] = pd.to_numeric(out[col], errors="coerce") * scale + offset
        if canon in out.columns and col != canon:
            out[canon] = _legacy_coalesce(out[canon], out[col])
            out.drop(columns=[col], inplace=True)
        elif canon != col:
            out.rename(columns={col: canon}, inplace=True)

    for col in list(out.columns):
        pref_hit = pref_to_canon.get(str(col))
        if pref_hit:
            _apply(col, *pref_hit)
            continue

        notation_hit = notation_to_canon.get(str(col))
        if notation_hit:
            _apply(col, *notation_hit)
            continue

        # Synonym fallback: altLabel / hiddenLabel slugs (e.g. "cycle_dimensionless").
        col_slug = _legacy_slugify(str(col))
        mr = synonym_idx.get(col_slug)
        if mr:
            qty = spec.COLUMN_ONTOLOGY[mr]
            _apply(col, qty.formatted_label, qty.unit, "", True)

    if keep_unmapped:
        return out, legacy_headers
    canonical_all = set(spec.COLUMN_ONTOLOGY.required_labels()) | set(spec.COLUMN_ONTOLOGY.optional_labels())
    out = out[[c for c in out.columns if c in canonical_all]].copy()
    return out, legacy_headers


def load(pathlike) -> pd.DataFrame:
    """Load a BDF artifact (CSV/parquet/feather/JSON) to a pandas DataFrame with human labels.

    Detects format and compression from the file extension. Legacy on-disk column
    labels are canonicalized to current preferred labels (with a warning) before
    the DataFrame is returned with human pref_labels.

    Args:
        pathlike: Local file path to a BDF artifact.

    Returns:
        Pandas DataFrame with human-readable canonical BDF column labels.

    Raises:
        FileNotFoundError: If ``pathlike`` does not exist.
        ValueError: If the format is unsupported, or parsing fails for any reason
            (re-raised with a short, path-sanitized message).
    """
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
    """Save a BDF table (pandas DataFrame) to a CSV/parquet/feather/JSON artifact.

    Detects format and compression from the file extension and creates parent
    directories as needed. Legacy column labels are canonicalized before saving.

    Args:
        df: BDF table to write.
        pathlike: Output file path; format/compression are inferred from its extension.
        metadata: Optional metadata dict written alongside as a ``.metadata.json`` sidecar.
        index: Write the DataFrame index as a column when True.
        human: Write human pref_labels when True; write machine notation labels when
            False (default).
        **opts: Additional keyword arguments forwarded to the pandas writer
            (``to_csv``/``to_parquet``/``to_feather``/``to_json``).

    Raises:
        ValueError: If the format is unsupported.
    """
    p = Path(pathlike)
    p.parent.mkdir(parents=True, exist_ok=True)
    fmt = _detect_format(p)
    comp = _detect_compression(p)

    with contextlib.suppress(Exception):
        df, _legacy = canonicalize_legacy_labels(df)
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
