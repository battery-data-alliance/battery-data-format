from __future__ import annotations

from collections.abc import Iterable
from typing import Optional

import pandas as pd

from . import spec


def _label_with_unit(name: str, unit: str) -> str:
    base = name.split(" / ", 1)[0].strip() if " / " in name else name
    return f"{base} / {unit}"


def _prepare_series(series: pd.Series, unit: Optional[str]) -> pd.Series:
    if not unit:
        return series
    src = spec.unit_from_label(str(series.name))
    if not src:
        return series
    conv = spec.get_unit_conversion(src, unit)
    if not conv:
        return series
    scale, offset = conv
    out = pd.to_numeric(series, errors="coerce") * scale + offset
    return out.rename(_label_with_unit(str(series.name), unit))


def _ensure_list(value: str | Iterable[str]) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _plot_bokeh(
    df: pd.DataFrame,
    *,
    xdata: str,
    ydata: str | Iterable[str],
    yydata: Optional[str | Iterable[str]],
    xunit: Optional[str],
    yunit: Optional[str],
    yyunit: Optional[str],
    kind: str,
    title: Optional[str],
    **kwargs,
):
    try:
        import holoviews as hv
        import hvplot  # type: ignore
        import hvplot.pandas  # noqa: F401
    except Exception as e:
        raise RuntimeError(
            "bdf.explore(..., backend='bokeh') requires hvplot. Install with `pip install batterydf[hvplot]`."
        ) from e

    hvplot.extension("bokeh")

    y_left = _ensure_list(ydata)
    x_series = _prepare_series(df[xdata], xunit)
    left_series = [_prepare_series(df[c], yunit) for c in y_left]
    df_left = pd.concat([x_series] + left_series, axis=1)
    left_labels = [s.name for s in left_series]

    plot_left = df_left.hvplot(
        x=x_series.name,
        y=left_labels,
        kind=kind,
        title=title,
        yaxis="left",
        **kwargs,
    ).opts(
        hv.opts.Curve(yaxis="left"),
        hv.opts.Scatter(yaxis="left"),
    )

    if yydata:
        y_right = _ensure_list(yydata)
        right_series = [_prepare_series(df[c], yyunit) for c in y_right]
        df_right = pd.concat([x_series] + right_series, axis=1)
        right_labels = [s.name for s in right_series]
        plot_right = df_right.hvplot(
            x=x_series.name,
            y=right_labels,
            kind=kind,
            yaxis="right",
            title=None,
            **kwargs,
        ).opts(
            hv.opts.Curve(yaxis="right"),
            hv.opts.Scatter(yaxis="right"),
        )
        overlay = plot_left * plot_right
        return overlay.opts(hv.opts.Overlay(multi_y=True))

    return plot_left


def _plot_plotly(
    df: pd.DataFrame,
    *,
    xdata: str,
    ydata: str | Iterable[str],
    yydata: Optional[str | Iterable[str]],
    xunit: Optional[str],
    yunit: Optional[str],
    yyunit: Optional[str],
    kind: str,
    title: Optional[str],
):
    try:
        import plotly.graph_objects as go  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "bdf.explore(..., backend='plotly') requires plotly. Install with `pip install batterydf[plotly]`."
        ) from e

    mode = "lines"
    if kind.lower() in {"scatter", "markers"}:
        mode = "markers"
    elif "marker" in kind.lower():
        mode = "lines+markers"

    y_left = _ensure_list(ydata)
    x_series = _prepare_series(df[xdata], xunit)
    left_series = [_prepare_series(df[c], yunit) for c in y_left]

    fig = go.Figure()
    for s in left_series:
        fig.add_trace(
            go.Scatter(
                x=x_series.to_numpy(),
                y=s.to_numpy(),
                name=str(s.name),
                mode=mode,
                yaxis="y",
            )
        )

    if yydata:
        y_right = _ensure_list(yydata)
        right_series = [_prepare_series(df[c], yyunit) for c in y_right]
        for s in right_series:
            fig.add_trace(
                go.Scatter(
                    x=x_series.to_numpy(),
                    y=s.to_numpy(),
                    name=str(s.name),
                    mode=mode,
                    yaxis="y2",
                )
            )
        right_title = str(right_series[0].name) if right_series else ""
        fig.update_layout(
            yaxis2=dict(overlaying="y", side="right", title=right_title),
        )

    left_title = str(left_series[0].name) if left_series else ""
    fig.update_layout(
        title=title,
        xaxis_title=str(x_series.name),
        yaxis_title=left_title,
        legend=dict(orientation="h"),
    )
    return fig


def explore(
    df: pd.DataFrame,
    *,
    xdata: str = "Test Time / s",
    ydata: str | Iterable[str] = "Voltage / V",
    yydata: Optional[str | Iterable[str]] = None,
    xunit: Optional[str] = None,
    yunit: Optional[str] = None,
    yyunit: Optional[str] = None,
    backend: str = "plotly",
    kind: str = "line",
    title: Optional[str] = None,
    **kwargs,
):
    """
    Interactive plotting entry point.

    backend:
      - "bokeh": hvPlot + Bokeh backend (supports dual y-axis)
      - "plotly": native Plotly (supports dual y-axis)
    """
    backend_norm = (backend or "plotly").lower()
    if backend_norm == "plotly":
        return _plot_plotly(
            df,
            xdata=xdata,
            ydata=ydata,
            yydata=yydata,
            xunit=xunit,
            yunit=yunit,
            yyunit=yyunit,
            kind=kind,
            title=title,
        )
    return _plot_bokeh(
        df,
        xdata=xdata,
        ydata=ydata,
        yydata=yydata,
        xunit=xunit,
        yunit=yunit,
        yyunit=yyunit,
        kind=kind,
        title=title,
        **kwargs,
    )
