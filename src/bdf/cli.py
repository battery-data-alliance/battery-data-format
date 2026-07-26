from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
from rich import print

from . import (
    BDFValidationError,
    detect as detect_source,
    ingest as ingest_bdf,
    templates as templates_api,
    validate as validate_any,
)
from .io import read, save
from .metadata import Creator, Dataset, RelatedIdentifier, save_jsonld
from .repair import clean as clean_bdf
from .visualize import plot as line_plot

app = typer.Typer(help="Battery Data Format utilities")


def _version_callback(value: bool) -> None:
    if value:
        from . import __version__
        from .spec import COLUMN_ONTOLOGY

        typer.echo(f"bdf {__version__} (ontology snapshot {COLUMN_ONTOLOGY.ontology_version})")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the package version and bundled ontology snapshot version.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Battery Data Format utilities."""


@app.command()
def ingest(
    source: str = typer.Argument(".", help="Path, URL, or directory to ingest"),
    out_dir: Optional[str] = typer.Option(None, help="Output root for converted files"),
    format: str = typer.Option("parquet", help="Output format: parquet or csv"),
    layout: str = typer.Option("flat", help="Output layout: flat or nested"),
    battery_metadata: str = typer.Option("embedded", help="Battery metadata mode: embedded or separate"),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive", help="Recurse into subdirectories"),
    validate_existing: bool = typer.Option(
        True, "--validate-existing/--no-validate-existing", help="Validate existing BDF files"
    ),
    validate_converted: bool = typer.Option(
        True, "--validate-converted/--no-validate-converted", help="Validate newly converted files"
    ),
    include_unknown: bool = typer.Option(
        False, "--include-unknown/--exclude-unknown", help="Include columns outside of the BDF spec"
    ),
    plugin: Optional[str] = typer.Option(None, help="Force a specific plugin id for raw files"),
    incremental: bool = typer.Option(True, "--incremental/--no-incremental", help="Skip unchanged files"),
    force: bool = typer.Option(False, help="Reprocess files even if unchanged"),
    raise_on_error: bool = typer.Option(False, help="Raise error on first failure"),
    discover_collections: bool = typer.Option(False, help="Ingest each folder with contribution.json"),
    refresh: bool = typer.Option(False, help="Refresh cached remote sources"),
    cache_dir: Optional[str] = typer.Option(None, help="Cache directory for remote sources"),
    data_dir: Optional[str] = typer.Option("timeseries", help="Output subdir for converted files"),
    raw_dir: Optional[str] = typer.Option("timeseries/raw", help="Input subdir for raw files"),
    cell_metadata_dir: Optional[str] = typer.Option("batteries", help="Base dir for per-cell metadata folders"),
    doi_enrich: bool = typer.Option(True, "--doi-enrich/--no-doi-enrich", help="Enrich missing metadata from DOI"),
    doi_timeout: int = typer.Option(15, help="Per-request timeout (seconds) for DOI lookups"),
    labels: str = typer.Option(
        "machine", "--labels", help="Column header style: 'preferred', 'machine', or 'unchanged'"
    ),
    human: Optional[bool] = typer.Option(
        None, "--human/--machine", help="Short for --label=preferred / --label=machine"
    ),
):
    """
    Convert raw vendor files to BDF and emit metadata sidecars.
    """
    if human is not None:
        labels = "preferred" if human else "machine"
    if labels not in ("preferred", "machine", "unchanged"):
        raise typer.BadParameter("--labels must be one of: preferred, machine, unchanged")
    summary = ingest_bdf(
        source,
        out_dir=out_dir,
        format=format,
        layout=layout,
        battery_metadata=battery_metadata,
        recursive=recursive,
        validate_existing=validate_existing,
        validate_converted=validate_converted,
        include_unknown=include_unknown,
        plugin=plugin,
        incremental=incremental,
        force=force,
        raise_on_error=raise_on_error,
        discover_collections=discover_collections,
        refresh=refresh,
        cache_dir=cache_dir,
        data_dir=data_dir,
        raw_dir=raw_dir,
        cell_metadata_dir=cell_metadata_dir,
        doi_enrich=doi_enrich,
        doi_timeout=doi_timeout,
        labels=labels,
    )
    print(summary)


@app.command("meta-jsonld")
def meta_jsonld(
    data: str = typer.Argument(..., help="Path to BDF CSV/Parquet"),
    out: Optional[str] = typer.Option(None, "--out", "-o", help="Metadata JSON-LD output path"),
    title: str = typer.Option(..., help="Dataset title"),
    description: str = typer.Option(..., help="Dataset description (markdown allowed)"),
    creator: List[str] = typer.Option(..., help="Creator spec(s): 'Name|ORCID?|Affiliation?'"),
    keyword: List[str] = typer.Option([], help="Keyword(s)"),
    license: str = typer.Option("CC-BY-4.0", help="License identifier"),
    access: str = typer.Option("open", help="Zenodo access_right"),
    version: Optional[str] = typer.Option(None, help="Version string"),
    pub_date: Optional[str] = typer.Option(None, help="Publication date YYYY-MM-DD"),
    doi: Optional[str] = typer.Option(None, help="DOI (optional)"),
    related: List[str] = typer.Option([], help="Related identifiers: 'relation|scheme|identifier'"),
    community: List[str] = typer.Option([], help="Zenodo community slugs"),
    schema_url: Optional[str] = typer.Option(None, help="CSVW schema URL (defaults to BDF_CSVW_SCHEMA_URL)"),
    infer_columns: bool = typer.Option(True, help="Infer CSVW columns from the data (recommended)"),
):
    """
    Build a JSON-LD sidecar that describes the dataset (schema.org) and the BDF table (CSVW),
    ready for Zenodo and linked to the BDF CSVW table schema.
    """
    # Parse creators
    creators: List[Creator] = []
    for spec in creator:
        parts = [p.strip() for p in spec.split("|")]
        name = parts[0]
        orcid = parts[1] if len(parts) > 1 and parts[1] else None
        aff = parts[2] if len(parts) > 2 and parts[2] else None
        creators.append(Creator(name=name, orcid=orcid, affiliation=aff))

    # Parse related identifiers
    rels: List[RelatedIdentifier] = []
    for r in related:
        parts = [p.strip() for p in r.split("|")]
        if len(parts) >= 3:
            rels.append(RelatedIdentifier(identifier=parts[2], relation=parts[0], scheme=parts[1]))
        elif len(parts) == 2:
            rels.append(RelatedIdentifier(identifier=parts[1], relation=parts[0]))
        else:
            raise typer.BadParameter("Use 'relation|scheme|identifier' for --related")

    meta = Dataset(
        title=title,
        creators=creators,
        description=description,
        keywords=keyword,
        license=license,
        access_right=access,
        version=version,
        publication_date=pub_date,
        doi=doi,
        communities=community,
        related_identifiers=rels,
    )

    df = None
    if infer_columns:
        try:
            df, _metadata = read(data)
        except Exception:
            df = None  # okay: JSON-LD will omit per-column metadata

    out_path = save_jsonld(meta, data, out_path=out, df=df, csvw_schema_url=schema_url)
    typer.echo(f"Wrote metadata: {out_path}")


@app.command()
def clean(
    path: str,
    out: str = typer.Option(..., "--out", "-o", help="Where to write cleaned BDF CSV"),
    as_: Optional[str] = typer.Option(None, "--as", help="Force plugin id for raw input"),
    time_fix: str = typer.Option("segment", help="segment|sort|drop|none"),
    outlier: str = typer.Option("none", help="none|drop|clip|interp"),
    z: float = typer.Option(8.0, help="Robust z threshold for outliers"),
    col: List[str] = typer.Option(["Voltage / V", "Current / A"], help="Columns to clean for outliers"),
):
    """
    Clean a dataset by fixing non-monotonic time and removing/repairing outliers.
    Accepts either BDF CSV/Parquet or a raw vendor file.
    """
    # Load BDF
    import polars as pl

    df_pl, _ = read(path, plugin=as_)
    df = df_pl.to_pandas()
    df, rep = clean_bdf(df, time_fix=time_fix, outlier=outlier, z_thresh=z, columns=col)
    df_pl = pl.from_pandas(df)
    save(df, out)
    typer.echo(str(rep))
    typer.echo(f"Saved: {out}")


@app.command()
def validate(
    path: str,
    strict: bool = typer.Option(False, help="Raise error (non-zero exit) on invalid BDF"),
    json: bool = typer.Option(False, help="Output machine-readable JSON report"),
):
    """
    Validate a CSV/Parquet file against the BDF schema and basic sanity checks.
    """
    try:
        report = validate_any(path, report=not json, raise_on_error=strict)
    except BDFValidationError as e:
        if json:
            import json as _json

            print(_json.dumps({"ok": False, "errors": str(e).splitlines(), "warnings": []}, indent=2))
        else:
            print(f"[bdf] INVALID\n{e}")
        raise typer.Exit(code=1) from None
    except Exception as e:
        print(f"[bdf] Error reading file: {e}")
        raise typer.Exit(code=2) from e

    ok = bool(report.get("ok"))
    if json:
        import json as _json

        print(_json.dumps(report, indent=2, default=str))
    else:
        status = "OK" if ok else "INVALID"
        missing = report.get("missing") or []
        extras = report.get("extras") or []
        print(f"[bdf] {status}")
        if missing:
            print("Missing required columns:")
            for c in missing:
                print(f"  - {c}")
        if extras:
            print("Non-canonical columns (ignored by BDF):")
            for c in extras:
                print(f"  - {c}")
    raise typer.Exit(code=0 if ok else 1)


@app.command()
def detect(path: str):
    plugin_id, _plugin = detect_source(path)
    print(plugin_id)


@app.command()
def templates(
    names: List[str] = typer.Argument(
        ..., help="Template names (contribution, battery, excel, data_download, mapping)"
    ),
    root: str = typer.Option(".", help="Target directory for template files"),
    overwrite: bool = typer.Option(False, help="Overwrite existing files"),
):
    """
    Create sidecar metadata templates with REQUIRED/OPTIONAL placeholders.
    """
    result = templates_api(*names, root=root, overwrite=overwrite)
    created = result.get("created") or []
    skipped = result.get("skipped") or []
    for p in created:
        print(f"[bdf] created {p}")
    for p in skipped:
        print(f"[bdf] skipped {p}")


@app.command()
def convert(
    path: str,
    to: str = "bdf.csv",
    as_: Optional[str] = typer.Option(None, "--as", help="Force a specific plugin id for raw input"),
    include_unknown: bool = typer.Option(
        False, "--include-unknown", help="Keep columns outside the BDF spec in the output"
    ),
    validate: bool = typer.Option(True, "--validate/--no-validate", help="Validate against the BDF schema"),
    labels: str = typer.Option(
        "machine", "--labels", help="Column header style: 'preferred', 'machine', or 'unchanged'"
    ),
    human: Optional[bool] = typer.Option(
        None, "--human/--machine", help="Short for --label=preferred / --label=machine"
    ),
):
    """
    Convert a raw vendor file or existing BDF artifact to another BDF artifact.
    """
    if human is not None:
        labels = "preferred" if human else "machine"
    if labels not in ("preferred", "machine", "unchanged"):
        raise typer.BadParameter("--labels must be one of: preferred, machine, unchanged")

    df, _ = read(path, plugin=as_, validate=validate, include_unknown=include_unknown)
    save(df, to, validate=validate, labels=labels)
    print(f"[bdf] wrote {to}")


@app.command()
def plot(
    path: str,
    xdata: str = typer.Option("Test Time / s", help="BDF column for x-axis"),
    ydata: List[str] = typer.Option(["Voltage / V"], help="One or more BDF columns for y-axis"),
    save: Optional[str] = typer.Option(None, "--save", "-s", help="Save figure to file"),
    show: bool = typer.Option(False, "--show/--no-show", help="Display window"),
    as_: Optional[str] = typer.Option(None, "--as", help="Force a specific plugin id (e.g., biologic_mpt)"),
):
    """
    Plot a BDF-normalized dataset. If the file isn't already BDF, auto-detect and convert on the fly.
    """
    p = Path(path)
    if not p.exists():
        raise typer.BadParameter(f"File not found: {p}")

    df, _metadata = read(p, plugin=as_)
    df = df.to_pandas()

    # Draw the plot
    line_plot(df, xdata=xdata, ydata=ydata, save=save, show=show, title=f"{', '.join(ydata)} vs {xdata}")
    print(f"[bdf] plotted {', '.join(ydata)} vs {xdata}" + (f" -> {save}" if save else ""))
