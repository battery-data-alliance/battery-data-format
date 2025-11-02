from __future__ import annotations
from pathlib import Path
from typing import List, Optional
import typer
from rich import print
from . import read_raw_to_bdf  # lazy import inside functions is also fine
from .visualize import line_plot
from .validate import validate_path, BDFValidationError
from .clean import clean_bdf
from .io import load as load_bdf, save_csv
from .metadata import BDFMetadata, Creator, RelatedIdentifier, save_jsonld


app = typer.Typer(help="Battery Data Format utilities")


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

    meta = BDFMetadata(
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
            df = load_bdf(data)
        except Exception:
            df = None  # okay: JSON-LD will omit per-column metadata

    out_path = save_jsonld(meta, data, out_path=out, df=df, csvw_schema_url=schema_url)
    typer.echo(f"Wrote metadata: {out_path}")


@app.command()
def clean(
    path: str,
    out: str = typer.Option(..., "--out", "-o", help="Where to write cleaned BDF CSV"),
    as_: str | None = typer.Option(None, "--as", help="Force plugin id for raw input"),
    assume_bdf: bool = typer.Option(False, help="Treat input as already-normalized BDF"),
    time_fix: str = typer.Option("segment", help="segment|sort|drop|none"),
    outlier: str = typer.Option("none", help="none|drop|clip|interp"),
    z: float = typer.Option(8.0, help="Robust z threshold for outliers"),
    col: list[str] = typer.Option(["Voltage / V", "Current / A"], help="Columns to clean for outliers"),
):
    """
    Clean a dataset by fixing non-monotonic time and removing/repairing outliers.
    Accepts either BDF CSV/Parquet or a raw vendor file.
    """
    # Load BDF or raw
    if assume_bdf:
        df = load_bdf(path)
    else:
        try:
            df = load_bdf(path)
        except Exception:
            df = read_raw_to_bdf(path, as_=as_)

    df2, rep = clean_bdf(df, time_fix=time_fix, outlier=outlier, z_thresh=z, columns=col)
    save_csv(df2, out)
    typer.echo(str(rep))
    typer.echo(f"Saved: {out}")


@app.command()
def validate(path: str, strict: bool = typer.Option(False, help="Raise error (non-zero exit) on invalid BDF"),
             json: bool = typer.Option(False, help="Output machine-readable JSON report")):
    """
    Validate a CSV/Parquet file against the BDF schema and basic sanity checks.
    """
    try:
        report = validate_path(path, strict=strict)
    except BDFValidationError as e:
        if json:
            import json as _json
            print(_json.dumps({"ok": False, "errors": str(e).splitlines(), "warnings": []}, indent=2))
        else:
            print(f"[bdf] INVALID\n{e}")
        raise typer.Exit(code=1)
    except Exception as e:
        print(f"[bdf] Error reading file: {e}")
        raise typer.Exit(code=2)

    if json:
        import json as _json
        print(_json.dumps({"ok": report.ok, "errors": report.errors, "warnings": report.warnings}, indent=2))
    else:
        status = "OK" if report.ok else "INVALID"
        print(f"[bdf] {status}\n{report}")
    raise typer.Exit(code=0 if report.ok else 1)

@app.command()
def detect(path: str):
    from . import detect_cycler
    sr = detect_cycler(path)
    print(f"{sr.id} ({sr.confidence:.2f}) — {sr.reason}")

@app.command()
def convert(path: str, to: str = "bdf.csv", as_: str = None):
    from . import read_raw_to_bdf
    df = read_raw_to_bdf(path, as_=as_)
    df.to_csv(to, index=False)
    print(f"[bdf] wrote {to}")



@app.command()
def plot(
    path: str,
    xdata: str = typer.Option("Test Time / s", help="BDF column for x-axis"),
    ydata: List[str] = typer.Option(["Voltage / V"], help="One or more BDF columns for y-axis"),
    save: Optional[str] = typer.Option(None, "--save", "-s", help="Save figure to file"),
    show: bool = typer.Option(False, "--show/--no-show", help="Display window"),
    as_: Optional[str] = typer.Option(None, "--as", help="Force a specific plugin id (e.g., biologic-mpt)"),
    assume_bdf: bool = typer.Option(False, help="Assume input is already BDF (skip detection/normalization)")
):
    """
    Plot a BDF-normalized dataset. If the file isn't already BDF, auto-detect and convert on the fly.
    """
    p = Path(path)
    if not p.exists():
        raise typer.BadParameter(f"File not found: {p}")

    df = None
    if assume_bdf:
        df = load_bdf(p)
    else:
        # First try BDF; if it errors, fall back to raw->BDF
        try:
            df = load_bdf(p)
        except Exception:
            df = read_raw_to_bdf(p, as_=as_)

    # Draw the plot
    fig = line_plot(df, xdata=xdata, ydata=ydata, save=save, show=show, title=f"{', '.join(ydata)} vs {xdata}")
    print(f"[bdf] plotted {', '.join(ydata)} vs {xdata}" + (f" → {save}" if save else ""))