"""Tests for the demand LightGBM strategies: the baseline (calendar + temperature +
D-7 lag), ``lightgbm_msm`` (baseline + the MSM forecast temperature),
``lightgbm_msm_popw`` (baseline + the population-weighted MSM forecast temperature)
and ``lightgbm_msm_popw_daytype`` (the same plus the day-type categorical).

Everything runs for real — feature building, LightGBM fits, TreeSHAP records,
MLflow logging into the session's temp file store — on a small synthetic
demand/temperature history. Assertions are structural (row counts, feature
values that can be hand-derived, refit bookkeeping, SHAP additivity), never
on predicted numbers.
"""

from __future__ import annotations

import math

import mlflow
import numpy as np
import pandas as pd
import pytest

from power_market_analytics.forecasting.backtest import BacktestRun, run_backtest
from power_market_analytics.forecasting.strategy import ForecastUnavailableError
from power_market_analytics.tasks.demand.features import (
    DAY_TYPE_FEATURE,
    FORECAST_TEMPERATURE_FEATURE,
    POPW_FORECAST_TEMPERATURE_FEATURE,
    TEMPERATURE_FEATURE,
    TEMPERATURE_HALF_LIFE_DAYS,
    TEMPERATURE_LAG_DAYS,
    hour_ending_of,
)
from power_market_analytics.tasks.demand.frames import (
    AreaDemand,
    AreaTemperature,
    AreaTemperatureForecast,
    DayTypeCalendar,
    DemandBacktestResult,
    DemandForecast,
)
from power_market_analytics.tasks.demand.strategies.lgbm import (
    DEMAND_LAG_FEATURE,
    FEATURE_COLS,
    MSM_FEATURE_COLS,
    MSM_POPW_DAY_TYPE_FEATURE_COLS,
    MSM_POPW_FEATURE_COLS,
    DemandLightGbmEvalSet,
    DemandLightGbmMsmEvalSet,
    DemandLightGbmMsmPopWeightedDayTypeEvalSet,
    DemandLightGbmMsmPopWeightedEvalSet,
    LightGbmMsmPopWeightedDayTypeStrategy,
    LightGbmMsmPopWeightedStrategy,
    LightGbmMsmStrategy,
    LightGbmStrategy,
)


@pytest.fixture(scope="module", autouse=True)
def experiment() -> None:
    mlflow.set_experiment("test_demand_lgbm")


HISTORY_START = pd.Timestamp("2024-03-01")
#: 60 days: 2024-03-01 .. 2024-04-29.
HISTORY_DAYS = pd.date_range(HISTORY_START, periods=60, freq="D")


def demand_at(day: pd.Timestamp, time_code: int) -> float:
    day_index = (day - HISTORY_START).days
    shape = 15_000_000 - 4_000_000 * math.cos(2 * math.pi * (time_code - 1) / 48)
    weekend = -1_000_000 if day.dayofweek >= 5 else 0.0
    return float(round((shape + weekend + 5_000 * day_index) / 1000) * 1000)


def temperature_at(day: pd.Timestamp, hour_ending: int) -> float:
    day_index = (day - HISTORY_START).days
    return round(8.0 + 0.15 * day_index + 5.0 * math.sin(2 * math.pi * (hour_ending - 9) / 24), 1)


def make_demand(days=HISTORY_DAYS) -> AreaDemand:
    return AreaDemand.from_df(
        pd.DataFrame(
            [
                {"trade_date": day, "time_code": tc, "demand_kwh": demand_at(day, tc)}
                for day in days
                for tc in range(1, 49)
            ]
        )
    )


def make_temperature(days=HISTORY_DAYS) -> AreaTemperature:
    return AreaTemperature.from_df(
        pd.DataFrame(
            [
                {"obs_date": day, "hour_ending": h, "temperature_c": temperature_at(day, h)}
                for day in days
                for h in range(1, 25)
            ]
        ).astype({"hour_ending": "int64"})
    )


def forecast_temperature_at(day: pd.Timestamp, hour_ending: int) -> float:
    return round(temperature_at(day, hour_ending) + 0.3 * math.cos(hour_ending / 3.0), 2)


def make_forecast_temperature(days=HISTORY_DAYS) -> AreaTemperatureForecast:
    return AreaTemperatureForecast.from_df(
        pd.DataFrame(
            [
                {
                    "trade_date": day,
                    "hour_ending": h,
                    "forecast_temperature_c": forecast_temperature_at(day, h),
                }
                for day in days
                for h in range(1, 25)
            ]
        ).astype({"hour_ending": "int64"})
    )


