"""Tests for the LightGBM spot-price strategies (base features + OCCTO features).

Everything runs for real — pandas feature building, LightGBM fits, TreeSHAP
contributions, MLflow logging into the session's temp file store. The price
history is a small synthetic series (a smooth daily shape plus a slow drift and
a weekend step) so the fits take a fraction of a second; predictions are never
hand-derived, so the assertions are structural (row counts, refit bookkeeping,
SHAP additivity, "the forecast is the model's own prediction on the recorded
features") rather than numeric.
"""

from __future__ import annotations

import math

import mlflow
import numpy as np
import pandas as pd
import pytest

from power_market_analytics.forecasting.backtest import BacktestRun, run_backtest
from power_market_analytics.forecasting.strategy import ForecastUnavailableError
from power_market_analytics.tasks.spot_price.frames import (
    OcctoDemandForecast,
    SpotPriceBacktestResult,
    SpotPriceForecast,
    SpotPrices,
)
from power_market_analytics.tasks.spot_price.strategies.lgbm import (
    BASE_FEATURE_COLS,
    OCCTO_FEATURE_COLS,
    LightGbmEvalSet,
    LightGbmOcctoEvalSet,
    LightGbmOcctoStrategy,
    LightGbmStrategy,
)


@pytest.fixture(scope="module", autouse=True)
def experiment() -> None:
    """Give this module its own MLflow experiment in the session store."""
    mlflow.set_experiment("test_spot_price_lgbm")


# --------------------------------------------------------------------------- synthetic history

HISTORY_START = pd.Timestamp("2024-03-01")
#: 45 days: 2024-03-01 .. 2024-04-14.
HISTORY_DAYS = pd.date_range(HISTORY_START, periods=45, freq="D")


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
    """The history a strategy is allowed to see when forecasting ``day``."""
    return SpotPrices.from_df(prices.df[prices.df["trade_date"] < day])


def occto_row(day: pd.Timestamp) -> dict[str, object]:
    day_index = (day - HISTORY_START).days
    return {
        "trade_date": day,
        "max_demand_hour_ending": 17 + day_index % 3,
        "max_demand_mw": 40_000 + 10 * day_index,
        "max_supply_capacity_mw": 46_000 + 10 * day_index,
    }


def make_occto(days) -> OcctoDemandForecast:
    return OcctoDemandForecast.from_df(
        pd.DataFrame([occto_row(day) for day in days]).astype(
            {
                "max_demand_hour_ending": "int64",
                "max_demand_mw": "int64",
                "max_supply_capacity_mw": "int64",
            }
        )
    )


def training_rows(strategy: LightGbmStrategy) -> int:
    """Rows the current model was fitted on, read off the first tree's root node."""
    root = strategy._model.booster_.dump_model()["tree_info"][0]["tree_structure"]
    return root["internal_count"] if "internal_count" in root else root["leaf_count"]


D = pd.Timestamp("2024-04-10")  # a Wednesday


@pytest.fixture(scope="module")
def prices() -> SpotPrices:
    return make_prices()


# --------------------------------------------------------------------------- eval sets


