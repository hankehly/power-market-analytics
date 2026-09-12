"""PresetLightGbmStrategy: the sliding-window LightGBM over a retrieved feature frame.

The behaviour tests of the former spot-price ``LightGbmStrategy`` /
``LightGbmOcctoStrategy``, on a synthetic price history and a feature frame
built from it (month, pandas day of week, the D-1 price lag, the three OCCTO
peak fields), so no Feast retrieval is involved.
"""

from __future__ import annotations

import math

import mlflow
import numpy as np
import pandas as pd
import pytest

from power_market_analytics.features.frame import FeatureFrame, feature_frame
from power_market_analytics.features.presets import Preset
from power_market_analytics.forecasting.backtest import BacktestRun, run_backtest
from power_market_analytics.forecasting.frames import DayAheadForecast
from power_market_analytics.forecasting.lgbm import LightGbmEvalSetBase
from power_market_analytics.forecasting.preset_lgbm import (
    PresetLightGbmStrategy,
    preset_eval_set_cls,
)
from power_market_analytics.forecasting.strategy import ForecastUnavailableError
from power_market_analytics.tasks.spot_price import TASK
from power_market_analytics.tasks.spot_price.frames import (
    SpotPriceBacktestResult,
    SpotPriceForecast,
    SpotPrices,
)
from power_market_analytics.tasks.spot_price.presets import LIGHTGBM, LIGHTGBM_OCCTO

DTYPES = {
    "month": "int64",
    "day_of_week": "int64",
    "lag_1d_price": "float64",
    "max_demand_hour_ending": "int64",
    "max_demand_mw": "int64",
    "max_supply_capacity_mw": "int64",
}


@pytest.fixture(scope="module", autouse=True)
def experiment() -> None:
    mlflow.set_experiment("test_forecasting_preset_lgbm")


# --------------------------------------------------------------------------- synthetic history

HISTORY_START = pd.Timestamp("2024-03-01")
#: 45 days: 2024-03-01 .. 2024-04-14.
HISTORY_DAYS = pd.date_range(HISTORY_START, periods=45, freq="D")
D = pd.Timestamp("2024-04-10")  # a Wednesday


def price_at(day: pd.Timestamp, time_code: int) -> float:
    """Deterministic price: daily sine shape, slow upward drift, weekend step."""
    day_index = (day - HISTORY_START).days
    shape = 10.0 + 5.0 * math.sin(2 * math.pi * (time_code - 1) / 48)
    weekend = 0.7 if day.dayofweek >= 5 else 0.0
    return round(shape + 0.05 * day_index + weekend, 2)


def make_prices(days=HISTORY_DAYS) -> SpotPrices:
    return SpotPrices.from_df(
        pd.DataFrame(
            [
                {"trade_date": day, "time_code": tc, "price_jpy_kwh": price_at(day, tc)}
                for day in days
                for tc in range(1, 49)
            ]
        )
    )


def history_before(prices: SpotPrices, day: pd.Timestamp) -> SpotPrices:
    return SpotPrices.from_df(prices.df[prices.df["trade_date"] < day])


def make_features(
    days=HISTORY_DAYS, *, lag_days=None, occto_days=None, columns=LIGHTGBM.columns
) -> FeatureFrame:
    """The preset's features for ``days``: the lag where ``lag_days`` has the previous day."""
    lag_days = HISTORY_DAYS if lag_days is None else lag_days
    occto_days = HISTORY_DAYS if occto_days is None else occto_days
    rows = []
    for day in days:
        day_index = (day - HISTORY_START).days
        for tc in range(1, 49):
            previous = day - pd.Timedelta(days=1)
            rows.append(
                {
                    "trade_date": day,
                    "time_code": tc,
                    "month": day.month,
                    "day_of_week": day.dayofweek,
                    "lag_1d_price": price_at(previous, tc) if previous in lag_days else np.nan,
                    "max_demand_hour_ending": 17 + day_index % 3 if day in occto_days else np.nan,
                    "max_demand_mw": 40_000 + 10 * day_index if day in occto_days else np.nan,
                    "max_supply_capacity_mw": (
                        46_000 + 10 * day_index if day in occto_days else np.nan
                    ),
                }
            )
    return feature_frame(pd.DataFrame(rows), columns)


