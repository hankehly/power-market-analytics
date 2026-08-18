"""Tests for the backtest error heatmaps and their aggregation helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from power_market_analytics.common.metrics import mae, mape
from power_market_analytics.forecasting.frames import MetricByYearTimeCode
from power_market_analytics.forecasting.plots import (
    SEQUENTIAL_AQUAS,
    SEQUENTIAL_BLUES,
    _colorscale,
    _period_label,
    error_heatmaps,
    metric_by_year_time_code,
)
from power_market_analytics.tasks.spot_price import TASK
from power_market_analytics.tasks.spot_price.frames import SpotPriceBacktestResult

Y2023 = pd.Timestamp("2023-12-31").as_unit("ns")
Y2024A = pd.Timestamp("2024-01-01").as_unit("ns")
Y2024B = pd.Timestamp("2024-01-02").as_unit("ns")


class TestPeriodLabel:
    @pytest.mark.parametrize(
        ("time_code", "label"),
        [
            (1, "00:00–00:30"),
            (2, "00:30–01:00"),
            (3, "01:00–01:30"),
            (24, "11:30–12:00"),
            (48, "23:30–24:00"),
        ],
    )
    def test_half_hour_window_with_en_dash(self, time_code, label):
        assert _period_label(time_code) == label


class TestColorscale:
    def test_positions_span_0_to_1_evenly(self):
        assert _colorscale(["#a", "#b", "#c"]) == [[0.0, "#a"], [0.5, "#b"], [1.0, "#c"]]

    def test_reference_ramps_have_13_stops_ending_at_1(self):
        for ramp in (SEQUENTIAL_BLUES, SEQUENTIAL_AQUAS):
            scale = _colorscale(ramp)
            assert len(scale) == 13
            assert scale[0] == [0.0, ramp[0]]
            assert scale[-1] == [1.0, ramp[-1]]


def two_year_result() -> SpotPriceBacktestResult:
    """Two time codes; 2023 has one day, 2024 has two days per time code."""
    return SpotPriceBacktestResult.from_df(
        pd.DataFrame(
            {
                "trade_date": [Y2023, Y2023, Y2024A, Y2024B, Y2024A, Y2024B],
                "time_code": np.array([1, 2, 1, 1, 2, 2], dtype="int64"),
                # 2023/1: |2-3| = 1 (50 %); 2023/2: exact (0, 0 %).
                # 2024/1: |10-8|, |10-14| -> MAE 3, MAPE (20 + 40)/2 = 30 %.
                # 2024/2: |5-5|, |0-1| -> MAE 0.5, MAPE 0 % (zero actual excluded).
                "actual_price_jpy_kwh": [2.0, 4.0, 10.0, 10.0, 5.0, 0.0],
                "forecast_price_jpy_kwh": [3.0, 4.0, 8.0, 14.0, 5.0, 1.0],
            }
        )
    )


class TestMetricByYearTimeCode:
    def test_mae_per_year_and_time_code(self):
        out = metric_by_year_time_code(two_year_result(), mae)
        assert isinstance(out, MetricByYearTimeCode)
        assert out.df.dtypes.tolist() == ["int64", "int64", "float64"]
        assert out.df.values.tolist() == [
            [2023, 1, 1.0],
            [2023, 2, 0.0],
            [2024, 1, 3.0],
            [2024, 2, 0.5],
        ]

    def test_mape_per_year_and_time_code(self):
        out = metric_by_year_time_code(two_year_result(), mape)
        assert out.df[["year", "time_code"]].values.tolist() == [
            [2023, 1],
            [2023, 2],
            [2024, 1],
            [2024, 2],
        ]
        assert out.df["value"].tolist() == pytest.approx([50.0, 0.0, 30.0, 0.0])

    def test_metric_callable_receives_actual_then_forecast(self):
        # A signed metric exposes the argument order: actual - forecast.
        out = metric_by_year_time_code(
            two_year_result(), lambda actual, forecast: float((actual - forecast).mean())
        )
        assert out.df.loc[0, "value"] == -1.0  # 2023/1: 2 - 3


def full_day_result(days: list[pd.Timestamp]) -> SpotPriceBacktestResult:
    """48 periods per day; forecast error scales with the time code.

    2023-12-31: actual 20, forecast 20 + tc/10 -> MAE tc/10, MAPE tc/2 %.
    2024 days: actual 10, forecast 10 + tc/5 -> MAE tc/5, MAPE 2 tc %.
    """
    tcs = list(range(1, 49))
    rows = []
    for day in days:
        actual, slope = (20.0, 0.1) if day.year == 2023 else (10.0, 0.2)
        rows.extend(
            {
                "trade_date": day,
                "time_code": tc,
                "actual_price_jpy_kwh": actual,
                "forecast_price_jpy_kwh": actual + slope * tc,
            }
            for tc in tcs
        )
    return SpotPriceBacktestResult.from_df(pd.DataFrame(rows).astype({"time_code": "int64"}))


@pytest.fixture(scope="module")
def fig() -> go.Figure:
    return error_heatmaps(TASK, full_day_result([Y2023, Y2024A]), "naive · tokyo")


class TestErrorHeatmaps:
    def test_two_heatmap_panels_mae_then_mape(self, fig):
        assert isinstance(fig, go.Figure)
        assert [trace.type for trace in fig.data] == ["heatmap", "heatmap"]
        assert "MAE: %{z:.2f} JPY/kWh" in fig.data[0].hovertemplate
        assert "MAPE: %{z:.2f} %" in fig.data[1].hovertemplate
        assert fig.data[0].colorbar.title.text == "JPY/kWh"
        assert fig.data[1].colorbar.title.text == "%"

    def test_axes_are_years_by_48_time_codes(self, fig):
        for trace in fig.data:
            assert np.asarray(trace.z).shape == (2, 48)
            assert list(trace.x) == list(range(1, 49))
            assert list(trace.y) == ["2023", "2024"]
            assert np.asarray(trace.customdata).shape == (2, 48)
            assert trace.customdata[0][0] == "00:00–00:30"
            assert trace.customdata[1][47] == "23:30–24:00"

    def test_cell_values_are_the_metric_per_year_and_time_code(self, fig):
        mae_z = np.asarray(fig.data[0].z)
        mape_z = np.asarray(fig.data[1].z)
        np.testing.assert_allclose(mae_z[0], [tc / 10 for tc in range(1, 49)])
        np.testing.assert_allclose(mae_z[1], [tc / 5 for tc in range(1, 49)])
        np.testing.assert_allclose(mape_z[0], [tc / 2 for tc in range(1, 49)])
        np.testing.assert_allclose(mape_z[1], [2.0 * tc for tc in range(1, 49)])

    def test_color_ramps_blue_then_aqua_from_zero(self, fig):
        assert fig.data[0].colorscale[0] == (0.0, SEQUENTIAL_BLUES[0])
        assert fig.data[0].colorscale[-1] == (1.0, SEQUENTIAL_BLUES[-1])
        assert fig.data[1].colorscale[0] == (0.0, SEQUENTIAL_AQUAS[0])
        assert fig.data[1].colorscale[-1] == (1.0, SEQUENTIAL_AQUAS[-1])
        assert fig.data[0].zmin == 0.0
        assert fig.data[1].zmin == 0.0

    def test_colorbars_centred_on_their_panels(self, fig):
        assert fig.data[0].colorbar.y == 0.75
        assert fig.data[1].colorbar.y == 0.25

    def test_layout_title_height_and_axes(self, fig):
        assert fig.layout.title.text == "naive · tokyo"
        # 170 + 2 * (36 * 2 years + 60) = 434.
        assert fig.layout.height == 434
        assert fig.layout.width == 1200
        assert fig.layout.yaxis.autorange == "reversed"
        assert fig.layout.yaxis2.autorange == "reversed"
        assert list(fig.layout.xaxis.tickvals) == list(range(4, 49, 4))
        assert fig.layout.xaxis.title.text is None
        assert fig.layout.xaxis2.title.text == "Time code (30-minute delivery period)"
        assert fig.layout.yaxis.title.text == "Year"

    def test_subplot_titles_recolored_to_primary_ink(self, fig):
        annotations = fig.layout.annotations
        assert [a.text for a in annotations] == ["MAE (JPY/kWh)", "MAPE (%)"]
        assert all(a.font.color == "#0b0b0b" and a.font.size == 13 for a in annotations)

    def test_height_grows_36_px_per_year_per_panel(self):
        one_year = error_heatmaps(TASK, full_day_result([Y2024A]), "t")
        three_years = error_heatmaps(
            TASK,
            full_day_result([Y2023, Y2024A, pd.Timestamp("2025-01-01").as_unit("ns")]),
            "t",
        )
        assert one_year.layout.height == 170 + 2 * (36 + 60)  # 362
        assert three_years.layout.height == 170 + 2 * (108 + 60)  # 506
        assert np.asarray(three_years.data[0].z).shape == (3, 48)
        assert list(three_years.data[0].y) == ["2023", "2024", "2025"]

    def test_mae_unit_comes_from_the_task(self):
        import dataclasses

        kwh = dataclasses.replace(TASK, unit="kWh")
        fig = error_heatmaps(kwh, full_day_result([Y2024A]), "t")
        assert fig.data[0].colorbar.title.text == "kWh"
        assert "MAE: %{z:.2f} kWh" in fig.data[0].hovertemplate
        assert [a.text for a in fig.layout.annotations] == ["MAE (kWh)", "MAPE (%)"]
