"""The demand registry: the presets built over Feast and the similar-day strategies."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.forecasting.preset_lgbm import PresetLightGbmStrategy
from power_market_analytics.tasks.demand.presets import PRESETS
from power_market_analytics.tasks.demand.strategies import STRATEGIES, build_strategy
from power_market_analytics.tasks.demand.strategies.lgbm import (
    LightGbmMsmPopWeightedDayTypeSimilarDayCalendarCountStrategy,
    LightGbmMsmPopWeightedDayTypeSimilarDayCalendarStrategy,
    LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDegreeStrategy,
    LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDistanceStrategy,
    LightGbmMsmPopWeightedDayTypeSimilarDayStrategy,
)
from tests.conftest import (
    CALENDAR_DAYS,
    DEMAND_HOLE_DAY,
    DEMAND_HOLE_TIME_CODES,
    FORECAST_MISSING_DAY,
    HOLIDAYS_2024_SPRING,
    HOURLY_LOAD_DAYS,
    SECOND_STATION_FORECAST_OFFSET_C,
    CuratedWarehouse,
    popw_forecast,
    synthetic_demand,
    synthetic_forecast_temperature,
    wavg_temperature,
)
from tests.test_demand_datasets import expected_day_type

DAYS = pd.date_range("2024-04-01", "2024-04-10", freq="D")
TRAIN_START = pd.Timestamp("2024-04-01")
BASE_FEATURE_COLS = ("time_code", "month", "day_of_week", "wavg_temperature_c", "lag_7d_demand_kwh")


def frame_by_period(strategy) -> pd.DataFrame:
    return strategy._features_df.set_index(["trade_date", "time_code"])


class TestRegistry:
    def test_registered_names(self):
        assert STRATEGIES == (
            "lightgbm",
            "lightgbm_msm",
            "lightgbm_msm_popw",
            "lightgbm_msm_popw_daytype",
            "lightgbm_msm_popw_daytype_simday",
            "lightgbm_msm_popw_daytype_simday_calendar",
            "lightgbm_msm_popw_daytype_simday_calendarcounts",
            "lightgbm_msm_popw_daytype_simday_holidaydegree",
            "lightgbm_msm_popw_daytype_simday_holidaydistance",
        )
        assert STRATEGIES[:4] == tuple(PRESETS)

    def test_unknown_name_raises_key_error(self):
        with pytest.raises(KeyError, match="arima"):
            build_strategy("arima", area_code="tokyo", days=DAYS)


class TestBuildPreset:
    def test_lightgbm_retrieves_its_features_as_of_each_day(self, feature_marts):
        strategy = build_strategy(
            "lightgbm", area_code="tokyo", days=DAYS, train_start_date=TRAIN_START
        )
        assert type(strategy) is PresetLightGbmStrategy
        assert strategy.name == "lightgbm"
        assert strategy.preset is PRESETS["lightgbm"]
        assert strategy.train_start_date == TRAIN_START
        assert strategy.feature_cols == BASE_FEATURE_COLS
        assert strategy.categorical_feature_cols == ()
        frame = strategy._features_df
        assert len(frame) == len(DAYS) * 48
        assert not frame.isna().any().any()
        day = pd.Timestamp("2024-04-05")
        row = frame_by_period(strategy).loc[(day, 10)]
        assert row["month"] == 4.0 and row["day_of_week"] == 4.0  # a Friday
        # Period 10 lies in hour-ending 5; the lag is the same period a week before.
        assert row["wavg_temperature_c"] == pytest.approx(wavg_temperature(day, 5))
        assert row["lag_7d_demand_kwh"] == synthetic_demand(day - pd.Timedelta(days=7), 10)

    def test_lightgbm_msm_adds_the_representative_stations_forecast(self, feature_marts):
        days = pd.date_range(
            FORECAST_MISSING_DAY - pd.Timedelta(days=1), FORECAST_MISSING_DAY + pd.Timedelta(days=1)
        )
        strategy = build_strategy("lightgbm_msm", area_code="tokyo", days=days)
        assert strategy.feature_cols == (*BASE_FEATURE_COLS, "forecast_temperature_c")
        frame = frame_by_period(strategy)
        day = FORECAST_MISSING_DAY + pd.Timedelta(days=1)
        assert frame.loc[(day, 7), "forecast_temperature_c"] == synthetic_forecast_temperature(
            day, 4
        )
        assert frame.loc[FORECAST_MISSING_DAY, "forecast_temperature_c"].isna().all()
        assert frame.loc[day, "forecast_temperature_c"].notna().all()

    def test_lightgbm_msm_popw_daytype_marks_the_day_type_categorical(self, feature_marts):
        days = pd.date_range("2024-04-26", "2024-04-30", freq="D")  # 04-27 Sat, 04-29 holiday
        strategy = build_strategy("lightgbm_msm_popw_daytype", area_code="tokyo", days=days)
        assert strategy.feature_cols == (
            *BASE_FEATURE_COLS,
            "popw_forecast_temperature_c",
            "day_type",
        )
        assert strategy.categorical_feature_cols == ("day_type",)
        frame = frame_by_period(strategy)
        assert frame.loc[(pd.Timestamp("2024-04-26"), 1), "day_type"] == 0.0
        assert frame.loc[(pd.Timestamp("2024-04-27"), 1), "day_type"] == 1.0
        assert pd.Timestamp("2024-04-29") in HOLIDAYS_2024_SPRING
        assert frame.loc[(pd.Timestamp("2024-04-29"), 1), "day_type"] == 2.0
        day = pd.Timestamp("2024-04-30")
        assert frame.loc[(day, 1), "popw_forecast_temperature_c"] == pytest.approx(
            popw_forecast(
                day, 1, synthetic_forecast_temperature(day, 1), SECOND_STATION_FORECAST_OFFSET_C
            )
        )

    def test_the_week_after_the_hole_lacks_its_lag(self, feature_marts):
        after = DEMAND_HOLE_DAY + pd.Timedelta(days=7)
        days = pd.date_range(after - pd.Timedelta(days=1), after, freq="D")
        frame = frame_by_period(build_strategy("lightgbm", area_code="tokyo", days=days))
        lag = frame.loc[after, "lag_7d_demand_kwh"]
        assert lag.loc[list(DEMAND_HOLE_TIME_CODES)].isna().all()
        assert lag.loc[1:10].notna().all()
        assert frame.loc[after - pd.Timedelta(days=1), "lag_7d_demand_kwh"].notna().all()

    def test_add_drop_and_label_compose_a_named_set(self, feature_marts):
        strategy = build_strategy(
            "lightgbm",
            area_code="tokyo",
            days=DAYS,
            add=("ftr_day_calendar:day_type",),
            drop=("ftr_day_calendar:day_of_week",),
            label="lightgbm_daytype",
        )
        assert strategy.name == "lightgbm_daytype"
        assert strategy.preset.name == "lightgbm_daytype" and strategy.preset.base == "lightgbm"
        assert strategy.feature_cols == (
            "time_code",
            "month",
            "wavg_temperature_c",
            "lag_7d_demand_kwh",
            "day_type",
        )
        assert strategy.categorical_feature_cols == ("day_type",)

    def test_a_label_alone_renames_the_run(self, feature_marts):
        strategy = build_strategy("lightgbm", area_code="tokyo", days=DAYS, label="lightgbm_again")
        assert strategy.name == "lightgbm_again" and strategy.preset is PRESETS["lightgbm"]

    def test_a_preset_needs_its_days(self):
        with pytest.raises(ValueError, match="'lightgbm' needs the days"):
            build_strategy("lightgbm", area_code="tokyo")

    def test_changes_need_a_label(self):
        with pytest.raises(
            ValueError, match="'lightgbm' with features added or dropped needs a label"
        ):
            build_strategy(
                "lightgbm", area_code="tokyo", days=DAYS, add=("ftr_day_calendar:day_type",)
            )

    def test_an_area_without_mart_rows_gets_nan_features(self, feature_marts):
        strategy = build_strategy("lightgbm_msm", area_code="kansai", days=DAYS)
        frame = strategy._features_df
        # The calendar covers kansai; the tokyo-only facts do not.
        assert frame["month"].eq(4.0).all()
        for col in ("wavg_temperature_c", "lag_7d_demand_kwh", "forecast_temperature_c"):
            assert np.isnan(frame[col]).all()


class TestBuildSimilarDay:
    def test_similar_day_strategy_retrieves_the_preset_and_loads_its_inputs(
        self, spark, curated_warehouse: CuratedWarehouse, feature_marts
    ):
        strategy = build_strategy(
            "lightgbm_msm_popw_daytype_simday", area_code="tokyo", days=DAYS, spark=spark
        )
        assert type(strategy) is LightGbmMsmPopWeightedDayTypeSimilarDayStrategy
        assert strategy.name == "lightgbm_msm_popw_daytype_simday"
        assert strategy.preset is PRESETS["lightgbm_msm_popw_daytype"]
        assert strategy.feature_cols == (
            *BASE_FEATURE_COLS,
            "popw_forecast_temperature_c",
            "day_type",
            "similar_day_demand_kwh",
        )
        assert strategy.categorical_feature_cols == ("day_type",)
        assert len(strategy._features_df) == len(DAYS) * 48
        assert strategy.census_year == 2020
        assert len(strategy.hourly_load) == len(curated_warehouse.hourly_load)
        assert strategy.selector.first_candidate_day == min(HOLIDAYS_2024_SPRING)
        assert strategy.selector.hourly_load_span == (HOURLY_LOAD_DAYS[0], HOURLY_LOAD_DAYS[-1])
        calendar = strategy.day_calendar.df
        assert calendar["day_type"].tolist() == [
            expected_day_type(d) for d in calendar["trade_date"]
        ]

    def test_calendar_strategy_keeps_the_whole_calendar(
        self, spark, curated_warehouse: CuratedWarehouse, feature_marts
    ):
        strategy = build_strategy(
            "lightgbm_msm_popw_daytype_simday_calendar", area_code="tokyo", days=DAYS, spark=spark
        )
        assert type(strategy) is LightGbmMsmPopWeightedDayTypeSimilarDayCalendarStrategy
        assert strategy.feature_cols[-10:] == strategy.calendar_feature_cols
        assert strategy.census_year == 2020
        assert len(strategy.hourly_load) == len(curated_warehouse.hourly_load)
        # The whole dim_date spine between its first and last holiday, with the counts.
        first, last = min(HOLIDAYS_2024_SPRING), max(HOLIDAYS_2024_SPRING)
        assert len(strategy.day_calendar) == (last - first).days + 1
        assert len(strategy.day_calendar) < len(CALENDAR_DAYS)
        assert "day_of_quarter" in strategy.day_calendar.df.columns

    @pytest.mark.parametrize(
        ("name", "cls"),
        [
            (
                "lightgbm_msm_popw_daytype_simday_holidaydegree",
                LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDegreeStrategy,
            ),
            (
                "lightgbm_msm_popw_daytype_simday_holidaydistance",
                LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDistanceStrategy,
            ),
            (
                "lightgbm_msm_popw_daytype_simday_calendarcounts",
                LightGbmMsmPopWeightedDayTypeSimilarDayCalendarCountStrategy,
            ),
        ],
    )
    def test_calendar_subset_strategies_load_the_similar_day_inputs(
        self, spark, curated_warehouse: CuratedWarehouse, feature_marts, name, cls
    ):
        strategy = build_strategy(name, area_code="tokyo", days=DAYS, spark=spark)
        assert type(strategy) is cls
        assert strategy.name == name
        assert strategy.census_year == 2020
        assert len(strategy.hourly_load) == len(curated_warehouse.hourly_load)
        assert set(strategy.calendar_feature_cols) <= set(strategy.day_calendar.df.columns)

    def test_add_drop_and_label_change_the_preset_under_the_similar_day(
        self, spark, curated_warehouse, feature_marts
    ):
        strategy = build_strategy(
            "lightgbm_msm_popw_daytype_simday",
            area_code="tokyo",
            days=DAYS,
            add=("ftr_day_calendar:half",),
            drop=("ftr_day_calendar:day_of_week",),
            label="simday_half",
            spark=spark,
        )
        assert type(strategy) is LightGbmMsmPopWeightedDayTypeSimilarDayStrategy
        assert strategy.name == "simday_half"
        assert strategy.preset.name == "simday_half"
        assert strategy.preset.base == "lightgbm_msm_popw_daytype"
        assert strategy.feature_cols == (
            "time_code",
            "month",
            "wavg_temperature_c",
            "lag_7d_demand_kwh",
            "popw_forecast_temperature_c",
            "day_type",
            "half",
            "similar_day_demand_kwh",
        )
        assert strategy.categorical_feature_cols == ("day_type",)

    def test_adding_a_calendar_column_a_variant_joins_itself_is_rejected(
        self, spark, curated_warehouse, feature_marts
    ):
        with pytest.raises(ValueError, match=r"\['holiday_degree'\] are this strategy's own"):
            build_strategy(
                "lightgbm_msm_popw_daytype_simday_holidaydegree",
                area_code="tokyo",
                days=DAYS,
                add=("ftr_day_calendar:holiday_degree",),
                label="twice",
                spark=spark,
            )

    def test_a_similar_day_strategy_needs_its_days_too(self):
        with pytest.raises(ValueError, match="'lightgbm_msm_popw_daytype_simday' needs the days"):
            build_strategy("lightgbm_msm_popw_daytype_simday", area_code="tokyo")