def strategy_for(
    preset: Preset = LIGHTGBM, features: FeatureFrame | None = None, **kwargs
) -> PresetLightGbmStrategy:
    features = make_features(columns=preset.columns) if features is None else features
    return PresetLightGbmStrategy(TASK, preset, features, dtypes=DTYPES, **kwargs)


def training_rows(strategy: PresetLightGbmStrategy) -> int:
    model = strategy._model
    assert model is not None
    root = model.booster_.dump_model()["tree_info"][0]["tree_structure"]
    return root["internal_count"] if "internal_count" in root else root["leaf_count"]


def _nodes(node: dict):
    """Every split node of a dumped LightGBM tree, depth first."""
    if "split_feature" in node:
        yield node
        yield from _nodes(node["left_child"])
        yield from _nodes(node["right_child"])


@pytest.fixture(scope="module")
def prices() -> SpotPrices:
    return make_prices()


# --------------------------------------------------------------------------- eval-set class


class TestPresetEvalSetCls:
    def test_schema_from_the_preset_and_task(self):
        cls = preset_eval_set_cls(TASK, LIGHTGBM, DTYPES)
        assert issubclass(cls, LightGbmEvalSetBase)
        assert cls.feature_cols == ("time_code", "month", "day_of_week", "lag_1d_price")
        assert cls.target_col == "actual_price_jpy_kwh"
        assert cls.forecast_col == "forecast_price_jpy_kwh"
        # A feature may be null, so an integer feature is pandas' nullable Int64.
        assert cls.schema == {
            "trade_date": "datetime64[ns]",
            "time_code": "int64",
            "month": "Int64",
            "day_of_week": "Int64",
            "lag_1d_price": "float64",
            "actual_price_jpy_kwh": "float64",
            "forecast_price_jpy_kwh": "float64",
        }
        assert cls.non_null_cols == [cls.target_col, cls.forecast_col]
        assert "lightgbm" in (cls.__doc__ or "")

    def test_to_eval_frame_drops_trade_date_and_casts_to_float(self):
        eval_set = preset_eval_set_cls(TASK, LIGHTGBM, DTYPES).from_df(
            pd.DataFrame(
                {
                    "trade_date": pd.to_datetime(["2024-04-10", "2024-04-10"]),
                    "time_code": [1, 2],
                    "month": pd.array([4, 4], dtype="Int64"),
                    "day_of_week": pd.array([2, None], dtype="Int64"),
                    "lag_1d_price": [10.5, 11.0],
                    "actual_price_jpy_kwh": [10.0, 12.0],
                    "forecast_price_jpy_kwh": [10.25, 11.5],
                }
            )
        )
        frame = eval_set.to_eval_frame()
        assert list(frame.columns) == [
            "time_code",
            "month",
            "day_of_week",
            "lag_1d_price",
            "actual_price_jpy_kwh",
            "forecast_price_jpy_kwh",
        ]
        assert set(frame.dtypes.astype(str)) == {"float64"}
        assert frame.iloc[0].tolist() == [1.0, 4.0, 2.0, 10.5, 10.0, 10.25]
        assert np.isnan(frame.iloc[1]["day_of_week"])

    def test_a_null_feature_is_accepted(self):
        eval_set = preset_eval_set_cls(TASK, LIGHTGBM_OCCTO, DTYPES).from_df(
            pd.DataFrame(
                {
                    "trade_date": pd.to_datetime(["2024-04-10"]),
                    "time_code": [1],
                    "month": pd.array([4], dtype="Int64"),
                    "day_of_week": pd.array([2], dtype="Int64"),
                    "lag_1d_price": [np.nan],
                    "max_demand_hour_ending": pd.array([18], dtype="Int64"),
                    "max_demand_mw": pd.array([None], dtype="Int64"),
                    "max_supply_capacity_mw": pd.array([46_000], dtype="Int64"),
                    "actual_price_jpy_kwh": [10.0],
                    "forecast_price_jpy_kwh": [10.25],
                }
            )
        )
        assert eval_set.df[["lag_1d_price", "max_demand_mw"]].isna().all().all()

    def test_a_null_forecast_is_rejected(self):
        with pytest.raises(ValueError, match="'forecast_price_jpy_kwh' has 1 null values"):
            preset_eval_set_cls(TASK, LIGHTGBM, DTYPES).from_df(
                pd.DataFrame(
                    {
                        "trade_date": pd.to_datetime(["2024-04-10"]),
                        "time_code": [1],
                        "month": pd.array([4], dtype="Int64"),
                        "day_of_week": pd.array([2], dtype="Int64"),
                        "lag_1d_price": [10.5],
                        "actual_price_jpy_kwh": [10.0],
                        "forecast_price_jpy_kwh": [np.nan],
                    }
                )
            )