class TestEvalSets:
    def test_to_eval_frame_drops_trade_date_and_casts_to_float(self):
        eval_set = LightGbmEvalSet.from_df(
            pd.DataFrame(
                {
                    "trade_date": pd.to_datetime(["2024-04-10", "2024-04-10"]),
                    "time_code": [1, 2],
                    "month": [4, 4],
                    "day_of_week": [2, 2],
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
        assert frame.iloc[1].tolist() == [2.0, 4.0, 2.0, 11.0, 12.0, 11.5]

    def test_occto_eval_frame_appends_the_three_occto_features(self):
        eval_set = LightGbmOcctoEvalSet.from_df(
            pd.DataFrame(
                {
                    "trade_date": pd.to_datetime(["2024-04-10"]),
                    "time_code": [1],
                    "month": [4],
                    "day_of_week": [2],
                    "lag_1d_price": [10.5],
                    "max_demand_hour_ending": [18],
                    "max_demand_mw": [40_000],
                    "max_supply_capacity_mw": [46_000],
                    "actual_price_jpy_kwh": [10.0],
                    "forecast_price_jpy_kwh": [10.25],
                }
            )
        )
        frame = eval_set.to_eval_frame()
        assert list(frame.columns) == [
            *BASE_FEATURE_COLS,
            *OCCTO_FEATURE_COLS,
            "actual_price_jpy_kwh",
            "forecast_price_jpy_kwh",
        ]
        assert set(frame.dtypes.astype(str)) == {"float64"}
        assert frame.iloc[0].tolist() == [
            1.0,
            4.0,
            2.0,
            10.5,
            18.0,
            40_000.0,
            46_000.0,
            10.0,
            10.25,
        ]

    def test_occto_eval_set_rejects_a_missing_occto_feature(self):
        # A NaN in an OCCTO column can only arrive as float64, which the
        # int64 contract refuses.
        with pytest.raises(ValueError, match="LightGbmOcctoEvalSet: dtype mismatch"):
            LightGbmOcctoEvalSet.from_df(
                pd.DataFrame(
                    {
                        "trade_date": pd.to_datetime(["2024-04-10"]),
                        "time_code": [1],
                        "month": [4],
                        "day_of_week": [2],
                        "lag_1d_price": [10.5],
                        "max_demand_hour_ending": [18],
                        "max_demand_mw": [np.nan],
                        "max_supply_capacity_mw": [46_000],
                        "actual_price_jpy_kwh": [10.0],
                        "forecast_price_jpy_kwh": [10.25],
                    }
                )
            )


# --------------------------------------------------------------------------- construction


class TestInit:
    def test_defaults(self):
        strategy = LightGbmStrategy()
        assert strategy.name == "lightgbm"
        assert strategy.train_window_days == 730
        assert strategy.refit_every_days == 7
        assert strategy.train_start_date is None
        assert strategy._n_fits == 0
        assert strategy._shap_records == {}

    def test_train_start_date_is_normalised_to_a_ns_timestamp(self):
        strategy = LightGbmStrategy(train_start_date="2024-04-01")
        assert strategy.train_start_date == pd.Timestamp("2024-04-01")
        assert strategy.train_start_date.unit == "ns"

    def test_shap_cols_follow_feature_cols(self):
        assert LightGbmStrategy().shap_cols == (
            "shap_time_code",
            "shap_month",
            "shap_day_of_week",
            "shap_lag_1d_price",
        )
        occto = LightGbmOcctoStrategy(make_occto(HISTORY_DAYS))
        assert occto.name == "lightgbm_occto"
        assert occto.feature_cols == (*BASE_FEATURE_COLS, *OCCTO_FEATURE_COLS)
        assert occto.eval_set_cls is LightGbmOcctoEvalSet
        assert occto.shap_cols[-3:] == (
            "shap_max_demand_hour_ending",
            "shap_max_demand_mw",
            "shap_max_supply_capacity_mw",
        )


# --------------------------------------------------------------------------- predict


@pytest.fixture(scope="module")
def fitted(prices: SpotPrices) -> tuple[LightGbmStrategy, SpotPriceForecast]:
    """One strategy after a single ``predict`` for ``D``, shared by the read-only checks."""
    strategy = LightGbmStrategy(train_window_days=30, refit_every_days=7)
    forecast = strategy.predict(D, history_before(prices, D))
    return strategy, forecast


class TestPredict:
    def test_returns_48_finite_prices_for_the_target_day(self, fitted):
        _, forecast = fitted
        assert isinstance(forecast, SpotPriceForecast)
        assert len(forecast) == 48
        assert forecast.df["trade_date"].dtype == "datetime64[ns]"
        assert forecast.df["trade_date"].eq(D).all()
        assert forecast.df["time_code"].tolist() == list(range(1, 49))
        assert forecast.df["forecast_price_jpy_kwh"].dtype == "float64"
        assert np.isfinite(forecast.df["forecast_price_jpy_kwh"]).all()

    def test_records_features_the_model_actually_scored(self, fitted):
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
        assert record["trade_date"].eq(D).all()
        assert record["time_code"].tolist() == list(range(1, 49))
        # Calendar features are the target day's, the lag is D-1's price for
        # the same time code (not D's: that would leak the answer).
        assert record["month"].eq(4).all()
        assert record["day_of_week"].eq(2).all()
        assert record["lag_1d_price"].tolist() == [
            price_at(D - pd.Timedelta(days=1), tc) for tc in range(1, 49)
        ]
        # The forecast is the fitted model's own prediction on those rows.
        features = record[list(BASE_FEATURE_COLS)].astype("float64")
        np.testing.assert_allclose(
            forecast.df["forecast_price_jpy_kwh"].to_numpy(),
            strategy._model.predict(features),
        )

    def test_shap_contributions_add_up_to_the_forecast(self, fitted):
        strategy, forecast = fitted
        record = strategy._shap_records[D]
        reconstructed = record[list(strategy.shap_cols)].sum(axis=1) + record["shap_expected_value"]
        np.testing.assert_allclose(
            reconstructed.to_numpy(), forecast.df["forecast_price_jpy_kwh"].to_numpy(), atol=1e-6
        )
        # Every row of one day is explained by the same model, so it shares
        # one baseline.
        assert record["shap_expected_value"].nunique() == 1

    def test_second_resolution_target_date_is_normalised(self, prices):
        strategy = LightGbmStrategy(train_window_days=30)
        target = pd.Timestamp("2024-04-10")
        assert target.unit == "s"
        forecast = strategy.predict(target, history_before(prices, target))
        assert forecast.df["trade_date"].dtype == "datetime64[ns]"
        (recorded,) = strategy._shap_records
        assert recorded.unit == "ns"

    def test_missing_previous_day_is_unforecastable(self, prices):
        strategy = LightGbmStrategy(train_window_days=30)
        # History stops two days before the target: enough to fit, but D-1 is absent.
        history = history_before(prices, D - pd.Timedelta(days=1))
        with pytest.raises(
            ForecastUnavailableError,
            match=r"lightgbm: features \['lag_1d_price'\] unavailable for 2024-04-10",
        ):
            strategy.predict(D, history)

    def test_feature_unavailable_for_target_day_raises(self, prices):
        # OCCTO forecasts exist for the training days but not for D itself.
        occto = make_occto(pd.date_range(HISTORY_START, D - pd.Timedelta(days=1)))
        strategy = LightGbmOcctoStrategy(occto, train_window_days=30)
        with pytest.raises(
            ForecastUnavailableError,
            match=(
                r"lightgbm_occto: features \['max_demand_hour_ending', 'max_demand_mw', "
                r"'max_supply_capacity_mw'\] unavailable for 2024-04-10"
            ),
        ):
            strategy.predict(D, history_before(prices, D))


# --------------------------------------------------------------------------- refit schedule


class TestEnsureFitted:
    def test_first_predict_fits_on_the_trailing_window(self, prices):
        strategy = LightGbmStrategy(train_window_days=30, refit_every_days=7)
        strategy.predict(D, history_before(prices, D))
        assert strategy._n_fits == 1
        assert strategy._trained_through == pd.Timestamp("2024-04-09")
        assert strategy._fit_anchor == D
        # 30 days back from D (2024-03-11 .. 2024-04-09), each with a lag.
        assert training_rows(strategy) == 30 * 48

    def test_next_day_within_cadence_reuses_the_model(self, prices):
        strategy = LightGbmStrategy(train_window_days=30, refit_every_days=7)
        strategy.predict(D, history_before(prices, D))
        model = strategy._model
        for offset in (1, 4):
            day = D + pd.Timedelta(days=offset)
            strategy.predict(day, history_before(prices, day))
        assert strategy._n_fits == 1
        assert strategy._model is model
        assert strategy._fit_anchor == D
        assert strategy._trained_through == pd.Timestamp("2024-04-09")

    def test_refits_once_the_cadence_has_elapsed(self, prices):
        strategy = LightGbmStrategy(train_window_days=30, refit_every_days=3)
        strategy.predict(D, history_before(prices, D))
        model = strategy._model
        day = D + pd.Timedelta(days=3)
        strategy.predict(day, history_before(prices, day))
        assert strategy._n_fits == 2
        assert strategy._model is not model
        assert strategy._fit_anchor == day
        assert strategy._trained_through == pd.Timestamp("2024-04-12")

    def test_predicting_an_earlier_day_refits_instead_of_leaking(self, prices):
        strategy = LightGbmStrategy(train_window_days=30, refit_every_days=7)
        strategy.predict(D, history_before(prices, D))
        earlier = D - pd.Timedelta(days=1)
        strategy.predict(earlier, history_before(prices, earlier))
        assert strategy._n_fits == 2
        # The cached model had seen 2024-04-09; the new one must not.
        assert strategy._trained_through == pd.Timestamp("2024-04-08")
        assert strategy._fit_anchor == earlier

    def test_train_window_days_bounds_the_training_rows(self, prices):
        strategy = LightGbmStrategy(train_window_days=3)
        strategy.predict(D, history_before(prices, D))
        assert training_rows(strategy) == 3 * 48
        assert strategy._trained_through == pd.Timestamp("2024-04-09")

    def test_train_start_date_bounds_the_training_rows(self, prices):
        strategy = LightGbmStrategy(train_window_days=30, train_start_date="2024-04-07")
        strategy.predict(D, history_before(prices, D))
        # 04-07, 04-08, 04-09 are eligible targets; 04-06 only supplies 04-07's lag.
        assert training_rows(strategy) == 3 * 48
        assert strategy._trained_through == pd.Timestamp("2024-04-09")

    def test_train_start_date_after_the_history_raises(self, prices):
        strategy = LightGbmStrategy(train_window_days=30, train_start_date=D)
        with pytest.raises(
            ForecastUnavailableError,
            match="lightgbm: no complete training rows in the 30 days before 2024-04-10",
        ):
            strategy.predict(D, history_before(prices, D))

    def test_history_of_a_single_day_has_no_complete_rows(self, prices):
        strategy = LightGbmStrategy(train_window_days=30)
        only_previous_day = SpotPrices.from_df(
            prices.df[prices.df["trade_date"] == D - pd.Timedelta(days=1)]
        )
        with pytest.raises(ForecastUnavailableError, match="no complete training rows"):
            strategy.predict(D, only_previous_day)


# --------------------------------------------------------------------------- build_eval_set

WINDOW_START = pd.Timestamp("2024-04-01")
WINDOW_END = pd.Timestamp("2024-04-14")


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


@pytest.fixture(scope="module")
def backtested(prices: SpotPrices) -> tuple[LightGbmStrategy, BacktestRun]:
    """One strategy backtested over ``WINDOW_START..WINDOW_END`` (two refits)."""
    strategy = LightGbmStrategy(train_window_days=30, refit_every_days=7)
    run = run_backtest(strategy, prices, WINDOW_START, WINDOW_END)
    return strategy, run


class TestBuildEvalSet:
    def test_requires_the_backtest_result(self, prices):
        with pytest.raises(ValueError, match="lightgbm: build_eval_set requires the backtest run"):
            LightGbmStrategy().build_eval_set(prices, WINDOW_START, WINDOW_END)

    def test_replays_the_backtest_forecasts_onto_the_feature_rows(self, backtested, prices):
        strategy, run = backtested
        eval_set = strategy.build_eval_set(prices, WINDOW_START, WINDOW_END, run=run)
        result = run.result
        assert type(eval_set) is LightGbmEvalSet
        assert len(eval_set) == 14 * 48
        assert eval_set.df.dtypes.astype(str).to_dict() == {
            "trade_date": "datetime64[ns]",
            "time_code": "int64",
            "month": "int64",
            "day_of_week": "int64",
            "lag_1d_price": "float64",
            "actual_price_jpy_kwh": "float64",
            "forecast_price_jpy_kwh": "float64",
        }
        merged = eval_set.df.merge(
            result.df,
            how="inner",
            on=["trade_date", "time_code"],
            suffixes=("", "_backtest"),
            validate="one_to_one",
        )
        assert len(merged) == 14 * 48
        assert merged["forecast_price_jpy_kwh"].equals(merged["forecast_price_jpy_kwh_backtest"])
        assert merged["actual_price_jpy_kwh"].equals(merged["actual_price_jpy_kwh_backtest"])
        # One row checked by hand: 2024-04-05 (Friday) period 10.
        row = eval_set.df.set_index(["trade_date", "time_code"]).loc[
            (pd.Timestamp("2024-04-05"), 10)
        ]
        assert row["month"] == 4
        assert row["day_of_week"] == 4
        assert row["lag_1d_price"] == price_at(pd.Timestamp("2024-04-04"), 10)
        assert row["actual_price_jpy_kwh"] == price_at(pd.Timestamp("2024-04-05"), 10)

    def test_rows_with_forecasts_missing_are_rejected(self, prices):
        # 2024-03-01 has no lag and is dropped; 2024-03-02 has complete
        # features but was never backtested, so it has no forecast to replay.
        strategy = LightGbmStrategy(train_window_days=30)
        run = run_backtest(strategy, prices, pd.Timestamp("2024-03-03"), pd.Timestamp("2024-03-04"))
        with pytest.raises(
            ValueError, match="LightGbmEvalSet: column 'forecast_price_jpy_kwh' has 48 null values"
        ):
            strategy.build_eval_set(
                prices, pd.Timestamp("2024-03-01"), pd.Timestamp("2024-03-04"), run=run
            )

    def test_window_with_only_incomplete_rows_raises(self, prices):
        with pytest.raises(
            ValueError,
            match="lightgbm: no complete feature rows between 2024-03-01 and 2024-03-01",
        ):
            LightGbmStrategy().build_eval_set(
                prices, HISTORY_START, HISTORY_START, run=hand_backtest_run()
            )

    def test_window_outside_the_history_raises(self, prices):
        with pytest.raises(ValueError, match="no complete feature rows between 2025-01-01"):
            LightGbmStrategy().build_eval_set(
                prices,
                pd.Timestamp("2025-01-01"),
                pd.Timestamp("2025-01-31"),
                run=hand_backtest_run(),
            )

    def test_skipped_days_are_dropped_from_the_eval_set(self, prices):
        strategy = LightGbmStrategy(train_window_days=30)
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
        assert pd.Timestamp("2024-04-02") not in set(eval_set.df["trade_date"])


# --------------------------------------------------------------------------- evaluate


def logged_model_names(run_id: str) -> list[str]:
    run = mlflow.MlflowClient().get_run(run_id)
    return [mlflow.get_logged_model(out.model_id).name for out in run.outputs.model_outputs]


class TestEvaluate:
    def test_before_any_backtest_raises(self, prices):
        strategy = LightGbmStrategy()
        eval_set = LightGbmEvalSet.from_df(
            pd.DataFrame(
                {
                    "trade_date": pd.to_datetime(["2024-04-10"]),
                    "time_code": [1],
                    "month": [4],
                    "day_of_week": [2],
                    "lag_1d_price": [10.5],
                    "actual_price_jpy_kwh": [10.0],
                    "forecast_price_jpy_kwh": [10.25],
                }
            )
        )
        with pytest.raises(RuntimeError, match="lightgbm: no fitted model or recorded"):
            strategy.evaluate(eval_set)

    def test_logs_backtest_metrics_params_plots_and_model(self, prices):
        strategy = LightGbmStrategy(train_window_days=30, refit_every_days=7)
        run = run_backtest(strategy, prices, WINDOW_START, WINDOW_END)
        eval_set = strategy.build_eval_set(prices, WINDOW_START, WINDOW_END, run=run)
        with mlflow.start_run() as run:
            evaluation = strategy.evaluate(eval_set)

        # Static-dataset mode: the metric is exactly the backtest's MAE.
        expected_mae = (
            (eval_set.df["forecast_price_jpy_kwh"] - eval_set.df["actual_price_jpy_kwh"])
            .abs()
            .mean()
        )
        assert evaluation.metrics["mean_absolute_error"] == pytest.approx(expected_mae, abs=1e-9)
        assert evaluation.metrics["example_count"] == 14 * 48

        client = mlflow.MlflowClient()
        data = client.get_run(run.info.run_id).data
        assert data.params["lgbm_n_estimators"] == "500"
        assert data.params["lgbm_learning_rate"] == "0.05"
        assert data.params["lgbm_train_window_days"] == "30"
        assert data.params["lgbm_refit_every_days"] == "7"
        assert data.params["lgbm_train_start_date"] == "none"
        assert data.params["lgbm_feature_cols"] == "time_code,month,day_of_week,lag_1d_price"
        # 14 days at a 7-day cadence: fits on 04-01 and 04-08.
        assert data.metrics["n_refits"] == 2.0
        assert data.metrics["mean_absolute_error"] == pytest.approx(expected_mae, abs=1e-9)

        artifacts = {a.path for a in client.list_artifacts(run.info.run_id)}
        assert {"shap_beeswarm_plot.png", "shap_feature_importance_plot.png"} <= artifacts

        # The final refit's booster is the logged model.
        (model_id,) = [
            out.model_id for out in client.get_run(run.info.run_id).outputs.model_outputs
        ]
        assert mlflow.get_logged_model(model_id).name == "lightgbm_model"
        reloaded = mlflow.lightgbm.load_model(f"models:/{model_id}")
        features = eval_set.to_eval_frame()[list(BASE_FEATURE_COLS)]
        np.testing.assert_allclose(reloaded.predict(features), strategy._model.predict(features))

    def test_train_start_date_is_logged_as_a_date(self, prices):
        strategy = LightGbmStrategy(
            train_window_days=30, refit_every_days=7, train_start_date="2024-03-20"
        )
        run = run_backtest(strategy, prices, WINDOW_START, WINDOW_END)
        eval_set = strategy.build_eval_set(prices, WINDOW_START, WINDOW_END, run=run)
        with mlflow.start_run() as run:
            strategy.evaluate(eval_set, explainability_nsamples=50)
        params = mlflow.MlflowClient().get_run(run.info.run_id).data.params
        assert params["lgbm_train_start_date"] == "2024-03-20"

    def test_contributions_must_cover_every_eval_row(self, prices):
        # `other` only backtested (and so only explained) the first 10 days.
        full = LightGbmStrategy(train_window_days=30, refit_every_days=7)
        run = run_backtest(full, prices, WINDOW_START, WINDOW_END)
        eval_set = full.build_eval_set(prices, WINDOW_START, WINDOW_END, run=run)
        other = LightGbmStrategy(train_window_days=30, refit_every_days=7)
        run_backtest(other, prices, WINDOW_START, pd.Timestamp("2024-04-10"))
        with mlflow.start_run():
            with pytest.raises(
                RuntimeError,
                match=(
                    "lightgbm: recorded contributions cover 480 of 672 eval rows; "
                    "backtest and eval windows disagree"
                ),
            ):
                other.evaluate(eval_set)


# --------------------------------------------------------------------------- OCCTO strategy


class TestLightGbmOcctoStrategy:
    def test_join_daily_features_left_joins_on_trade_date(self):
        strategy = LightGbmOcctoStrategy(make_occto(pd.to_datetime(["2024-04-10"])))
        featured = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2024-04-10", "2024-04-10", "2024-04-11"]),
                "time_code": [1, 2, 1],
                "lag_1d_price": [10.0, 11.0, 12.0],
            }
        )
        joined = strategy._join_daily_features(featured)
        assert len(joined) == 3
        assert list(joined.columns) == [
            "trade_date",
            "time_code",
            "lag_1d_price",
            *OCCTO_FEATURE_COLS,
        ]
        # 2024-04-10 is day 40 of the history: 17 + 40 % 3, 40_000 + 400, 46_000 + 400.
        assert joined.loc[0, list(OCCTO_FEATURE_COLS)].tolist() == [18, 40_400, 46_400]
        assert joined.loc[1, list(OCCTO_FEATURE_COLS)].tolist() == [18, 40_400, 46_400]
        assert joined.loc[2, list(OCCTO_FEATURE_COLS)].isna().all()

    def test_backtest_eval_set_and_evaluation_use_the_occto_features(self, prices):
        occto = make_occto(pd.date_range("2024-03-15", "2024-04-14"))
        strategy = LightGbmOcctoStrategy(occto, train_window_days=30, refit_every_days=7)
        run = run_backtest(strategy, prices, WINDOW_START, WINDOW_END)
        # OCCTO rows start 03-15, so the 04-01 fit sees 03-15..03-31 only.
        assert training_rows(strategy) == 24 * 48  # last refit (04-08): 03-15..04-07
        eval_set = strategy.build_eval_set(prices, WINDOW_START, WINDOW_END, run=run)
        assert type(eval_set) is LightGbmOcctoEvalSet
        assert len(eval_set) == 14 * 48
        assert eval_set.df.dtypes.astype(str).to_dict() == {
            "trade_date": "datetime64[ns]",
            "time_code": "int64",
            "month": "int64",
            "day_of_week": "int64",
            "lag_1d_price": "float64",
            "max_demand_hour_ending": "int64",
            "max_demand_mw": "int64",
            "max_supply_capacity_mw": "int64",
            "actual_price_jpy_kwh": "float64",
            "forecast_price_jpy_kwh": "float64",
        }
        row = eval_set.df.set_index(["trade_date", "time_code"]).loc[
            (pd.Timestamp("2024-04-05"), 10)
        ]
        # 2024-04-05 is day 35 of the history.
        assert row[list(OCCTO_FEATURE_COLS)].tolist() == [19, 40_350, 46_350]

        record = strategy._shap_records[pd.Timestamp("2024-04-14")]
        assert list(record.columns[:9]) == [
            "trade_date",
            "time_code",
            "month",
            "day_of_week",
            "lag_1d_price",
            *OCCTO_FEATURE_COLS,
            "shap_time_code",
        ]
        assert "shap_max_demand_mw" in record.columns

        with mlflow.start_run() as run:
            evaluation = strategy.evaluate(eval_set, explainability_nsamples=50)
        expected_mae = (
            (eval_set.df["forecast_price_jpy_kwh"] - eval_set.df["actual_price_jpy_kwh"])
            .abs()
            .mean()
        )
        assert evaluation.metrics["mean_absolute_error"] == pytest.approx(expected_mae, abs=1e-9)
        client = mlflow.MlflowClient()
        params = client.get_run(run.info.run_id).data.params
        assert params["lgbm_feature_cols"] == (
            "time_code,month,day_of_week,lag_1d_price,"
            "max_demand_hour_ending,max_demand_mw,max_supply_capacity_mw"
        )
        assert logged_model_names(run.info.run_id) == ["lightgbm_occto_model"]
        artifacts = {a.path for a in client.list_artifacts(run.info.run_id)}
        assert {"shap_beeswarm_plot.png", "shap_feature_importance_plot.png"} <= artifacts

    def test_days_without_an_occto_forecast_are_dropped_from_the_eval_set(self, prices):
        # No forecast for 2024-04-06: it can be neither trained on nor
        # forecast, so the backtest skips it and the eval set drops its rows.
        days = pd.date_range("2024-03-15", "2024-04-14").drop(pd.Timestamp("2024-04-06"))
        strategy = LightGbmOcctoStrategy(make_occto(days), train_window_days=30, refit_every_days=7)
        first = run_backtest(strategy, prices, WINDOW_START, pd.Timestamp("2024-04-05"))
        second = run_backtest(
            strategy, prices, pd.Timestamp("2024-04-07"), pd.Timestamp("2024-04-10")
        )
        run = BacktestRun(
            result=SpotPriceBacktestResult.from_df(
                pd.concat([first.result.df, second.result.df], ignore_index=True)
            ),
            skipped_days=(),
        )
        eval_set = strategy.build_eval_set(
            prices, WINDOW_START, pd.Timestamp("2024-04-10"), run=run
        )
        assert len(eval_set) == 9 * 48
        assert pd.Timestamp("2024-04-06") not in set(eval_set.df["trade_date"])
        assert eval_set.df["max_demand_mw"].dtype == "int64"
