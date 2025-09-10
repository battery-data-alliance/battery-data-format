# src/bdf/visualize.py
from __future__ import annotations
from typing import Iterable, Optional, Union, Dict, Tuple
import re
import pandas as pd
import matplotlib.pyplot as plt

X_DEFAULT = "Test Time / s"
Y_DEFAULT = "Voltage / V"

# ---------- helpers ----------

def _ensure_numeric(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        raise KeyError(f"Column not found: {col}")
    s = pd.to_numeric(df[col], errors="coerce")
    if s.isna().all():
        raise ValueError(f"Column {col!r} is not numeric.")
    return s

_UNIT_PAT = re.compile(r"/\s*([A-Za-zµμ]+)\s*$")

def _infer_unit_from_colname(col: str) -> Optional[str]:
    m = _UNIT_PAT.search(col)
    if not m:
        return None
    u = m.group(1)
    if u in {"°C", "celsius"}:
        return "degC"
    if u in {"uA", "µA", "μA"}:
        return "uA"
    return u

def _norm_unit(u: Optional[str]) -> Optional[str]:
    if u is None:
        return None
    u = u.strip()
    aliases = {
        "sec": "s", "secs": "s", "second": "s", "seconds": "s",
        "minute": "min", "minutes": "min",
        "hour": "h", "hrs": "h",
        "mv": "mV", "v": "V",
        "ma": "mA", "a": "A", "ua": "uA", "µa": "uA", "μa": "uA",
        "degc": "degC", "k": "K",
        "wh": "Wh", "mwh": "mWh",
        "ah": "Ah", "mah": "mAh",
    }
    return aliases.get(u.lower(), u)

def _convert_series(s: pd.Series, from_unit: Optional[str], to_unit: Optional[str]) -> Tuple[pd.Series, Optional[str]]:
    from_unit = _norm_unit(from_unit)
    to_unit = _norm_unit(to_unit)
    if not to_unit or not from_unit or from_unit == to_unit:
        return s, from_unit or to_unit

    lin_scales = {
        ("s", "min"): (1/60.0, 0.0), ("s", "h"): (1/3600.0, 0.0),
        ("min", "s"): (60.0, 0.0), ("h", "s"): (3600.0, 0.0),
        ("min", "h"): (1/60.0, 0.0), ("h", "min"): (60.0, 0.0),
        ("V", "mV"): (1000.0, 0.0), ("mV", "V"): (1/1000.0, 0.0),
        ("A", "mA"): (1000.0, 0.0), ("mA", "A"): (1/1000.0, 0.0),
        ("A", "uA"): (1e6, 0.0), ("uA", "A"): (1e-6, 0.0),
        ("mA", "uA"): (1000.0, 0.0), ("uA", "mA"): (1/1000.0, 0.0),
        ("Ah", "mAh"): (1000.0, 0.0), ("mAh", "Ah"): (1/1000.0, 0.0),
        ("Wh", "mWh"): (1000.0, 0.0), ("mWh", "Wh"): (1/1000.0, 0.0),
    }
    if (from_unit, to_unit) == ("degC", "K"):
        return s.astype(float) + 273.15, "K"
    if (from_unit, to_unit) == ("K", "degC"):
        return s.astype(float) - 273.15, "degC"

    key = (from_unit, to_unit)
    if key in lin_scales:
        a, b = lin_scales[key]
        return s.astype(float) * a + b, to_unit

    return s, from_unit

def _to_list(val: Union[str, Iterable[str], None]) -> list[str]:
    if val is None:
        return []
    return [val] if isinstance(val, str) else list(val)

def _unit_for_each(cols: list[str], unit: Optional[Union[str, Dict[str, str]]]) -> Dict[str, Optional[str]]:
    if unit is None or isinstance(unit, str):
        return {c: unit for c in cols}
    return {c: unit.get(c) for c in cols}

def _apply_bdf_style(ax, ax2=None, *, title=None, primary_color="#1f77b4", secondary_color="#4d4d4d"):
    # Title
    if title:
        ax.set_title(title, fontsize=22, weight="bold", pad=10)

    # Grid & ticks
    ax.set_axisbelow(True)
    ax.minorticks_on()
    ax.grid(True, which="major", linestyle="--", linewidth=0.8, alpha=0.5)
    ax.grid(True, which="minor", linestyle=":", linewidth=0.5, alpha=0.3)

    # Spines & ticks
    for spine in ax.spines.values():
        spine.set_linewidth(1.5)
    ax.tick_params(axis="both", labelsize=13, width=1.2)

    if ax2 is not None:
        ax2.minorticks_on()
        for spine in ax2.spines.values():
            spine.set_linewidth(1.5)
        ax2.tick_params(axis="y", labelsize=13, width=1.2, colors=secondary_color)
        ax2.spines["right"].set_color(secondary_color)

# ---------- main API ----------

def plot(
    df: pd.DataFrame,
    *,
    xdata: str = X_DEFAULT,
    ydata: Union[str, Iterable[str]] = Y_DEFAULT,
    yydata: Optional[Union[str, Iterable[str]]] = None,  # secondary y-axis
    # unit overrides
    xunit: Optional[str] = None,
    yunit: Optional[Union[str, Dict[str, str]]] = None,
    yyunit: Optional[Union[str, Dict[str, str]]] = None,
    title: Optional[str] = None,
    save: Optional[str] = None,
    show: bool = False,
):
    """
    Publication-style BDF plot:
      - Thick, clean lines; dashed major/minor grid
      - Secondary axis via yydata
      - Unit conversion via xunit/yunit/yyunit (e.g., 'h', 'mA', 'K', 'mAh')
      - Primary axis data is always drawn on top of secondary axis data.
    """
    ys = _to_list(ydata)
    yys = _to_list(yydata)
    if not ys and not yys:
        raise ValueError("Provide at least one series in ydata or yydata.")

    # Colors & line widths
    primary_color = "#1f77b4"   # blue
    secondary_color = "#4d4d4d" # dark grey
    lw_primary = 2.8
    lw_secondary = 3.2

    # Layering controls (lines)
    z_primary_line = 4.0
    z_secondary_line = 2.0

    # X & label
    x_raw = _ensure_numeric(df, xdata)
    x_from = _infer_unit_from_colname(xdata)
    x_conv, _ = _convert_series(x_raw, x_from, xunit)
    x_label = f"{xdata}" if not xunit else f"{xdata.split('/')[0].strip()} / {_norm_unit(xunit)}"

    # Create axes
    fig, ax = plt.subplots()

    # Create secondary axis if needed and put it BEHIND primary axes
    ax2 = None
    if yys:
        ax2 = ax.twinx()
        ax2.set_zorder(2)              # behind the primary axes
        ax2.patch.set_alpha(0.0)       # transparent so it won't cover primary lines

    # Ensure primary axes is ON TOP and also transparent (so it won't cover twin)
    ax.set_zorder(3)
    ax.patch.set_alpha(0.0)

    # --- Plot SECONDARY (right) first with lower zorder ---
    if ax2 and yys:
        yy_units_map = _unit_for_each(yys, yyunit)
        yy_labels = []
        for j, y in enumerate(yys):
            y_raw = _ensure_numeric(df, y)
            y_from = _infer_unit_from_colname(y)
            y_conv, y_eff = _convert_series(y_raw, y_from, yy_units_map.get(y))
            label = y if (y_eff is None or y_from == y_eff) else f"{y.split('/')[0].strip()} / {y_eff}"
            color = secondary_color if j == 0 else None
            ax2.plot(
                x_conv, y_conv,
                label=label,
                color=color,
                linewidth=lw_secondary,
                linestyle="-",
                solid_capstyle="round",
                zorder=z_secondary_line,
            )
            yy_labels.append(label)
    else:
        yy_labels = []

    # --- Plot PRIMARY (left) after with higher zorder ---
    y_units_map = _unit_for_each(ys, yunit)
    y_labels = []
    for i, y in enumerate(ys):
        y_raw = _ensure_numeric(df, y)
        y_from = _infer_unit_from_colname(y)
        y_conv, y_eff = _convert_series(y_raw, y_from, y_units_map.get(y))
        label = y if (y_eff is None or y_from == y_eff) else f"{y.split('/')[0].strip()} / {y_eff}"
        color = primary_color if i == 0 else None
        ax.plot(
            x_conv, y_conv,
            label=label,
            color=color,
            linewidth=lw_primary,
            solid_capstyle="round",
            zorder=z_primary_line,
        )
        y_labels.append(label)

    # Labels
    ax.set_xlabel(x_label, fontsize=18)
    if ys:
        left_label = y_labels[0] if len(y_labels) == 1 else " / ".join(y_labels)
        if len(ys) == 1:
            ax.set_ylabel(left_label, fontsize=18, color=primary_color)
            ax.tick_params(axis="y", colors=primary_color)
        else:
            ax.set_ylabel(left_label, fontsize=18)

    if ax2 and yys:
        right_label = yy_labels[0] if len(yy_labels) == 1 else " / ".join(yy_labels)
        # If a single shared yyunit was provided, reflect it in label
        if isinstance(yyunit, str):
            right_label = f"{right_label.split('/')[0].strip()} / {_norm_unit(yyunit)}"
        ax2.set_ylabel(right_label, fontsize=18, color=secondary_color)
        ax2.tick_params(axis="y", colors=secondary_color)

    # Style & title
    _apply_bdf_style(ax, ax2=ax2, title=title, primary_color=primary_color, secondary_color=secondary_color)

    # Legend (merge both axes)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = (ax2.get_legend_handles_labels() if ax2 else ([], []))
    if h1 or h2:
        leg = ax.legend(h1 + h2, l1 + l2, loc="upper left", frameon=True)
        leg.get_frame().set_facecolor("white")
        leg.get_frame().set_edgecolor("#333333")
        leg.get_frame().set_linewidth(1.2)

    fig.tight_layout()
    if save:
        fig.savefig(save, bbox_inches="tight", dpi=150)
    if show:
        plt.show()

    return fig

__all__ = ["plot", "X_DEFAULT", "Y_DEFAULT"]