# --------------------------------------------------------------------------- construction


class TestInit:
    def test_defaults_come_from_the_preset(self):
        strategy = strategy_for()
        assert strategy.name == "lightgbm"
        assert strategy.task is TASK
        assert strategy.preset is LIGHTGBM
        assert strategy.feature_cols == ("time_code", "month", "day_of_week", "lag_1d_price")
        assert strategy.categorical_feature_cols == ()
        assert strategy.lookback_days == 0
        assert strategy.train_window_days == 730
        assert strategy.refit_every_days == 7
        assert strategy.train_start_date is None
        assert strategy.shap_cols == (
            "shap_time_code",
            "shap_month",
            "shap_day_of_week",
            "shap_lag_1d_price",
        )
        assert strategy._extra_params() == {
            "feature_preset": "lightgbm",
            "feature_preset_base": "none",
            "feature_refs": (
                "ftr_day_calendar:month,ftr_day_calendar:day_of_week,ftr_period_jepx:lag_1d_price"
            ),
        }

    def test_categorical_columns_are_passed_through(self, prices):
        strategy = strategy_for(train_window_days=30, categorical=("day_of_week",))
        assert strategy.categorical_feature_cols == ("day_of_week",)
        forecast = strategy.predict(D, history_before(prices, D))
        assert np.isfinite(forecast.df["forecast_price_jpy_kwh"]).all()

    def test_a_categorical_that_is_not_a_feature_is_rejected(self):
        with pytest.raises(
            ValueError, match=r"categorical columns \['day_type'\] are not features"
        ):
            strategy_for(categorical=("day_type",))

    def test_name_overrides_the_label_and_train_start_is_normalised(self):
        strategy = strategy_for(name="lightgbm_x", train_start_date="2024-04-01")
        assert strategy.name == "lightgbm_x"
        assert strategy.preset.name == "lightgbm"
        assert strategy.train_start_date == pd.Timestamp("2024-04-01")
        assert strategy.train_start_date.unit == "ns"

    def test_occto_preset_appends_its_features(self):
        strategy = strategy_for(LIGHTGBM_OCCTO)
        assert strategy.name == "lightgbm_occto"
        assert strategy.feature_cols[-3:] == (
            "max_demand_hour_ending",
            "max_demand_mw",
            "max_supply_capacity_mw",
        )

    def test_a_frame_without_the_presets_columns_is_rejected(self):
        with pytest.raises(ValueError, match=r"lightgbm_occto: the feature frame lacks columns"):
            strategy_for(LIGHTGBM_OCCTO, features=make_features(columns=LIGHTGBM.columns))


# --------------------------------------------------------------------------- predict


@pytest.fixture(scope="module")
def fitted(prices: SpotPrices) -> tuple[PresetLightGbmStrategy, DayAheadForecast]:
    strategy = strategy_for(train_window_days=30, refit_every_days=7)
    forecast = strategy.predict(D, history_before(prices, D))
    return strategy, forecast


