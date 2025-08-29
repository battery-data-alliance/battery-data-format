from __future__ import annotations
from typing import Iterable, Optional, Union
import pandas as pd
import matplotlib.pyplot as plt

X_DEFAULT = "Test Time / s"
Y_DEFAULT = "Voltage / V"

def _ensure_numeric(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        raise KeyError(f"Column not found: {col}")
    s = pd.to_numeric(df[col], errors="coerce")
    if s.isna().all():
        raise ValueError(f"Column {col!r} is not numeric.")
    return s

def line_plot(
    df: pd.DataFrame,
    *,
    xdata: str = X_DEFAULT,
    ydata: Union[str, Iterable[str]] = Y_DEFAULT,
    title: Optional[str] = None,
    grid: bool = True,
    save: Optional[str] = None,
    show: bool = False,
):
    """
    Plot one or multiple y columns against an x column from a BDF-normalized DataFrame.

    Parameters
    ----------
    xdata : BDF column name for x (default 'Test Time / s')
    ydata : one or more BDF column names for y (default 'Voltage / V')
    save  : optional path to save (png/pdf/svg)
    show  : display the window (False by default for CI/headless)
    """
    # Normalize ydata to a list
    ys = [ydata] if isinstance(ydata, str) else list(ydata)
    if not ys:
        raise ValueError("ydata must contain at least one column name.")

    x = _ensure_numeric(df, xdata)

    fig, ax = plt.subplots()
    for y in ys:
        yv = _ensure_numeric(df, y)
        ax.plot(x, yv, label=y)

    ax.set_xlabel(xdata)
    ax.set_ylabel(", ".join(ys) if len(ys) == 1 else " / ".join(ys))
    if title:
        ax.set_title(title)
    if grid:
        ax.grid(True, alpha=0.3)
    if len(ys) > 1:
        ax.legend()

    fig.tight_layout()
    if save:
        fig.savefig(save, bbox_inches="tight", dpi=150)
    if show:
        plt.show()

    return fig
