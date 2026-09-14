"""The generated feature views match the marts they describe."""

from __future__ import annotations

from feast.types import Float64, Int64

from power_market_analytics.features import views
from power_market_analytics.features.entities import (
    AREA_CODE,
    HOUR_ENDING,
    TIME_CODE,
    TRADE_DATE_KEY,
)


def test_one_view_per_mart_on_the_entities_of_its_grain():
    by_name = {view.name: view for view in views.VIEWS}
    assert sorted(by_name) == [
        "ftr_day_actuals",
        "ftr_day_calendar",
        "ftr_day_occto",
        "ftr_hour_jma_obs",
        "ftr_hour_msm",
        "ftr_period_actuals",
        "ftr_period_jepx",
        "ftr_period_similar_day",
    ]
    day = [AREA_CODE.name, TRADE_DATE_KEY.name]
    assert by_name["ftr_day_actuals"].entities == day
    assert by_name["ftr_day_calendar"].entities == day
    assert by_name["ftr_hour_msm"].entities == [*day, HOUR_ENDING.name]
    assert by_name["ftr_period_jepx"].entities == [*day, TIME_CODE.name]
    assert by_name["ftr_hour_msm"].tags == {"grain": "hour"}


def test_fields_carry_the_marts_types_and_categorical_and_expression_tags():
    # The features, not the schema: Feast fills a view's entity columns into its
    # schema only once a store applies it, so the schema depends on test order.
    calendar = {field.name: field for field in views.FTR_DAY_CALENDAR.features}
    assert calendar["day_type"].dtype == Int64
    assert calendar["day_type"].tags == {"categorical": "true", "expression": "day_type"}
    assert calendar["holiday_degree"].dtype == Float64
    assert calendar["holiday_degree"].tags == {
        "categorical": "false",
        "expression": "holiday_degree",
    }
    assert calendar["days_since_holiday"].tags["expression"] == "DAYS_SINCE(is_holiday)"
    assert "available_at" not in calendar
    assert not {"area_code", "trade_date_key"} & set(calendar)


def test_sources_select_the_keys_the_features_and_available_at_from_the_mart():
    query = views.FTR_PERIOD_JEPX_SOURCE.query
    assert query.startswith("select area_code, cast(date_format(trade_date, 'yyyyMMdd') as int)")
    assert query.endswith("time_code, lag_1d_price, available_at from pma_features.ftr_period_jepx")
    assert views.FTR_PERIOD_JEPX_SOURCE.timestamp_field == "available_at"
    assert views.FTR_PERIOD_JEPX_SOURCE.created_timestamp_column == ""
    assert views.FTR_HOUR_MSM.online is False


def test_the_similar_day_source_breaks_ties_on_the_vintages_published_at():
    query = views.FTR_PERIOD_SIMILAR_DAY_SOURCE.query
    assert query.endswith(
        "time_code, similar_day_rank1_demand_kwh, similar_day_rank2_demand_kwh, "
        "similar_day_rank3_demand_kwh, wavg_similar_day_top3_demand_kwh, available_at, "
        "published_at from pma_features.ftr_period_similar_day"
    )
    assert views.FTR_PERIOD_SIMILAR_DAY_SOURCE.created_timestamp_column == "published_at"
    assert views.FTR_PERIOD_SIMILAR_DAY.entities == [
        AREA_CODE.name,
        TRADE_DATE_KEY.name,
        TIME_CODE.name,
    ]


def test_the_similar_day_view_carries_ranks_1_to_3_and_their_weighted_mean():
    fields = {field.name: field for field in views.FTR_PERIOD_SIMILAR_DAY.features}
    pool = "power_usage_demand_kwh, gap=(2d, 335d), window=(30, 60)"
    assert {name: field.tags for name, field in fields.items()} == {
        **{
            f"similar_day_rank{rank}_demand_kwh": {
                "categorical": "false",
                "expression": f"SIMILAR_DAY({pool}, rank={rank}, holidays=last_year) / 2",
            }
            for rank in (1, 2, 3)
        },
        "wavg_similar_day_top3_demand_kwh": {
            "categorical": "false",
            "expression": (
                f"SIMILAR_DAY_MEAN({pool}, k=3, weight=inverse_distance, holidays=last_year) / 2"
            ),
        },
    }
    assert list(fields) == [
        "similar_day_rank1_demand_kwh",
        "similar_day_rank2_demand_kwh",
        "similar_day_rank3_demand_kwh",
        "wavg_similar_day_top3_demand_kwh",
    ]
    assert all(field.dtype == Float64 for field in fields.values())