class TestPredict:
    def test_returns_48_finite_prices_for_the_target_day(self, fitted):
        _, forecast = fitted
        assert isinstance(forecast, SpotPriceForecast)
        assert len(forecast) == 48
        assert forecast.df["trade_date"].eq(D).all()
        assert forecast.df["time_code"].tolist() == list(range(1, 49))
        assert np.isfinite(forecast.df["forecast_price_jpy_kwh"]).all()

    def test_records_the_frames_features_for_the_scored_rows(self, fitted):
        strategy, forecast = fitted
        record = strategy._shap_records[D]
        assert list(record.columns) == [
            "trade_date",
            "time_code",
            "month",
            "day_of_week",
            "lag_1d_price",
            "shap_time_code",
            "shap_month",
            "shap_day_of_week",
            "shap_lag_1d_price",
            "shap_expected_value",
        ]
        assert record["month"].eq(4).all() and record["day_of_week"].eq(2).all()
        assert record["lag_1d_price"].tolist() == [
            price_at(D - pd.Timedelta(days=1), tc) for tc in range(1, 49)
        ]
        features = record[list(strategy.feature_cols)].astype("float64")
        np.testing.assert_allclose(
            forecast.df["forecast_price_jpy_kwh"].to_numpy(), strategy._model.predict(features)
        )

    def test_shap_contributions_add_up_to_the_forecast(self, fitted):
        strategy, forecast = fitted
        record = strategy._shap_records[D]
        reconstructed = record[list(strategy.shap_cols)].sum(axis=1) + record["shap_expected_value"]
        np.testing.assert_allclose(
            reconstructed.to_numpy(), forecast.df["forecast_price_jpy_kwh"].to_numpy(), atol=1e-6
        )

    def test_a_feature_missing_for_the_target_day_is_forecast_with_the_null(self, prices):
        # The frame has no D-1 lag for D (the previous day never happened).
        features = make_features(lag_days=HISTORY_DAYS[HISTORY_DAYS < D - pd.Timedelta(days=1)])
        strategy = strategy_for(features=features, train_window_days=30)
        forecast = strategy.predict(D, history_before(prices, D))
        assert len(forecast) == 48
        assert np.isfinite(forecast.df["forecast_price_jpy_kwh"]).all()
        record = strategy._shap_records[D]
        assert record["lag_1d_price"].isna().all()
        reconstructed = record[list(strategy.shap_cols)].sum(axis=1) + record["shap_expected_value"]
        np.testing.assert_allclose(
            reconstructed.to_numpy(), forecast.df["forecast_price_jpy_kwh"].to_numpy(), atol=1e-6
        )

    def test_occto_features_missing_for_the_target_day_are_forecast_with_the_nulls(self, prices):
        features = make_features(
            occto_days=HISTORY_DAYS[HISTORY_DAYS < D], columns=LIGHTGBM_OCCTO.columns
        )
        strategy = strategy_for(LIGHTGBM_OCCTO, features=features, train_window_days=30)
        forecast = strategy.predict(D, history_before(prices, D))
        assert np.isfinite(forecast.df["forecast_price_jpy_kwh"]).all()

    def test_a_missing_feature_is_not_read_as_zero(self, prices):
        # Training rows with the lag missing teach the trees where a missing
        # lag goes. Dropping them would leave LightGBM no missing branch, and
        # it would then score a missing lag as 0.0.
        gap = pd.Timestamp("2024-03-20")
        features = make_features(lag_days=HISTORY_DAYS[HISTORY_DAYS != gap - pd.Timedelta(days=1)])
        strategy = strategy_for(features=features, train_window_days=30)
        strategy.predict(D, history_before(prices, D))
        splits = [
            node
            for tree in strategy._model.booster_.dump_model()["tree_info"]
            for node in _nodes(tree["tree_structure"])
            if node.get("split_feature") == strategy.feature_cols.index("lag_1d_price")
        ]
        assert splits and {node["missing_type"] for node in splits} == {"NaN"}

    def test_a_target_day_outside_the_frame_is_unforecastable(self, prices):
        strategy = strategy_for(features=make_features(HISTORY_DAYS[HISTORY_DAYS < D]))
        with pytest.raises(
            ForecastUnavailableError, match="lightgbm: no feature values for 2024-04-10"
        ):
            strategy.predict(D, history_before(prices, D))


# --------------------------------------------------------------------------- refit schedule


