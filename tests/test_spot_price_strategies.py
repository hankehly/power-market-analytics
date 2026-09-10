"""The spot-price registry: the naive rule and the presets built over Feast."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.forecasting.preset_lgbm import PresetLightGbmStrategy
from power_market_analytics.tasks.spot_price.presets import PRESETS
from power_market_analytics.tasks.spot_price.strategies import STRATEGIES, build_strategy
from power_market_analytics.tasks.spot_price.strategies.naive import PreviousDayStrategy
from tests.conftest import OCCTO_DAYS, PRICE_DAYS, synthetic_price

DAYS = pd.date_range("2024-04-01", "2024-04-10", freq="D")
TRAIN_START = pd.Timestamp("2024-04-01")


class TestRegistry:
    def test_registered_names(self):
        assert STRATEGIES == ("previous_day", "lightgbm", "lightgbm_occto")
        assert set(PRESETS) == {"lightgbm", "lightgbm_occto"}


class TestBuildNaive:
    def test_previous_day(self):
        assert type(build_strategy("previous_day", area_code="tokyo")) is PreviousDayStrategy

    def test_previous_day_rejects_train_start_date(self):
        with pytest.raises(ValueError, match="'previous_day' has no training step"):
            build_strategy("previous_day", area_code="tokyo", train_start_date=TRAIN_START)

    @pytest.mark.parametrize(
        "kwargs", [{"add": ("ftr_day_occto:max_demand_mw",)}, {"drop": ("x:y",)}, {"label": "n"}]
    )
    def test_previous_day_rejects_feature_changes(self, kwargs):
        with pytest.raises(ValueError, match="'previous_day' has no features"):
            build_strategy("previous_day", area_code="tokyo", **kwargs)

    def test_unknown_name_raises_key_error(self):
        with pytest.raises(KeyError, match="arima"):
            build_strategy("arima", area_code="tokyo", days=DAYS)


class TestBuildPreset:
    def test_a_preset_needs_its_days(self):
        with pytest.raises(ValueError, match="'lightgbm' needs the days"):
            build_strategy("lightgbm", area_code="tokyo")

    def test_changes_need_a_label(self):
        with pytest.raises(
            ValueError, match="'lightgbm' with features added or dropped needs a label"
        ):
            build_strategy(
                "lightgbm", area_code="tokyo", days=DAYS, add=("ftr_day_occto:max_demand_mw",)
            )

    def test_lightgbm_retrieves_the_presets_features_as_of_each_day(self, feature_marts):
        strategy = build_strategy(
            "lightgbm", area_code="tokyo", days=DAYS, train_start_date=TRAIN_START
        )
        assert type(strategy) is PresetLightGbmStrategy
        assert strategy.name == "lightgbm"
        assert strategy.preset is PRESETS["lightgbm"]
        assert strategy.train_start_date == TRAIN_START
        assert strategy.feature_cols == ("time_code", "month", "day_of_week", "lag_1d_price")
        frame = strategy._features_df
        assert len(frame) == len(DAYS) * 48
        assert frame["trade_date"].min() == DAYS[0] and frame["trade_date"].max() == DAYS[-1]
        row = frame.set_index(["trade_date", "time_code"]).loc[(pd.Timestamp("2024-04-05"), 10)]
        assert row["month"] == 4.0 and row["day_of_week"] == 4.0  # a Friday
        assert row["lag_1d_price"] == synthetic_price(pd.Timestamp("2024-04-04"), 10)
        assert not frame[["month", "day_of_week", "lag_1d_price"]].isna().any().any()

    def test_lightgbm_occto_adds_the_peak_forecast(self, feature_marts):
        strategy = build_strategy("lightgbm_occto", area_code="tokyo", days=DAYS)
        assert strategy.name == "lightgbm_occto"
        frame = strategy._features_df
        assert list(frame.columns)[-3:] == [
            "max_demand_hour_ending",
            "max_demand_mw",
            "max_supply_capacity_mw",
        ]
        row = frame.set_index(["trade_date", "time_code"]).loc[(pd.Timestamp("2024-04-05"), 1)]
        # Day 4 of the fixture's OCCTO days: hour 18, 40,040 MW, 46,040 MW.
        assert OCCTO_DAYS[4] == pd.Timestamp("2024-04-05")
        assert row[
            ["max_demand_hour_ending", "max_demand_mw", "max_supply_capacity_mw"]
        ].tolist() == [18.0, 40_040.0, 46_040.0]

    def test_a_day_before_the_data_has_no_lag(self, feature_marts):
        days = pd.date_range(PRICE_DAYS[0], PRICE_DAYS[1], freq="D")
        strategy = build_strategy("lightgbm", area_code="tokyo", days=days)
        frame = strategy._features_df.set_index("trade_date")
        assert frame.loc[PRICE_DAYS[0], "lag_1d_price"].isna().all()
        assert not frame.loc[PRICE_DAYS[1], "lag_1d_price"].isna().any()

    def test_add_drop_and_label_compose_a_named_set(self, feature_marts):
        strategy = build_strategy(
            "lightgbm",
            area_code="tokyo",
            days=DAYS,
            add=("ftr_day_occto:max_demand_mw",),
            drop=("ftr_day_calendar:day_of_week",),
            label="lightgbm_peak",
        )
        assert strategy.name == "lightgbm_peak"
        assert strategy.preset.name == "lightgbm_peak" and strategy.preset.base == "lightgbm"
        assert strategy.feature_cols == ("time_code", "month", "lag_1d_price", "max_demand_mw")
        assert strategy._extra_params()["feature_preset_base"] == "lightgbm"

    def test_a_label_alone_renames_the_run(self, feature_marts):
        strategy = build_strategy("lightgbm", area_code="tokyo", days=DAYS, label="lightgbm_again")
        assert strategy.name == "lightgbm_again" and strategy.preset is PRESETS["lightgbm"]

    def test_an_area_without_occto_rows_gets_nan_features(self, feature_marts):
        strategy = build_strategy("lightgbm_occto", area_code="kansai", days=DAYS)
        frame = strategy._features_df
        assert np.isnan(frame["max_demand_mw"]).all()
        # kansai has no prices in the fixture either, but the calendar covers it.
        assert frame["month"].eq(4.0).all()
