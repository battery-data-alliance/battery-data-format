# src/bdf/visualize.py
from __future__ import annotations

from collections.abc import Iterable
from typing import Dict, Optional, Tuple, Union

import matplotlib.pyplot as plt
import pandas as pd

from bdf import spec

X_DEFAULT = "Test Time / s"
Y_DEFAULT = "Voltage / V"

# ---------- helpers ----------


def _ensure_numeric(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        raise KeyError(f"Column not found: {col}")
    # Let convert() handle dtype; here just ensure the column exists.
    return df[col]


def _to_list(val: Union[str, Iterable[str], None]) -> list[str]:
    if val is None:
        return []
    return [val] if isinstance(val, str) else list(val)


def _unit_for_each(cols: list[str], unit: Optional[Union[str, Dict[str, str]]]) -> Dict[str, Optional[str]]:
    if unit is None or isinstance(unit, str):
        return {c: unit for c in cols}
    return {c: unit.get(c) for c in cols}


def _left_of_label(label: str) -> str:
    # Works with canonical "Name / UNIT" labels and arbitrary strings.
    return label.split("/", 1)[0].strip()


def _effective_unit_for_series(s: pd.Series) -> Optional[str]:
    # Resolve from the column name, which is canonical ("Name / UNIT") in BDF.
    return spec.unit_from_label(str(s.name))


def _convert_for_plot(s: pd.Series, target_unit: Optional[str]) -> Tuple[pd.Series, Optional[str], Optional[str]]:
    """
    Convert series to target_unit if provided.
    Returns (converted_series, from_unit, to_unit_effective).
    """
    from_u = _effective_unit_for_series(s)
    num = pd.to_numeric(s, errors="coerce")
    if target_unit and from_u:
        conv = spec.get_unit_conversion(from_u, target_unit)
        if conv:
            scale, offset = conv
            return num * scale + offset, from_u, target_unit
    return num, from_u, from_u


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
      - Unit conversion via xunit/yunit/yyunit (spec.get_unit_conversion)
      - Primary axis data is always drawn on top of secondary axis data.
    """
    ys = _to_list(ydata)
    yys = _to_list(yydata)
    if not ys and not yys:
        raise ValueError("Provide at least one series in ydata or yydata.")

    # Colors & line widths
    primary_color = "#1f77b4"  # blue
    secondary_color = "#4d4d4d"  # dark grey
    lw_primary = 2.8
    lw_secondary = 3.2

    # Layering controls (lines)
    z_primary_line = 4.0
    z_secondary_line = 2.0

    # ----- X data -----
    x_raw = _ensure_numeric(df, xdata)
    x_conv, x_from, x_to = _convert_for_plot(x_raw, xunit)
    # prefer shown unit as the target if provided, else resolved source
    x_unit_label = x_to or x_from
    x_left = _left_of_label(xdata)
    x_label = f"{x_left}" if not x_unit_label else f"{x_left} / {x_unit_label}"

    # Create axes
    fig, ax = plt.subplots()

    # Secondary axis (behind)
    ax2 = None
    if yys:
        ax2 = ax.twinx()
        ax2.set_zorder(2)
        ax2.patch.set_alpha(0.0)

    # Ensure primary axes is on top
    ax.set_zorder(3)
    ax.patch.set_alpha(0.0)

    # --- Plot SECONDARY (right) first ---
    yy_labels: list[str] = []
    if ax2 and yys:
        yy_units_map = _unit_for_each(yys, yyunit)
        for j, y in enumerate(yys):
            y_raw = _ensure_numeric(df, y)
            y_conv, y_from, y_to = _convert_for_plot(y_raw, yy_units_map.get(y))
            y_unit_label = y_to or y_from
            y_left = _left_of_label(y)
            label = y_left if not y_unit_label else f"{y_left} / {y_unit_label}"
            color = secondary_color if j == 0 else None
            ax2.plot(
                x_conv,
                y_conv,
                label=label,
                color=color,
                linewidth=lw_secondary,
                linestyle="-",
                solid_capstyle="round",
                zorder=z_secondary_line,
            )
            yy_labels.append(label)

    # --- Plot PRIMARY (left) after ---
    y_units_map = _unit_for_each(ys, yunit)
    y_labels: list[str] = []
    for i, y in enumerate(ys):
        y_raw = _ensure_numeric(df, y)
        y_conv, y_from, y_to = _convert_for_plot(y_raw, y_units_map.get(y))
        y_unit_label = y_to or y_from
        y_left = _left_of_label(y)
        label = y_left if not y_unit_label else f"{y_left} / {y_unit_label}"
        color = primary_color if i == 0 else None
        ax.plot(
            x_conv,
            y_conv,
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
        # If a single shared yyunit was provided, reflect it in label explicitly
        if isinstance(yyunit, str):
            right_left = _left_of_label(right_label)
            right_label = f"{right_left} / {yyunit}"
        ax2.set_ylabel(right_label, fontsize=18, color=secondary_color)
        ax2.tick_params(axis="y", colors=secondary_color)

    # Style & title
    _apply_bdf_style(ax, ax2=ax2, title=title, primary_color=primary_color, secondary_color=secondary_color)

    # Legend (merge both axes)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels() if ax2 else ([], [])
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
        return None

    plt.close(fig)
    return fig


__all__ = ["plot", "X_DEFAULT", "Y_DEFAULT"]