def visible(demand: AreaDemand, day: pd.Timestamp) -> AreaDemand:
    """History the strategy may see for target ``day`` (delivery days <= D-2)."""
    return AreaDemand.from_df(demand.df[demand.df["trade_date"] <= day - pd.Timedelta(days=2)])


def expected_wavg(day: pd.Timestamp, time_code: int) -> float:
    hour = int(hour_ending_of(pd.Series([time_code], dtype="int64")).iloc[0])
    weights = [
        0.5 ** ((k - TEMPERATURE_LAG_DAYS[0]) / TEMPERATURE_HALF_LIFE_DAYS)
        for k in TEMPERATURE_LAG_DAYS
    ]
    temps = [temperature_at(day - pd.Timedelta(days=k), hour) for k in TEMPERATURE_LAG_DAYS]
    return sum(w * t for w, t in zip(weights, temps)) / sum(weights)


D = pd.Timestamp("2024-04-10")  # a Wednesday


@pytest.fixture(scope="module")
def demand() -> AreaDemand:
    return make_demand()


@pytest.fixture(scope="module")
def temperature() -> AreaTemperature:
    return make_temperature()


@pytest.fixture(scope="module")
def forecast_temperature() -> AreaTemperatureForecast:
    return make_forecast_temperature()


class TestClassAttributes:
    def test_features_and_frames(self):
        assert LightGbmStrategy.name == "lightgbm"
        assert LightGbmStrategy.task.name == "demand"
        assert FEATURE_COLS == (
            "time_code",
            "month",
            "day_of_week",
            TEMPERATURE_FEATURE,
            DEMAND_LAG_FEATURE,
        )
        assert LightGbmStrategy.feature_cols == FEATURE_COLS
        assert LightGbmStrategy.eval_set_cls is DemandLightGbmEvalSet
        assert LightGbmStrategy.lookback_days == 8
        assert DemandLightGbmEvalSet.target_col == "actual_demand_kwh"
        assert DemandLightGbmEvalSet.forecast_col == "forecast_demand_kwh"
        assert list(DemandLightGbmEvalSet.schema) == [
            "trade_date",
            "time_code",
            "month",
            "day_of_week",
            TEMPERATURE_FEATURE,
            DEMAND_LAG_FEATURE,
            "actual_demand_kwh",
            "forecast_demand_kwh",
        ]


class TestPredict:
    def test_returns_48_finite_demand_values(self, demand, temperature):
        strategy = LightGbmStrategy(temperature, train_window_days=30)
        forecast = strategy.predict(D, visible(demand, D))
        assert isinstance(forecast, DemandForecast)
        assert forecast.df["trade_date"].eq(D).all()
        assert forecast.df["time_code"].tolist() == list(range(1, 49))
        assert np.isfinite(forecast.df["forecast_demand_kwh"]).all()

    def test_features_are_the_d7_lag_and_the_recency_weighted_temperature(
        self, demand, temperature
    ):
        strategy = LightGbmStrategy(temperature, train_window_days=30)
        forecast = strategy.predict(D, visible(demand, D))
        record = strategy._shap_records[pd.Timestamp(D).as_unit("ns")]
        assert list(record.columns) == [
            "trade_date",
            "time_code",
            "month",
            "day_of_week",
            TEMPERATURE_FEATURE,
            DEMAND_LAG_FEATURE,
            *[f"shap_{c}" for c in FEATURE_COLS],
            "shap_expected_value",
        ]
        assert record["month"].eq(4).all()
        assert record["day_of_week"].eq(2).all()
        assert record[DEMAND_LAG_FEATURE].tolist() == [
            demand_at(D - pd.Timedelta(days=7), tc) for tc in range(1, 49)
        ]
        np.testing.assert_allclose(
            record[TEMPERATURE_FEATURE].to_numpy(),
            [expected_wavg(D, tc) for tc in range(1, 49)],
        )
        # The forecast is the fitted model's own prediction on those rows.
        np.testing.assert_allclose(
            forecast.df["forecast_demand_kwh"].to_numpy(),
            strategy._model.predict(record[list(FEATURE_COLS)].astype("float64")),
        )
        reconstructed = record[list(strategy.shap_cols)].sum(axis=1) + record["shap_expected_value"]
        np.testing.assert_allclose(
            reconstructed.to_numpy(), forecast.df["forecast_demand_kwh"].to_numpy(), atol=1e-3
        )

    def test_missing_d7_demand_is_unforecastable(self, demand, temperature):
        strategy = LightGbmStrategy(temperature, train_window_days=30)
        without_d7 = AreaDemand.from_df(
            demand.df[demand.df["trade_date"] != D - pd.Timedelta(days=7)]
        )
        with pytest.raises(
            ForecastUnavailableError,
            match=rf"lightgbm: features \['{DEMAND_LAG_FEATURE}'\] unavailable for 2024-04-10",
        ):
            strategy.predict(D, visible(without_d7, D))

    def test_missing_temperature_window_is_unforecastable(self, demand):
        # Temperature stops before D-8: every lag is missing for D.
        short = make_temperature(pd.date_range(HISTORY_START, D - pd.Timedelta(days=9)))
        strategy = LightGbmStrategy(short, train_window_days=5)
        with pytest.raises(
            ForecastUnavailableError,
            match=rf"lightgbm: features \['{TEMPERATURE_FEATURE}'\] unavailable for 2024-04-10",
        ):
            strategy.predict(D, visible(demand, D))

    def test_training_window_needs_eight_days_of_lookback(self, demand, temperature):
        # 30-day window before D: 03-11 .. 04-08 all have a D-7 lag (history
        # from 03-01), so every window row is complete.
        strategy = LightGbmStrategy(temperature, train_window_days=30)
        strategy.predict(D, visible(demand, D))
        root = strategy._model.booster_.dump_model()["tree_info"][0]["tree_structure"]
        n_rows = root["internal_count"] if "internal_count" in root else root["leaf_count"]
        assert n_rows == 29 * 48  # 03-11 .. 04-08 inclusive
        assert strategy._trained_through == pd.Timestamp("2024-04-08")


