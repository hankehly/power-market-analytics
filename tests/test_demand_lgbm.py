"""Tests for the demand similar-day strategies: LightGBM over the
``lightgbm_msm_popw_daytype`` preset's features (a synthetic ``FeatureFrame``
standing in for Feast) plus the load of a learned similar day one year
earlier, and the four calendar variants.

Everything runs for real — the selector fit, LightGBM fits, TreeSHAP records,
MLflow logging into the session's temp file store — on a small synthetic
history. Assertions are structural (row counts, feature values that can be
hand-derived, refit bookkeeping, SHAP additivity), never on predicted numbers.
"""

from __future__ import annotations

import math
import re

import mlflow
import numpy as np
import pandas as pd
import pytest

from power_market_analytics.features.frame import FeatureFrame, feature_frame
from power_market_analytics.forecasting.backtest import BacktestRun, run_backtest
from power_market_analytics.forecasting.preset_lgbm import PresetLightGbmStrategy
from power_market_analytics.forecasting.strategy import ForecastUnavailableError
from power_market_analytics.tasks.demand.features import (
    CALENDAR_COUNT_FEATURE_COLS,
    DAY_CALENDAR_FEATURE_COLS,
    HOLIDAY_DEGREE_FEATURE_COLS,
    HOLIDAY_DISTANCE_FEATURE_COLS,
)
from power_market_analytics.tasks.demand.frames import (
    AreaDemand,
    DemandBacktestResult,
    DemandForecast,
)
from power_market_analytics.tasks.demand.presets import LIGHTGBM_MSM_POPW_DAYTYPE
from power_market_analytics.tasks.demand.similar_day import (
    SIMILAR_DAY_COMPONENTS,
    SIMILAR_DAY_FEATURE,
    SimilarDayRetrieval,
    SimilarDaySelection,
)
from power_market_analytics.tasks.demand.strategies.lgbm import (
    DAY_CALENDAR_FEATURE_DTYPES,
    LightGbmMsmPopWeightedDayTypeSimilarDayCalendarCountStrategy,
    LightGbmMsmPopWeightedDayTypeSimilarDayCalendarStrategy,
    LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDegreeStrategy,
    LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDistanceStrategy,
    LightGbmMsmPopWeightedDayTypeSimilarDayStrategy,
)
from tests.test_demand_similar_day import HOLIDAYS as SIM_HOLIDAYS
from tests.test_demand_similar_day import (
    load_at,
    make_calendar,
    make_forecast,
    make_hourly_load,
    make_observed,
    temperature_at,
)


@pytest.fixture(scope="module", autouse=True)
def experiment() -> None:
    mlflow.set_experiment("test_demand_lgbm")


SIM_DEMAND_DAYS = pd.date_range("2024-02-01", "2024-04-29", freq="D")
#: The frame Feast would return covers one day past the history too: 04-30 has
#: every preset feature but the day type (the calendar ends at its last holiday).
FEATURE_DAYS = pd.date_range("2024-02-01", "2024-04-30", freq="D")
SIM_D = pd.Timestamp("2024-04-10")  # a Wednesday
PRESET = LIGHTGBM_MSM_POPW_DAYTYPE
DTYPES = {
    "month": "int64",
    "day_of_week": "int64",
    "wavg_temperature_c": "float64",
    "lag_7d_demand_kwh": "int64",
    "popw_forecast_temperature_c": "float64",
    "day_type": "int64",
}
CATEGORICAL = ("day_type",)
STRATEGY = LightGbmMsmPopWeightedDayTypeSimilarDayStrategy
CALENDAR_STRATEGIES = [
    LightGbmMsmPopWeightedDayTypeSimilarDayCalendarStrategy,
    LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDegreeStrategy,
    LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDistanceStrategy,
    LightGbmMsmPopWeightedDayTypeSimilarDayCalendarCountStrategy,
]


