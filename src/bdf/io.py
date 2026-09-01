# src/bdf/io.py
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, cast

if TYPE_CHECKING:
    import pandas as pd
import polars as pl

from bdf._time_scale import detect_scale_mismatch
from bdf.file_utils import open_compressed, strip_compression_suffix
from bdf.metadata import Metadata
from bdf.metadata_parsers import BdfSidecarParser
from bdf.normalization import DayMonthOrder
from bdf.plugins import PLUGINS, Plugin, detect
from bdf.spec import COLUMN_ONTOLOGY


def _assemble_metadata(
    path: str | Path, resolved_plugin: Plugin, *, tz: str, day_month_order: DayMonthOrder | None = None
) -> Metadata:
    """Take this read's metadata from exactly one source, never combined.

    Args:
        path: Local file path or URL being read.
        resolved_plugin: The plugin whose ``metadata_parser`` runs when no
            reserved sidecar sits beside ``path``.
        tz: IANA timezone forwarded to the plugin's metadata parser.
        day_month_order: Field order forwarded to the plugin's metadata parser.

    Returns:
        The reserved sidecar's ``Metadata`` where ``<stem>.metadata.json``
        exists beside ``path``, restored repair records included; the
        plugin parser's ``Metadata`` otherwise. The caller still assigns
        ``bdf.source`` and applies this read's own repairs.

    Raises:
        BDFMetadataError: A sidecar exists and cannot be restored. An empty
            ``Metadata`` therefore always means that no sidecar exists, and
            never a sidecar this read failed on. A later ``save()`` of that
            metadata would otherwise write over the file the read could not
            read.
    """
    reserved = BdfSidecarParser()
    if reserved.matches(path):
        return reserved.parse(path)

    preamble_lines = None
    if resolved_plugin.metadata_parser.uses_preamble_boundary:
        preamble = resolved_plugin.table_parser.preamble(path)
        preamble_lines = None if preamble is None else len(preamble)
    return resolved_plugin.metadata_parser.parse(
        path, tz=tz, day_month_order=day_month_order, preamble_lines=preamble_lines
    )


def _read(
    path: str | Path,
    *,
    plugin: Plugin | str | None = None,
    normalize: bool = True,
    validate: bool = True,
    include_unknown: bool = False,
    lazy: bool = True,
    tz: str = "UTC",
    day_month_order: DayMonthOrder | None = None,
    reconcile_time: bool = False,
) -> tuple[pl.DataFrame | pl.LazyFrame, Metadata]:
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
        day_month_order=day_month_order,
    )

    metadata = _assemble_metadata(path, resolved_plugin, tz=tz, day_month_order=day_month_order)

    if normalize:
        bdf_df, repairs = _reconcile_time_scale(bdf_df, reconcile_time=reconcile_time, strict=validate)
        if repairs:
            metadata.bdf.time_reconciliation = repairs

    metadata.bdf.source = plugin_id or "custom"

    return bdf_df, metadata


# Rows sampled for the elapsed-vs-wall-clock scale estimate; a uniform unit
# error shows up in any contiguous slice, so bounding the sample keeps lazy
# reads cheap on large files.
_RECONCILE_SAMPLE_ROWS = 100_000


