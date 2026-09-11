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
        "ftr_day_calendar",
        "ftr_day_occto",
        "ftr_hour_jma_obs",
        "ftr_hour_msm",
        "ftr_period_actuals",
        "ftr_period_jepx",
        "ftr_period_similar_day",
    ]
    day = [AREA_CODE.name, TRADE_DATE_KEY.name]
    assert by_name["ftr_day_calendar"].entities == day
    assert by_name["ftr_hour_msm"].entities == [*day, HOUR_ENDING.name]
    assert by_name["ftr_period_jepx"].entities == [*day, TIME_CODE.name]
    assert by_name["ftr_hour_msm"].tags == {"grain": "hour"}


def test_fields_carry_the_marts_types_and_categorical_tags():
    # The features, not the schema: Feast fills a view's entity columns into its
    # schema only once a store applies it, so the schema depends on test order.
    calendar = {field.name: field for field in views.FTR_DAY_CALENDAR.features}
    assert calendar["day_type"].dtype == Int64
    assert calendar["day_type"].tags == {"categorical": "true"}
    assert calendar["holiday_degree"].dtype == Float64
    assert calendar["holiday_degree"].tags == {"categorical": "false"}
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
        "time_code, similar_day_demand_kwh, available_at, published_at "
        "from pma_features.ftr_period_similar_day"
    )
    assert views.FTR_PERIOD_SIMILAR_DAY_SOURCE.created_timestamp_column == "published_at"
    assert views.FTR_PERIOD_SIMILAR_DAY.entities == [
        AREA_CODE.name,
        TRADE_DATE_KEY.name,
        TIME_CODE.name,
    ]
