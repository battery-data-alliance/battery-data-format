from __future__ import annotations
from pathlib import Path
from typing import List, Optional
import typer
from rich import print
from . import read_raw_to_bdf  # lazy import inside functions is also fine
from .io import load as load_bdf
from .visualize import line_plot
from .validate import validate_path, BDFValidationError


app = typer.Typer(help="Battery Data Format utilities")

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