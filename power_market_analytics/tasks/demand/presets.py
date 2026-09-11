"""The demand task's presets: the feature sets its LightGBM strategy runs on.

Each preset lists its features in the order the strategy class it replaces
used, so the models and their SHAP components read the same. The similar
day is a mart column (``ftr_period_similar_day``, scored from the weights
``scripts/fit_similar_day.py`` publishes), so the five similar-day presets
are plain feature lists like the others.
"""

from __future__ import annotations

from power_market_analytics.features.presets import Preset

TASK_NAME = "demand"

#: The load of a learned similar day one year earlier, halved per period
#: (research demand/R-004 E-002).
SIMILAR_DAY_FEATURE = "ftr_period_similar_day:similar_day_demand_kwh"
#: The delivery day's ``dim_date`` calendar attributes (research demand/R-005
#: E-001), in the order the deleted calendar strategy used them.
DAY_CALENDAR_FEATURES: tuple[str, ...] = tuple(
    f"ftr_day_calendar:{column}"
    for column in (
        "half",
        "quarter",
        "day_of_month",
        "day_of_quarter",
        "day_of_year",
        "holiday_degree",
        "is_business_day",
        "fiscal_quarter",
        "days_since_holiday",
        "days_until_holiday",
    )
)
#: Subsets of ``DAY_CALENDAR_FEATURES`` tried on their own after R-005 E-001:
#: the graded holiday degree alone (E-002), the two distances in days to the
#: nearest named holiday (E-003) and the six calendar counts (E-004).
HOLIDAY_DEGREE_FEATURES: tuple[str, ...] = ("ftr_day_calendar:holiday_degree",)
HOLIDAY_DISTANCE_FEATURES: tuple[str, ...] = (
    "ftr_day_calendar:days_since_holiday",
    "ftr_day_calendar:days_until_holiday",
)
CALENDAR_COUNT_FEATURES: tuple[str, ...] = tuple(
    f"ftr_day_calendar:{column}"
    for column in (
        "half",
        "quarter",
        "day_of_month",
        "day_of_quarter",
        "day_of_year",
        "fiscal_quarter",
    )
)

#: Calendar, the recency-weighted same-hour temperature over D-8..D-2 at the
#: representative station and the D-7 demand lag.
LIGHTGBM = Preset(
    task=TASK_NAME,
    name="lightgbm",
    features=(
        "ftr_day_calendar:month",
        "ftr_day_calendar:day_of_week",
        "ftr_hour_jma_obs:wavg_temperature_c",
        "ftr_period_actuals:lag_7d_demand_kwh",
    ),
)
#: Plus the MSM forecast temperature at the representative station (demand/R-001).
LIGHTGBM_MSM = LIGHTGBM.with_changes(
    name="lightgbm_msm", add=("ftr_hour_msm:forecast_temperature_c",)
)
#: Plus the same forecast population-weighted over the area's stations instead
#: (demand/R-002).
LIGHTGBM_MSM_POPW = LIGHTGBM.with_changes(
    name="lightgbm_msm_popw", add=("ftr_hour_msm:popw_forecast_temperature_c",)
)
#: Plus the delivery day's type, categorical by the mart's tag (demand/R-003;
#: the script default and the Kansai baseline).
LIGHTGBM_MSM_POPW_DAYTYPE = LIGHTGBM_MSM_POPW.with_changes(
    name="lightgbm_msm_popw_daytype", add=("ftr_day_calendar:day_type",)
)
#: Plus the similar day's load (demand/R-004 E-002; the Tokyo demand baseline,
#: reference run 008868fe…). Tokyo-only until another TSO's でんき予報 hourly
#: load is loaded and its weights fitted.
LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY = LIGHTGBM_MSM_POPW_DAYTYPE.with_changes(
    name="lightgbm_msm_popw_daytype_simday", add=(SIMILAR_DAY_FEATURE,)
)
#: Plus the ten calendar attributes (demand/R-005 E-001, rejected; a reference).
LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_CALENDAR = LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY.with_changes(
    name="lightgbm_msm_popw_daytype_simday_calendar", add=DAY_CALENDAR_FEATURES
)
#: Plus the six calendar counts alone (demand/R-005 E-004, rejected; a reference).
LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_CALENDARCOUNTS = LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY.with_changes(
    name="lightgbm_msm_popw_daytype_simday_calendarcounts", add=CALENDAR_COUNT_FEATURES
)
#: Plus the holiday degree alone (demand/R-005 E-002, rejected; a reference).
LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_HOLIDAYDEGREE = LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY.with_changes(
    name="lightgbm_msm_popw_daytype_simday_holidaydegree", add=HOLIDAY_DEGREE_FEATURES
)
#: Plus the two holiday distances alone (demand/R-005 E-003, rejected; a reference).
LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_HOLIDAYDISTANCE = LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY.with_changes(
    name="lightgbm_msm_popw_daytype_simday_holidaydistance", add=HOLIDAY_DISTANCE_FEATURES
)

#: Every preset by name: the registry keys of the demand backtest script.
PRESETS: dict[str, Preset] = {
    preset.name: preset
    for preset in (
        LIGHTGBM,
        LIGHTGBM_MSM,
        LIGHTGBM_MSM_POPW,
        LIGHTGBM_MSM_POPW_DAYTYPE,
        LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY,
        LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_CALENDAR,
        LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_CALENDARCOUNTS,
        LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_HOLIDAYDEGREE,
        LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_HOLIDAYDISTANCE,
    )
}