WINDOW_START = pd.Timestamp("2024-04-01")
WINDOW_END = pd.Timestamp("2024-04-14")


class TestBacktestEvalAndEvaluate:
    @pytest.fixture(scope="class")
    def backtested(self, demand, temperature) -> tuple[LightGbmStrategy, BacktestRun]:
        strategy = LightGbmStrategy(temperature, train_window_days=30, refit_every_days=7)
        return strategy, run_backtest(strategy, demand, WINDOW_START, WINDOW_END)

    def test_backtest_covers_the_window(self, backtested):
        _, run = backtested
        assert isinstance(run.result, DemandBacktestResult)
        assert run.skipped_days == ()
        assert len(run.result) == 14 * 48

    def test_eval_set_replays_the_forecasts(self, backtested, demand):
        strategy, run = backtested
        eval_set = strategy.build_eval_set(demand, WINDOW_START, WINDOW_END, run=run)
        assert type(eval_set) is DemandLightGbmEvalSet
        assert len(eval_set) == 14 * 48
        merged = eval_set.df.merge(
            run.result.df,
            how="inner",
            on=["trade_date", "time_code"],
            suffixes=("", "_bt"),
            validate="one_to_one",
        )
        assert merged["forecast_demand_kwh"].equals(merged["forecast_demand_kwh_bt"])
        assert merged["actual_demand_kwh"].equals(merged["actual_demand_kwh_bt"])

    def test_evaluate_logs_metrics_temperature_params_and_plots(self, backtested, demand):
        strategy, run = backtested
        eval_set = strategy.build_eval_set(demand, WINDOW_START, WINDOW_END, run=run)
        with mlflow.start_run() as active:
            evaluation = strategy.evaluate(eval_set, explainability_nsamples=20)
        finished = mlflow.get_run(active.info.run_id)
        assert evaluation.metrics["mean_absolute_error"] >= 0
        assert "mape_excl_zero_actuals" in evaluation.metrics
        params = finished.data.params
        assert params["lgbm_feature_cols"] == ",".join(FEATURE_COLS)
        assert params["temperature_lag_days"] == "2,3,4,5,6,7,8"
        assert params["temperature_half_life_days"] == "1.0"
        assert params["lgbm_categorical_feature_cols"] == "none"
        assert finished.data.metrics["n_refits"] == 2.0
        artifacts = {a.path for a in mlflow.MlflowClient().list_artifacts(active.info.run_id)}
        assert {"shap_beeswarm_plot.png", "shap_feature_importance_plot.png"} <= artifacts