class TestEnsureFitted:
    def test_first_predict_fits_on_the_trailing_window(self, prices):
        strategy = strategy_for(train_window_days=30, refit_every_days=7)
        strategy.predict(D, history_before(prices, D))
        assert strategy._n_fits == 1
        assert strategy._trained_through == pd.Timestamp("2024-04-09")
        assert strategy._fit_anchor == D
        assert training_rows(strategy) == 30 * 48

    def test_next_day_within_cadence_reuses_the_model(self, prices):
        strategy = strategy_for(train_window_days=30, refit_every_days=7)
        strategy.predict(D, history_before(prices, D))
        model = strategy._model
        for offset in (1, 4):
            day = D + pd.Timedelta(days=offset)
            strategy.predict(day, history_before(prices, day))
        assert strategy._n_fits == 1 and strategy._model is model

    def test_refits_once_the_cadence_has_elapsed(self, prices):
        strategy = strategy_for(train_window_days=30, refit_every_days=3)
        strategy.predict(D, history_before(prices, D))
        day = D + pd.Timedelta(days=3)
        strategy.predict(day, history_before(prices, day))
        assert strategy._n_fits == 2 and strategy._trained_through == pd.Timestamp("2024-04-12")

    def test_train_start_date_bounds_the_training_rows(self, prices):
        strategy = strategy_for(train_window_days=30, train_start_date="2024-04-07")
        strategy.predict(D, history_before(prices, D))
        assert training_rows(strategy) == 3 * 48

    def test_train_start_date_after_the_history_raises(self, prices):
        strategy = strategy_for(train_window_days=30, train_start_date=D)
        with pytest.raises(
            ForecastUnavailableError,
            match="lightgbm: no training rows with a feature value in the 30 days before 2024-04-10",
        ):
            strategy.predict(D, history_before(prices, D))

    def test_training_rows_with_a_missing_feature_are_kept(self, prices):
        # No lag for the window's first day: all 30 window days still train.
        strategy = strategy_for(
            features=make_features(lag_days=HISTORY_DAYS[1:]), train_window_days=30
        )
        first = HISTORY_START + pd.Timedelta(days=31)
        strategy.predict(first, history_before(prices, first))
        assert training_rows(strategy) == 30 * 48

    def test_training_rows_without_any_feature_value_are_dropped(self, prices):
        # The frame starts on 2024-03-06: the window's first four days (03-02 .. 03-05)
        # have no feature at all and carry nothing but the period.
        strategy = strategy_for(features=make_features(HISTORY_DAYS[5:]), train_window_days=30)
        first = HISTORY_START + pd.Timedelta(days=31)
        strategy.predict(first, history_before(prices, first))
        assert training_rows(strategy) == 26 * 48


# --------------------------------------------------------------------------- build_eval_set

WINDOW_START = pd.Timestamp("2024-04-01")
WINDOW_END = pd.Timestamp("2024-04-14")


@pytest.fixture(scope="module")
def backtested(prices: SpotPrices) -> tuple[PresetLightGbmStrategy, BacktestRun]:
    strategy = strategy_for(train_window_days=30, refit_every_days=7)
    run = run_backtest(strategy, prices, WINDOW_START, WINDOW_END)
    return strategy, run


def hand_backtest_run() -> BacktestRun:
    """A minimal, valid BacktestRun for tests that only need *some* run."""
    result = SpotPriceBacktestResult.from_df(
        pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2024-04-01"]),
                "time_code": [1],
                "actual_price_jpy_kwh": [10.0],
                "forecast_price_jpy_kwh": [10.5],
            }
        )
    )
    return BacktestRun(result=result, skipped_days=())


def one_row_eval_set() -> LightGbmEvalSetBase:
    return preset_eval_set_cls(TASK, LIGHTGBM, DTYPES).from_df(
        pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2024-04-10"]),
                "time_code": [1],
                "month": pd.array([4], dtype="Int64"),
                "day_of_week": pd.array([2], dtype="Int64"),
                "lag_1d_price": [10.5],
                "actual_price_jpy_kwh": [10.0],
                "forecast_price_jpy_kwh": [10.25],
            }
        )
    )