def demand_at(day: pd.Timestamp, time_code: int) -> float:
    day_index = (day - SIM_DEMAND_DAYS[0]).days
    shape = 15_000_000 - 4_000_000 * math.cos(2 * math.pi * (time_code - 1) / 48)
    weekend = -1_000_000 if day.dayofweek >= 5 else 0.0
    return float(round((shape + weekend + 5_000 * day_index) / 1000) * 1000)


def make_demand(days=SIM_DEMAND_DAYS) -> AreaDemand:
    return AreaDemand.from_df(
        pd.DataFrame(
            [
                {"trade_date": day, "time_code": tc, "demand_kwh": demand_at(day, tc)}
                for day in days
                for tc in range(1, 49)
            ]
        )
    )


def visible(demand: AreaDemand, day: pd.Timestamp) -> AreaDemand:
    """History the strategy may see for target ``day`` (delivery days <= D-2)."""
    return AreaDemand.from_df(demand.df[demand.df["trade_date"] <= day - pd.Timedelta(days=2)])


def wavg_at(day: pd.Timestamp, hour: int) -> float:
    """The mart's recency-weighted temperature: D-2..D-8 within the history, weights halving."""
    total = weight_sum = 0.0
    for k in range(2, 9):
        obs_day = day - pd.Timedelta(days=k)
        if obs_day not in SIM_DEMAND_DAYS:
            continue
        total += 0.5 ** (k - 2) * temperature_at(obs_day, hour)
        weight_sum += 0.5 ** (k - 2)
    return total / weight_sum if weight_sum else np.nan


def make_features(days=FEATURE_DAYS, *, calendar=None, forecast=None) -> FeatureFrame:
    """The ``lightgbm_msm_popw_daytype`` preset's frame over ``days``, as Feast would return it:
    the calendar columns from ``calendar`` (NaN outside it), the population-weighted
    forecast temperature from ``forecast`` (NaN where absent), the D-7 lag and the
    temperature within the demand history."""
    calendar = make_calendar() if calendar is None else calendar
    forecast = make_forecast() if forecast is None else forecast
    day_types = calendar.df.set_index("trade_date")["day_type"]
    temps = forecast.df.set_index(["trade_date", "hour_ending"])["forecast_temperature_c"]
    rows = []
    for day in days:
        lag_day = day - pd.Timedelta(days=7)
        for tc in range(1, 49):
            hour = (tc + 1) // 2
            rows.append(
                {
                    "trade_date": day,
                    "time_code": tc,
                    "month": day.month,
                    "day_of_week": day.dayofweek,
                    "wavg_temperature_c": wavg_at(day, hour),
                    "lag_7d_demand_kwh": (
                        demand_at(lag_day, tc) if lag_day in SIM_DEMAND_DAYS else np.nan
                    ),
                    "popw_forecast_temperature_c": temps.get((day, hour), np.nan),
                    "day_type": day_types.get(day, np.nan),
                }
            )
    return feature_frame(pd.DataFrame(rows), PRESET.columns)


@pytest.fixture(scope="module")
def sim_inputs():
    calendar, forecast = make_calendar(), make_forecast()
    return {
        "demand": make_demand(),
        "features": make_features(calendar=calendar, forecast=forecast),
        "weather_forecast": forecast,
        "day_calendar": calendar,
        "weather_observed": make_observed(),
        "hourly_load": make_hourly_load(),
    }


def make_strategy(inputs, cls=STRATEGY, **kwargs):
    return cls(
        PRESET,
        inputs["features"],
        inputs["weather_forecast"],
        inputs["day_calendar"],
        inputs["weather_observed"],
        inputs["hourly_load"],
        dtypes=DTYPES,
        categorical=CATEGORICAL,
        census_year=2020,
        train_window_days=30,
        **kwargs,
    )


def record_columns(strategy) -> list[str]:
    return [
        "trade_date",
        "time_code",
        *[c for c in strategy.feature_cols if c != "time_code"],
        *[f"shap_{c}" for c in strategy.feature_cols],
        "shap_expected_value",
    ]


