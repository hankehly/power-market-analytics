"""Interactive visualizations for spot price backtests.

Figures are Plotly objects meant to be logged to MLflow as HTML artifacts
(``mlflow.log_figure(fig, "name.html")``), which the MLflow UI renders
interactively. Colors follow the dataviz reference palette: error magnitudes
take sequential one-hue ramps (light = low); with two sequential contexts in
one figure, the first uses the blue ramp and the second the aqua ramp. Chart
chrome uses the palette's ink tokens on the light surface.
"""

from __future__ import annotations

from typing import Callable

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from power_market_analytics.common.metrics import mae, mape
from power_market_analytics.tasks.spot_price.frames import (
    N_PERIODS,
    BacktestResult,
    MetricByYearTimeCode,
)

SEQUENTIAL_BLUES = [
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
    "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
]
# Aqua ramp: same hue family as categorical slot 2 (#1baf7a), monotone
# lightness, light -> dark, mirroring the blue ramp's progression.
SEQUENTIAL_AQUAS = [
    "#d2f3e6", "#bdecda", "#a3e4cc", "#8adabd", "#6fd0ae", "#52c49e",
    "#35b78d", "#1baf7a", "#17996b", "#12835b", "#0e6e4c", "#0a583d", "#07452f",
]
SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
FONT_FAMILY = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def metric_by_year_time_code(
    result: BacktestResult, metric: Callable[[pd.Series, pd.Series], float]
) -> MetricByYearTimeCode:
    """Aggregate backtest errors to one metric value per year and time code.

    Parameters
    ----------
    result : BacktestResult
    metric : callable
        Error metric taking (actual, forecast) Series, e.g.
        :func:`power_market_analytics.common.metrics.mae`.

    Returns
    -------
    MetricByYearTimeCode
    """
    df = result.df.assign(year=result.df["trade_date"].dt.year)
    long = (
        df.groupby(["year", "time_code"])
        .apply(
            lambda g: metric(g["actual_price_jpy_kwh"], g["forecast_price_jpy_kwh"]),
            include_groups=False,
        )
        .rename("value")
        .reset_index()
        .astype({"year": "int64", "time_code": "int64", "value": "float64"})
    )
    return MetricByYearTimeCode.from_df(long)


def _period_label(time_code: int) -> str:
    start = (time_code - 1) * 30
    end = time_code * 30
    return f"{start // 60:02d}:{start % 60:02d}–{end // 60:02d}:{end % 60:02d}"


def _colorscale(ramp: list[str]) -> list[list]:
    return [[i / (len(ramp) - 1), color] for i, color in enumerate(ramp)]


def error_heatmaps(result: BacktestResult, title: str) -> go.Figure:
    """Build stacked interactive year x time_code heatmaps for MAE and MAPE.

    Parameters
    ----------
    result : BacktestResult
    title : str
        Figure title, e.g. the strategy and area under test.

    Returns
    -------
    plotly.graph_objects.Figure
    """
    panels = [
        ("MAE", "JPY/kWh", mae, SEQUENTIAL_BLUES),
        ("MAPE", "%", mape, SEQUENTIAL_AQUAS),
    ]
    fig = make_subplots(
        rows=len(panels),
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.10,
        subplot_titles=[f"{name} ({unit})" for name, unit, _, _ in panels],
    )

    n_years = 0
    for row, (name, unit, metric, ramp) in enumerate(panels, start=1):
        pivot = metric_by_year_time_code(result, metric).to_matrix()
        n_years = len(pivot.index)
        period_labels = [_period_label(tc) for tc in pivot.columns]
        # One colorbar per panel, each aligned to its own subplot.
        colorbar_y = 1.0 - (row - 0.5) / len(panels)
        fig.add_trace(
            go.Heatmap(
                z=pivot.to_numpy(),
                x=pivot.columns.to_list(),
                y=[str(y) for y in pivot.index],
                customdata=[period_labels] * n_years,
                colorscale=_colorscale(ramp),
                zmin=0.0,
                xgap=2,
                ygap=2,
                colorbar=dict(
                    title=dict(text=unit, font=dict(color=INK_SECONDARY)),
                    tickfont=dict(color=INK_SECONDARY),
                    outlinewidth=0,
                    len=0.42,
                    y=colorbar_y,
                    x=1.01,
                ),
                hovertemplate=(
                    "Year %{y} · Time code %{x} (%{customdata})<br>"
                    + name
                    + ": %{z:.2f} "
                    + unit
                    + "<extra></extra>"
                ),
            ),
            row=row,
            col=1,
        )

    fig.update_layout(
        title=dict(text=title, font=dict(color=INK_PRIMARY, size=16), x=0.01),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family=FONT_FAMILY, color=INK_SECONDARY, size=12),
        width=1200,
        height=170 + 2 * (36 * n_years + 60),
        margin=dict(l=70, r=110, t=90, b=60),
    )
    fig.update_xaxes(
        tickmode="array",
        tickvals=list(range(4, N_PERIODS + 1, 4)),
        showgrid=False,
        zeroline=False,
        tickfont=dict(color=INK_SECONDARY),
    )
    fig.update_yaxes(
        autorange="reversed",
        title=dict(text="Year", font=dict(color=INK_SECONDARY)),
        showgrid=False,
        zeroline=False,
        tickfont=dict(color=INK_SECONDARY),
    )
    fig.update_xaxes(
        title=dict(
            text="Time code (30-minute delivery period)",
            font=dict(color=INK_SECONDARY),
        ),
        row=len(panels),
        col=1,
    )
    for annotation in fig.layout.annotations:
        annotation.font = dict(color=INK_PRIMARY, size=13)
    return fig