def _reconcile_time_scale(
    df: pl.DataFrame | pl.LazyFrame,
    *,
    reconcile_time: bool,
    strict: bool,
) -> tuple[pl.DataFrame | pl.LazyFrame, list[dict]]:
    """Detect elapsed-time columns stored in the wrong unit; repair only on request.

    Compares ``Test Time / s`` and ``Step Time / s`` increments against the
    independently recorded wall clock (``Unix Time / s``). Detection always
    runs; the fsck model applies to what happens on a mismatch:

    - ``reconcile_time=True`` and the ratio matches a known unit factor (see
      :data:`bdf._time_scale.KNOWN_SCALE_FACTORS`): the column is rescaled to
      seconds, the repair is recorded, and a ``UserWarning`` announces it.
    - otherwise, ``strict=True`` raises :class:`BDFValidationError`
      (loud failure, nothing modified) and ``strict=False`` downgrades to a
      ``UserWarning``.

    Args:
        df: Normalized BDF frame (eager or lazy).
        reconcile_time: Rescale columns whose mismatch matches a known unit factor.
        strict: Raise on unrepaired mismatches instead of warning.

    Returns:
        Tuple of (possibly rescaled frame, list of repair records). The list is
        empty when nothing was repaired.

    Raises:
        BDFValidationError: On an unrepaired mismatch when ``strict`` is True.
    """
    wall_label = COLUMN_ONTOLOGY.unix_time_second.formatted_label
    elapsed_labels = (
        COLUMN_ONTOLOGY.test_time_second.formatted_label,
        COLUMN_ONTOLOGY.step_time_second.formatted_label,
    )

    columns = df.collect_schema().names() if isinstance(df, pl.LazyFrame) else df.columns
    if wall_label not in columns:
        return df, []
    present = [lbl for lbl in elapsed_labels if lbl in columns]
    if not present:
        return df, []

    sample = df.select([wall_label, *present]).head(_RECONCILE_SAMPLE_ROWS)
    if isinstance(sample, pl.LazyFrame):
        sample = sample.collect()
    wall = sample[wall_label].cast(pl.Float64).to_numpy()

    records: list[dict] = []
    rescale: list[pl.Expr] = []
    problems: list[str] = []
    for label in present:
        mismatch = detect_scale_mismatch(sample[label].cast(pl.Float64).to_numpy(), wall)
        if mismatch is None:
            continue
        if mismatch.unit_name:
            described = (
                f"'{label}' values appear to be {mismatch.unit_name}, not the declared seconds "
                f"(increments disagree with '{wall_label}' by ~{mismatch.ratio:g}x)"
            )
        else:
            described = (
                f"'{label}' increments disagree with '{wall_label}' increments by "
                f"~{mismatch.ratio:g}x, which matches no known unit"
            )
        if reconcile_time and mismatch.factor is not None:
            rescale.append(pl.col(label) / mismatch.factor)
            records.append(
                {
                    "column": label,
                    "declared_unit": "s",
                    "actual_unit": mismatch.unit_name,
                    "ratio_vs_wall_clock": mismatch.ratio,
                    "n_samples": mismatch.n_samples,
                    "action": f"divided by {mismatch.factor:g}",
                }
            )
            warnings.warn(
                f"{described}; rescaled to seconds as requested (reconcile_time=True). "
                f"Recorded in metadata.bdf.time_reconciliation.",
                UserWarning,
                stacklevel=4,
            )
        else:
            problems.append(described)

    if problems:
        detail = "; ".join(problems)
        if strict:
            from ._errors import BDFValidationError

            raise BDFValidationError(
                f"Elapsed-time/wall-clock mismatch: {detail}. Pass reconcile_time=True to "
                f"rescale known unit factors, or validate=False to load the data as-is."
            )
        warnings.warn(f"Elapsed-time/wall-clock mismatch: {detail}.", UserWarning, stacklevel=4)

    if rescale:
        df = df.with_columns(rescale)
    return df, records


def read(
    path: str | Path,
    *,
    plugin: Plugin | str | None = None,
    normalize: bool = True,
    validate: bool = True,
    include_unknown: bool = False,
    tz: str = "UTC",
    day_month_order: DayMonthOrder | None = None,
    reconcile_time: bool = False,
) -> tuple[pl.DataFrame, Metadata]:
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
        day_month_order: Field order applied to an ambiguous numeric date the table
            column and the staged metadata each declare. ``"day_first"`` reads it day
            then month, ``"month_first"`` reads it month then day; ``None`` (default)
            leaves every declared format unchanged.
        reconcile_time: Elapsed-time columns are cross-checked against wall-clock
            increments when both are present (e.g. a vendor export storing milliseconds
            under a seconds header, GH #65). A mismatch raises ``BDFValidationError`` by
            default (warns when ``validate=False``); pass ``reconcile_time=True`` to
            explicitly rescale known unit factors, recorded under
            ``metadata.bdf.time_reconciliation``. Only active when ``normalize=True``.

    Returns:
        Tuple of (df, metadata): the BDF table as a DataFrame, and a ``Metadata``
        carrying the five entity records, ``bdf`` (with at least ``source`` naming the
        resolved plugin id, ``"custom"`` for a directly-supplied ``Plugin``), ``raw``,
        and ``extras``.

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
        day_month_order=day_month_order,
        reconcile_time=reconcile_time,
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
    day_month_order: DayMonthOrder | None = None,
    reconcile_time: bool = False,
) -> tuple[pl.LazyFrame, Metadata]:
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
        day_month_order: Field order applied to an ambiguous numeric date the table
            column and the staged metadata each declare. ``"day_first"`` reads it day
            then month, ``"month_first"`` reads it month then day; ``None`` (default)
            leaves every declared format unchanged. Fixed when the expression is
            built: no row is read to choose it, and the choice is never deferred to
            ``collect()``.
        reconcile_time: Elapsed-time columns are cross-checked against wall-clock
            increments when both are present (e.g. a vendor export storing milliseconds
            under a seconds header, GH #65). A mismatch raises ``BDFValidationError`` by
            default (warns when ``validate=False``); pass ``reconcile_time=True`` to
            explicitly rescale known unit factors, recorded under
            ``metadata.bdf.time_reconciliation``. Only active when ``normalize=True``.

    Returns:
        Tuple of (df, metadata): the BDF table as a LazyFrame, and a ``Metadata``
        carrying the five entity records, ``bdf`` (with at least ``source`` naming the
        resolved plugin id, ``"custom"`` for a directly-supplied ``Plugin``), ``raw``,
        and ``extras``.

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
        day_month_order=day_month_order,
        reconcile_time=reconcile_time,
    )
    return cast(pl.LazyFrame, bdf_df), metadata