def empty_run() -> BacktestRun:
    return BacktestRun(
        DemandBacktestResult.from_df(
            pd.DataFrame(
                {
                    "trade_date": pd.to_datetime([]),
                    "time_code": np.array([], dtype="int64"),
                    "actual_demand_kwh": np.array([], dtype="float64"),
                    "forecast_demand_kwh": np.array([], dtype="float64"),
                }
            )
        ),
        (),
    )


class TestInit:
    def test_features_frames_and_defaults(self, sim_inputs):
        strategy = make_strategy(sim_inputs)
        assert isinstance(strategy, PresetLightGbmStrategy)
        assert STRATEGY.strategy_name == "lightgbm_msm_popw_daytype_simday"
        assert strategy.name == STRATEGY.strategy_name
        assert strategy.preset is PRESET
        assert strategy.calendar_feature_cols == ()
        assert strategy.feature_cols == (*PRESET.feature_cols, SIMILAR_DAY_FEATURE)
        assert strategy.categorical_feature_cols == ("day_type",)
        assert strategy.lookback_days == 0
        assert strategy.census_year == 2020
        assert strategy.train_window_days == 30
        eval_set_cls = strategy.eval_set_cls
        assert eval_set_cls.feature_cols == strategy.feature_cols
        assert list(eval_set_cls.schema) == [
            "trade_date",
            "time_code",
            "month",
            "day_of_week",
            "wavg_temperature_c",
            "lag_7d_demand_kwh",
            "popw_forecast_temperature_c",
            "day_type",
            SIMILAR_DAY_FEATURE,
            "actual_demand_kwh",
            "forecast_demand_kwh",
        ]
        assert eval_set_cls.schema[SIMILAR_DAY_FEATURE] == "float64"
        assert SIMILAR_DAY_FEATURE in eval_set_cls.non_null_cols

    def test_name_overrides_the_label(self, sim_inputs):
        strategy = make_strategy(sim_inputs, name="simday_again")
        assert strategy.name == "simday_again"
        assert strategy.preset is PRESET

    @pytest.mark.parametrize("cls", CALENDAR_STRATEGIES, ids=lambda c: c.strategy_name)
    def test_calendar_variants_append_their_columns(self, sim_inputs, cls):
        strategy = make_strategy(sim_inputs, cls=cls)
        assert strategy.name == cls.strategy_name
        assert cls.calendar_feature_cols
        assert set(cls.calendar_feature_cols) <= set(DAY_CALENDAR_FEATURE_COLS)
        assert strategy.feature_cols == (
            *PRESET.feature_cols,
            SIMILAR_DAY_FEATURE,
            *cls.calendar_feature_cols,
        )
        assert strategy.categorical_feature_cols == ("day_type",)
        schema = strategy.eval_set_cls.schema
        assert list(schema) == [
            "trade_date",
            *strategy.feature_cols,
            "actual_demand_kwh",
            "forecast_demand_kwh",
        ]
        for col in cls.calendar_feature_cols:
            assert schema[col] == DAY_CALENDAR_FEATURE_DTYPES[col]
        assert set(cls.calendar_feature_cols) <= set(strategy.eval_set_cls.non_null_cols)

    @pytest.mark.parametrize(
        ("cls", "ref"),
        [
            (LightGbmMsmPopWeightedDayTypeSimilarDayCalendarStrategy, "ftr_day_calendar:half"),
            (
                LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDegreeStrategy,
                "ftr_day_calendar:holiday_degree",
            ),
        ],
        ids=["calendar+half", "holidaydegree+holiday_degree"],
    )
    def test_a_preset_column_the_strategy_adds_itself_is_rejected(self, sim_inputs, cls, ref):
        # `--add` of a column the variant joins from the calendar would put it in
        # the design matrix twice.
        preset = PRESET.with_changes(add=(ref,), name="twice")
        column = ref.partition(":")[2]
        features = feature_frame(sim_inputs["features"].df.assign(**{column: 1.0}), preset.columns)
        with pytest.raises(
            ValueError, match=rf"{cls.strategy_name}: \['{column}'\] are this strategy's own"
        ):
            cls(
                preset,
                features,
                sim_inputs["weather_forecast"],
                sim_inputs["day_calendar"],
                sim_inputs["weather_observed"],
                sim_inputs["hourly_load"],
                dtypes={**DTYPES, column: "int64"},
                categorical=CATEGORICAL,
                census_year=2020,
            )

    def test_the_variants_cover_the_calendar_columns_as_documented(self):
        calendar, degree, distance, counts = CALENDAR_STRATEGIES
        assert calendar.calendar_feature_cols == DAY_CALENDAR_FEATURE_COLS
        assert degree.calendar_feature_cols == HOLIDAY_DEGREE_FEATURE_COLS
        assert distance.calendar_feature_cols == HOLIDAY_DISTANCE_FEATURE_COLS
        assert counts.calendar_feature_cols == CALENDAR_COUNT_FEATURE_COLS
        assert [c.strategy_name for c in CALENDAR_STRATEGIES] == [
            "lightgbm_msm_popw_daytype_simday_calendar",
            "lightgbm_msm_popw_daytype_simday_holidaydegree",
            "lightgbm_msm_popw_daytype_simday_holidaydistance",
            "lightgbm_msm_popw_daytype_simday_calendarcounts",
        ]