class TestMsmClassAttributes:
    def test_features_and_frames(self):
        assert LightGbmMsmStrategy.name == "lightgbm_msm"
        assert LightGbmMsmStrategy.task.name == "demand"
        assert issubclass(LightGbmMsmStrategy, LightGbmStrategy)
        assert MSM_FEATURE_COLS == (*FEATURE_COLS, FORECAST_TEMPERATURE_FEATURE)
        assert LightGbmMsmStrategy.feature_cols == MSM_FEATURE_COLS
        assert LightGbmMsmStrategy.eval_set_cls is DemandLightGbmMsmEvalSet
        assert LightGbmMsmStrategy.lookback_days == LightGbmStrategy.lookback_days
        assert issubclass(DemandLightGbmMsmEvalSet, DemandLightGbmEvalSet)
        assert DemandLightGbmMsmEvalSet.feature_cols == MSM_FEATURE_COLS
        assert list(DemandLightGbmMsmEvalSet.schema) == [
            "trade_date",
            "time_code",
            "month",
            "day_of_week",
            TEMPERATURE_FEATURE,
            DEMAND_LAG_FEATURE,
            FORECAST_TEMPERATURE_FEATURE,
            "actual_demand_kwh",
            "forecast_demand_kwh",
        ]
        assert DemandLightGbmMsmEvalSet.schema[FORECAST_TEMPERATURE_FEATURE] == "float64"
        assert FORECAST_TEMPERATURE_FEATURE in DemandLightGbmMsmEvalSet.non_null_cols


class TestMsmPredict:
    def test_features_are_the_baselines_plus_the_delivery_day_forecast_temperature(
        self, demand, temperature, forecast_temperature
    ):
        strategy = LightGbmMsmStrategy(temperature, forecast_temperature, train_window_days=30)
        forecast = strategy.predict(D, visible(demand, D))
        assert isinstance(forecast, DemandForecast)
        assert np.isfinite(forecast.df["forecast_demand_kwh"]).all()
        record = strategy._shap_records[pd.Timestamp(D).as_unit("ns")]
        assert list(record.columns) == [
            "trade_date",
            "time_code",
            "month",
            "day_of_week",
            TEMPERATURE_FEATURE,
            DEMAND_LAG_FEATURE,
            FORECAST_TEMPERATURE_FEATURE,
            *[f"shap_{c}" for c in MSM_FEATURE_COLS],
            "shap_expected_value",
        ]
        # The baseline features are unchanged ...
        assert record[DEMAND_LAG_FEATURE].tolist() == [
            demand_at(D - pd.Timedelta(days=7), tc) for tc in range(1, 49)
        ]
        np.testing.assert_allclose(
            record[TEMPERATURE_FEATURE].to_numpy(),
            [expected_wavg(D, tc) for tc in range(1, 49)],
        )
        # ... and the new one is D's own forecast at the hour containing each period.
        hours = hour_ending_of(pd.Series(range(1, 49), dtype="int64"))
        assert record[FORECAST_TEMPERATURE_FEATURE].tolist() == [
            forecast_temperature_at(D, h) for h in hours
        ]
        np.testing.assert_allclose(
            forecast.df["forecast_demand_kwh"].to_numpy(),
            strategy._model.predict(record[list(MSM_FEATURE_COLS)].astype("float64")),
        )

    def test_missing_delivery_day_forecast_is_unforecastable(self, demand, temperature):
        without_d = make_forecast_temperature([day for day in HISTORY_DAYS if day != D])
        strategy = LightGbmMsmStrategy(temperature, without_d, train_window_days=30)
        with pytest.raises(
            ForecastUnavailableError,
            match=rf"lightgbm_msm: features \['{FORECAST_TEMPERATURE_FEATURE}'\] unavailable "
            "for 2024-04-10",
        ):
            strategy.predict(D, visible(demand, D))

    def test_training_rows_without_a_forecast_are_dropped(self, demand, temperature):
        # No forecasts before 03-20: the 30-day window before D (03-11 .. 04-08)
        # keeps only the 20 days 03-20 .. 04-08.
        late = make_forecast_temperature(pd.date_range("2024-03-20", HISTORY_DAYS[-1]))
        strategy = LightGbmMsmStrategy(temperature, late, train_window_days=30)
        strategy.predict(D, visible(demand, D))
        root = strategy._model.booster_.dump_model()["tree_info"][0]["tree_structure"]
        n_rows = root["internal_count"] if "internal_count" in root else root["leaf_count"]
        assert n_rows == 20 * 48