class _ArtifactFormat(NamedTuple):
    """How one BDF artifact format is recognized and written.

    Attributes:
        extensions: Suffixes that name the format, compression suffix removed.
        write: ``polars.DataFrame`` method that writes an eager frame.
        sink: ``polars.LazyFrame`` method that streams a lazy frame straight to
            the target, or None where polars has no sink for the format. A
            format with no sink collects the frame first.
        compressible: False where the writer needs a real file path, so a
            compressed target is an error.
    """

    extensions: tuple[str, ...]
    write: str
    sink: str | None
    compressible: bool = True


_FORMATS: dict[str, _ArtifactFormat] = {
    "csv": _ArtifactFormat((".csv", ".bdf.csv"), "write_csv", "sink_csv"),
    "parquet": _ArtifactFormat((".parquet", ".bdf.parquet", ".pq", ".bdf.pq"), "write_parquet", "sink_parquet"),
    "ipc": _ArtifactFormat(
        (".ipc", ".bdf.ipc", ".feather", ".bdf.feather", ".ftr", ".bdf.ftr", ".arrow", ".bdf.arrow"),
        "write_ipc",
        "sink_ipc",
    ),
    "json": _ArtifactFormat((".json", ".bdf.json"), "write_json", None),
    "ndjson": _ArtifactFormat((".ndjson", ".bdf.ndjson"), "write_ndjson", "sink_ndjson"),
    "xlsx": _ArtifactFormat((".xlsx", ".bdf.xlsx"), "write_excel", None, compressible=False),
}


def _detect_format(path: Path) -> str:
    """Return the BDF artifact format ("csv"/"parquet"/"ipc"/"json"/"ndjson"/"xlsx") for ``path``.

    Args:
        path: File path whose suffixes are inspected (e.g. ``.bdf.csv.gz``).

    Returns:
        Format name whose extensions in :data:`_FORMATS` match ``path``.

    Raises:
        ValueError: If no known format extension is found in ``path``.
    """
    sfx = "".join(Path(strip_compression_suffix(path.name)).suffixes).lower()
    for fmt, spec in _FORMATS.items():
        if any(sfx.endswith(e) for e in spec.extensions):
            return fmt
    raise ValueError(f"Unknown BDF artifact format: {path.name}")


def _as_polars(df: pl.DataFrame | pl.LazyFrame | pd.DataFrame) -> pl.DataFrame | pl.LazyFrame:
    """Return ``df`` as a polars frame, keeping a LazyFrame lazy.

    Args:
        df: Table to write, polars eager, polars lazy, or pandas.

    Returns:
        ``df`` unchanged where it is already a polars frame, a
        ``polars.DataFrame`` built from it otherwise.
    """
    if isinstance(df, (pl.DataFrame, pl.LazyFrame)):
        return df
    return pl.DataFrame(df)


def _version_stamps() -> dict[str, str | None]:
    """Return the writer-identity stamps a written sidecar carries (GH #106).

    Returns:
        ``bdf_version`` (the installed package version), ``ontology_version``
        (the pinned BDF ontology release), and ``battinfo_ref`` (the upstream
        commit the bundled BattINFO schemas were fetched at).
    """
    import bdf
    from bdf.battinfo import bundled_ref

    return {
        "bdf_version": bdf.__version__,
        "ontology_version": COLUMN_ONTOLOGY.ontology_version or None,
        "battinfo_ref": bundled_ref(),
    }