def test_the_actuals_views_carry_the_recent_load_columns():
    period = {field.name: field for field in views.FTR_PERIOD_ACTUALS.features}
    assert list(period) == [
        "lag_2d_demand_kwh",
        "lag_3d_demand_kwh",
        "lag_7d_demand_kwh",
        "lag_9d_demand_kwh",
        "lag_14d_demand_kwh",
        "lag_21d_demand_kwh",
        "lag_28d_demand_kwh",
        "mean_weekly_lags_demand_kwh",
        "ewm_weekly_lags_demand_kwh",
        "ewstd_weekly_lags_demand_kwh",
        "trend_weekly_lags_demand_kwh",
        "std_weekly_lags_demand_kwh",
        "median_weekly_lags_demand_kwh",
        "zscore_7d_vs_14d_28d_demand_kwh",
        "change_2d_9d_demand_kwh",
        "lag_7d_adjacent_mean_demand_kwh",
        "lag_7d_ramp_demand_kwh",
        "mean_weekly_lags_ramp_demand_kwh",
        "mean_daytype_4d_demand_kwh",
        "ewm_daytype_4d_demand_kwh",
        "ewm_5d_demand_kwh",
        "std_5d_demand_kwh",
        "ewstd_5d_demand_kwh",
        "ewm_5d_minus_ewm_weekly_lags_demand_kwh",
    ]
    assert period["lag_7d_ramp_demand_kwh"].dtype == Int64
    assert (
        period["lag_7d_adjacent_mean_demand_kwh"].tags["expression"]
        == "LAG(ROLLING_MEAN(demand_kwh, window=3, step=30m, center=true), 7d)"
    )
    assert period["lag_2d_demand_kwh"].dtype == Int64
    assert period["ewm_daytype_4d_demand_kwh"].dtype == Float64
    assert period["ewm_5d_demand_kwh"].dtype == Float64
    assert period["trend_weekly_lags_demand_kwh"].dtype == Float64
    assert (
        period["trend_weekly_lags_demand_kwh"].tags["expression"]
        == "ROLLING_TREND(demand_kwh, gap=7d, window=4, step=7d)"
    )
    day = {field.name: field for field in views.FTR_DAY_ACTUALS.features}
    assert list(day) == [
        "lag_2d_mean_demand_kwh",
        "lag_2d_max_demand_kwh",
        "lag_2d_min_demand_kwh",
        "lag_2d_range_demand_kwh",
        "lag_2d_load_factor_demand",
        "lag_2d_morning_mean_demand_kwh",
        "lag_2d_afternoon_mean_demand_kwh",
        "lag_2d_evening_mean_demand_kwh",
        "lag_2d_peak_time_code",
        "lag_2d_morning_ramp_demand_kwh",
        "lag_2d_evening_ramp_demand_kwh",
        *(
            f"{prefix}_daily_{stat}_demand_kwh"
            for stat in ("max", "mean", "min")
            for prefix in ("ewm_5d", "ewm_weekly_lags", "ewm_5d_minus_ewm_weekly_lags")
        ),
    ]
    assert day["lag_2d_peak_time_code"].dtype == Int64
    assert day["lag_2d_morning_ramp_demand_kwh"].tags["expression"] == (
        "LAG(DAILY_TREND(demand_kwh, time=06:00-10:00), 2d)"
    )
    assert day["lag_2d_mean_demand_kwh"].dtype == Float64
    assert day["lag_2d_max_demand_kwh"].dtype == Int64
    assert day["lag_2d_min_demand_kwh"].dtype == Int64
    assert all(field.tags["categorical"] == "false" for field in [*period.values(), *day.values()])
    assert period["change_2d_9d_demand_kwh"].tags["expression"] == "LAG(DIFF(demand_kwh, 7d), 2d)"
    assert day["lag_2d_range_demand_kwh"].tags["expression"] == "LAG(DAILY_RANGE(demand_kwh), 2d)"