class TestMsmBacktestEvalAndEvaluate:
    @pytest.fixture(scope="class")
    def backtested(
        self, demand, temperature, forecast_temperature
    ) -> tuple[LightGbmMsmStrategy, BacktestRun]:
        strategy = LightGbmMsmStrategy(
            temperature, forecast_temperature, train_window_days=30, refit_every_days=7
        )
        return strategy, run_backtest(strategy, demand, WINDOW_START, WINDOW_END)

    def test_backtest_covers_the_window(self, backtested):
        _, run = backtested
        assert isinstance(run.result, DemandBacktestResult)
        assert run.skipped_days == ()
        assert len(run.result) == 14 * 48

    def test_eval_set_carries_the_forecast_temperature(self, backtested, demand):
        strategy, run = backtested
        eval_set = strategy.build_eval_set(demand, WINDOW_START, WINDOW_END, run=run)
        assert type(eval_set) is DemandLightGbmMsmEvalSet
        assert len(eval_set) == 14 * 48
        hours = hour_ending_of(eval_set.df["time_code"])
        expected = [
            forecast_temperature_at(day, h) for day, h in zip(eval_set.df["trade_date"], hours)
        ]
        assert eval_set.df[FORECAST_TEMPERATURE_FEATURE].tolist() == expected
        merged = eval_set.df.merge(
            run.result.df,
            how="inner",
            on=["trade_date", "time_code"],
            suffixes=("", "_bt"),
            validate="one_to_one",
        )
        assert merged["forecast_demand_kwh"].equals(merged["forecast_demand_kwh_bt"])

    def test_evaluate_logs_the_extended_feature_list(self, backtested, demand):
        strategy, run = backtested
        eval_set = strategy.build_eval_set(demand, WINDOW_START, WINDOW_END, run=run)
        with mlflow.start_run() as active:
            evaluation = strategy.evaluate(eval_set, explainability_nsamples=20)
        finished = mlflow.get_run(active.info.run_id)
        assert evaluation.metrics["mean_absolute_error"] >= 0
        params = finished.data.params
        assert params["lgbm_feature_cols"] == ",".join(MSM_FEATURE_COLS)
        assert params["temperature_lag_days"] == "2,3,4,5,6,7,8"
        assert finished.data.metrics["n_refits"] == 2.0
        artifacts = {a.path for a in mlflow.MlflowClient().list_artifacts(active.info.run_id)}
        assert {"shap_beeswarm_plot.png", "shap_feature_importance_plot.png"} <= artifacts


class TestMsmPopWeightedClassAttributes:
    def test_features_and_frames(self):
        assert LightGbmMsmPopWeightedStrategy.name == "lightgbm_msm_popw"
        assert issubclass(LightGbmMsmPopWeightedStrategy, LightGbmMsmStrategy)
        assert MSM_POPW_FEATURE_COLS == (*FEATURE_COLS, POPW_FORECAST_TEMPERATURE_FEATURE)
        assert LightGbmMsmPopWeightedStrategy.feature_cols == MSM_POPW_FEATURE_COLS
        assert LightGbmMsmPopWeightedStrategy.forecast_feature == POPW_FORECAST_TEMPERATURE_FEATURE
        assert LightGbmMsmStrategy.forecast_feature == FORECAST_TEMPERATURE_FEATURE
        assert LightGbmMsmPopWeightedStrategy.eval_set_cls is DemandLightGbmMsmPopWeightedEvalSet
        assert issubclass(DemandLightGbmMsmPopWeightedEvalSet, DemandLightGbmEvalSet)
        assert DemandLightGbmMsmPopWeightedEvalSet.feature_cols == MSM_POPW_FEATURE_COLS
        assert list(DemandLightGbmMsmPopWeightedEvalSet.schema) == [
            "trade_date",
            "time_code",
            "month",
            "day_of_week",
            TEMPERATURE_FEATURE,
            DEMAND_LAG_FEATURE,
            POPW_FORECAST_TEMPERATURE_FEATURE,
            "actual_demand_kwh",
            "forecast_demand_kwh",
        ]
        assert FORECAST_TEMPERATURE_FEATURE not in DemandLightGbmMsmPopWeightedEvalSet.schema