def _write_sidecar(sidecar: Path, metadata: Metadata) -> None:
    """Write ``metadata`` to ``sidecar``, or delete the sidecar where it carries nothing.

    A written sidecar is stamped with the writer's versions (GH #106):
    ``bdf.bdf_version``, ``bdf.ontology_version``, and ``bdf.battinfo_ref``.
    The stamps identify the file's writer, so they overwrite any stamps the
    caller's object carries, and a read-then-save records the version that
    performed the save. The caller's object is not modified. A ``Metadata``
    that carries nothing still writes no sidecar: the stamps describe a
    sidecar, so they never create one.

    Args:
        sidecar: Path of the ``.metadata.json`` file beside the artifact.
        metadata: Metadata to write. Only the values that differ from their
            defaults reach the file, so a ``Metadata`` that carries nothing
            writes no sidecar at all, and deletes one the target already had.
    """
    payload = metadata.model_dump(mode="json", exclude_defaults=True)
    if payload:
        payload.setdefault("bdf", {})
        payload["bdf"].update({k: v for k, v in _version_stamps().items() if v})
        sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        sidecar.unlink(missing_ok=True)


def save(
    df: pl.DataFrame | pl.LazyFrame | pd.DataFrame,
    pathlike: str | Path,
    *,
    metadata: Metadata | None = None,
    validate: bool = True,
    labels: Literal["preferred", "machine", "unchanged"] = "unchanged",
    **opts,
) -> None:
    """Save a BDF table to a CSV/parquet/IPC/JSON/ndjson/xlsx artifact.

    Detects format and compression from the file extension and creates parent
    directories as needed.

    A ``LazyFrame`` reaches the target through the polars ``sink_*`` writer of
    the format, so polars streams the table and does not materialize it first.
    JSON and xlsx have no sink, so a ``LazyFrame`` collects for those two
    formats.

    Args:
        df: BDF table to write.
        pathlike: Output file path; format/compression are inferred from its extension.
        metadata: Optional ``Metadata`` written alongside as a ``.metadata.json``
            sidecar (``mydata.bdf.parquet`` pairs with ``mydata.bdf.metadata.json``).
            A ``Metadata`` carrying nothing deletes the sidecar, so the artifact
            keeps no metadata. Omit the argument only where the target has no
            sidecar: a save that omits it beside an existing sidecar raises,
            because the sidecar describes the data the previous save wrote. The
            message states each out, one of which is a save to a different path.
        validate: Check columns against the BDF ontology, raising on missing required ones
            (default True); False only warns.
        labels: Style of column names to use (default: "unchanged"):
            "preferred": BDF preferred label, e.g. "Voltage / V"
            "machine": BDF machine-readable label e.g. "voltage_volt"
            "unchanged": Keep column names as-is
        **opts: Additional keyword arguments forwarded to the polars writer
            (``write_csv``/``write_parquet``/``write_ipc``/``write_json``/``write_ndjson``/
            ``write_excel``), or to the matching ``sink_*`` writer where ``df`` is a
            streamed ``LazyFrame``.

    Raises:
        ValueError: If the format is unsupported, or compression is requested for xlsx output.
        FileExistsError: If ``metadata`` is omitted and a ``.metadata.json`` sidecar
            already sits beside the target.
    """
    p = Path(pathlike)
    sidecar = BdfSidecarParser().sidecar_path(p)
    if metadata is None and sidecar.exists():
        msg = (
            f"{sidecar} describes the data a previous save wrote, and this save states no metadata. "
            "Pass metadata= to keep or update it, or metadata=Metadata() to clear it. "
            "To keep both the sidecar and the data it describes, save to a different path. "
            "To discard it, delete the sidecar."
        )
        raise FileExistsError(msg)

    fmt = _detect_format(p)
    spec = _FORMATS[fmt]
    compressed = strip_compression_suffix(p.name) != p.name
    if compressed and not spec.compressible:
        msg = f"Compression is not supported for {fmt} output"
        raise ValueError(msg)

    frame = _as_polars(df)
    COLUMN_ONTOLOGY.validate_df(frame, raise_on_error=validate)
    frame = COLUMN_ONTOLOGY.rename_labels(frame, labels)

    if isinstance(frame, pl.LazyFrame) and spec.sink is not None:
        writer = getattr(frame, spec.sink)
    else:
        if isinstance(frame, pl.LazyFrame):
            frame = frame.collect()
        writer = getattr(frame, spec.write)

    p.parent.mkdir(parents=True, exist_ok=True)
    target: Any = open_compressed(p)
    try:
        writer(target, **opts)
    finally:
        if not isinstance(target, Path):
            target.close()

    if metadata is not None:
        _write_sidecar(sidecar, metadata)