class TestBuildEvalSet:
    def test_requires_the_backtest_result(self, prices):
        with pytest.raises(ValueError, match="lightgbm: build_eval_set requires the backtest run"):
            strategy_for().build_eval_set(prices, WINDOW_START, WINDOW_END)

    def test_window_with_only_skipped_days_raises(self, prices):
        skipped = BacktestRun(result=hand_backtest_run().result, skipped_days=(WINDOW_START,))
        with pytest.raises(
            ValueError, match="lightgbm: no scored rows between 2024-04-01 and 2024-04-01"
        ):
            strategy_for().build_eval_set(prices, WINDOW_START, WINDOW_START, run=skipped)

    def test_replays_the_backtest_forecasts_onto_the_feature_rows(self, backtested, prices):
        strategy, run = backtested
        eval_set = strategy.build_eval_set(prices, WINDOW_START, WINDOW_END, run=run)
        assert type(eval_set) is strategy.eval_set_cls
        assert len(eval_set) == 14 * 48
        assert eval_set.df.dtypes.astype(str).to_dict() == strategy.eval_set_cls.schema
        merged = eval_set.df.merge(
            run.result.df,
            how="inner",
            on=["trade_date", "time_code"],
            suffixes=("", "_backtest"),
            validate="one_to_one",
        )
        assert merged["forecast_price_jpy_kwh"].equals(merged["forecast_price_jpy_kwh_backtest"])
        row = eval_set.df.set_index(["trade_date", "time_code"]).loc[
            (pd.Timestamp("2024-04-05"), 10)
        ]
        assert row["month"] == 4 and row["day_of_week"] == 4
        assert row["lag_1d_price"] == price_at(pd.Timestamp("2024-04-04"), 10)

    def test_skipped_days_are_dropped_from_the_eval_set(self, prices):
        strategy = strategy_for(train_window_days=30)
        run = run_backtest(strategy, prices, WINDOW_START, pd.Timestamp("2024-04-03"))
        skipped = BacktestRun(
            result=SpotPriceBacktestResult.from_df(
                run.result.df[run.result.df["trade_date"] != pd.Timestamp("2024-04-02")]
            ),
            skipped_days=(pd.Timestamp("2024-04-02"),),
        )
        eval_set = strategy.build_eval_set(
            prices, WINDOW_START, pd.Timestamp("2024-04-03"), run=skipped
        )
        assert len(eval_set) == 2 * 48

    def test_importance_needs_a_recorded_forecast_for_every_scored_period(self, backtested):
        strategy, run = backtested
        unscored = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2024-03-20"]),
                "time_code": [1],
                "actual_price_jpy_kwh": [10.0],
                "forecast_price_jpy_kwh": [10.5],
            }
        )
        wider = BacktestRun(
            result=SpotPriceBacktestResult.from_df(pd.concat([run.result.df, unscored])),
            skipped_days=(),
        )
        with pytest.raises(
            RuntimeError,
            match=r"lightgbm: 1 scored period\(s\) have no recorded forecast, e.g. 2024-03-20",
        ):
            strategy.permutation_importance(wider, n_repeats=1)

    def test_contributions_and_importance_use_the_presets_feature_order(self, backtested):
        strategy, run = backtested
        contributions = strategy.contributions()
        components = contributions.df["component"].unique().tolist()
        assert components == ["base", "time_code", "month", "day_of_week", "lag_1d_price"]
        importance = strategy.permutation_importance(run, n_repeats=1)
        assert importance is not None
        assert sorted(importance.df["feature"].unique()) == sorted(strategy.feature_cols)


# --------------------------------------------------------------------------- evaluate