class TestMsmPopWeightedPredictAndEvaluate:
    def test_the_weighted_forecast_replaces_the_single_station_feature(
        self, demand, temperature, forecast_temperature
    ):
        strategy = LightGbmMsmPopWeightedStrategy(
            temperature, forecast_temperature, census_year=2020, train_window_days=30
        )
        forecast = strategy.predict(D, visible(demand, D))
        assert isinstance(forecast, DemandForecast)
        record = strategy._shap_records[pd.Timestamp(D).as_unit("ns")]
        assert list(record.columns) == [
            "trade_date",
            "time_code",
            "month",
            "day_of_week",
            TEMPERATURE_FEATURE,
            DEMAND_LAG_FEATURE,
            POPW_FORECAST_TEMPERATURE_FEATURE,
            *[f"shap_{c}" for c in MSM_POPW_FEATURE_COLS],
            "shap_expected_value",
        ]
        hours = hour_ending_of(pd.Series(range(1, 49), dtype="int64"))
        assert record[POPW_FORECAST_TEMPERATURE_FEATURE].tolist() == [
            forecast_temperature_at(D, h) for h in hours
        ]

    def test_evaluate_logs_the_census_year_and_feature_list(
        self, demand, temperature, forecast_temperature
    ):
        strategy = LightGbmMsmPopWeightedStrategy(
            temperature,
            forecast_temperature,
            census_year=2020,
            train_window_days=30,
            refit_every_days=7,
        )
        run = run_backtest(strategy, demand, WINDOW_START, WINDOW_END)
        eval_set = strategy.build_eval_set(demand, WINDOW_START, WINDOW_END, run=run)
        assert type(eval_set) is DemandLightGbmMsmPopWeightedEvalSet
        with mlflow.start_run() as active:
            strategy.evaluate(eval_set, explainability_nsamples=20)
        params = mlflow.get_run(active.info.run_id).data.params
        assert params["lgbm_feature_cols"] == ",".join(MSM_POPW_FEATURE_COLS)
        assert params["population_weight_census_year"] == "2020"
        assert params["temperature_lag_days"] == "2,3,4,5,6,7,8"


#: Holidays inside HISTORY_DAYS: 春分の日 2024-03-20 (a Wednesday) and 昭和の日
#: 2024-04-29 (a Monday) — the real ones.
HOLIDAYS = (pd.Timestamp("2024-03-20"), pd.Timestamp("2024-04-29"))


def day_type_at(day: pd.Timestamp) -> int:
    if day in HOLIDAYS:
        return 2
    return 1 if day.dayofweek >= 5 else 0


def make_day_types(days=HISTORY_DAYS) -> DayTypeCalendar:
    return DayTypeCalendar.from_df(
        pd.DataFrame(
            {
                "trade_date": list(days),
                "day_type": np.array([day_type_at(day) for day in days], dtype="int64"),
            }
        )
    )


@pytest.fixture(scope="module")
def day_types() -> DayTypeCalendar:
    return make_day_types()


class TestMsmPopWeightedDayTypeClassAttributes:
    def test_features_and_frames(self):
        assert LightGbmMsmPopWeightedDayTypeStrategy.name == "lightgbm_msm_popw_daytype"
        assert issubclass(LightGbmMsmPopWeightedDayTypeStrategy, LightGbmMsmPopWeightedStrategy)
        assert MSM_POPW_DAY_TYPE_FEATURE_COLS == (*MSM_POPW_FEATURE_COLS, DAY_TYPE_FEATURE)
        assert LightGbmMsmPopWeightedDayTypeStrategy.feature_cols == MSM_POPW_DAY_TYPE_FEATURE_COLS
        assert LightGbmMsmPopWeightedDayTypeStrategy.categorical_feature_cols == (DAY_TYPE_FEATURE,)
        assert (
            LightGbmMsmPopWeightedDayTypeStrategy.forecast_feature
            == POPW_FORECAST_TEMPERATURE_FEATURE
        )
        assert LightGbmMsmPopWeightedDayTypeStrategy.lookback_days == LightGbmStrategy.lookback_days
        assert (
            LightGbmMsmPopWeightedDayTypeStrategy.eval_set_cls
            is DemandLightGbmMsmPopWeightedDayTypeEvalSet
        )
        assert issubclass(
            DemandLightGbmMsmPopWeightedDayTypeEvalSet, DemandLightGbmMsmPopWeightedEvalSet
        )
        assert (
            DemandLightGbmMsmPopWeightedDayTypeEvalSet.feature_cols
            == MSM_POPW_DAY_TYPE_FEATURE_COLS
        )
        assert list(DemandLightGbmMsmPopWeightedDayTypeEvalSet.schema) == [
            "trade_date",
            "time_code",
            "month",
            "day_of_week",
            TEMPERATURE_FEATURE,
            DEMAND_LAG_FEATURE,
            POPW_FORECAST_TEMPERATURE_FEATURE,
            DAY_TYPE_FEATURE,
            "actual_demand_kwh",
            "forecast_demand_kwh",
        ]
        assert DemandLightGbmMsmPopWeightedDayTypeEvalSet.schema[DAY_TYPE_FEATURE] == "int64"
        assert DAY_TYPE_FEATURE in DemandLightGbmMsmPopWeightedDayTypeEvalSet.non_null_cols

    def test_the_other_strategies_declare_no_categorical_features(self):
        for cls in (LightGbmStrategy, LightGbmMsmStrategy, LightGbmMsmPopWeightedStrategy):
            assert cls.categorical_feature_cols == ()