class TestPredict:
    def test_first_predict_fits_then_joins_the_selected_days_load(self, sim_inputs):
        strategy = make_strategy(sim_inputs)
        history = visible(sim_inputs["demand"], SIM_D)
        forecast = strategy.predict(SIM_D, history)
        assert isinstance(forecast, DemandForecast)
        weights = strategy.selector.weights
        assert weights.fit_through == history.df["trade_date"].max()
        assert weights.fit_from == SIM_HOLIDAYS[0] + pd.Timedelta(days=394)
        record = strategy._shap_records[SIM_D]
        assert list(record.columns) == record_columns(strategy)
        # The preset's features are the frame's values for the day.
        frame = sim_inputs["features"].df.set_index(["trade_date", "time_code"]).loc[SIM_D]
        assert record["day_type"].tolist() == frame["day_type"].tolist()
        assert record["lag_7d_demand_kwh"].tolist() == frame["lag_7d_demand_kwh"].tolist()
        selection = strategy._selections[SIM_D]
        assert list(selection.columns) == list(SimilarDaySelection.schema)
        assert selection.equals(strategy.selector.select([SIM_D]).df)
        reference = selection.iloc[0]["reference_date"]
        expected = [load_at(reference, (tc + 1) // 2) / 2 for tc in range(1, 49)]
        assert record[SIMILAR_DAY_FEATURE].tolist() == pytest.approx(expected)
        reconstructed = record[list(strategy.shap_cols)].sum(axis=1) + record["shap_expected_value"]
        np.testing.assert_allclose(
            reconstructed.to_numpy(), forecast.df["forecast_demand_kwh"].to_numpy(), atol=1e-3
        )

    def test_weights_are_fitted_once(self, sim_inputs):
        strategy = make_strategy(sim_inputs)
        strategy.predict(SIM_D, visible(sim_inputs["demand"], SIM_D))
        first = strategy.selector.weights
        later = SIM_D + pd.Timedelta(days=7)
        strategy.predict(later, visible(sim_inputs["demand"], later))
        assert strategy.selector.weights is first
        assert set(strategy._selections) == {SIM_D, later}

    def test_a_day_without_pairs_yet_is_unforecastable(self, sim_inputs):
        strategy = make_strategy(sim_inputs)
        # History ends 02-06, before the first scorable target 02-07.
        early = pd.Timestamp("2024-02-08")
        with pytest.raises(
            ForecastUnavailableError,
            match="no training pairs with a target day on or before 2024-02-06",
        ):
            strategy.predict(early, visible(sim_inputs["demand"], early))

    def test_a_day_outside_the_calendar_is_unforecastable(self, sim_inputs):
        strategy = make_strategy(sim_inputs)
        strategy.predict(SIM_D, visible(sim_inputs["demand"], SIM_D))
        beyond = pd.Timestamp("2024-04-30")  # after the calendar's last holiday
        with pytest.raises(
            ForecastUnavailableError,
            match=rf"features \['day_type', '{SIMILAR_DAY_FEATURE}'\] unavailable",
        ):
            strategy.predict(beyond, visible(sim_inputs["demand"], beyond))

    def test_build_eval_set_before_predict_raises(self, sim_inputs):
        strategy = make_strategy(sim_inputs)
        with pytest.raises(RuntimeError, match="not fitted"):
            strategy.build_eval_set(sim_inputs["demand"], SIM_D, SIM_D, run=empty_run())


SIM_WINDOW_START = pd.Timestamp("2024-04-08")
SIM_WINDOW_END = pd.Timestamp("2024-04-14")


class TestBacktestEvalAndEvaluate:
    @pytest.fixture(scope="class")
    def backtested(self, sim_inputs):
        strategy = make_strategy(sim_inputs, refit_every_days=7)
        return strategy, run_backtest(
            strategy, sim_inputs["demand"], SIM_WINDOW_START, SIM_WINDOW_END
        )

    def test_backtest_covers_the_window(self, backtested):
        _, run = backtested
        assert run.skipped_days == ()
        assert len(run.result) == 7 * 48

    def test_eval_set_and_contributions_carry_the_feature(self, backtested, sim_inputs):
        strategy, run = backtested
        eval_set = strategy.build_eval_set(
            sim_inputs["demand"], SIM_WINDOW_START, SIM_WINDOW_END, run=run
        )
        assert type(eval_set) is strategy.eval_set_cls
        assert len(eval_set) == 7 * 48
        assert eval_set.df[SIMILAR_DAY_FEATURE].notna().all()
        assert eval_set.df["day_type"].dtype == "int64"
        contributions = strategy.contributions()
        assert SIMILAR_DAY_FEATURE in set(contributions.df["component"])

    def test_evaluate_logs_the_preset_and_selector_params(self, backtested, sim_inputs):
        strategy, run = backtested
        eval_set = strategy.build_eval_set(
            sim_inputs["demand"], SIM_WINDOW_START, SIM_WINDOW_END, run=run
        )
        with mlflow.start_run() as active:
            strategy.evaluate(eval_set, explainability_nsamples=20)
        params = mlflow.get_run(active.info.run_id).data.params
        assert params["feature_preset"] == "lightgbm_msm_popw_daytype"
        assert params["feature_refs"] == ",".join(PRESET.features)
        assert params["lgbm_feature_cols"] == ",".join(strategy.feature_cols)
        assert params["lgbm_categorical_feature_cols"] == "day_type"
        assert params["population_weight_census_year"] == "2020"
        assert params["similar_day_center_lag_days"] == "364"
        assert params["similar_day_window_half_width_days"] == "30"
        assert params["similar_day_components"] == ",".join(SIMILAR_DAY_COMPONENTS)
        assert params["similar_day_weights"].startswith("calendar_days=")
        assert params["similar_day_first_selectable_day"] == "2024-02-07"
        assert params["similar_day_hourly_load_span"] == "2023-01-01..2024-04-30"
        assert params["similar_day_periods_per_hour"] == "2"
        assert params["similar_day_fit_through"] == "2024-04-06"

    def test_diagnostics_frames_and_metrics(self, backtested, sim_inputs):
        strategy, run = backtested
        with mlflow.start_run() as active:
            frames = strategy.diagnostics(sim_inputs["demand"], run)
        assert set(frames) == {"similar_day_selection", "similar_day_retrieval"}
        selection = SimilarDaySelection.from_df(frames["similar_day_selection"])
        retrieval = SimilarDayRetrieval.from_df(frames["similar_day_retrieval"])
        window = list(pd.date_range(SIM_WINDOW_START, SIM_WINDOW_END))
        assert selection.df["trade_date"].tolist() == window
        assert retrieval.df["trade_date"].tolist() == window
        metrics = mlflow.get_run(active.info.run_id).data.metrics
        assert set(metrics) == {
            "similar_day_load_difference_selected",
            "similar_day_load_difference_lag_364",
            "similar_day_load_difference_oracle",
            "similar_day_share_better_than_lag_364",
        }
        assert 0 <= metrics["similar_day_share_better_than_lag_364"] <= 1

    def test_diagnostics_without_forecasts_is_empty(self, sim_inputs):
        strategy = make_strategy(sim_inputs)
        with mlflow.start_run():
            assert strategy.diagnostics(sim_inputs["demand"], empty_run()) == {}


SIM_SATURDAY = pd.Timestamp("2024-04-13")


class TestCalendarVariantsPredict:
    def test_the_calendar_strategy_adds_all_ten_as_floats(self, sim_inputs):
        cls = LightGbmMsmPopWeightedDayTypeSimilarDayCalendarStrategy
        strategy = make_strategy(sim_inputs, cls=cls)
        forecast = strategy.predict(SIM_D, visible(sim_inputs["demand"], SIM_D))
        record = strategy._shap_records[SIM_D]
        assert list(record.columns) == record_columns(strategy)
        # Every period of the day carries the day's calendar row, as floats.
        row = sim_inputs["day_calendar"].df.set_index("trade_date").loc[SIM_D]
        for col in DAY_CALENDAR_FEATURE_COLS:
            assert record[col].tolist() == [float(row[col])] * 48, col
        # SIM_D = 2024-04-10: a working Wednesday, day 101 of a leap year, day 10 of
        # Q2, fiscal Q1, 21 days after the 03-20 holiday and 19 before 04-29.
        first = record.iloc[0]
        assert first["day_of_year"] == 101.0
        assert first["day_of_quarter"] == 10.0
        assert first["is_business_day"] == 1.0
        assert first["fiscal_quarter"] == 1.0
        assert first["days_since_holiday"] == 21.0
        assert first["days_until_holiday"] == 19.0
        reconstructed = record[list(strategy.shap_cols)].sum(axis=1) + record["shap_expected_value"]
        np.testing.assert_allclose(
            reconstructed.to_numpy(), forecast.df["forecast_demand_kwh"].to_numpy(), atol=1e-3
        )

    def test_holiday_degree_is_the_only_calendar_feature_added(self, sim_inputs):
        cls = LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDegreeStrategy
        strategy = make_strategy(sim_inputs, cls=cls)
        strategy.predict(SIM_SATURDAY, visible(sim_inputs["demand"], SIM_SATURDAY))
        record = strategy._shap_records[SIM_SATURDAY]
        assert list(record.columns) == record_columns(strategy)
        assert "days_since_holiday" not in record.columns
        # 2024-04-13 is a Saturday: holiday degree 0.8 on every period.
        assert record["holiday_degree"].tolist() == [0.8] * 48

    def test_holiday_distances_are_the_only_calendar_features_added(self, sim_inputs):
        cls = LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDistanceStrategy
        strategy = make_strategy(sim_inputs, cls=cls)
        strategy.predict(SIM_D, visible(sim_inputs["demand"], SIM_D))
        record = strategy._shap_records[SIM_D]
        assert list(record.columns) == record_columns(strategy)
        assert "holiday_degree" not in record.columns
        assert record["days_since_holiday"].tolist() == [21.0] * 48
        assert record["days_until_holiday"].tolist() == [19.0] * 48

    def test_calendar_counts_are_the_only_calendar_features_added(self, sim_inputs):
        cls = LightGbmMsmPopWeightedDayTypeSimilarDayCalendarCountStrategy
        strategy = make_strategy(sim_inputs, cls=cls)
        strategy.predict(SIM_D, visible(sim_inputs["demand"], SIM_D))
        record = strategy._shap_records[SIM_D]
        assert list(record.columns) == record_columns(strategy)
        assert "holiday_degree" not in record.columns
        assert "days_since_holiday" not in record.columns
        expected = {
            "half": 1.0,
            "quarter": 2.0,
            "day_of_month": 10.0,
            "day_of_quarter": 10.0,
            "day_of_year": 101.0,
            "fiscal_quarter": 1.0,
        }
        for col, value in expected.items():
            assert record[col].tolist() == [value] * 48, col

    @pytest.mark.parametrize("cls", CALENDAR_STRATEGIES, ids=lambda c: c.strategy_name)
    def test_a_day_outside_the_calendar_is_unforecastable(self, sim_inputs, cls):
        strategy = make_strategy(sim_inputs, cls=cls)
        strategy.predict(SIM_D, visible(sim_inputs["demand"], SIM_D))
        beyond = pd.Timestamp("2024-04-30")  # after the calendar's last holiday
        missing = ["day_type", SIMILAR_DAY_FEATURE, *cls.calendar_feature_cols]
        with pytest.raises(
            ForecastUnavailableError, match=re.escape(f"features {missing} unavailable")
        ):
            strategy.predict(beyond, visible(sim_inputs["demand"], beyond))


class TestCalendarVariantsBacktestEvalAndEvaluate:
    @pytest.fixture(scope="class", params=CALENDAR_STRATEGIES, ids=lambda c: c.strategy_name)
    def backtested(self, request, sim_inputs):
        strategy = make_strategy(sim_inputs, cls=request.param, refit_every_days=7)
        return strategy, run_backtest(
            strategy, sim_inputs["demand"], SIM_WINDOW_START, SIM_WINDOW_END
        )

    def test_backtest_covers_the_window(self, backtested):
        _, run = backtested
        assert run.skipped_days == ()
        assert len(run.result) == 7 * 48

    def test_eval_set_carries_only_its_calendar_features(self, backtested, sim_inputs):
        strategy, run = backtested
        eval_set = strategy.build_eval_set(
            sim_inputs["demand"], SIM_WINDOW_START, SIM_WINDOW_END, run=run
        )
        assert type(eval_set) is strategy.eval_set_cls
        assert len(eval_set) == 7 * 48
        df = eval_set.df
        present = [c for c in DAY_CALENDAR_FEATURE_COLS if c in df.columns]
        assert present == list(strategy.calendar_feature_cols)
        for col in strategy.calendar_feature_cols:
            assert df[col].dtype == DAY_CALENDAR_FEATURE_DTYPES[col], col
        # 2024-04-08..14, Monday to Sunday: five working days (0), a Saturday
        # (0.8) and a Sunday (1.0); days 99..105 of a leap year, 8..14 of April
        # and of Q2, all in half 1 / Q2 / fiscal Q1; 19..25 days after 03-20,
        # 21..15 before 04-29.
        if "holiday_degree" in df.columns:
            assert set(df["holiday_degree"]) == {0.0, 0.8, 1.0}
        if "day_of_year" in df.columns:
            assert sorted(df["day_of_year"].unique()) == list(range(99, 106))
            assert sorted(df["day_of_month"].unique()) == list(range(8, 15))
            assert set(df["half"]) == {1}
            assert set(df["quarter"]) == {2}
            assert set(df["fiscal_quarter"]) == {1}
        if "days_since_holiday" in df.columns:
            assert sorted(df["days_since_holiday"].unique()) == list(range(19, 26))
            assert sorted(df["days_until_holiday"].unique()) == list(range(15, 22))
        if "is_business_day" in df.columns:
            assert set(df["is_business_day"]) == {0, 1}
        components = set(strategy.contributions().df["component"])
        assert set(strategy.calendar_feature_cols) <= components
        others = set(DAY_CALENDAR_FEATURE_COLS) - set(strategy.calendar_feature_cols)
        assert not others & components

    def test_evaluate_logs_the_feature_list(self, backtested, sim_inputs):
        strategy, run = backtested
        eval_set = strategy.build_eval_set(
            sim_inputs["demand"], SIM_WINDOW_START, SIM_WINDOW_END, run=run
        )
        with mlflow.start_run() as active:
            strategy.evaluate(eval_set, explainability_nsamples=20)
        params = mlflow.get_run(active.info.run_id).data.params
        assert params["lgbm_feature_cols"] == ",".join(strategy.feature_cols)
        assert params["lgbm_categorical_feature_cols"] == "day_type"
        assert params["feature_preset"] == "lightgbm_msm_popw_daytype"
        assert params["similar_day_center_lag_days"] == "364"