class TestEvaluate:
    def test_before_any_backtest_raises(self):
        with pytest.raises(RuntimeError, match="lightgbm: no fitted model or recorded"):
            strategy_for().evaluate(one_row_eval_set())

    def test_unknown_keyword_arguments_raise(self, fitted):
        strategy, _ = fitted
        with pytest.raises(
            TypeError, match=r"lightgbm.evaluate got unexpected keyword arguments \['nsamples'\]"
        ):
            strategy.evaluate(one_row_eval_set(), nsamples=5)

    def test_recorded_contributions_must_cover_the_eval_rows(self, backtested, fitted, prices):
        backtested_strategy, run = backtested
        eval_set = backtested_strategy.build_eval_set(prices, WINDOW_START, WINDOW_END, run=run)
        one_day_strategy, _ = fitted
        with (
            mlflow.start_run(),
            pytest.raises(RuntimeError, match="recorded contributions cover 48 of 672 eval rows"),
        ):
            one_day_strategy.evaluate(eval_set, explainability_nsamples=20)

    def test_logs_the_preset_params_and_evaluates(self, backtested, prices):
        strategy, run = backtested
        eval_set = strategy.build_eval_set(prices, WINDOW_START, WINDOW_END, run=run)
        with mlflow.start_run():
            evaluation = strategy.evaluate(eval_set, explainability_nsamples=20)
            run_id = mlflow.active_run().info.run_id
        params = mlflow.get_run(run_id).data.params
        assert params["feature_preset"] == "lightgbm"
        assert params["lgbm_feature_cols"] == "time_code,month,day_of_week,lag_1d_price"
        assert params["feature_refs"].startswith("ftr_day_calendar:month,")
        assert "mean_absolute_error" in evaluation.metrics


# --------------------------------------------------------------------------- a feature gap

#: 2024-04-05 has no D-1 lag, nor has its month (a calendar row missing), in the frame.
GAP_DAY = pd.Timestamp("2024-04-05")


@pytest.fixture(scope="module")
def backtested_with_a_gap(prices: SpotPrices) -> tuple[PresetLightGbmStrategy, BacktestRun]:
    features = make_features(lag_days=HISTORY_DAYS[HISTORY_DAYS != GAP_DAY - pd.Timedelta(days=1)])
    df = features.df.copy()
    df.loc[(df["trade_date"] == GAP_DAY) & (df["time_code"] <= 24), "month"] = np.nan
    strategy = strategy_for(features=type(features).from_df(df), train_window_days=30)
    return strategy, run_backtest(strategy, prices, WINDOW_START, WINDOW_END)


class TestFeatureGap:
    def test_every_day_is_scored(self, backtested_with_a_gap):
        _, run = backtested_with_a_gap
        assert run.skipped_days == ()
        assert run.result.df["trade_date"].nunique() == 14
        assert np.isfinite(run.result.df["forecast_price_jpy_kwh"]).all()

    def test_contributions_carry_the_null_feature_values(self, backtested_with_a_gap):
        strategy, run = backtested_with_a_gap
        df = strategy.contributions().df
        on_gap = df[df["trade_date"] == GAP_DAY]
        assert on_gap.loc[on_gap["component"] == "lag_1d_price", "feature_value"].isna().all()
        assert on_gap.loc[on_gap["component"] == "month", "feature_value"].isna().sum() == 24
        per_period = df.groupby(["trade_date", "time_code"])["contribution"].sum().reset_index()
        merged = per_period.merge(run.result.df, on=["trade_date", "time_code"], validate="1:1")
        np.testing.assert_allclose(
            merged["contribution"], merged["forecast_price_jpy_kwh"], atol=1e-6
        )

    def test_importance_scores_the_gap_rows(self, backtested_with_a_gap):
        strategy, run = backtested_with_a_gap
        importance = strategy.permutation_importance(run, n_repeats=1)
        assert set(importance.df["n_periods"]) == {14 * 48}
        assert np.isfinite(importance.df["permuted_mae"]).all()

    def test_eval_set_keeps_the_rows_with_nulls_and_evaluates(self, backtested_with_a_gap, prices):
        strategy, run = backtested_with_a_gap
        eval_set = strategy.build_eval_set(prices, WINDOW_START, WINDOW_END, run=run)
        assert len(eval_set) == 14 * 48
        assert eval_set.df.dtypes.astype(str).to_dict() == strategy.eval_set_cls.schema
        on_gap = eval_set.df[eval_set.df["trade_date"] == GAP_DAY]
        assert on_gap["lag_1d_price"].isna().all() and on_gap["month"].isna().sum() == 24
        with mlflow.start_run():
            evaluation = strategy.evaluate(eval_set, explainability_nsamples=20)
        assert evaluation.metrics["mean_absolute_error"] == pytest.approx(
            (run.result.df["forecast_price_jpy_kwh"] - run.result.df["actual_price_jpy_kwh"])
            .abs()
            .mean()
        )