class TestMsmPopWeightedDayTypePredict:
    def make_strategy(self, temperature, forecast_temperature, day_types, **kwargs):
        return LightGbmMsmPopWeightedDayTypeStrategy(
            temperature,
            forecast_temperature,
            day_types,
            census_year=2020,
            train_window_days=30,
            **kwargs,
        )

    def test_features_are_the_weighted_ones_plus_the_delivery_days_type(
        self, demand, temperature, forecast_temperature, day_types
    ):
        strategy = self.make_strategy(temperature, forecast_temperature, day_types)
        forecast = strategy.predict(D, visible(demand, D))
        assert isinstance(forecast, DemandForecast)
        assert np.isfinite(forecast.df["forecast_demand_kwh"]).all()
        record = strategy._shap_records[pd.Timestamp(D).as_unit("ns")]
        assert list(record.columns) == [
            "trade_date",
            "time_code",
            "month",
            "day_of_week",
            TEMPERATURE_FEATURE,
            DEMAND_LAG_FEATURE,
            POPW_FORECAST_TEMPERATURE_FEATURE,
            DAY_TYPE_FEATURE,
            *[f"shap_{c}" for c in MSM_POPW_DAY_TYPE_FEATURE_COLS],
            "shap_expected_value",
        ]
        # D is a plain Wednesday.
        assert record[DAY_TYPE_FEATURE].eq(0).all()
        hours = hour_ending_of(pd.Series(range(1, 49), dtype="int64"))
        assert record[POPW_FORECAST_TEMPERATURE_FEATURE].tolist() == [
            forecast_temperature_at(D, h) for h in hours
        ]
        assert record[DEMAND_LAG_FEATURE].tolist() == [
            demand_at(D - pd.Timedelta(days=7), tc) for tc in range(1, 49)
        ]
        np.testing.assert_allclose(
            forecast.df["forecast_demand_kwh"].to_numpy(),
            strategy._model.predict(record[list(MSM_POPW_DAY_TYPE_FEATURE_COLS)].astype("float64")),
        )
        reconstructed = record[list(strategy.shap_cols)].sum(axis=1) + record["shap_expected_value"]
        np.testing.assert_allclose(
            reconstructed.to_numpy(), forecast.df["forecast_demand_kwh"].to_numpy(), atol=1e-3
        )

    def test_a_holiday_is_coded_2_and_a_weekend_day_1(
        self, demand, temperature, forecast_temperature, day_types
    ):
        strategy = self.make_strategy(temperature, forecast_temperature, day_types)
        holiday = pd.Timestamp("2024-04-29")  # 昭和の日, a Monday
        strategy.predict(holiday, visible(demand, holiday))
        assert strategy._shap_records[holiday][DAY_TYPE_FEATURE].eq(2).all()
        saturday = pd.Timestamp("2024-04-27")
        strategy.predict(saturday, visible(demand, saturday))
        assert strategy._shap_records[saturday][DAY_TYPE_FEATURE].eq(1).all()

    def test_lightgbm_is_told_day_type_is_categorical(
        self, demand, temperature, forecast_temperature, day_types
    ):
        strategy = self.make_strategy(temperature, forecast_temperature, day_types)
        strategy.predict(D, visible(demand, D))
        booster = strategy._model.booster_
        assert booster.dump_model()["feature_names"] == list(MSM_POPW_DAY_TYPE_FEATURE_COLS)
        assert booster.params["categorical_column"] == [
            MSM_POPW_DAY_TYPE_FEATURE_COLS.index(DAY_TYPE_FEATURE)
        ]

    def test_the_baseline_strategies_fit_without_categorical_columns(self, demand, temperature):
        strategy = LightGbmStrategy(temperature, train_window_days=30)
        strategy.predict(D, visible(demand, D))
        assert "categorical_column" not in strategy._model.booster_.params

    def test_missing_calendar_day_is_unforecastable(
        self, demand, temperature, forecast_temperature
    ):
        without_d = make_day_types([day for day in HISTORY_DAYS if day != D])
        strategy = self.make_strategy(temperature, forecast_temperature, without_d)
        with pytest.raises(
            ForecastUnavailableError,
            match=rf"lightgbm_msm_popw_daytype: features \['{DAY_TYPE_FEATURE}'\] unavailable "
            "for 2024-04-10",
        ):
            strategy.predict(D, visible(demand, D))

    def test_training_rows_without_a_calendar_day_are_dropped(
        self, demand, temperature, forecast_temperature
    ):
        # No calendar before 03-20: the 30-day window before D (03-11 .. 04-08)
        # keeps only the 20 days 03-20 .. 04-08.
        late = make_day_types(pd.date_range("2024-03-20", HISTORY_DAYS[-1]))
        strategy = self.make_strategy(temperature, forecast_temperature, late)
        strategy.predict(D, visible(demand, D))
        root = strategy._model.booster_.dump_model()["tree_info"][0]["tree_structure"]
        n_rows = root["internal_count"] if "internal_count" in root else root["leaf_count"]
        assert n_rows == 20 * 48


class TestMsmPopWeightedDayTypeBacktestEvalAndEvaluate:
    @pytest.fixture(scope="class")
    def backtested(
        self, demand, temperature, forecast_temperature, day_types
    ) -> tuple[LightGbmMsmPopWeightedDayTypeStrategy, BacktestRun]:
        strategy = LightGbmMsmPopWeightedDayTypeStrategy(
            temperature,
            forecast_temperature,
            day_types,
            census_year=2020,
            train_window_days=30,
            refit_every_days=7,
        )
        return strategy, run_backtest(strategy, demand, WINDOW_START, WINDOW_END)

    def test_backtest_covers_the_window(self, backtested):
        _, run = backtested
        assert isinstance(run.result, DemandBacktestResult)
        assert run.skipped_days == ()
        assert len(run.result) == 14 * 48

    def test_eval_set_carries_the_day_type_as_int64(self, backtested, demand):
        strategy, run = backtested
        eval_set = strategy.build_eval_set(demand, WINDOW_START, WINDOW_END, run=run)
        assert type(eval_set) is DemandLightGbmMsmPopWeightedDayTypeEvalSet
        assert len(eval_set) == 14 * 48
        assert eval_set.df[DAY_TYPE_FEATURE].dtype == "int64"
        assert eval_set.df[DAY_TYPE_FEATURE].tolist() == [
            day_type_at(day) for day in eval_set.df["trade_date"]
        ]
        # The window (04-01 .. 04-14) has weekdays and weekends but no holiday.
        assert set(eval_set.df[DAY_TYPE_FEATURE]) == {0, 1}
        merged = eval_set.df.merge(
            run.result.df,
            how="inner",
            on=["trade_date", "time_code"],
            suffixes=("", "_bt"),
            validate="one_to_one",
        )
        assert merged["forecast_demand_kwh"].equals(merged["forecast_demand_kwh_bt"])

    def test_evaluate_logs_the_categorical_feature_and_its_levels(self, backtested, demand):
        strategy, run = backtested
        eval_set = strategy.build_eval_set(demand, WINDOW_START, WINDOW_END, run=run)
        with mlflow.start_run() as active:
            evaluation = strategy.evaluate(eval_set, explainability_nsamples=20)
        finished = mlflow.get_run(active.info.run_id)
        assert evaluation.metrics["mean_absolute_error"] >= 0
        params = finished.data.params
        assert params["lgbm_feature_cols"] == ",".join(MSM_POPW_DAY_TYPE_FEATURE_COLS)
        assert params["lgbm_categorical_feature_cols"] == DAY_TYPE_FEATURE
        assert params["day_type_levels"] == "0=Weekday,1=Weekend,2=Holiday"
        assert params["population_weight_census_year"] == "2020"
        assert params["temperature_lag_days"] == "2,3,4,5,6,7,8"
        assert finished.data.metrics["n_refits"] == 2.0
        artifacts = {a.path for a in mlflow.MlflowClient().list_artifacts(active.info.run_id)}
        assert {"shap_beeswarm_plot.png", "shap_feature_importance_plot.png"} <= artifacts
