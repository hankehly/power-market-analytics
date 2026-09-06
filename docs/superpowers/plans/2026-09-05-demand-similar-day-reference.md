# Learned Similar-Day Reference Load (R-004 E-002) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the strategy `lightgbm_msm_popw_daytype_simday` to the demand task: the baseline plus `similar_day_demand_kwh`, the でんき予報 hourly load of the nearest day in D − 364 ± 30 under a learned seven-part distance, divided by 2 per period.

**Architecture:** One new module, `tasks/demand/similar_day.py`, holds the pair differences, the softmax-weighted distance, its nonlinear-least-squares fit, the selection, the feature join and the retrieval check. Four new frames and four loaders feed it; a subclass of the day-type strategy wires it into the LightGBM framework; a new `ForecastStrategy.diagnostics` hook lets both backtest scripts log the selection and retrieval frames.

**Tech Stack:** Python 3.12, pandas, NumPy, `scipy.optimize.least_squares`, LightGBM, MLflow, PySpark warehouse reads, pytest (100 % coverage gate), ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-05-demand-similar-day-reference-design.md` (approved 2026-09-05). Research record: E-002 of `docs/research/demand/R-004-prior-year-load-lag.md`.

## Global Constraints

- Coverage gate 100 % (`just test`); every new line must be exercised. `just lint` and `just mypy` clean.
- NumPy-style docstrings (`Parameters` / `Returns` / `Raises`, underlined headers) on every public function and class.
- Pandas rules: domain wrappers via `from_df`, every `merge` with `how=`, `on=`, `validate=`; no in-place mutation in shared code.
- The PostToolUse hook runs `ruff format` + `ruff check --fix` on every `.py` file you write; re-read a file before editing it again.
- Commits: `type(scope): description` on branch `feature/demand-similar-day-reference`; scope `demand` for the task code, `forecasting` for the hook, `docs`/`research` for docs. Trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Anything that creates a SparkSession runs in the devcontainer (`just python …`); pytest runs host-side (`just test`).
- Spec deviations taken in this plan (recorded in the spec's §4.2 in Task 9): the forecast profiles come from a new frame `AreaWeatherForecast` (not by widening `AreaTemperatureForecast`, which many tests construct); the observed frame is keyed `obs_date` like `AreaTemperature`; the holiday distances are computed in pandas (`ffill`/`bfill`) rather than SQL window functions.

## File structure

| File | Responsibility |
|---|---|
| `power_market_analytics/tasks/demand/frames.py` (modify) | `AreaHourlyLoad` (restored from commit `83b1f91`), `AreaWeatherForecast` (+ `temperature_forecast()`), `AreaObservedWeather`, `DayCalendar` (+ `day_types()`), `HOLIDAY_DEGREE_LEVELS` |
| `power_market_analytics/tasks/demand/datasets.py` (modify) | `_load_station_weights` (extracted), `load_area_hourly_load` (restored), `load_day_calendar`, `load_area_weather_forecast_population_weighted`, `load_area_observed_weather_population_weighted` |
| `power_market_analytics/tasks/demand/similar_day.py` (create) | constants, `DayPairDifferences`, `SimilarDayTrainingPairs`, `SimilarDaySelection`, `SimilarDayRetrieval`, `SimilarDayWeights`, `fit_similar_day_weights`, `SimilarDaySelector`, `join_similar_day_load`, `retrieval_metrics` |
| `power_market_analytics/forecasting/strategy.py` (modify) | `ForecastStrategy.diagnostics` default hook |
| `power_market_analytics/tasks/demand/strategies/lgbm.py` (modify) | `DemandLightGbmMsmPopWeightedDayTypeSimilarDayEvalSet`, `LightGbmMsmPopWeightedDayTypeSimilarDayStrategy` |
| `power_market_analytics/tasks/demand/strategies/__init__.py` (modify) | registry entry, `build_strategy` branch |
| `scripts/demand_backtest.py`, `scripts/spot_price_backtest.py` (modify) | call `strategy.diagnostics`, log each frame as `<stem>.csv` |
| `pyproject.toml` (modify) | `scipy>=1.18` dependency; mypy `scipy.*` ignore |
| `tests/conftest.py` (modify) | `fct_area_power_usage_hourly` fixture table, `dim_date` span + `holiday_degree`, humidity/rain on both weather tables |
| `tests/test_demand_frames.py` (create), `tests/test_demand_similar_day.py` (create), `tests/test_demand_datasets.py`, `tests/test_demand_lgbm.py`, `tests/test_demand_strategies.py`, `tests/test_demand_scripts.py`, `tests/test_spot_price_scripts.py`, `tests/test_forecasting_backtest.py` (modify) | tests |
| `CLAUDE.md`, the spec, `docs/research/demand/R-004-prior-year-load-lag.md` | docs |

Conventions used by every task: `GRAIN_COLS = ["trade_date", "time_code"]`; hour convention `hour_ending = (time_code + 1) // 2` (`features.hour_ending_of`); frames are validated with `Frame.from_df(df)` and expose `.df`.

---

### Task 1: Frames

**Files:**
- Modify: `power_market_analytics/tasks/demand/frames.py` (append after `DayTypeCalendar`)
- Create: `tests/test_demand_frames.py`

**Interfaces:**
- Produces: `AreaHourlyLoad` (keys `load_date`, `hour_ending`; `demand_kwh` float64 > 0), `AreaWeatherForecast` (keys `trade_date`, `hour_ending`; `forecast_temperature_c`, `forecast_relative_humidity_pct`, `forecast_precipitation_mm` float64 nullable; `temperature_forecast() -> AreaTemperatureForecast`), `AreaObservedWeather` (keys `obs_date`, `hour_ending`; `temperature_c`, `humidity_pct`, `precipitation_mm` float64 nullable), `DayCalendar` (key `trade_date`; `day_type` int64, `days_since_holiday` int64, `days_until_holiday` int64, `holiday_degree` float64; `day_types() -> DayTypeCalendar`), `HOLIDAY_DEGREE_LEVELS = (0.0, 0.3, 0.5, 0.8, 1.0)`.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the demand task's hourly-load, weather-profile and calendar frames."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.tasks.demand.frames import (
    HOLIDAY_DEGREE_LEVELS,
    AreaHourlyLoad,
    AreaObservedWeather,
    AreaTemperatureForecast,
    AreaWeatherForecast,
    DayCalendar,
    DayTypeCalendar,
)

DAY = pd.Timestamp("2024-04-10")


def hourly(**overrides) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "load_date": [DAY, DAY],
            "hour_ending": np.array([1, 2], dtype="int64"),
            "demand_kwh": [30_000_000.0, 29_000_000.0],
        }
    )
    return df.assign(**overrides)


class TestAreaHourlyLoad:
    def test_keys_and_columns(self):
        frame = AreaHourlyLoad.from_df(hourly())
        assert frame.keys == ["load_date", "hour_ending"]
        assert list(frame.df.columns) == ["load_date", "hour_ending", "demand_kwh"]

    def test_hour_outside_1_24_is_rejected(self):
        with pytest.raises(ValueError, match="hour_ending outside 1..24"):
            AreaHourlyLoad.from_df(hourly(hour_ending=np.array([0, 25], dtype="int64")))

    def test_non_positive_load_is_rejected(self):
        with pytest.raises(ValueError, match=r"demand_kwh must be positive; 1 row\(s\)"):
            AreaHourlyLoad.from_df(hourly(demand_kwh=[30_000_000.0, 0.0]))


def weather_forecast(**overrides) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "trade_date": [DAY, DAY],
            "hour_ending": np.array([1, 2], dtype="int64"),
            "forecast_temperature_c": [10.0, 11.0],
            "forecast_relative_humidity_pct": [60.0, np.nan],
            "forecast_precipitation_mm": [0.0, 0.5],
        }
    )
    return df.assign(**overrides)


class TestAreaWeatherForecast:
    def test_nullable_measures_and_keys(self):
        frame = AreaWeatherForecast.from_df(weather_forecast())
        assert frame.keys == ["trade_date", "hour_ending"]
        assert frame.df["forecast_relative_humidity_pct"].isna().tolist() == [False, True]

    def test_temperature_forecast_view(self):
        view = AreaWeatherForecast.from_df(weather_forecast()).temperature_forecast()
        assert type(view) is AreaTemperatureForecast
        assert list(view.df.columns) == ["trade_date", "hour_ending", "forecast_temperature_c"]
        assert view.df["forecast_temperature_c"].tolist() == [10.0, 11.0]

    def test_hour_outside_1_24_is_rejected(self):
        with pytest.raises(ValueError, match="hour_ending outside 1..24"):
            AreaWeatherForecast.from_df(
                weather_forecast(hour_ending=np.array([1, 25], dtype="int64"))
            )


class TestAreaObservedWeather:
    def test_keys_and_nullable_measures(self):
        frame = AreaObservedWeather.from_df(
            pd.DataFrame(
                {
                    "obs_date": [DAY],
                    "hour_ending": np.array([24], dtype="int64"),
                    "temperature_c": [np.nan],
                    "humidity_pct": [70.0],
                    "precipitation_mm": [0.0],
                }
            )
        )
        assert frame.keys == ["obs_date", "hour_ending"]
        assert np.isnan(frame.df["temperature_c"].iloc[0])

    def test_hour_outside_1_24_is_rejected(self):
        with pytest.raises(ValueError, match="hour_ending outside 1..24"):
            AreaObservedWeather.from_df(
                pd.DataFrame(
                    {
                        "obs_date": [DAY],
                        "hour_ending": np.array([0], dtype="int64"),
                        "temperature_c": [1.0],
                        "humidity_pct": [1.0],
                        "precipitation_mm": [0.0],
                    }
                )
            )


def calendar(**overrides) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "trade_date": [DAY, DAY + pd.Timedelta(days=1)],
            "day_type": np.array([0, 2], dtype="int64"),
            "days_since_holiday": np.array([3, 0], dtype="int64"),
            "days_until_holiday": np.array([1, 0], dtype="int64"),
            "holiday_degree": [0.0, 1.0],
        }
    )
    return df.assign(**overrides)


class TestDayCalendar:
    def test_levels(self):
        assert HOLIDAY_DEGREE_LEVELS == (0.0, 0.3, 0.5, 0.8, 1.0)

    def test_keys_and_day_types_view(self):
        frame = DayCalendar.from_df(calendar())
        assert frame.keys == ["trade_date"]
        view = frame.day_types()
        assert type(view) is DayTypeCalendar
        assert view.df["day_type"].tolist() == [0, 2]

    def test_day_type_outside_levels_is_rejected(self):
        with pytest.raises(ValueError, match="day_type outside 0..2"):
            DayCalendar.from_df(calendar(day_type=np.array([0, 3], dtype="int64")))

    def test_negative_holiday_distance_is_rejected(self):
        with pytest.raises(ValueError, match="days_since_holiday must be >= 0"):
            DayCalendar.from_df(calendar(days_since_holiday=np.array([-1, 0], dtype="int64")))
        with pytest.raises(ValueError, match="days_until_holiday must be >= 0"):
            DayCalendar.from_df(calendar(days_until_holiday=np.array([1, -2], dtype="int64")))

    def test_holiday_degree_outside_levels_is_rejected(self):
        with pytest.raises(ValueError, match=r"holiday_degree outside \(0.0, 0.3, 0.5, 0.8, 1.0\)"):
            DayCalendar.from_df(calendar(holiday_degree=[0.0, 0.9]))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_demand_frames.py -q --no-cov`
Expected: FAIL with `ImportError: cannot import name 'HOLIDAY_DEGREE_LEVELS'`.

- [ ] **Step 3: Append the frames to `frames.py`**

Add `numpy` to the imports (`import numpy as np`), then append after `DayTypeCalendar`:

```python
class AreaHourlyLoad(DomainFrame):
    """Hourly area load history: energy over each hour in kWh, as
    ``fct_area_power_usage_hourly`` publishes it (the でんき予報 1時間平均 over
    one hour). ``hour_ending`` is the hour label 1..24 shared with
    :class:`AreaTemperature` (the fact's ``hour_of_day`` + 1), so a delivery
    period maps to its hour through ``hour_ending = (time_code + 1) // 2``.
    Loads are positive: the fact never carries TEPCO's not-yet-final zero, so
    a zero here would be a load error, not a reading.

    Grain: (load_date, hour_ending).
    """

    schema = {
        "load_date": "datetime64[ns]",
        "hour_ending": "int64",
        "demand_kwh": "float64",
    }
    keys = ["load_date", "hour_ending"]
    non_null_cols = ["demand_kwh"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        _check_hour_ending(cls.__name__, df)
        bad = df[df["demand_kwh"] <= 0]
        if not bad.empty:
            first = bad.iloc[0]
            raise ValueError(
                f"{cls.__name__}: demand_kwh must be positive; {len(bad)} row(s) are not "
                f"(e.g. {first['load_date'].date()} hour {int(first['hour_ending'])})"
            )


class AreaWeatherForecast(DomainFrame):
    """Hourly population-weighted MSM forecast of temperature, relative humidity
    and rain for an area, keyed by the delivery day it is valid for.

    Same grain and hour convention as :class:`AreaTemperatureForecast`; the
    three measures are nullable (an hour no weighted station forecast). The
    temperature column alone is what the parent strategies consume, exposed
    through :meth:`temperature_forecast`.

    Grain: (trade_date, hour_ending).
    """

    schema = {
        "trade_date": "datetime64[ns]",
        "hour_ending": "int64",
        "forecast_temperature_c": "float64",
        "forecast_relative_humidity_pct": "float64",
        "forecast_precipitation_mm": "float64",
    }
    keys = ["trade_date", "hour_ending"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        _check_hour_ending(cls.__name__, df)

    def temperature_forecast(self) -> AreaTemperatureForecast:
        """The temperature column as the frame the parent strategies take.

        Returns
        -------
        AreaTemperatureForecast
        """
        return AreaTemperatureForecast.from_df(
            self.df[["trade_date", "hour_ending", "forecast_temperature_c"]]
        )


class AreaObservedWeather(DomainFrame):
    """Hourly population-weighted observed temperature, relative humidity and
    rain for an area (``fct_jma_weather_hourly`` over the weighted stations).

    Same grain and hour convention as :class:`AreaTemperature`; the measures
    are nullable (an hour at which no weighted station reported).

    Grain: (obs_date, hour_ending).
    """

    schema = {
        "obs_date": "datetime64[ns]",
        "hour_ending": "int64",
        "temperature_c": "float64",
        "humidity_pct": "float64",
        "precipitation_mm": "float64",
    }
    keys = ["obs_date", "hour_ending"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        _check_hour_ending(cls.__name__, df)


#: The values ``dim_date.holiday_degree`` takes (the graded 休日度合い).
HOLIDAY_DEGREE_LEVELS: tuple[float, ...] = (0.0, 0.3, 0.5, 0.8, 1.0)


class DayCalendar(DomainFrame):
    """Calendar attributes of every ``dim_date`` day the similar-day selector reads.

    ``day_type`` is :class:`DayTypeCalendar`'s code (for the parent strategy);
    ``days_since_holiday`` / ``days_until_holiday`` count calendar days to the
    nearest named holiday (``dim_date.is_holiday``; 0 on a holiday itself);
    ``holiday_degree`` is ``dim_date.holiday_degree``.

    Grain: (trade_date).
    """

    schema = {
        "trade_date": "datetime64[ns]",
        "day_type": "int64",
        "days_since_holiday": "int64",
        "days_until_holiday": "int64",
        "holiday_degree": "float64",
    }
    keys = ["trade_date"]
    non_null_cols = ["day_type", "days_since_holiday", "days_until_holiday", "holiday_degree"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        last = len(DAY_TYPE_LEVELS) - 1
        bad = df.loc[~df["day_type"].between(0, last), "day_type"]
        if not bad.empty:
            codes = sorted(int(code) for code in bad.unique())
            raise ValueError(f"{cls.__name__}: day_type outside 0..{last}: {codes}")
        for col in ("days_since_holiday", "days_until_holiday"):
            if (df[col] < 0).any():
                raise ValueError(f"{cls.__name__}: {col} must be >= 0")
        levels = np.asarray(HOLIDAY_DEGREE_LEVELS)
        off = ~np.isclose(df["holiday_degree"].to_numpy()[:, None], levels[None, :]).any(axis=1)
        if off.any():
            values = sorted(float(v) for v in df.loc[off, "holiday_degree"].unique())
            raise ValueError(
                f"{cls.__name__}: holiday_degree outside {HOLIDAY_DEGREE_LEVELS}: {values}"
            )

    def day_types(self) -> DayTypeCalendar:
        """The day-type column as the frame the parent strategy takes.

        Returns
        -------
        DayTypeCalendar
        """
        return DayTypeCalendar.from_df(self.df[["trade_date", "day_type"]])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_demand_frames.py -q --no-cov`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/tasks/demand/frames.py tests/test_demand_frames.py
git commit -m "feat(demand): hourly-load, weather-profile and calendar frames for the similar-day selector

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
### Task 2: Test fixtures for the new inputs

**Files:**
- Modify: `tests/conftest.py`

**Interfaces:**
- Produces (module constants for later tests): `CALENDAR_DAYS` (= `HOURLY_LOAD_DAYS`, `DEMAND_DAYS[0] − 394` days through `DEMAND_DAYS[-1]`), `synthetic_hourly_load(day, hour_of_day) -> int`, `synthetic_holiday_degree(day) -> float`, `synthetic_humidity(day, hour_ending) -> float`, `synthetic_precipitation(day, hour_ending) -> float`, `synthetic_forecast_humidity(day, hour_ending)`, `synthetic_forecast_precipitation(day, hour_ending)`, `SECOND_STATION_FORECAST_HUMIDITY_OFFSET_PCT = 5.0`, `SECOND_STATION_FORECAST_RAIN_OFFSET_MM = 0.1`; `CuratedWarehouse.hourly_load` (pandas frame of `fct_area_power_usage_hourly`); `CuratedWarehouse.dates` now spans `CALENDAR_DAYS` and carries `holiday_degree`; `CuratedWarehouse.weather` carries `humidity_pct`, `precipitation_mm`; `CuratedWarehouse.weather_forecast` carries `forecast_relative_humidity_pct`, `forecast_precipitation_mm`.

- [ ] **Step 1: Add the constants and synthetic functions** (after `FORECAST_MISSING_DAY`)

```python
#: Days of hourly load history (fct_area_power_usage_hourly, tokyo) and of the
#: dim_date spine: from the earliest window day a DEMAND_DAYS target can have
#: (394 days before the first) through the last demand day.
HOURLY_LOAD_DAYS = pd.date_range(
    DEMAND_DAYS[0] - pd.Timedelta(days=394), DEMAND_DAYS[-1], freq="D"
)
CALENDAR_DAYS = HOURLY_LOAD_DAYS
#: Second-station offsets for the two non-temperature MSM measures.
SECOND_STATION_FORECAST_HUMIDITY_OFFSET_PCT = 5.0
SECOND_STATION_FORECAST_RAIN_OFFSET_MM = 0.1


def synthetic_hourly_load(day: pd.Timestamp, hour_of_day: int) -> int:
    """Deterministic hourly energy in kWh: daily shape, weekend dip, slow drift.

    Multiples of 10,000 like the でんき予報 fact (integer 万kW × 10,000).
    """
    day_index = (day - HOURLY_LOAD_DAYS[0]).days
    shape = 30_000_000 - 8_000_000 * math.cos(2 * math.pi * hour_of_day / 24)
    weekend = -2_000_000 if day.dayofweek >= 5 else 0
    return int(round((shape + weekend + 10_000 * day_index) / 10_000) * 10_000)


def synthetic_holiday_degree(day: pd.Timestamp) -> float:
    """dim_date.holiday_degree in the fixture: 1.0 on a holiday or Sunday, 0.8 on a
    Saturday, 0.5 on a working day squeezed between two off days, else 0."""
    off = day in HOLIDAYS_2024_SPRING or day.dayofweek >= 5
    if day in HOLIDAYS_2024_SPRING or day.dayofweek == 6:
        return 1.0
    if day.dayofweek == 5:
        return 0.8
    before, after = day - pd.Timedelta(days=1), day + pd.Timedelta(days=1)
    if not off and all(d in HOLIDAYS_2024_SPRING or d.dayofweek >= 5 for d in (before, after)):
        return 0.5
    return 0.0


def synthetic_humidity(day: pd.Timestamp, hour_ending: int) -> float:
    """Deterministic hourly relative humidity in %: drier by day, wetter overnight."""
    day_index = (day - PRICE_DAYS[0]).days
    return round(65.0 + 0.1 * (day_index % 20) - 10.0 * math.sin(2 * math.pi * (hour_ending - 9) / 24), 1)


def synthetic_precipitation(day: pd.Timestamp, hour_ending: int) -> float:
    """Deterministic hourly rain in mm: dry except a wet afternoon every fifth day."""
    day_index = (day - PRICE_DAYS[0]).days
    return 1.5 if day_index % 5 == 0 and 13 <= hour_ending <= 16 else 0.0


def synthetic_forecast_humidity(day: pd.Timestamp, hour_ending: int) -> float:
    """MSM humidity forecast: the observation plus a small hour-dependent error."""
    return round(synthetic_humidity(day, hour_ending) + 2.0 * math.cos(hour_ending / 4.0), 2)


def synthetic_forecast_precipitation(day: pd.Timestamp, hour_ending: int) -> float:
    """MSM rain forecast: the observation scaled by 0.8 (never negative)."""
    return round(0.8 * synthetic_precipitation(day, hour_ending), 2)
```

- [ ] **Step 2: Extend the fixture bodies and tables**

In `CuratedWarehouse` add the field `hourly_load: pd.DataFrame` (docstring: "Contents of ``fct_area_power_usage_hourly`` (tokyo, ``HOURLY_LOAD_DAYS`` × hours 0–23, ``synthetic_hourly_load``)") and amend the `dates`, `weather` and `weather_forecast` docstrings.

Replace the `dates` construction with:

```python
    dates = pd.DataFrame(
        {
            "date_key": [day.date() for day in CALENDAR_DAYS],
            "is_weekend": [day.dayofweek >= 5 for day in CALENDAR_DAYS],
            "is_holiday": [day in HOLIDAYS_2024_SPRING for day in CALENDAR_DAYS],
            "holiday_degree": [synthetic_holiday_degree(day) for day in CALENDAR_DAYS],
        }
    )
```

In the weather loop, append humidity and rain to each row and record:

```python
            weather_rows.append(
                (
                    TOKYO_STATION_ID,
                    (day + pd.Timedelta(hours=hour)).to_pydatetime(),
                    (day + pd.Timedelta(hours=hour - 1)).to_pydatetime(),
                    day.date(),
                    temperature,
                    synthetic_humidity(day, hour),
                    synthetic_precipitation(day, hour),
                )
            )
            weather_records.append(
                {
                    "station_id": TOKYO_STATION_ID,
                    "date_key": day.date(),
                    "hour_ending": hour,
                    "temperature_c": temperature,
                    "humidity_pct": synthetic_humidity(day, hour),
                    "precipitation_mm": synthetic_precipitation(day, hour),
                }
            )
```

In the forecast loop, compute the three measures per station (`offset` is the tuple index of the station: `(TOKYO_STATION_ID, 0.0, 0.0, 0.0)` and `(TOKYO_SECOND_STATION_ID, SECOND_STATION_FORECAST_OFFSET_C, SECOND_STATION_FORECAST_HUMIDITY_OFFSET_PCT, SECOND_STATION_FORECAST_RAIN_OFFSET_MM)`), append `humidity` and `rain` after `forecast` in the row tuple and add `"forecast_relative_humidity_pct": humidity, "forecast_precipitation_mm": rain` to the record. The chubu two-vintage rows get `55.0, 0.0` appended.

Add the hourly-load rows after the demand loop:

```python
    hourly_load_rows: list[tuple] = []
    hourly_load_records: list[dict] = []
    for day in HOURLY_LOAD_DAYS:
        for hour in range(24):
            load_kwh = synthetic_hourly_load(day, hour)
            hourly_load_rows.append(
                (
                    day.date(),
                    hour,
                    TOKYO_AREA_KEY,
                    (day + pd.Timedelta(hours=hour)).to_pydatetime(),
                    load_kwh,
                )
            )
            hourly_load_records.append(
                {"date_key": day.date(), "hour_of_day": hour, "demand_kwh": load_kwh}
            )
    hourly_load = pd.DataFrame(hourly_load_records)
```

Table writes (widen the schemas and add the new table):

```python
    spark.createDataFrame(
        weather_rows,
        "station_id string, observed_at timestamp, observed_hour_start_at timestamp, "
        "date_key date, temperature_c double, humidity_pct double, precipitation_mm double",
    ).write.mode("overwrite").saveAsTable("pma_curated.fct_jma_weather_hourly")
    spark.createDataFrame(
        forecast_rows,
        "station_id string, forecast_reference_at timestamp, forecast_valid_at timestamp, "
        "forecast_hour_start_at timestamp, date_key date, temperature_c double, "
        "relative_humidity_pct double, precipitation_mm double",
    ).write.mode("overwrite").saveAsTable("pma_curated.fct_jma_msm_weather_forecast_hourly")
    spark.createDataFrame(
        dates, "date_key date, is_weekend boolean, is_holiday boolean, holiday_degree double"
    ).write.mode("overwrite").saveAsTable("pma_curated.dim_date")
    spark.createDataFrame(
        hourly_load_rows,
        "date_key date, hour_of_day int, area_key int, delivery_datetime timestamp, "
        "demand_kwh bigint",
    ).write.mode("overwrite").saveAsTable("pma_curated.fct_area_power_usage_hourly")
```

and pass `hourly_load=hourly_load` to the returned `CuratedWarehouse`.

- [ ] **Step 3: Run the whole suite**

Run: `just test`
Expected: PASS. If a test asserts the exact row count of `dim_date` or the exact column list of the weather tables, update that expectation (the loaders under test select named columns, so `load_day_types` / `load_area_temperature` keep working; `expected_day_type` in `tests/test_demand_datasets.py` already handles any day).

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/test_demand_datasets.py
git commit -m "test(demand): hourly load, holiday degree and humidity/rain in the curated fixture

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
### Task 3: Loaders

**Files:**
- Modify: `power_market_analytics/tasks/demand/datasets.py`
- Modify: `tests/test_demand_datasets.py`

**Interfaces:**
- Consumes: Task 1 frames; Task 2 fixture columns.
- Produces: `_load_station_weights(area_code, census_year, spark, caller) -> tuple[int, pd.DataFrame]` (private; year used + the used weights sorted descending); `load_area_hourly_load(area_code="tokyo", spark=None) -> AreaHourlyLoad`; `load_day_calendar(spark=None) -> DayCalendar`; `PopulationWeightedWeatherForecast(forecast: AreaWeatherForecast, census_year: int, n_stations: int)` and `load_area_weather_forecast_population_weighted(area_code="tokyo", census_year=None, spark=None)`; `PopulationWeightedObservedWeather(weather: AreaObservedWeather, census_year: int, n_stations: int)` and `load_area_observed_weather_population_weighted(area_code="tokyo", census_year=None, spark=None)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_demand_datasets.py`; extend its imports with the new loaders, frames and `CALENDAR_DAYS`, `HOLIDAYS_2024_SPRING`, `HOURLY_LOAD_DAYS`, `SECOND_STATION_FORECAST_HUMIDITY_OFFSET_PCT`, `SECOND_STATION_FORECAST_RAIN_OFFSET_MM`, `STATION_POPULATION_WEIGHTS`, `TOKYO_SECOND_STATION_ID`, `synthetic_holiday_degree`, `synthetic_hourly_load`, `synthetic_humidity`, `synthetic_precipitation`)

```python
class TestLoadAreaHourlyLoad:
    def test_loads_every_hour_as_hour_ending(self, spark, curated_warehouse: CuratedWarehouse):
        frame = load_area_hourly_load("tokyo", spark=spark)
        assert type(frame) is AreaHourlyLoad
        assert len(frame) == len(HOURLY_LOAD_DAYS) * 24
        first = frame.df.iloc[0]
        assert first["load_date"] == HOURLY_LOAD_DAYS[0]
        assert first["hour_ending"] == 1
        assert first["demand_kwh"] == float(synthetic_hourly_load(HOURLY_LOAD_DAYS[0], 0))
        assert frame.df["hour_ending"].max() == 24

    def test_unknown_area_raises(self, spark, curated_warehouse):
        with pytest.raises(ValueError, match="No hourly load history found for area_code='kansai'"):
            load_area_hourly_load("kansai", spark=spark)


def expected_holiday_distances(day: pd.Timestamp) -> tuple[int, int]:
    holidays = sorted(HOLIDAYS_2024_SPRING)
    if day in holidays:
        return 0, 0
    before = [h for h in holidays if h < day]
    after = [h for h in holidays if h > day]
    return (day - before[-1]).days, (after[0] - day).days


class TestLoadDayCalendar:
    def test_attributes_per_day(self, spark, curated_warehouse: CuratedWarehouse):
        frame = load_day_calendar(spark=spark)
        assert type(frame) is DayCalendar
        # Days before the first holiday (03-20) and after the last (05-06) have no distance.
        first, last = min(HOLIDAYS_2024_SPRING), max(HOLIDAYS_2024_SPRING)
        assert frame.df["trade_date"].min() == first
        assert frame.df["trade_date"].max() == last
        assert len(frame) == (last - first).days + 1
        by_day = frame.df.set_index("trade_date")
        for day in (pd.Timestamp("2024-04-10"), pd.Timestamp("2024-04-30"), first, last):
            since, until = expected_holiday_distances(day)
            assert by_day.loc[day, "days_since_holiday"] == since
            assert by_day.loc[day, "days_until_holiday"] == until
            assert by_day.loc[day, "holiday_degree"] == synthetic_holiday_degree(day)
            assert by_day.loc[day, "day_type"] == expected_day_type(day)

    def test_day_types_view_matches_load_day_types(self, spark, curated_warehouse):
        calendar = load_day_calendar(spark=spark).day_types()
        full = load_day_types(spark=spark).df.set_index("trade_date")
        view = calendar.df.set_index("trade_date")
        assert view["day_type"].equals(full.loc[view.index, "day_type"])

    def test_empty_dim_date_raises(self, spark, monkeypatch):
        monkeypatch.setattr(
            "power_market_analytics.tasks.demand.datasets.query_pandas",
            lambda *a, **k: pd.DataFrame(),
        )
        with pytest.raises(ValueError, match="No calendar days found in dim_date"):
            load_day_calendar(spark=spark)


def weighted(first: float, second: float | None, w1: float, w2: float) -> float:
    if second is None:
        return first
    return (w1 * first + w2 * second) / (w1 + w2)


class TestLoadAreaWeatherForecastPopulationWeighted:
    def test_three_measures_weighted_and_renormalised(
        self, spark, curated_warehouse: CuratedWarehouse
    ):
        loaded = load_area_weather_forecast_population_weighted("tokyo", spark=spark)
        assert type(loaded) is PopulationWeightedWeatherForecast
        assert type(loaded.forecast) is AreaWeatherForecast
        assert loaded.census_year == 2020
        assert loaded.n_stations == 2
        w1, w2 = (STATION_POPULATION_WEIGHTS[2020][s] for s in (TOKYO_STATION_ID, TOKYO_SECOND_STATION_ID))
        by_hour = loaded.forecast.df.set_index(["trade_date", "hour_ending"])
        day, hour = pd.Timestamp("2024-04-10"), 7
        t = synthetic_forecast_temperature(day, hour)
        h = synthetic_forecast_humidity(day, hour)
        r = synthetic_forecast_precipitation(day, hour)
        row = by_hour.loc[(day, hour)]
        assert row["forecast_temperature_c"] == pytest.approx(
            weighted(t, t + SECOND_STATION_FORECAST_OFFSET_C, w1, w2)
        )
        assert row["forecast_relative_humidity_pct"] == pytest.approx(
            weighted(h, h + SECOND_STATION_FORECAST_HUMIDITY_OFFSET_PCT, w1, w2)
        )
        assert row["forecast_precipitation_mm"] == pytest.approx(
            weighted(r, r + SECOND_STATION_FORECAST_RAIN_OFFSET_MM, w1, w2)
        )
        # The hour the second station lacks falls back to the first station alone.
        day, hour = SECOND_STATION_MISSING_HOUR
        assert by_hour.loc[(day, hour), "forecast_temperature_c"] == pytest.approx(
            synthetic_forecast_temperature(day, hour)
        )
        assert loaded.forecast.temperature_forecast().df.equals(
            load_area_temperature_forecast_population_weighted("tokyo", spark=spark).forecast.df
        )

    def test_explicit_census_year(self, spark, curated_warehouse):
        assert load_area_weather_forecast_population_weighted(
            "tokyo", census_year=2015, spark=spark
        ).census_year == 2015

    def test_two_vintages_for_one_hour_raise(self, spark, curated_warehouse):
        with pytest.raises(ValueError, match="forecast vintages"):
            load_area_weather_forecast_population_weighted("chubu", spark=spark)

    def test_no_weights_raise(self, spark, curated_warehouse):
        with pytest.raises(ValueError, match="No station population weights found"):
            load_area_weather_forecast_population_weighted("tokyo", census_year=1999, spark=spark)

    def test_no_forecast_rows_raise(self, spark, curated_warehouse):
        with pytest.raises(ValueError, match="No weather forecasts found for the weighted stations"):
            load_area_weather_forecast_population_weighted("kansai", spark=spark)


class TestLoadAreaObservedWeatherPopulationWeighted:
    def test_only_the_observing_station_counts(self, spark, curated_warehouse: CuratedWarehouse):
        loaded = load_area_observed_weather_population_weighted("tokyo", spark=spark)
        assert type(loaded) is PopulationWeightedObservedWeather
        assert type(loaded.weather) is AreaObservedWeather
        assert loaded.census_year == 2020
        assert loaded.n_stations == 2
        # The second station has no observations, so the weighted value is s47662's.
        by_hour = loaded.weather.df.set_index(["obs_date", "hour_ending"])
        day, hour = pd.Timestamp("2024-04-10"), 7
        row = by_hour.loc[(day, hour)]
        assert row["temperature_c"] == pytest.approx(synthetic_temperature(day, hour))
        assert row["humidity_pct"] == pytest.approx(synthetic_humidity(day, hour))
        assert row["precipitation_mm"] == pytest.approx(synthetic_precipitation(day, hour))
        # A missing temperature hour is null for temperature and present for the others.
        day, hour = next(iter(sorted(TEMPERATURE_MISSING_HOURS)))
        assert np.isnan(by_hour.loc[(day, hour), "temperature_c"])
        assert by_hour.loc[(day, hour), "humidity_pct"] == pytest.approx(synthetic_humidity(day, hour))
        assert len(loaded.weather) == len(curated_warehouse.weather)

    def test_explicit_census_year(self, spark, curated_warehouse):
        assert load_area_observed_weather_population_weighted(
            "tokyo", census_year=2015, spark=spark
        ).census_year == 2015

    def test_no_observation_rows_raise(self, spark, curated_warehouse):
        with pytest.raises(ValueError, match="No weather observations found for the weighted stations"):
            load_area_observed_weather_population_weighted("kansai", spark=spark)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_demand_datasets.py -q --no-cov`
Expected: FAIL with `ImportError` on the new names.

- [ ] **Step 3: Implement the loaders**

Extract the weights query from `load_area_temperature_forecast_population_weighted` into a private helper and make the existing loader call it (its behaviour and log line are unchanged):

```python
def _load_station_weights(
    area_code: str, census_year: int | None, spark: SparkSession | None, caller: str
) -> tuple[int, pd.DataFrame]:
    """Census population weights of an area's stations for one vintage.

    Parameters
    ----------
    area_code : str
        dim_area.area_code value.
    census_year : int or None
        Vintage to use; None = the latest loaded.
    spark : pyspark.sql.SparkSession or None
        Existing session to reuse.
    caller : str
        Loader name for the log line.

    Returns
    -------
    tuple of int and pandas.DataFrame
        The vintage used and its weights (``station_id``,
        ``area_population_weight``), largest first.

    Raises
    ------
    ValueError
        If the area has no weights (for that vintage).
    """
    year_clause = "" if census_year is None else f"and w.census_year = {int(census_year)}"
    weights = query_pandas(
        f"""
        select
          w.census_year,
          w.station_id,
          w.area_population_weight
        from pma_curated.fct_census_population_jma_station w
        join pma_curated.dim_area a on w.area_key = a.area_key
        where a.area_code = '{area_code}' {year_clause}
        """,
        spark=spark,
    )
    if weights.empty:
        detail = "" if census_year is None else f", census_year={int(census_year)}"
        raise ValueError(f"No station population weights found for area_code={area_code!r}{detail}")
    year = int(weights["census_year"].max())
    used = weights[weights["census_year"] == year].sort_values(
        "area_population_weight", ascending=False
    )
    logger.info(
        "{}: {} census weights over {} stations for {} (largest: {})",
        caller,
        year,
        len(used),
        area_code,
        ", ".join(
            f"{r.station_id}={r.area_population_weight:.3f}" for r in used.head(5).itertuples()
        ),
    )
    return year, used
```

`load_area_hourly_load` is the function from commit `83b1f91` verbatim (shown in the spec's §3 context; SQL over `fct_area_power_usage_hourly` joined to `dim_area`, `hour_of_day + 1 as hour_ending`, `ValueError("No hourly load history found for area_code=…")` when empty, one log line, `AreaHourlyLoad.from_df`).

```python
def load_day_calendar(spark: SparkSession | None = None) -> DayCalendar:
    """Load the calendar attributes the similar-day selector reads from ``dim_date``.

    ``day_type`` is :func:`day_type_code` of the weekend / holiday flags; the
    two holiday distances count calendar days to the nearest named holiday
    (``is_holiday``: the 国民の祝日 plus the customary 年末年始 / ゴールデン
    ウィーク / お盆 days; 0 on a holiday itself), computed over the gapless
    spine with a forward and a backward fill; days before the spine's first
    holiday or after its last have no distance and are dropped.
    ``holiday_degree`` is the dimension's column.

    Parameters
    ----------
    spark : pyspark.sql.SparkSession, optional
        Existing session to reuse.

    Returns
    -------
    DayCalendar

    Raises
    ------
    ValueError
        If ``dim_date`` returns no rows or the result violates the
        DayCalendar contract.
    """
    pdf = query_pandas(
        """
        select
          d.date_key as trade_date,
          d.is_weekend,
          d.is_holiday,
          d.holiday_degree
        from pma_curated.dim_date d
        """,
        spark=spark,
    )
    if pdf.empty:
        raise ValueError("No calendar days found in dim_date")
    pdf = pdf.assign(trade_date=lambda d: pd.to_datetime(d["trade_date"])).sort_values(
        "trade_date", ignore_index=True
    )
    holiday_dates = pdf["trade_date"].where(pdf["is_holiday"].astype(bool))
    last_holiday = holiday_dates.ffill()
    next_holiday = holiday_dates.bfill()
    pdf = (
        pdf.assign(
            day_type=lambda d: day_type_code(d["is_weekend"], d["is_holiday"]),
            days_since_holiday=(pdf["trade_date"] - last_holiday).dt.days,
            days_until_holiday=(next_holiday - pdf["trade_date"]).dt.days,
        )
        .dropna(subset=["days_since_holiday", "days_until_holiday"])
        .astype(
            {
                "days_since_holiday": "int64",
                "days_until_holiday": "int64",
                "holiday_degree": "float64",
            }
        )
        .reset_index(drop=True)
    )
    logger.info(
        "load_day_calendar: {} days ({}..{}), {} holidays, holiday_degree > 0 on {} days",
        len(pdf),
        pdf["trade_date"].iloc[0].date(),
        pdf["trade_date"].iloc[-1].date(),
        int(pdf["is_holiday"].astype(bool).sum()),
        int((pdf["holiday_degree"] > 0).sum()),
    )
    return DayCalendar.from_df(pdf)
```

The two population-weighted profile loaders share one shape: a weights lookup, one grouped query with a renormalised weighted mean per measure, the vintage check (forecast only), and a NamedTuple result.

```python
def _weighted_mean_sql(measure: str, alias: str) -> str:
    """The SQL for a population-weighted mean renormalised over stations with a value."""
    return (
        f"sum(w.area_population_weight * m.{measure}) "
        f"/ sum(case when m.{measure} is not null then w.area_population_weight end) as {alias}"
    )


class PopulationWeightedWeatherForecast(NamedTuple):
    """A population-weighted area weather forecast plus its weighting provenance.

    Attributes
    ----------
    forecast : AreaWeatherForecast
        The weighted hourly forecast of temperature, humidity and rain by delivery day.
    census_year : int
        Census vintage of the station weights that were applied.
    n_stations : int
        Number of weighted stations in the area for that vintage.
    """

    forecast: AreaWeatherForecast
    census_year: int
    n_stations: int


def load_area_weather_forecast_population_weighted(
    area_code: str = "tokyo",
    census_year: int | None = None,
    spark: SparkSession | None = None,
) -> PopulationWeightedWeatherForecast:
    """Load the population-weighted hourly MSM forecast of temperature, humidity and rain.

    The temperature is exactly :func:`load_area_temperature_forecast_population_weighted`'s;
    ``relative_humidity_pct`` and ``precipitation_mm`` are weighted the same way, each
    renormalised over the stations that have a value for the hour. One vintage per
    delivery-day hour, as there.

    Parameters
    ----------
    area_code : str, default "tokyo"
        dim_area.area_code value.
    census_year : int, optional
        Census vintage whose weights to use; default: the latest loaded.
    spark : pyspark.sql.SparkSession, optional
        Existing session to reuse.

    Returns
    -------
    PopulationWeightedWeatherForecast

    Raises
    ------
    ValueError
        If the area has no weights, none of its weighted stations has forecast
        rows, more than one forecast vintage covers a delivery-day hour, or the
        result violates the AreaWeatherForecast contract.
    """
    year, used = _load_station_weights(
        area_code, census_year, spark, "load_area_weather_forecast_population_weighted"
    )
    measures = ", ".join(
        _weighted_mean_sql(measure, alias)
        for measure, alias in (
            ("temperature_c", "forecast_temperature_c"),
            ("relative_humidity_pct", "forecast_relative_humidity_pct"),
            ("precipitation_mm", "forecast_precipitation_mm"),
        )
    )
    pdf = query_pandas(
        f"""
        select
          m.date_key as trade_date,
          hour(m.forecast_hour_start_at) + 1 as hour_ending,
          m.forecast_reference_at,
          {measures}
        from pma_curated.fct_jma_msm_weather_forecast_hourly m
        join pma_curated.fct_census_population_jma_station w
          on w.station_id = m.station_id and w.census_year = {year}
        join pma_curated.dim_area a on w.area_key = a.area_key
        where a.area_code = '{area_code}'
        group by 1, 2, 3
        """,
        spark=spark,
    )
    if pdf.empty:
        raise ValueError(
            f"No weather forecasts found for the weighted stations of "
            f"area_code={area_code!r} (census_year={year})"
        )
    pdf = (
        pdf.assign(trade_date=lambda d: pd.to_datetime(d["trade_date"]))
        .astype(
            {
                "hour_ending": "int64",
                "forecast_temperature_c": "float64",
                "forecast_relative_humidity_pct": "float64",
                "forecast_precipitation_mm": "float64",
            }
        )
        .sort_values(["trade_date", "hour_ending", "forecast_reference_at"], ignore_index=True)
    )
    _reject_multiple_vintages(pdf, area_code)
    return PopulationWeightedWeatherForecast(
        forecast=AreaWeatherForecast.from_df(pdf.drop(columns="forecast_reference_at")),
        census_year=year,
        n_stations=len(used),
    )
```

`_reject_multiple_vintages(pdf, area_code)` is the existing duplicate-vintage block of `load_area_temperature_forecast_population_weighted` (the `hour_keys` / `duplicated` / `raise ValueError(... "forecast vintages" ...)` lines) moved into a private function and called from both loaders.

```python
class PopulationWeightedObservedWeather(NamedTuple):
    """A population-weighted observed area weather series plus its weighting provenance.

    Attributes
    ----------
    weather : AreaObservedWeather
        The weighted hourly observed temperature, humidity and rain.
    census_year : int
        Census vintage of the station weights that were applied.
    n_stations : int
        Number of weighted stations in the area for that vintage.
    """

    weather: AreaObservedWeather
    census_year: int
    n_stations: int


def load_area_observed_weather_population_weighted(
    area_code: str = "tokyo",
    census_year: int | None = None,
    spark: SparkSession | None = None,
) -> PopulationWeightedObservedWeather:
    """Load the population-weighted hourly observed temperature, humidity and rain.

    Averages ``fct_jma_weather_hourly`` over the area's weighted stations with
    the census weights, each measure renormalised over the stations reporting
    it for the hour; a station without observations simply carries no weight.
    ``hour_ending`` follows :func:`load_area_temperature` (the 24:00 reading
    stays on its observation day as hour 24).

    Parameters
    ----------
    area_code : str, default "tokyo"
        dim_area.area_code value.
    census_year : int, optional
        Census vintage whose weights to use; default: the latest loaded.
    spark : pyspark.sql.SparkSession, optional
        Existing session to reuse.

    Returns
    -------
    PopulationWeightedObservedWeather

    Raises
    ------
    ValueError
        If the area has no weights, none of its weighted stations has
        observations, or the result violates the AreaObservedWeather contract.
    """
    year, used = _load_station_weights(
        area_code, census_year, spark, "load_area_observed_weather_population_weighted"
    )
    measures = ", ".join(
        _weighted_mean_sql(measure, measure)
        for measure in ("temperature_c", "humidity_pct", "precipitation_mm")
    )
    pdf = query_pandas(
        f"""
        select
          m.date_key as obs_date,
          hour(m.observed_hour_start_at) + 1 as hour_ending,
          {measures}
        from pma_curated.fct_jma_weather_hourly m
        join pma_curated.fct_census_population_jma_station w
          on w.station_id = m.station_id and w.census_year = {year}
        join pma_curated.dim_area a on w.area_key = a.area_key
        where a.area_code = '{area_code}'
        group by 1, 2
        """,
        spark=spark,
    )
    if pdf.empty:
        raise ValueError(
            f"No weather observations found for the weighted stations of "
            f"area_code={area_code!r} (census_year={year})"
        )
    pdf = (
        pdf.assign(obs_date=lambda d: pd.to_datetime(d["obs_date"]))
        .astype(
            {
                "hour_ending": "int64",
                "temperature_c": "float64",
                "humidity_pct": "float64",
                "precipitation_mm": "float64",
            }
        )
        .sort_values(["obs_date", "hour_ending"], ignore_index=True)
    )
    return PopulationWeightedObservedWeather(
        weather=AreaObservedWeather.from_df(pdf), census_year=year, n_stations=len(used)
    )
```

Update the module docstring to name the new inputs. The existing `load_area_temperature_forecast_population_weighted` keeps its signature, result type and tests; it now calls `_load_station_weights(..., "load_area_temperature_forecast_population_weighted")` and `_reject_multiple_vintages`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_demand_datasets.py -q --no-cov` then `just test`
Expected: PASS, coverage 100 %.

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/tasks/demand/datasets.py tests/test_demand_datasets.py
git commit -m "feat(demand): hourly load, day calendar and population-weighted weather loaders

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
### Task 4: Selector, part 1 — constants, frames, profiles, candidates, pair differences

**Files:**
- Create: `power_market_analytics/tasks/demand/similar_day.py`
- Create: `tests/test_demand_similar_day.py`

**Interfaces:**
- Consumes: Task 1 frames.
- Produces: constants `SIMILAR_DAY_CENTER_LAG_DAYS = 364`, `SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS = 30`, `SIMILAR_DAY_FEATURE = "similar_day_demand_kwh"`, `PERIODS_PER_HOUR = 2`, `HOURS_PER_DAY = 24`, `SIMILAR_DAY_COMPONENTS` (7 names in order), `MIN_FIT_PAIRS = 8`; frames `DayPairDifferences` (keys `target_date`, `candidate_date`; the 7 parts float64 ≥ 0), `SimilarDayTrainingPairs` (+ `load_difference` ≥ 0), `SimilarDaySelection` (key `trade_date`; `reference_date`, `distance`, `reference_lag_days` int64, `n_candidates` int64, `lag_364_rank` float64 nullable), `SimilarDayRetrieval` (key `trade_date`; `reference_date`, `distance`, `selected_load_difference`, `lag_364_load_difference` nullable, `oracle_date`, `oracle_load_difference`, `selected_rank_by_outcome` int64); `SimilarDaySelector(calendar, weather_forecast, weather_observed, hourly_load, *, center_lag_days=364, half_width_days=30)` with `lags` (ndarray 334..394), `first_candidate_day`, `hourly_load_span`, `scorable_days(days) -> DatetimeIndex`, `differences(days) -> DayPairDifferences`.

- [ ] **Step 1: Write the failing tests** (`tests/test_demand_similar_day.py`, the module's fixtures are reused by Tasks 5 and 6)

```python
"""Tests for the learned similar-day selector (R-004 E-002)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.tasks.demand.frames import (
    AreaHourlyLoad,
    AreaObservedWeather,
    AreaWeatherForecast,
    DayCalendar,
)
from power_market_analytics.tasks.demand.similar_day import (
    HOURS_PER_DAY,
    MIN_FIT_PAIRS,
    PERIODS_PER_HOUR,
    SIMILAR_DAY_CENTER_LAG_DAYS,
    SIMILAR_DAY_COMPONENTS,
    SIMILAR_DAY_FEATURE,
    SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS,
    DayPairDifferences,
    SimilarDaySelection,
    SimilarDaySelector,
    SimilarDayTrainingPairs,
)

#: Calendar, observations and hourly load: 2023-01-01 .. 2024-04-30.
HISTORY_DAYS = pd.date_range("2023-01-01", "2024-04-30", freq="D")
#: Forecast profiles exist for these delivery days only.
FORECAST_DAYS = pd.date_range("2024-01-01", "2024-04-30", freq="D")
HOLIDAYS = (
    pd.Timestamp("2023-01-09"),
    pd.Timestamp("2023-03-21"),
    pd.Timestamp("2023-05-03"),
    pd.Timestamp("2024-01-08"),
    pd.Timestamp("2024-03-20"),
    pd.Timestamp("2024-04-29"),
)
D = pd.Timestamp("2024-04-10")  # a Wednesday; D - 364 = 2023-04-12, also a Wednesday
D_MINUS_364 = D - pd.Timedelta(days=364)


def temperature_at(day: pd.Timestamp, hour: int) -> float:
    doy = day.dayofyear
    return 10.0 + 12.0 * math.sin(2 * math.pi * (doy - 100) / 365) + 4.0 * math.sin(2 * math.pi * (hour - 9) / 24)


def humidity_at(day: pd.Timestamp, hour: int) -> float:
    return 60.0 + 10.0 * math.cos(2 * math.pi * hour / 24) + (day.dayofyear % 7)


def rain_at(day: pd.Timestamp, hour: int) -> float:
    return 1.0 if day.dayofyear % 9 == 0 and 12 <= hour <= 15 else 0.0


def load_at(day: pd.Timestamp, hour: int) -> float:
    weekend = -5_000_000.0 if day.dayofweek >= 5 or day in HOLIDAYS else 0.0
    return 30_000_000.0 - 8_000_000.0 * math.cos(2 * math.pi * hour / 24) + weekend + 1_000.0 * (day - HISTORY_DAYS[0]).days


def holiday_degree_at(day: pd.Timestamp) -> float:
    if day in HOLIDAYS or day.dayofweek == 6:
        return 1.0
    return 0.8 if day.dayofweek == 5 else 0.0


def make_calendar(days=HISTORY_DAYS) -> DayCalendar:
    holidays = sorted(HOLIDAYS)
    rows = []
    for day in days:
        before = [h for h in holidays if h <= day]
        after = [h for h in holidays if h >= day]
        if not before or not after:
            continue
        rows.append(
            {
                "trade_date": day,
                "day_type": 2 if day in HOLIDAYS else (1 if day.dayofweek >= 5 else 0),
                "days_since_holiday": (day - before[-1]).days,
                "days_until_holiday": (after[0] - day).days,
                "holiday_degree": holiday_degree_at(day),
            }
        )
    return DayCalendar.from_df(
        pd.DataFrame(rows).astype(
            {"day_type": "int64", "days_since_holiday": "int64", "days_until_holiday": "int64"}
        )
    )


def make_forecast(days=FORECAST_DAYS, *, drop: set[tuple[pd.Timestamp, int]] = frozenset()) -> AreaWeatherForecast:
    rows = [
        {
            "trade_date": day,
            "hour_ending": h,
            "forecast_temperature_c": temperature_at(day, h) + 0.5,
            "forecast_relative_humidity_pct": humidity_at(day, h) - 2.0,
            "forecast_precipitation_mm": 0.8 * rain_at(day, h),
        }
        for day in days
        for h in range(1, 25)
        if (day, h) not in drop
    ]
    return AreaWeatherForecast.from_df(pd.DataFrame(rows).astype({"hour_ending": "int64"}))


def make_observed(days=HISTORY_DAYS, *, null_hours: set[tuple[pd.Timestamp, int]] = frozenset()) -> AreaObservedWeather:
    rows = [
        {
            "obs_date": day,
            "hour_ending": h,
            "temperature_c": np.nan if (day, h) in null_hours else temperature_at(day, h),
            "humidity_pct": humidity_at(day, h),
            "precipitation_mm": rain_at(day, h),
        }
        for day in days
        for h in range(1, 25)
    ]
    return AreaObservedWeather.from_df(pd.DataFrame(rows).astype({"hour_ending": "int64"}))


def make_hourly_load(days=HISTORY_DAYS) -> AreaHourlyLoad:
    rows = [
        {"load_date": day, "hour_ending": h, "demand_kwh": load_at(day, h)}
        for day in days
        for h in range(1, 25)
    ]
    return AreaHourlyLoad.from_df(pd.DataFrame(rows).astype({"hour_ending": "int64"}))


@pytest.fixture(scope="module")
def selector() -> SimilarDaySelector:
    return SimilarDaySelector(make_calendar(), make_forecast(), make_observed(), make_hourly_load())


class TestConstants:
    def test_values(self):
        assert SIMILAR_DAY_CENTER_LAG_DAYS == 364
        assert SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS == 30
        assert SIMILAR_DAY_FEATURE == "similar_day_demand_kwh"
        assert PERIODS_PER_HOUR == 2
        assert HOURS_PER_DAY == 24
        assert MIN_FIT_PAIRS == 8
        assert SIMILAR_DAY_COMPONENTS == (
            "calendar_days",
            "temperature",
            "humidity",
            "rain",
            "days_since_holiday",
            "days_until_holiday",
            "holiday_degree",
        )


class TestSelectorSetup:
    def test_window_and_candidates(self, selector):
        assert selector.lags.tolist() == list(range(334, 395))
        # Candidates need a calendar row: the calendar starts at the first holiday.
        assert selector.first_candidate_day == HOLIDAYS[0]
        assert selector.hourly_load_span == (HISTORY_DAYS[0], HISTORY_DAYS[-1])

    def test_bad_window_is_rejected(self):
        with pytest.raises(ValueError, match="window"):
            SimilarDaySelector(
                make_calendar(), make_forecast(), make_observed(), make_hourly_load(),
                center_lag_days=10, half_width_days=10,
            )

    def test_no_candidates_is_rejected(self):
        with pytest.raises(ValueError, match="no candidate days"):
            SimilarDaySelector(
                make_calendar(), make_forecast(), make_observed(pd.date_range("2022-01-01", "2022-01-05")), make_hourly_load()
            )

    def test_scorable_days(self, selector):
        days = [
            D,
            pd.Timestamp("2023-12-31"),  # no forecast profile
            pd.Timestamp("2024-01-20"),  # window starts 2022-12-22, before the first candidate
            pd.Timestamp("2024-04-30"),  # calendar ends at the last holiday 04-29
            D,  # duplicate
        ]
        assert selector.scorable_days(days).tolist() == [D]


class TestDifferences:
    def test_one_row_per_window_day(self, selector):
        diffs = selector.differences([D])
        assert type(diffs) is DayPairDifferences
        assert len(diffs) == 61
        assert list(diffs.df.columns) == ["target_date", "candidate_date", *SIMILAR_DAY_COMPONENTS]
        lags = (diffs.df["target_date"] - diffs.df["candidate_date"]).dt.days
        assert lags.tolist() == list(range(394, 333, -1))

    def test_calendar_days_from_the_same_weekday_a_year_back(self, selector):
        df = selector.differences([D]).df.set_index("candidate_date")
        assert df.loc[D_MINUS_364, "calendar_days"] == 0.0
        assert df.loc[D - pd.Timedelta(days=394), "calendar_days"] == 30.0
        assert df.loc[D - pd.Timedelta(days=334), "calendar_days"] == 30.0

    def test_weather_parts_are_hourly_rmse_of_forecast_against_observed(self, selector):
        row = selector.differences([D]).df.set_index("candidate_date").loc[D_MINUS_364]
        expected_t = math.sqrt(
            np.mean([(temperature_at(D, h) + 0.5 - temperature_at(D_MINUS_364, h)) ** 2 for h in range(1, 25)])
        )
        expected_h = math.sqrt(
            np.mean([(humidity_at(D, h) - 2.0 - humidity_at(D_MINUS_364, h)) ** 2 for h in range(1, 25)])
        )
        expected_r = math.sqrt(
            np.mean([(0.8 * rain_at(D, h) - rain_at(D_MINUS_364, h)) ** 2 for h in range(1, 25)])
        )
        assert row["temperature"] == pytest.approx(expected_t)
        assert row["humidity"] == pytest.approx(expected_h)
        assert row["rain"] == pytest.approx(expected_r)

    def test_holiday_parts_are_absolute_differences(self, selector):
        calendar = make_calendar().df.set_index("trade_date")
        row = selector.differences([D]).df.set_index("candidate_date").loc[D_MINUS_364]
        for col in ("days_since_holiday", "days_until_holiday", "holiday_degree"):
            assert row[col] == pytest.approx(abs(calendar.loc[D, col] - calendar.loc[D_MINUS_364, col]))

    def test_a_candidate_missing_an_observed_hour_is_left_out(self):
        selector = SimilarDaySelector(
            make_calendar(), make_forecast(), make_observed(null_hours={(D_MINUS_364, 5)}), make_hourly_load()
        )
        diffs = selector.differences([D]).df
        assert len(diffs) == 60
        assert D_MINUS_364 not in set(diffs["candidate_date"])

    def test_unscorable_days_yield_no_rows(self, selector):
        assert len(selector.differences([pd.Timestamp("2023-12-31")])) == 0


class TestPairFrames:
    def test_negative_part_is_rejected(self, selector):
        df = selector.differences([D]).df.copy()
        df.loc[0, "rain"] = -0.1
        with pytest.raises(ValueError, match="rain must be >= 0"):
            DayPairDifferences.from_df(df)

    def test_candidate_after_target_is_rejected(self, selector):
        df = selector.differences([D]).df.copy()
        df.loc[0, "candidate_date"] = D
        with pytest.raises(ValueError, match="candidate_date must precede target_date"):
            DayPairDifferences.from_df(df)

    def test_training_pairs_need_a_non_negative_load_difference(self, selector):
        df = selector.differences([D]).df.assign(load_difference=-1.0)
        with pytest.raises(ValueError, match="load_difference must be >= 0"):
            SimilarDayTrainingPairs.from_df(df)

    def test_selection_checks_the_lag(self):
        df = pd.DataFrame(
            {
                "trade_date": [D],
                "reference_date": [D_MINUS_364],
                "distance": [1.0],
                "reference_lag_days": np.array([363], dtype="int64"),
                "n_candidates": np.array([61], dtype="int64"),
                "lag_364_rank": [1.0],
            }
        )
        with pytest.raises(ValueError, match="reference_lag_days must equal"):
            SimilarDaySelection.from_df(df)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_demand_similar_day.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: power_market_analytics.tasks.demand.similar_day`.

- [ ] **Step 3: Create `similar_day.py` with this part of the module**

```python
"""Learned similar-day reference load for the demand task (R-004 E-002).

For a delivery day D the selector scores every day in a window one year back
(D − 364 ± 30) by a weighted distance over seven parts — calendar days from
D − 364, the 24-hour RMSE of D's MSM forecast against the candidate's
observation for temperature, humidity and rain, and the absolute differences
of three ``dim_date`` holiday attributes — and picks the nearest. The weights
are fitted once per run on past pairs (Park, Song and Kwon 2020, §2.2). The
chosen day's でんき予報 hourly load, halved per period, is the feature
``similar_day_demand_kwh``. Design:
docs/superpowers/specs/2026-09-05-demand-similar-day-reference-design.md.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable

import numpy as np
import pandas as pd
from loguru import logger

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.tasks.demand.frames import (
    AreaHourlyLoad,
    AreaObservedWeather,
    AreaWeatherForecast,
    DayCalendar,
)

SIMILAR_DAY_CENTER_LAG_DAYS = 364
SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS = 30
SIMILAR_DAY_FEATURE = "similar_day_demand_kwh"
#: An hour's energy is spread evenly over its two delivery periods.
PERIODS_PER_HOUR = 2
HOURS_PER_DAY = 24
#: The distance parts, in the fixed order the weights and the fit use.
SIMILAR_DAY_COMPONENTS: tuple[str, ...] = (
    "calendar_days",
    "temperature",
    "humidity",
    "rain",
    "days_since_holiday",
    "days_until_holiday",
    "holiday_degree",
)
#: Fewer training pairs than this cannot pin down seven weights, α and β.
MIN_FIT_PAIRS = 8
#: (part, forecast column of the target, observed column of the candidate).
_WEATHER_MEASURES: tuple[tuple[str, str, str], ...] = (
    ("temperature", "forecast_temperature_c", "temperature_c"),
    ("humidity", "forecast_relative_humidity_pct", "humidity_pct"),
    ("rain", "forecast_precipitation_mm", "precipitation_mm"),
)
_CALENDAR_ATTRIBUTES: tuple[str, ...] = ("days_since_holiday", "days_until_holiday", "holiday_degree")
_DATE = "datetime64[ns]"


def _check_non_negative(name: str, df: pd.DataFrame, columns: Iterable[str]) -> None:
    for col in columns:
        if (df[col] < 0).any():
            raise ValueError(f"{name}: {col} must be >= 0")


class DayPairDifferences(DomainFrame):
    """The seven distance parts of (target day, candidate day) pairs.

    Every part is non-negative: calendar days from the window's centre, the
    24-hour RMSE of the target's forecast against the candidate's observation
    for temperature (°C), humidity (%) and rain (mm/h), and the absolute
    differences of the days since and until a named holiday and of the holiday
    degree. The candidate always precedes the target.

    Grain: (target_date, candidate_date).
    """

    schema = {
        "target_date": _DATE,
        "candidate_date": _DATE,
        **{part: "float64" for part in SIMILAR_DAY_COMPONENTS},
    }
    keys = ["target_date", "candidate_date"]
    non_null_cols = list(SIMILAR_DAY_COMPONENTS)

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        _check_non_negative(cls.__name__, df, SIMILAR_DAY_COMPONENTS)
        if (df["candidate_date"] >= df["target_date"]).any():
            raise ValueError(f"{cls.__name__}: candidate_date must precede target_date")


class SimilarDayTrainingPairs(DayPairDifferences):
    """Pair differences plus the realised load difference the weights are fitted to.

    ``load_difference`` is the paper's Eq. (3): the mean over the 24 hours of
    the absolute relative difference of the two hourly load curves.

    Grain: (target_date, candidate_date).
    """

    schema = {**DayPairDifferences.schema, "load_difference": "float64"}
    non_null_cols = [*SIMILAR_DAY_COMPONENTS, "load_difference"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        super()._validate_extra(df)
        _check_non_negative(cls.__name__, df, ["load_difference"])


class SimilarDaySelection(DomainFrame):
    """The similar day chosen for each delivery day.

    ``reference_lag_days`` = ``trade_date − reference_date`` in days;
    ``n_candidates`` the window days that could be scored; ``lag_364_rank``
    the distance rank (1 = nearest) of the plain same-weekday day one year
    back, NaN when it was not a candidate.

    Grain: (trade_date).
    """

    schema = {
        "trade_date": _DATE,
        "reference_date": _DATE,
        "distance": "float64",
        "reference_lag_days": "int64",
        "n_candidates": "int64",
        "lag_364_rank": "float64",
    }
    keys = ["trade_date"]
    non_null_cols = ["reference_date", "distance", "reference_lag_days", "n_candidates"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        lag = (df["trade_date"] - df["reference_date"]).dt.days
        if (lag != df["reference_lag_days"]).any():
            raise ValueError(
                f"{cls.__name__}: reference_lag_days must equal trade_date - reference_date"
            )


class SimilarDayRetrieval(DomainFrame):
    """After-the-fact check of a selection once the delivery day's load is known.

    Per day: the selected day's realised load difference, the plain D − 364
    day's (NaN when not a candidate), the best candidate (``oracle_date``) and
    its load difference, and where the selected day ranked by that outcome
    (1 = the oracle).

    Grain: (trade_date).
    """

    schema = {
        "trade_date": _DATE,
        "reference_date": _DATE,
        "distance": "float64",
        "selected_load_difference": "float64",
        "lag_364_load_difference": "float64",
        "oracle_date": _DATE,
        "oracle_load_difference": "float64",
        "selected_rank_by_outcome": "int64",
    }
    keys = ["trade_date"]
    non_null_cols = [
        "reference_date",
        "distance",
        "selected_load_difference",
        "oracle_date",
        "oracle_load_difference",
        "selected_rank_by_outcome",
    ]


@dataclasses.dataclass(frozen=True)
class _Profiles:
    """Complete 24-hour profiles of one or more measures, one row per day."""

    days: pd.DatetimeIndex
    values: dict[str, np.ndarray]


def _complete_profiles(df: pd.DataFrame, date_col: str, columns: dict[str, str]) -> _Profiles:
    """Pivot an hourly frame into (days × 24) matrices, keeping complete days only.

    Parameters
    ----------
    df : pandas.DataFrame
        Hourly rows with ``date_col``, ``hour_ending`` and the value columns.
    date_col : str
        The day column.
    columns : dict of str to str
        Part name -> value column.

    Returns
    -------
    _Profiles
        Days with all 24 hours present and non-null for every column, sorted.
    """
    hours = range(1, HOURS_PER_DAY + 1)
    wide = df.pivot(index=date_col, columns="hour_ending", values=list(columns.values()))
    wide = wide.reindex(columns=pd.MultiIndex.from_product([list(columns.values()), hours]))
    complete = wide.dropna().sort_index()
    days = pd.DatetimeIndex(complete.index)
    return _Profiles(
        days=days,
        values={name: complete[col].to_numpy(dtype="float64") for name, col in columns.items()},
    )


class SimilarDaySelector:
    """Scores, fits and selects similar days for the demand task.

    Parameters
    ----------
    calendar : DayCalendar
        Holiday attributes of every day (targets and candidates).
    weather_forecast : AreaWeatherForecast
        The target side of the weather parts: population-weighted MSM
        forecasts by delivery day.
    weather_observed : AreaObservedWeather
        The candidate side: population-weighted observations by day.
    hourly_load : AreaHourlyLoad
        The でんき予報 hourly load (candidates' loads; targets' too once known).
    center_lag_days : int, optional
        Window centre, the same weekday one year back.
    half_width_days : int, optional
        Window half width in days.

    Raises
    ------
    ValueError
        If the window is empty or reaches the target day, or no day has an
        observed profile, a load profile and a calendar row.
    """

    def __init__(
        self,
        calendar: DayCalendar,
        weather_forecast: AreaWeatherForecast,
        weather_observed: AreaObservedWeather,
        hourly_load: AreaHourlyLoad,
        *,
        center_lag_days: int = SIMILAR_DAY_CENTER_LAG_DAYS,
        half_width_days: int = SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS,
    ) -> None:
        if half_width_days < 0 or center_lag_days - half_width_days < 1:
            raise ValueError(
                f"window {center_lag_days} ± {half_width_days} days must lie strictly in the past"
            )
        self.center_lag_days = center_lag_days
        self.half_width_days = half_width_days
        self._calendar = calendar.df.set_index("trade_date").sort_index()
        self._forecast = _complete_profiles(
            weather_forecast.df, "trade_date", {name: col for name, col, _ in _WEATHER_MEASURES}
        )
        self._observed = _complete_profiles(
            weather_observed.df, "obs_date", {name: col for name, _, col in _WEATHER_MEASURES}
        )
        self._load = _complete_profiles(hourly_load.df, "load_date", {"load": "demand_kwh"})
        candidates = self._observed.days.intersection(self._load.days).intersection(
            self._calendar.index
        )
        if candidates.empty:
            raise ValueError(
                "no candidate days: no day has an observed profile, a load profile and a "
                "calendar row"
            )
        self._candidates = pd.DatetimeIndex(candidates).sort_values()
        self.first_candidate_day: pd.Timestamp = self._candidates[0]
        self.hourly_load_span: tuple[pd.Timestamp, pd.Timestamp] = (
            self._load.days[0],
            self._load.days[-1],
        )
        logger.info(
            "SimilarDaySelector: {} candidate days ({}..{}), {} forecast days, window {} ± {}",
            len(self._candidates),
            self.first_candidate_day.date(),
            self._candidates[-1].date(),
            len(self._forecast.days),
            center_lag_days,
            half_width_days,
        )

    @property
    def lags(self) -> np.ndarray:
        """The window's lags in days, ascending."""
        return np.arange(
            self.center_lag_days - self.half_width_days,
            self.center_lag_days + self.half_width_days + 1,
            dtype="int64",
        )

    def scorable_days(self, days: Iterable[pd.Timestamp]) -> pd.DatetimeIndex:
        """The delivery days among ``days`` that can be scored.

        A day needs a complete forecast profile, a calendar row, and a window
        that starts on or after the first candidate day.

        Parameters
        ----------
        days : iterable of pandas.Timestamp

        Returns
        -------
        pandas.DatetimeIndex
            Unique, sorted.
        """
        index = pd.DatetimeIndex(pd.to_datetime(list(days))).unique().sort_values()
        earliest_window_start = index - pd.Timedelta(days=int(self.lags.max()))
        ok = (
            index.isin(self._forecast.days)
            & index.isin(self._calendar.index)
            & (earliest_window_start >= self.first_candidate_day)
        )
        return index[ok]

    def _pairs(self, targets: pd.DatetimeIndex) -> pd.DataFrame:
        """Every (target, candidate) pair inside the window, with the lag in days."""
        lags = self.lags
        pairs = pd.DataFrame(
            {
                "target_date": np.repeat(targets.to_numpy(), len(lags)),
                "lag_days": np.tile(lags, len(targets)),
            }
        )
        pairs["candidate_date"] = pairs["target_date"] - pd.to_timedelta(pairs["lag_days"], unit="D")
        return pairs[pairs["candidate_date"].isin(self._candidates)].reset_index(drop=True)

    def differences(self, days: Iterable[pd.Timestamp]) -> DayPairDifferences:
        """The seven parts for every window pair of the scorable days among ``days``.

        Parameters
        ----------
        days : iterable of pandas.Timestamp

        Returns
        -------
        DayPairDifferences
            Sorted by target day, then candidate day; empty when nothing is scorable.
        """
        pairs = self._pairs(self.scorable_days(days))
        t_pos = self._forecast.days.get_indexer(pairs["target_date"])
        c_pos = self._observed.days.get_indexer(pairs["candidate_date"])
        parts: dict[str, np.ndarray] = {
            "calendar_days": np.abs(pairs["lag_days"].to_numpy() - self.center_lag_days).astype(
                "float64"
            )
        }
        for name, _, _ in _WEATHER_MEASURES:
            gap = self._forecast.values[name][t_pos] - self._observed.values[name][c_pos]
            parts[name] = np.sqrt(np.mean(gap**2, axis=1))
        target_attrs = self._calendar.loc[pairs["target_date"], list(_CALENDAR_ATTRIBUTES)]
        candidate_attrs = self._calendar.loc[pairs["candidate_date"], list(_CALENDAR_ATTRIBUTES)]
        for col in _CALENDAR_ATTRIBUTES:
            parts[col] = np.abs(
                target_attrs[col].to_numpy(dtype="float64")
                - candidate_attrs[col].to_numpy(dtype="float64")
            )
        out = pairs[["target_date", "candidate_date"]].assign(**parts)
        out = out.sort_values(["target_date", "candidate_date"], ignore_index=True)
        return DayPairDifferences.from_df(out[list(DayPairDifferences.schema)])
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_demand_similar_day.py -q --no-cov`
Expected: PASS. Then `uv run ruff check power_market_analytics/tasks/demand/similar_day.py tests/test_demand_similar_day.py`.

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/tasks/demand/similar_day.py tests/test_demand_similar_day.py
git commit -m "feat(demand): similar-day pair differences over a D-364 window

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
### Task 5: Selector, part 2 — load difference, training pairs and the weight fit

**Files:**
- Modify: `power_market_analytics/tasks/demand/similar_day.py`
- Modify: `tests/test_demand_similar_day.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: Task 4.
- Produces: `load_difference(target: ndarray, candidate: ndarray) -> ndarray` (row-wise Eq. (3) over (n, 24) arrays); `SimilarDayWeights` frozen dataclass (`components`, `weights`, `scales`, `alpha`, `beta`, `n_pairs`, `n_targets`, `fit_from`, `fit_through`, `fit_rmse`; `distance(differences) -> ndarray`; `as_params() -> dict[str, object]`); `fit_similar_day_weights(pairs) -> SimilarDayWeights`; `SimilarDaySelector.training_pairs(through) -> SimilarDayTrainingPairs`, `.fit(through) -> SimilarDayWeights`, `.ensure_fitted(through) -> SimilarDayWeights`, `.weights` property (raises `RuntimeError` before a fit).

- [ ] **Step 1: Declare scipy**

In `pyproject.toml` add `"scipy>=1.18",` to `[project] dependencies` (alphabetical, after `pyspark…`/before `shap`) and `"scipy.*"` to the mypy `module = [...]` ignore list. Run `uv lock` (the lock already resolves scipy 1.18.0 as a transitive dependency; the command only records the direct requirement) and `uv sync`.

- [ ] **Step 2: Write the failing tests** (append; add `SimilarDayWeights`, `fit_similar_day_weights`, `load_difference` to the imports)

```python
class TestLoadDifference:
    def test_mean_absolute_relative_difference_per_row(self):
        target = np.array([[100.0] * 24, [200.0] * 24])
        candidate = np.array([[110.0] * 24, [150.0] * 24])
        assert load_difference(target, candidate).tolist() == pytest.approx([0.1, 0.25])


class TestTrainingPairs:
    def test_targets_up_to_through_with_a_known_load(self, selector):
        through = pd.Timestamp("2024-03-31")
        pairs = selector.training_pairs(through)
        assert type(pairs) is SimilarDayTrainingPairs
        # The first scorable forecast day: its window must start on the first candidate.
        first = HOLIDAYS[0] + pd.Timedelta(days=394)
        targets = pairs.df["target_date"].unique()
        assert targets.min() == first
        assert targets.max() == through
        assert len(pairs) == len(pd.date_range(first, through)) * 61
        row = pairs.df.set_index(["target_date", "candidate_date"]).loc[(D - pd.Timedelta(days=14), D_MINUS_364 - pd.Timedelta(days=14))]
        t, c = D - pd.Timedelta(days=14), D_MINUS_364 - pd.Timedelta(days=14)
        expected = np.mean([abs(load_at(t, h) - load_at(c, h)) / load_at(t, h) for h in range(1, 25)])
        assert row["load_difference"] == pytest.approx(expected)

    def test_no_pairs_before_the_first_scorable_day(self, selector):
        assert len(selector.training_pairs(pd.Timestamp("2024-01-31"))) == 0


def planted_pairs(n: int = 400, seed: int = 0) -> tuple[SimilarDayTrainingPairs, np.ndarray, float, float]:
    rng = np.random.default_rng(seed)
    parts = np.abs(rng.normal(size=(n, 7))) * np.array([10, 3, 8, 0.5, 5, 5, 0.4])
    planted = np.array([0.30, 0.25, 0.05, 0.05, 0.15, 0.10, 0.10])
    scales = np.sqrt(np.mean(parts**2, axis=0))
    distance = np.sqrt(((parts / scales) ** 2) @ planted)
    alpha, beta = 2.0, 0.1
    y = alpha * distance + beta
    days = pd.date_range("2024-02-07", periods=n, freq="D")
    df = pd.DataFrame(parts, columns=list(SIMILAR_DAY_COMPONENTS)).assign(
        target_date=days, candidate_date=days - pd.Timedelta(days=364), load_difference=y
    )
    return SimilarDayTrainingPairs.from_df(df), planted, alpha, beta


class TestFitSimilarDayWeights:
    def test_recovers_planted_weights(self):
        pairs, planted, alpha, beta = planted_pairs()
        fitted = fit_similar_day_weights(pairs)
        assert type(fitted) is SimilarDayWeights
        assert fitted.components == SIMILAR_DAY_COMPONENTS
        np.testing.assert_allclose(fitted.weights, planted, atol=1e-3)
        assert fitted.weights.sum() == pytest.approx(1.0)
        assert (fitted.weights >= 0).all()
        assert fitted.alpha == pytest.approx(alpha, abs=1e-3)
        assert fitted.beta == pytest.approx(beta, abs=1e-3)
        assert fitted.fit_rmse == pytest.approx(0.0, abs=1e-6)
        assert fitted.n_pairs == 400
        assert fitted.n_targets == 400
        assert fitted.fit_from == pd.Timestamp("2024-02-07")
        assert fitted.fit_through == pd.Timestamp("2024-02-07") + pd.Timedelta(days=399)
        # distance() reproduces the planted distance up to the fitted weights.
        np.testing.assert_allclose(fitted.distance(pairs), (pairs.df["load_difference"] - beta) / alpha, atol=1e-3)

    def test_as_params(self):
        fitted = fit_similar_day_weights(planted_pairs()[0])
        params = fitted.as_params()
        assert set(params) == {
            "similar_day_weights",
            "similar_day_scales",
            "similar_day_alpha",
            "similar_day_beta",
            "similar_day_fit_n_pairs",
            "similar_day_fit_n_targets",
            "similar_day_fit_from",
            "similar_day_fit_through",
            "similar_day_fit_rmse",
        }
        assert params["similar_day_weights"].startswith("calendar_days=0.30")
        assert params["similar_day_fit_from"] == "2024-02-07"
        assert params["similar_day_fit_n_pairs"] == 400

    def test_too_few_pairs(self):
        pairs, _, _, _ = planted_pairs(n=MIN_FIT_PAIRS - 1)
        with pytest.raises(ValueError, match=f"{MIN_FIT_PAIRS - 1} training pairs; at least {MIN_FIT_PAIRS}"):
            fit_similar_day_weights(pairs)

    def test_all_parts_zero_fits_the_mean(self):
        days = pd.date_range("2024-02-07", periods=MIN_FIT_PAIRS, freq="D")
        df = pd.DataFrame(np.zeros((MIN_FIT_PAIRS, 7)), columns=list(SIMILAR_DAY_COMPONENTS)).assign(
            target_date=days, candidate_date=days - pd.Timedelta(days=364), load_difference=np.arange(MIN_FIT_PAIRS) / 10
        )
        fitted = fit_similar_day_weights(SimilarDayTrainingPairs.from_df(df))
        assert fitted.beta == pytest.approx(np.mean(np.arange(MIN_FIT_PAIRS) / 10), abs=1e-6)
        assert (fitted.scales == 1.0).all()

    def test_solver_failure(self, monkeypatch):
        import power_market_analytics.tasks.demand.similar_day as module

        class Failed:
            success = False
            message = "maximum iterations"

        monkeypatch.setattr(module, "least_squares", lambda *a, **k: Failed())
        with pytest.raises(RuntimeError, match="similar-day weight fit failed: maximum iterations"):
            fit_similar_day_weights(planted_pairs()[0])


class TestSelectorFit:
    def test_weights_before_fit_raise(self):
        fresh = SimilarDaySelector(make_calendar(), make_forecast(), make_observed(), make_hourly_load())
        with pytest.raises(RuntimeError, match="not fitted"):
            fresh.weights

    def test_fit_once_and_reuse(self):
        fresh = SimilarDaySelector(make_calendar(), make_forecast(), make_observed(), make_hourly_load())
        first = fresh.ensure_fitted(pd.Timestamp("2024-03-31"))
        assert fresh.weights is first
        assert fresh.ensure_fitted(pd.Timestamp("2024-04-15")) is first
        assert first.fit_through == pd.Timestamp("2024-03-31")
        assert first.n_pairs == len(fresh.training_pairs(pd.Timestamp("2024-03-31")))

    def test_fit_without_pairs_raises(self, selector):
        with pytest.raises(ValueError, match="no training pairs with a target day on or before 2024-01-31"):
            selector.fit(pd.Timestamp("2024-01-31"))
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_demand_similar_day.py -q --no-cov`
Expected: FAIL with `ImportError` on `load_difference`.

- [ ] **Step 4: Implement**

Add `from scipy.optimize import least_squares` to the imports, then these definitions (place the functions and the dataclass before `SimilarDaySelector`, the methods inside it after `differences`):

```python
def load_difference(target: np.ndarray, candidate: np.ndarray) -> np.ndarray:
    """The paper's Eq. (3): mean absolute relative difference of two hourly load curves.

    Parameters
    ----------
    target, candidate : numpy.ndarray
        Shape (n, 24); the target's loads are positive.

    Returns
    -------
    numpy.ndarray
        Shape (n,).
    """
    return np.mean(np.abs(target - candidate) / target, axis=1)


def _softmax(scores: np.ndarray) -> np.ndarray:
    """Weights of the parts: softmax of the free scores with the last part's score fixed at 0."""
    full = np.append(scores, 0.0)
    exp = np.exp(full - full.max())
    return exp / exp.sum()


def _distance(scaled: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """``sqrt(Σ_j w_j · scaled_j²)`` per row."""
    return np.sqrt((scaled**2) @ weights)


@dataclasses.dataclass(frozen=True)
class SimilarDayWeights:
    """A fitted similar-day distance: ``d = sqrt(Σ_j w_j (Δ_j / s_j)²)``, ``ŷ = α d + β``.

    Attributes
    ----------
    components : tuple of str
        The parts, in ``SIMILAR_DAY_COMPONENTS`` order.
    weights : numpy.ndarray
        Non-negative, summing to one: each part's share of the squared distance.
    scales : numpy.ndarray
        The RMS of each part over the training pairs (1 where a part was all zero).
    alpha, beta : float
        The straight line from distance to load difference (``alpha >= 0``).
    n_pairs, n_targets : int
        Training pairs and distinct target days.
    fit_from, fit_through : pandas.Timestamp
        First and last target day of the fit.
    fit_rmse : float
        RMSE of ``ŷ`` against the realised load differences.
    """

    components: tuple[str, ...]
    weights: np.ndarray
    scales: np.ndarray
    alpha: float
    beta: float
    n_pairs: int
    n_targets: int
    fit_from: pd.Timestamp
    fit_through: pd.Timestamp
    fit_rmse: float

    def distance(self, differences: DayPairDifferences) -> np.ndarray:
        """The distance of every pair.

        Parameters
        ----------
        differences : DayPairDifferences

        Returns
        -------
        numpy.ndarray
            One value per row of ``differences``.
        """
        parts = differences.df[list(self.components)].to_numpy(dtype="float64")
        return _distance(parts / self.scales, self.weights)

    def as_params(self) -> dict[str, object]:
        """The fit as MLflow run params.

        Returns
        -------
        dict of str to object
        """
        return {
            "similar_day_weights": ",".join(
                f"{c}={w:.4f}" for c, w in zip(self.components, self.weights, strict=True)
            ),
            "similar_day_scales": ",".join(
                f"{c}={s:.4g}" for c, s in zip(self.components, self.scales, strict=True)
            ),
            "similar_day_alpha": round(self.alpha, 6),
            "similar_day_beta": round(self.beta, 6),
            "similar_day_fit_n_pairs": self.n_pairs,
            "similar_day_fit_n_targets": self.n_targets,
            "similar_day_fit_from": str(self.fit_from.date()),
            "similar_day_fit_through": str(self.fit_through.date()),
            "similar_day_fit_rmse": round(self.fit_rmse, 6),
        }


def fit_similar_day_weights(pairs: SimilarDayTrainingPairs) -> SimilarDayWeights:
    """Fit the distance weights by nonlinear least squares on the paper's cost.

    Minimises ``Σ_k (y_k − (α d_k + β))²`` over the free softmax scores (the
    last part's score is fixed at 0 so the weights are identified), ``α ≥ 0``
    and ``β``, with ``scipy.optimize.least_squares`` (``trf``). Every part is
    first divided by its RMS over the pairs, so a weight reads as a share.
    The start is equal weights with ``α``, ``β`` from the straight-line fit
    of ``y`` on that distance; nothing is random.

    Parameters
    ----------
    pairs : SimilarDayTrainingPairs

    Returns
    -------
    SimilarDayWeights

    Raises
    ------
    ValueError
        Fewer than ``MIN_FIT_PAIRS`` pairs.
    RuntimeError
        The solver did not converge.
    """
    df = pairs.df
    if len(df) < MIN_FIT_PAIRS:
        raise ValueError(f"{len(df)} training pairs; at least {MIN_FIT_PAIRS} are needed")
    parts = df[list(SIMILAR_DAY_COMPONENTS)].to_numpy(dtype="float64")
    y = df["load_difference"].to_numpy(dtype="float64")
    rms = np.sqrt(np.mean(parts**2, axis=0))
    scales = np.where(rms > 0, rms, 1.0)
    scaled = parts / scales
    n_free = len(SIMILAR_DAY_COMPONENTS) - 1
    start_distance = _distance(scaled, _softmax(np.zeros(n_free)))
    spread = float(np.var(start_distance))
    alpha0 = max(float(np.cov(start_distance, y, bias=True)[0, 1] / spread), 0.0) if spread > 0 else 0.0
    beta0 = float(np.mean(y) - alpha0 * np.mean(start_distance))

    def residuals(theta: np.ndarray) -> np.ndarray:
        weights = _softmax(theta[:n_free])
        return y - (theta[n_free] * _distance(scaled, weights) + theta[n_free + 1])

    lower = np.concatenate([np.full(n_free, -np.inf), [0.0, -np.inf]])
    result = least_squares(
        residuals, np.concatenate([np.zeros(n_free), [alpha0, beta0]]), bounds=(lower, np.inf), method="trf"
    )
    if not result.success:
        raise RuntimeError(f"similar-day weight fit failed: {result.message}")
    return SimilarDayWeights(
        components=SIMILAR_DAY_COMPONENTS,
        weights=_softmax(result.x[:n_free]),
        scales=scales,
        alpha=float(result.x[n_free]),
        beta=float(result.x[n_free + 1]),
        n_pairs=len(df),
        n_targets=int(df["target_date"].nunique()),
        fit_from=df["target_date"].min(),
        fit_through=df["target_date"].max(),
        fit_rmse=float(np.sqrt(np.mean(result.fun**2))),
    )
```

Inside `SimilarDaySelector` (add `self._weights: SimilarDayWeights | None = None` to `__init__`):

```python
    def training_pairs(self, through: pd.Timestamp) -> SimilarDayTrainingPairs:
        """Every window pair of the scorable forecast days on or before ``through``
        whose own hourly load is known, with the realised load difference.

        Parameters
        ----------
        through : pandas.Timestamp
            Last target day allowed (the newest day the strategy may see).

        Returns
        -------
        SimilarDayTrainingPairs
        """
        through = pd.Timestamp(through)
        targets = self._forecast.days[self._forecast.days <= through]
        diffs = self.differences(targets[targets.isin(self._load.days)]).df
        loads = self._load.values["load"]
        realised = load_difference(
            loads[self._load.days.get_indexer(diffs["target_date"])],
            loads[self._load.days.get_indexer(diffs["candidate_date"])],
        )
        return SimilarDayTrainingPairs.from_df(diffs.assign(load_difference=realised))

    def fit(self, through: pd.Timestamp) -> SimilarDayWeights:
        """Fit and store the weights on the training pairs up to ``through``.

        Parameters
        ----------
        through : pandas.Timestamp

        Returns
        -------
        SimilarDayWeights

        Raises
        ------
        ValueError
            No pair has a target day on or before ``through`` (or too few).
        RuntimeError
            The solver did not converge.
        """
        pairs = self.training_pairs(through)
        if len(pairs) == 0:
            raise ValueError(
                f"no training pairs with a target day on or before {pd.Timestamp(through).date()}"
            )
        self._weights = fit_similar_day_weights(pairs)
        logger.info("SimilarDaySelector: fitted on {}", self._weights.as_params())
        return self._weights

    def ensure_fitted(self, through: pd.Timestamp) -> SimilarDayWeights:
        """Fit once; later calls return the stored weights whatever ``through`` is.

        Parameters
        ----------
        through : pandas.Timestamp

        Returns
        -------
        SimilarDayWeights
        """
        return self._weights if self._weights is not None else self.fit(through)

    @property
    def weights(self) -> SimilarDayWeights:
        """The fitted weights.

        Raises
        ------
        RuntimeError
            Before any fit.
        """
        if self._weights is None:
            raise RuntimeError("similar-day weights are not fitted; call fit(through) first")
        return self._weights
```

- [ ] **Step 5: Run the tests, lint and mypy**

Run: `uv run pytest tests/test_demand_similar_day.py -q --no-cov && just lint && just mypy`
Expected: PASS, clean.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock power_market_analytics/tasks/demand/similar_day.py tests/test_demand_similar_day.py
git commit -m "feat(demand): fit the similar-day distance weights by nonlinear least squares

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
### Task 6: Selector, part 3 — selection, feature join, retrieval check

**Files:**
- Modify: `power_market_analytics/tasks/demand/similar_day.py`
- Modify: `tests/test_demand_similar_day.py`

**Interfaces:**
- Consumes: Tasks 4–5.
- Produces: `SimilarDaySelector.select(days) -> SimilarDaySelection`, `.retrieval(selection) -> SimilarDayRetrieval`; `join_similar_day_load(points, selection, hourly_load, *, name=SIMILAR_DAY_FEATURE) -> pd.DataFrame`; `retrieval_metrics(retrieval) -> dict[str, float]` with keys `similar_day_load_difference_selected`, `similar_day_load_difference_lag_364`, `similar_day_load_difference_oracle`, `similar_day_share_better_than_lag_364`.

- [ ] **Step 1: Write the failing tests** (append; import `SimilarDayRetrieval`, `join_similar_day_load`, `retrieval_metrics`)

```python
@pytest.fixture(scope="module")
def fitted(selector) -> SimilarDaySelector:
    selector.ensure_fitted(pd.Timestamp("2024-03-31"))
    return selector


def hand_weights(**shares: float) -> SimilarDayWeights:
    weights = np.array([shares.get(c, 0.0) for c in SIMILAR_DAY_COMPONENTS])
    return SimilarDayWeights(
        components=SIMILAR_DAY_COMPONENTS, weights=weights / weights.sum(), scales=np.ones(7),
        alpha=1.0, beta=0.0, n_pairs=8, n_targets=8,
        fit_from=pd.Timestamp("2024-02-07"), fit_through=pd.Timestamp("2024-02-14"), fit_rmse=0.0,
    )


class TestSelect:
    def test_nearest_candidate(self, fitted):
        selection = fitted.select([D, pd.Timestamp("2023-12-31")])
        assert type(selection) is SimilarDaySelection
        assert len(selection) == 1
        row = selection.df.iloc[0]
        assert row["trade_date"] == D
        assert 334 <= row["reference_lag_days"] <= 394
        assert row["n_candidates"] == 61
        assert 1 <= row["lag_364_rank"] <= 61
        diffs = fitted.differences([D])
        distances = pd.Series(fitted.weights.distance(diffs), index=diffs.df["candidate_date"])
        assert row["distance"] == pytest.approx(distances.min())
        assert row["reference_date"] == distances.idxmin()

    def test_tie_goes_to_the_centre_then_the_earlier_day(self):
        # Rain is zero on every window day of D, so a rain-only distance ties at 0.
        selector = SimilarDaySelector(make_calendar(), make_forecast(), make_observed(), make_hourly_load())
        selector._weights = hand_weights(rain=1.0)
        assert all(rain_at(D - pd.Timedelta(days=lag), h) == 0 for lag in range(334, 395) for h in range(1, 25))
        assert selector.select([D]).df.iloc[0]["reference_date"] == D_MINUS_364
        without_centre = SimilarDaySelector(
            make_calendar(), make_forecast(), make_observed(null_hours={(D_MINUS_364, 1)}), make_hourly_load()
        )
        without_centre._weights = hand_weights(rain=1.0)
        row = without_centre.select([D]).df.iloc[0]
        assert row["reference_date"] == D - pd.Timedelta(days=365)
        assert np.isnan(row["lag_364_rank"])
        assert row["n_candidates"] == 60

    def test_nothing_scorable_gives_an_empty_frame(self, fitted):
        selection = fitted.select([pd.Timestamp("2023-12-31")])
        assert len(selection) == 0
        assert list(selection.df.columns) == list(SimilarDaySelection.schema)

    def test_select_before_fit_raises(self):
        fresh = SimilarDaySelector(make_calendar(), make_forecast(), make_observed(), make_hourly_load())
        with pytest.raises(RuntimeError, match="not fitted"):
            fresh.select([D])


class TestJoinSimilarDayLoad:
    def test_hourly_load_of_the_reference_halved_per_period(self, fitted):
        selection = fitted.select([D])
        reference = selection.df.iloc[0]["reference_date"]
        points = pd.DataFrame({"trade_date": [D] * 48 + [pd.Timestamp("2023-12-31")] * 2, "time_code": list(range(1, 49)) + [1, 2]})
        points["time_code"] = points["time_code"].astype("int64")
        joined = join_similar_day_load(points, selection, make_hourly_load())
        assert list(joined.columns) == ["trade_date", "time_code", SIMILAR_DAY_FEATURE]
        expected = [load_at(reference, (tc + 1) // 2) / PERIODS_PER_HOUR for tc in range(1, 49)]
        assert joined[SIMILAR_DAY_FEATURE].head(48).tolist() == pytest.approx(expected)
        assert joined[SIMILAR_DAY_FEATURE].tail(2).isna().all()

    def test_custom_name(self, fitted):
        selection = fitted.select([D])
        points = pd.DataFrame({"trade_date": [D], "time_code": np.array([1], dtype="int64")})
        assert "ref_kwh" in join_similar_day_load(points, selection, make_hourly_load(), name="ref_kwh")


class TestRetrieval:
    def test_outcomes_per_forecast_day(self, fitted):
        days = [D, D + pd.Timedelta(days=1), pd.Timestamp("2024-04-30")]  # 04-30 has no calendar row
        selection = fitted.select(days)
        retrieval = fitted.retrieval(selection)
        assert type(retrieval) is SimilarDayRetrieval
        assert retrieval.df["trade_date"].tolist() == [D, D + pd.Timedelta(days=1)]
        row = retrieval.df.set_index("trade_date").loc[D]
        sel = selection.df.set_index("trade_date").loc[D]
        assert row["reference_date"] == sel["reference_date"]
        assert row["distance"] == sel["distance"]
        candidates = fitted.differences([D]).df["candidate_date"]
        realised = {
            c: np.mean([abs(load_at(D, h) - load_at(c, h)) / load_at(D, h) for h in range(1, 25)])
            for c in candidates
        }
        assert row["selected_load_difference"] == pytest.approx(realised[sel["reference_date"]])
        assert row["lag_364_load_difference"] == pytest.approx(realised[D_MINUS_364])
        assert row["oracle_load_difference"] == pytest.approx(min(realised.values()))
        assert row["oracle_date"] == min(realised, key=lambda c: (realised[c], c))
        assert row["oracle_load_difference"] <= row["selected_load_difference"]
        assert row["selected_rank_by_outcome"] >= 1

    def test_lag_364_is_nan_when_it_was_not_a_candidate(self):
        selector = SimilarDaySelector(
            make_calendar(), make_forecast(), make_observed(null_hours={(D_MINUS_364, 1)}), make_hourly_load()
        )
        selector.ensure_fitted(pd.Timestamp("2024-03-31"))
        retrieval = selector.retrieval(selector.select([D]))
        assert np.isnan(retrieval.df.iloc[0]["lag_364_load_difference"])

    def test_days_without_a_known_load_are_left_out(self, fitted):
        # A forecast day after the hourly load ends: selectable, not checkable.
        beyond = SimilarDaySelector(
            make_calendar(), make_forecast(), make_observed(), make_hourly_load(pd.date_range("2023-01-01", "2024-04-09"))
        )
        beyond.ensure_fitted(pd.Timestamp("2024-03-31"))
        assert len(beyond.retrieval(beyond.select([D]))) == 0
        assert list(beyond.retrieval(beyond.select([D])).df.columns) == list(SimilarDayRetrieval.schema)


class TestRetrievalMetrics:
    def test_means_and_share(self):
        df = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2024-04-10", "2024-04-11", "2024-04-12"]),
                "reference_date": pd.to_datetime(["2023-04-12", "2023-04-13", "2023-04-14"]),
                "distance": [1.0, 1.0, 1.0],
                "selected_load_difference": [0.02, 0.05, 0.03],
                "lag_364_load_difference": [0.04, 0.04, np.nan],
                "oracle_date": pd.to_datetime(["2023-04-12", "2023-04-20", "2023-04-14"]),
                "oracle_load_difference": [0.02, 0.01, 0.03],
                "selected_rank_by_outcome": np.array([1, 5, 1], dtype="int64"),
            }
        )
        metrics = retrieval_metrics(SimilarDayRetrieval.from_df(df))
        assert metrics == {
            "similar_day_load_difference_selected": pytest.approx(0.1 / 3),
            "similar_day_load_difference_lag_364": pytest.approx(0.04),
            "similar_day_load_difference_oracle": pytest.approx(0.02),
            "similar_day_share_better_than_lag_364": pytest.approx(0.5),
        }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_demand_similar_day.py -q --no-cov`
Expected: FAIL with `ImportError` on `join_similar_day_load`.

- [ ] **Step 3: Implement**

Add the imports `from power_market_analytics.forecasting.frames import GRAIN_COLS` and `from power_market_analytics.tasks.demand.features import hour_ending_of`. A helper for empty frames:

```python
def _empty(frame_cls: type[DomainFrame]) -> pd.DataFrame:
    """An empty frame with ``frame_cls``'s columns and dtypes."""
    return pd.DataFrame({col: pd.Series(dtype=dtype) for col, dtype in frame_cls.schema.items()})
```

Selector methods (after `weights`):

```python
    def _scored(self, days: Iterable[pd.Timestamp]) -> pd.DataFrame:
        """Window pairs with their distance, lag and gap from the window's centre."""
        diffs = self.differences(days)
        df = diffs.df.assign(distance=self.weights.distance(diffs))
        lag = (df["target_date"] - df["candidate_date"]).dt.days
        return df.assign(lag_days=lag, centre_gap=(lag - self.center_lag_days).abs())

    def select(self, days: Iterable[pd.Timestamp]) -> SimilarDaySelection:
        """Pick the nearest window day for every scorable day among ``days``.

        Ties go to the candidate nearest the window's centre, then the earlier
        date.

        Parameters
        ----------
        days : iterable of pandas.Timestamp

        Returns
        -------
        SimilarDaySelection
            One row per scorable day; empty when none is.

        Raises
        ------
        RuntimeError
            Before any fit.
        """
        scored = self._scored(days)
        if scored.empty:
            return SimilarDaySelection.from_df(_empty(SimilarDaySelection))
        best = (
            scored.sort_values(["target_date", "distance", "centre_gap", "candidate_date"])
            .groupby("target_date", sort=True)
            .head(1)
            .set_index("target_date")
        )
        ranks = scored.assign(rank=scored.groupby("target_date")["distance"].rank(method="min"))
        at_centre = ranks[ranks["lag_days"] == self.center_lag_days].set_index("target_date")["rank"]
        counts = scored.groupby("target_date").size()
        out = pd.DataFrame(
            {
                "trade_date": best.index,
                "reference_date": best["candidate_date"].to_numpy(),
                "distance": best["distance"].to_numpy(dtype="float64"),
                "reference_lag_days": best["lag_days"].to_numpy(dtype="int64"),
                "n_candidates": counts.loc[best.index].to_numpy(dtype="int64"),
                "lag_364_rank": at_centre.reindex(best.index).to_numpy(dtype="float64"),
            }
        )
        return SimilarDaySelection.from_df(out)

    def retrieval(self, selection: SimilarDaySelection) -> SimilarDayRetrieval:
        """Judge a selection against what every candidate's load turned out to be.

        Only the selected days whose own hourly load is known are checked.

        Parameters
        ----------
        selection : SimilarDaySelection

        Returns
        -------
        SimilarDayRetrieval
            Empty when no selected day has a known load yet.
        """
        known = selection.df[selection.df["trade_date"].isin(self._load.days)]
        scored = self._scored(known["trade_date"]) if not known.empty else pd.DataFrame()
        if scored.empty:
            return SimilarDayRetrieval.from_df(_empty(SimilarDayRetrieval))
        loads = self._load.values["load"]
        scored = scored.assign(
            load_difference=load_difference(
                loads[self._load.days.get_indexer(scored["target_date"])],
                loads[self._load.days.get_indexer(scored["candidate_date"])],
            )
        )
        scored = scored.assign(
            outcome_rank=scored.groupby("target_date")["load_difference"].rank(method="min")
        )
        chosen = scored.merge(
            known[["trade_date", "reference_date"]].rename(
                columns={"trade_date": "target_date", "reference_date": "candidate_date"}
            ),
            how="inner",
            on=["target_date", "candidate_date"],
            validate="one_to_one",
        ).set_index("target_date")
        at_centre = scored[scored["lag_days"] == self.center_lag_days].set_index("target_date")[
            "load_difference"
        ]
        oracle = (
            scored.sort_values(["target_date", "load_difference", "candidate_date"])
            .groupby("target_date", sort=True)
            .head(1)
            .set_index("target_date")
        )
        out = pd.DataFrame(
            {
                "trade_date": chosen.index,
                "reference_date": chosen["candidate_date"].to_numpy(),
                "distance": chosen["distance"].to_numpy(dtype="float64"),
                "selected_load_difference": chosen["load_difference"].to_numpy(dtype="float64"),
                "lag_364_load_difference": at_centre.reindex(chosen.index).to_numpy(dtype="float64"),
                "oracle_date": oracle.loc[chosen.index, "candidate_date"].to_numpy(),
                "oracle_load_difference": oracle.loc[chosen.index, "load_difference"].to_numpy(
                    dtype="float64"
                ),
                "selected_rank_by_outcome": chosen["outcome_rank"].to_numpy(dtype="int64"),
            }
        )
        return SimilarDayRetrieval.from_df(out)
```

Module-level functions:

```python
def join_similar_day_load(
    points: pd.DataFrame,
    selection: SimilarDaySelection,
    hourly_load: AreaHourlyLoad,
    *,
    name: str = SIMILAR_DAY_FEATURE,
) -> pd.DataFrame:
    """Attach the selected similar day's hourly load, halved per period.

    For a point (D, time_code) the feature is the hourly load on D's
    ``reference_date`` at ``hour_ending_of(time_code)`` divided by
    ``PERIODS_PER_HOUR`` (kWh per 30-minute period, the target's scale); NaN
    where D has no selection or the reference hour has no load.

    Parameters
    ----------
    points : pandas.DataFrame
        Rows keyed on (trade_date, time_code); other columns pass through.
    selection : SimilarDaySelection
    hourly_load : AreaHourlyLoad
    name : str, optional
        Name for the new column.

    Returns
    -------
    pandas.DataFrame
        ``points`` plus ``name`` (float64), in the original row order.
    """
    keyed = points[GRAIN_COLS].merge(
        selection.df[["trade_date", "reference_date"]],
        how="left",
        on="trade_date",
        validate="many_to_one",
    )
    keyed = keyed.assign(hour_ending=hour_ending_of(keyed["time_code"]))
    load = hourly_load.df.rename(columns={"load_date": "reference_date"})
    joined = keyed.merge(
        load, how="left", on=["reference_date", "hour_ending"], validate="many_to_one"
    )
    return points.assign(**{name: joined["demand_kwh"].to_numpy(dtype="float64") / PERIODS_PER_HOUR})


def retrieval_metrics(retrieval: SimilarDayRetrieval) -> dict[str, float]:
    """Mean realised load differences of the selected, D − 364 and oracle days,
    and the share of days the selected day beat D − 364 (over days where the
    latter was a candidate). NaN where a mean has no rows.

    Parameters
    ----------
    retrieval : SimilarDayRetrieval

    Returns
    -------
    dict of str to float
    """
    df = retrieval.df
    comparable = df.dropna(subset=["lag_364_load_difference"])
    return {
        "similar_day_load_difference_selected": float(df["selected_load_difference"].mean()),
        "similar_day_load_difference_lag_364": float(df["lag_364_load_difference"].mean()),
        "similar_day_load_difference_oracle": float(df["oracle_load_difference"].mean()),
        "similar_day_share_better_than_lag_364": float(
            (comparable["selected_load_difference"] < comparable["lag_364_load_difference"]).mean()
        ),
    }
```

- [ ] **Step 4: Run the tests, lint, mypy and the coverage report for the module**

Run: `uv run pytest tests/test_demand_similar_day.py -q --cov=power_market_analytics/tasks/demand/similar_day.py --cov-report=term-missing && just lint && just mypy`
Expected: PASS, 100 % on the module.

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/tasks/demand/similar_day.py tests/test_demand_similar_day.py
git commit -m "feat(demand): similar-day selection, feature join and retrieval check

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
### Task 7: The `diagnostics` hook and the scripts' artifact logging

**Files:**
- Modify: `power_market_analytics/forecasting/strategy.py` (after `contributions`)
- Modify: `scripts/demand_backtest.py`, `scripts/spot_price_backtest.py`
- Modify: `tests/test_forecasting_strategy.py`, `tests/test_demand_scripts.py`, `tests/test_spot_price_scripts.py`

**Interfaces:**
- Produces: `ForecastStrategy.diagnostics(self, history: HistoryT, run: BacktestRun) -> dict[str, pd.DataFrame]`, default `{}`; both scripts log every returned frame as `<stem>.csv` right after the contributions are published.

- [ ] **Step 1: Write the failing tests**

In `tests/test_forecasting_strategy.py`, inside `TestForecastStrategy`, after the existing complete-subclass test (reuse its `Done` stub — a subclass implementing `predict`, `build_eval_set`, `evaluate` with `pass` bodies):

```python
    def test_diagnostics_default_is_empty(self):
        class Done(ForecastStrategy):
            name = "done"

            def predict(self, target_date, history):
                pass

            def build_eval_set(self, history, start_date, end_date, run=None):
                pass

            def evaluate(self, eval_set, **kwargs):
                pass

        assert Done().diagnostics(history=None, run=None) == {}
```

In `tests/test_demand_scripts.py::TestBacktestScript`:

```python
    def test_diagnostics_frames_are_logged_as_csv(self, spark, curated_warehouse, monkeypatch):
        script = import_script("demand_backtest")
        real_build_strategy = script.build_strategy
        seen: dict[str, object] = {}

        def build_strategy_with_diagnostics(*args, **kwargs):
            strategy = real_build_strategy(*args, **kwargs)

            def diagnostics(history, run):
                seen["history_rows"] = len(history)
                seen["run"] = run
                mlflow.log_metric("similar_day_share_better_than_lag_364", 0.75)
                return {"similar_day_selection": pd.DataFrame({"trade_date": ["2024-04-10"], "reference_date": ["2023-04-12"]})}

            monkeypatch.setattr(strategy, "diagnostics", diagnostics)
            return strategy

        monkeypatch.setattr(script, "build_strategy", build_strategy_with_diagnostics)
        script.main(["--start-date", "2024-04-10", "--end-date", "2024-04-10", "--shap-nsamples", "20"])
        run = last_run()
        assert seen["history_rows"] == len(curated_warehouse.demand.dropna(subset=["demand_kwh"]))
        assert seen["run"].result.df["trade_date"].tolist() == [pd.Timestamp("2024-04-10")] * 48
        assert run.data.metrics["similar_day_share_better_than_lag_364"] == 0.75
        assert "similar_day_selection.csv" in artifact_names(run.info.run_id)
        logged = pd.read_csv(mlflow.artifacts.download_artifacts(run_id=run.info.run_id, artifact_path="similar_day_selection.csv"))
        assert logged["reference_date"].tolist() == ["2023-04-12"]
```

The same test in `tests/test_spot_price_scripts.py::TestBacktestScript` with `--strategy previous_day --area tokyo --start-date 2024-04-10 --end-date 2024-04-10` (no `--shap-nsamples`; `previous_day` has no SHAP), `seen["history_rows"] == len(curated_warehouse.prices)`, the stem `spot_diagnostic` and one column `value`.

- [ ] **Step 2: Run the three test files to verify the new tests fail**

Run: `uv run pytest tests/test_forecasting_strategy.py tests/test_demand_scripts.py tests/test_spot_price_scripts.py -q --no-cov -k "diagnostics"`
Expected: FAIL — `AttributeError: 'Done' object has no attribute 'diagnostics'`; the script tests fail on the missing artifact.

- [ ] **Step 3: Implement**

`strategy.py`, after `contributions` (the `BacktestRun` name is already imported under `TYPE_CHECKING`):

```python
    def diagnostics(self, history: HistoryT, run: BacktestRun) -> dict[str, pd.DataFrame]:
        """Per-run artifacts computed after the backtest, keyed by artifact stem.

        Optional, like :meth:`contributions`. Called by the backtest scripts
        inside the MLflow run, after the forecasts and contributions are
        published, so an implementation may also log metrics. The scripts log
        each frame as ``<stem>.csv``.

        Parameters
        ----------
        history : HalfHourlySeries
            The full history the backtest ran on.
        run : BacktestRun
            The backtest's forecasts and skipped days.

        Returns
        -------
        dict of str to pandas.DataFrame
            Empty by default.
        """
        return {}
```

Both scripts, right after the `contributions` block (before `heatmaps = …`):

```python
        for stem, frame in strategy.diagnostics(demand, run).items():
            log_dataframe(frame, f"{stem}.csv")
```

(`prices` instead of `demand` in the spot script.)

- [ ] **Step 4: Run the suite**

Run: `just test`
Expected: PASS, 100 %.

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/forecasting/strategy.py scripts/demand_backtest.py scripts/spot_price_backtest.py tests/test_forecasting_strategy.py tests/test_demand_scripts.py tests/test_spot_price_scripts.py
git commit -m "feat(forecasting): diagnostics hook logged by both backtest scripts

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
### Task 8: The strategy, its registry entry and `build_strategy`

**Files:**
- Modify: `power_market_analytics/tasks/demand/similar_day.py` (one property)
- Modify: `power_market_analytics/tasks/demand/strategies/lgbm.py` (append)
- Modify: `power_market_analytics/tasks/demand/strategies/__init__.py`
- Modify: `tests/test_demand_similar_day.py`, `tests/test_demand_lgbm.py`, `tests/test_demand_strategies.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 4–7.
- Produces: `SimilarDaySelector.first_scorable_day -> pd.Timestamp | None`; `SIMILAR_DAY_FEATURE_COLS = (*MSM_POPW_DAY_TYPE_FEATURE_COLS, SIMILAR_DAY_FEATURE)`; `DemandLightGbmMsmPopWeightedDayTypeSimilarDayEvalSet`; `LightGbmMsmPopWeightedDayTypeSimilarDayStrategy(temperature, weather_forecast, day_calendar, weather_observed, hourly_load, *, census_year, window_half_width_days=30, **kwargs)` with `name = "lightgbm_msm_popw_daytype_simday"`, `selector`, `hourly_load`, `_selections`, `diagnostics`; `STRATEGIES["lightgbm_msm_popw_daytype_simday"]`; a `build_strategy` branch.

- [ ] **Step 1: Write the failing tests**

`tests/test_demand_similar_day.py`, in `TestSelectorSetup`:

```python
    def test_first_scorable_day(self, selector):
        assert selector.first_scorable_day == HOLIDAYS[0] + pd.Timedelta(days=394)
        none = SimilarDaySelector(make_calendar(), make_forecast(pd.date_range("2024-01-01", "2024-01-05")), make_observed(), make_hourly_load())
        assert none.first_scorable_day is None
```

`tests/test_demand_lgbm.py` (append; imports: `SIMILAR_DAY_FEATURE`, `SIMILAR_DAY_COMPONENTS`, `SimilarDayRetrieval`, `SimilarDaySelection` from `similar_day`; `SIMILAR_DAY_FEATURE_COLS`, `DemandLightGbmMsmPopWeightedDayTypeSimilarDayEvalSet`, `LightGbmMsmPopWeightedDayTypeSimilarDayStrategy` from `strategies.lgbm`; and from `tests.test_demand_similar_day` the helpers `make_calendar`, `make_forecast`, `make_observed`, `make_hourly_load`, `load_at`, `HOLIDAYS as SIM_HOLIDAYS`):

```python
#: The similar-day strategy needs a year of candidates: demand and temperature from
#: 2024-02-01, weather profiles, hourly load and the calendar from 2023 (the
#: test_demand_similar_day fixtures).
SIM_DEMAND_DAYS = pd.date_range("2024-02-01", "2024-04-29", freq="D")
SIM_D = pd.Timestamp("2024-04-10")


@pytest.fixture(scope="module")
def sim_inputs():
    return {
        "demand": make_demand(SIM_DEMAND_DAYS),
        "temperature": make_temperature(SIM_DEMAND_DAYS),
        "weather_forecast": make_forecast(),
        "day_calendar": make_calendar(),
        "weather_observed": make_observed(),
        "hourly_load": make_hourly_load(),
    }


def make_sim_strategy(inputs, **kwargs) -> LightGbmMsmPopWeightedDayTypeSimilarDayStrategy:
    return LightGbmMsmPopWeightedDayTypeSimilarDayStrategy(
        inputs["temperature"],
        inputs["weather_forecast"],
        inputs["day_calendar"],
        inputs["weather_observed"],
        inputs["hourly_load"],
        census_year=2020,
        train_window_days=30,
        **kwargs,
    )


class TestSimilarDayClassAttributes:
    def test_features_and_frames(self):
        cls = LightGbmMsmPopWeightedDayTypeSimilarDayStrategy
        assert cls.name == "lightgbm_msm_popw_daytype_simday"
        assert issubclass(cls, LightGbmMsmPopWeightedDayTypeStrategy)
        assert SIMILAR_DAY_FEATURE_COLS == (*MSM_POPW_DAY_TYPE_FEATURE_COLS, SIMILAR_DAY_FEATURE)
        assert cls.feature_cols == SIMILAR_DAY_FEATURE_COLS
        assert cls.categorical_feature_cols == (DAY_TYPE_FEATURE,)
        assert cls.lookback_days == LightGbmStrategy.lookback_days
        assert cls.eval_set_cls is DemandLightGbmMsmPopWeightedDayTypeSimilarDayEvalSet
        assert list(DemandLightGbmMsmPopWeightedDayTypeSimilarDayEvalSet.schema) == [
            "trade_date", "time_code", "month", "day_of_week", TEMPERATURE_FEATURE, DEMAND_LAG_FEATURE,
            POPW_FORECAST_TEMPERATURE_FEATURE, DAY_TYPE_FEATURE, SIMILAR_DAY_FEATURE,
            "actual_demand_kwh", "forecast_demand_kwh",
        ]
        assert SIMILAR_DAY_FEATURE in DemandLightGbmMsmPopWeightedDayTypeSimilarDayEvalSet.non_null_cols


class TestSimilarDayPredict:
    def test_first_predict_fits_then_joins_the_selected_days_load(self, sim_inputs):
        strategy = make_sim_strategy(sim_inputs)
        history = visible(sim_inputs["demand"], SIM_D)
        forecast = strategy.predict(SIM_D, history)
        assert isinstance(forecast, DemandForecast)
        weights = strategy.selector.weights
        assert weights.fit_through == history.df["trade_date"].max()
        assert weights.fit_from == SIM_HOLIDAYS[0] + pd.Timedelta(days=394)
        record = strategy._shap_records[SIM_D]
        assert list(record.columns)[: len(SIMILAR_DAY_FEATURE_COLS) + 2] == ["trade_date", "time_code", *SIMILAR_DAY_FEATURE_COLS[1:]] or SIMILAR_DAY_FEATURE in record.columns
        selection = strategy._selections[SIM_D]
        assert list(selection.columns) == list(SimilarDaySelection.schema)
        reference = selection.iloc[0]["reference_date"]
        assert selection.iloc[0].equals(strategy.selector.select([SIM_D]).df.iloc[0])
        expected = [load_at(reference, (tc + 1) // 2) / 2 for tc in range(1, 49)]
        assert record[SIMILAR_DAY_FEATURE].tolist() == pytest.approx(expected)
        reconstructed = record[list(strategy.shap_cols)].sum(axis=1) + record["shap_expected_value"]
        np.testing.assert_allclose(reconstructed.to_numpy(), forecast.df["forecast_demand_kwh"].to_numpy(), atol=1e-3)

    def test_weights_are_fitted_once(self, sim_inputs):
        strategy = make_sim_strategy(sim_inputs)
        strategy.predict(SIM_D, visible(sim_inputs["demand"], SIM_D))
        first = strategy.selector.weights
        later = SIM_D + pd.Timedelta(days=7)
        strategy.predict(later, visible(sim_inputs["demand"], later))
        assert strategy.selector.weights is first
        assert set(strategy._selections) == {SIM_D, later}

    def test_a_day_without_pairs_yet_is_unforecastable(self, sim_inputs):
        strategy = make_sim_strategy(sim_inputs)
        early = pd.Timestamp("2024-02-08")  # history ends 02-06, before the first scorable target 02-07
        with pytest.raises(ForecastUnavailableError, match="no training pairs with a target day on or before 2024-02-06"):
            strategy.predict(early, visible(sim_inputs["demand"], early))

    def test_a_day_outside_the_calendar_is_unforecastable(self, sim_inputs):
        strategy = make_sim_strategy(sim_inputs)
        strategy.predict(SIM_D, visible(sim_inputs["demand"], SIM_D))
        beyond = pd.Timestamp("2024-04-30")  # after the calendar's last holiday
        with pytest.raises(ForecastUnavailableError, match=rf"features \['{DAY_TYPE_FEATURE}', '{SIMILAR_DAY_FEATURE}'\] unavailable"):
            strategy.predict(beyond, visible(sim_inputs["demand"], beyond))

    def test_build_eval_set_before_predict_raises(self, sim_inputs):
        strategy = make_sim_strategy(sim_inputs)
        with pytest.raises(RuntimeError, match="not fitted"):
            strategy.build_eval_set(sim_inputs["demand"], SIM_D, SIM_D, run=None)


SIM_WINDOW_START = pd.Timestamp("2024-04-08")
SIM_WINDOW_END = pd.Timestamp("2024-04-14")


class TestSimilarDayBacktestEvalAndEvaluate:
    @pytest.fixture(scope="class")
    def backtested(self, sim_inputs):
        strategy = make_sim_strategy(sim_inputs, refit_every_days=7)
        return strategy, run_backtest(strategy, sim_inputs["demand"], SIM_WINDOW_START, SIM_WINDOW_END)

    def test_backtest_covers_the_window(self, backtested):
        _, run = backtested
        assert run.skipped_days == ()
        assert len(run.result) == 7 * 48

    def test_eval_set_and_contributions_carry_the_feature(self, backtested, sim_inputs):
        strategy, run = backtested
        eval_set = strategy.build_eval_set(sim_inputs["demand"], SIM_WINDOW_START, SIM_WINDOW_END, run=run)
        assert type(eval_set) is DemandLightGbmMsmPopWeightedDayTypeSimilarDayEvalSet
        assert len(eval_set) == 7 * 48
        assert eval_set.df[SIMILAR_DAY_FEATURE].notna().all()
        contributions = strategy.contributions()
        assert SIMILAR_DAY_FEATURE in set(contributions.df["component"])

    def test_evaluate_logs_the_selector_params(self, backtested, sim_inputs):
        strategy, run = backtested
        eval_set = strategy.build_eval_set(sim_inputs["demand"], SIM_WINDOW_START, SIM_WINDOW_END, run=run)
        with mlflow.start_run() as active:
            strategy.evaluate(eval_set, explainability_nsamples=20)
        params = mlflow.get_run(active.info.run_id).data.params
        assert params["lgbm_feature_cols"] == ",".join(SIMILAR_DAY_FEATURE_COLS)
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
        assert selection.df["trade_date"].tolist() == list(pd.date_range(SIM_WINDOW_START, SIM_WINDOW_END))
        assert retrieval.df["trade_date"].tolist() == list(pd.date_range(SIM_WINDOW_START, SIM_WINDOW_END))
        metrics = mlflow.get_run(active.info.run_id).data.metrics
        assert set(metrics) == {
            "similar_day_load_difference_selected",
            "similar_day_load_difference_lag_364",
            "similar_day_load_difference_oracle",
            "similar_day_share_better_than_lag_364",
        }
        assert 0 <= metrics["similar_day_share_better_than_lag_364"] <= 1

    def test_diagnostics_without_forecasts_is_empty(self, sim_inputs):
        strategy = make_sim_strategy(sim_inputs)
        run = BacktestRun(result=DemandBacktestResult.from_df(pd.DataFrame({"trade_date": pd.to_datetime([]), "time_code": np.array([], dtype="int64"), "actual_demand_kwh": np.array([], dtype="float64"), "forecast_demand_kwh": np.array([], dtype="float64")})), skipped_days=())
        with mlflow.start_run():
            assert strategy.diagnostics(sim_inputs["demand"], run) == {}
```

`tests/test_demand_strategies.py`: add `"lightgbm_msm_popw_daytype_simday"` to `test_registered_names` (and its class identity), and:

```python
    def test_similar_day_strategy_loads_its_five_inputs(self, spark, curated_warehouse: CuratedWarehouse):
        strategy = build_strategy("lightgbm_msm_popw_daytype_simday", area_code="tokyo", spark=spark)
        assert type(strategy) is LightGbmMsmPopWeightedDayTypeSimilarDayStrategy
        assert strategy.census_year == 2020
        assert len(strategy.temperature) == len(curated_warehouse.weather)
        assert len(strategy.hourly_load) == len(curated_warehouse.hourly_load)
        assert strategy.selector.first_candidate_day == min(HOLIDAYS_2024_SPRING)
        assert strategy.selector.hourly_load_span == (HOURLY_LOAD_DAYS[0], HOURLY_LOAD_DAYS[-1])
        assert strategy.day_types.df["day_type"].tolist() == [expected_day_type(d) for d in strategy.day_types.df["trade_date"]]
```

(import `expected_day_type` from `tests.test_demand_datasets`, `HOLIDAYS_2024_SPRING`, `HOURLY_LOAD_DAYS` from `tests.conftest`).

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_demand_lgbm.py tests/test_demand_strategies.py tests/test_demand_similar_day.py -q --no-cov -k "SimilarDay or similar_day or first_scorable"`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement**

`similar_day.py`, in `SimilarDaySelector` after `lags`:

```python
    @property
    def first_scorable_day(self) -> pd.Timestamp | None:
        """The earliest forecast day that can be scored, if any."""
        scorable = self.scorable_days(self._forecast.days)
        return None if scorable.empty else scorable[0]
```

`strategies/lgbm.py` (imports: `math`, `mlflow`, `ForecastUnavailableError` from `forecasting.strategy`, `BacktestRun` from `forecasting.backtest`, `HalfHourlySeries` from `forecasting.frames`, the frames `AreaHourlyLoad`, `AreaObservedWeather`, `AreaWeatherForecast`, `DayCalendar`, and from `similar_day`: `PERIODS_PER_HOUR`, `SIMILAR_DAY_COMPONENTS`, `SIMILAR_DAY_FEATURE`, `SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS`, `SimilarDaySelection`, `SimilarDaySelector`, `join_similar_day_load`, `retrieval_metrics`); append:

```python
SIMILAR_DAY_FEATURE_COLS = (*MSM_POPW_DAY_TYPE_FEATURE_COLS, SIMILAR_DAY_FEATURE)


class DemandLightGbmMsmPopWeightedDayTypeSimilarDayEvalSet(DemandLightGbmMsmPopWeightedDayTypeEvalSet):
    """Design matrix for :class:`LightGbmMsmPopWeightedDayTypeSimilarDayStrategy`:
    the day-type design matrix plus the similar day's load per period.

    Grain: (trade_date, time_code).
    """

    feature_cols = SIMILAR_DAY_FEATURE_COLS
    schema = {
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "month": "int64",
        "day_of_week": "int64",
        TEMPERATURE_FEATURE: "float64",
        DEMAND_LAG_FEATURE: "float64",
        POPW_FORECAST_TEMPERATURE_FEATURE: "float64",
        DAY_TYPE_FEATURE: "int64",
        SIMILAR_DAY_FEATURE: "float64",
        TARGET_COL: "float64",
        FORECAST_COL: "float64",
    }
    non_null_cols = [*SIMILAR_DAY_FEATURE_COLS, TARGET_COL, FORECAST_COL]


class LightGbmMsmPopWeightedDayTypeSimilarDayStrategy(LightGbmMsmPopWeightedDayTypeStrategy):
    """:class:`LightGbmMsmPopWeightedDayTypeStrategy` plus the load of a learned
    similar day one year earlier (``similar_day_demand_kwh``).

    Experiment E-002 of docs/research/demand/R-004-prior-year-load-lag.md. A
    :class:`SimilarDaySelector` picks, for every delivery day, the nearest
    day in D − 364 ± 30 under a seven-part weighted distance whose weights are
    fitted once per run — on the first :meth:`predict`, over every pair whose
    target day the strategy may see — then frozen. The chosen day's でんき予報
    hourly load, halved per period, is the feature. Training rows and target
    days that cannot be scored get NaN and are dropped or skipped as usual.

    Parameters
    ----------
    temperature : AreaTemperature
        Hourly observed temperature at the representative station.
    weather_forecast : AreaWeatherForecast
        Population-weighted MSM forecast of temperature, humidity and rain by
        delivery day; its temperature is the parent's forecast feature.
    day_calendar : DayCalendar
        Holiday attributes of every day; its day types are the parent's.
    weather_observed : AreaObservedWeather
        Population-weighted observed temperature, humidity and rain.
    hourly_load : AreaHourlyLoad
        The でんき予報 hourly load.
    census_year : int
        Census vintage of the population weights, logged to the run.
    window_half_width_days : int, optional
        Half width of the candidate window around D − 364.
    **kwargs
        Forwarded to the parent.
    """

    name = "lightgbm_msm_popw_daytype_simday"
    feature_cols = SIMILAR_DAY_FEATURE_COLS
    eval_set_cls = DemandLightGbmMsmPopWeightedDayTypeSimilarDayEvalSet

    def __init__(
        self,
        temperature: AreaTemperature,
        weather_forecast: AreaWeatherForecast,
        day_calendar: DayCalendar,
        weather_observed: AreaObservedWeather,
        hourly_load: AreaHourlyLoad,
        *,
        census_year: int,
        window_half_width_days: int = SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS,
        **kwargs,
    ) -> None:
        super().__init__(
            temperature,
            weather_forecast.temperature_forecast(),
            day_calendar.day_types(),
            census_year=census_year,
            **kwargs,
        )
        self.hourly_load = hourly_load
        self.selector = SimilarDaySelector(
            day_calendar,
            weather_forecast,
            weather_observed,
            hourly_load,
            half_width_days=window_half_width_days,
        )
        self._selections: dict[pd.Timestamp, pd.DataFrame] = {}

    def predict(self, target_date: pd.Timestamp, history: HalfHourlySeries) -> DayAheadForecast:
        """Fit the selector once on the visible history, then score the day.

        Parameters
        ----------
        target_date : pandas.Timestamp
        history : HalfHourlySeries

        Returns
        -------
        DayAheadForecast

        Raises
        ------
        ForecastUnavailableError
            If no training pair exists by the day's cutoff, or the parent
            cannot forecast the day.
        """
        target_date = pd.Timestamp(target_date).as_unit("ns")
        try:
            self.selector.ensure_fitted(history.df["trade_date"].max())
        except ValueError as exc:
            raise ForecastUnavailableError(f"{self.name}: {exc}") from exc
        forecast = super().predict(target_date, history)
        self._selections[target_date] = self.selector.select([target_date]).df
        return forecast

    def _add_features(self, featured: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
        """The parent's features, then the selected similar day's load per period.

        Parameters
        ----------
        featured : pandas.DataFrame
            Rows keyed on (trade_date, time_code) with the calendar features.
        history : pandas.DataFrame
            Demand history in the ``AreaDemand`` layout.

        Returns
        -------
        pandas.DataFrame
            ``featured`` plus this strategy's ``feature_cols`` (the similar
            day's load NaN on days that cannot be scored).
        """
        featured = super()._add_features(featured, history)
        selection = self.selector.select(featured["trade_date"].unique())
        return join_similar_day_load(featured, selection, self.hourly_load, name=SIMILAR_DAY_FEATURE)

    def _extra_params(self) -> dict[str, object]:
        """Log the window, the parts and the fitted weights next to the inherited params.

        Returns
        -------
        dict of str to object
        """
        first = self.selector.first_scorable_day
        start, end = self.selector.hourly_load_span
        return {
            **super()._extra_params(),
            "similar_day_center_lag_days": self.selector.center_lag_days,
            "similar_day_window_half_width_days": self.selector.half_width_days,
            "similar_day_components": ",".join(SIMILAR_DAY_COMPONENTS),
            **self.selector.weights.as_params(),
            "similar_day_first_selectable_day": "none" if first is None else str(first.date()),
            "similar_day_hourly_load_span": f"{start.date()}..{end.date()}",
            "similar_day_periods_per_hour": PERIODS_PER_HOUR,
        }

    def diagnostics(self, history: HalfHourlySeries, run: BacktestRun) -> dict[str, pd.DataFrame]:
        """The forecast days' selections and the retrieval check, with four metrics logged.

        Parameters
        ----------
        history : HalfHourlySeries
            Unused: the selector holds the hourly load itself.
        run : BacktestRun

        Returns
        -------
        dict of str to pandas.DataFrame
            ``similar_day_selection`` and ``similar_day_retrieval``; empty
            when no forecast day was recorded.
        """
        days = [d for d in pd.to_datetime(run.result.df["trade_date"].unique()) if d in self._selections]
        if not days:
            return {}
        selection = SimilarDaySelection.from_df(
            pd.concat([self._selections[d] for d in sorted(days)], ignore_index=True)
        )
        retrieval = self.selector.retrieval(selection)
        mlflow.log_metrics(
            {key: value for key, value in retrieval_metrics(retrieval).items() if not math.isnan(value)}
        )
        return {"similar_day_selection": selection.df, "similar_day_retrieval": retrieval.df}
```

`strategies/__init__.py`: import the class and the four loaders (`load_area_hourly_load`, `load_area_observed_weather_population_weighted`, `load_area_weather_forecast_population_weighted`, `load_day_calendar`); register `LightGbmMsmPopWeightedDayTypeSimilarDayStrategy.name: LightGbmMsmPopWeightedDayTypeSimilarDayStrategy` after the day-type entry; in `build_strategy`, before the day-type branch:

```python
    if issubclass(cls, LightGbmMsmPopWeightedDayTypeSimilarDayStrategy):
        weather = load_area_weather_forecast_population_weighted(area_code, spark=spark)
        observed = load_area_observed_weather_population_weighted(
            area_code, census_year=weather.census_year, spark=spark
        )
        return cls(
            temperature,
            weather.forecast,
            load_day_calendar(spark=spark),
            observed.weather,
            load_area_hourly_load(area_code, spark=spark),
            census_year=weather.census_year,
            train_start_date=train_start_date,
        )
```

Update the docstring's list of what is loaded.

- [ ] **Step 4: Run the suite, lint and mypy**

Run: `just test && just lint && just mypy`
Expected: PASS, 100 %, clean. If the `_shap_records` column-order assertion in the first predict test is awkward, replace it with `assert SIMILAR_DAY_FEATURE in record.columns`.

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/tasks/demand/similar_day.py power_market_analytics/tasks/demand/strategies tests/test_demand_similar_day.py tests/test_demand_lgbm.py tests/test_demand_strategies.py
git commit -m "feat(demand): lightgbm_msm_popw_daytype_simday — the learned similar-day reference load

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
### Task 9: Docs and the spec's deviation notes

**Files:**
- Modify: `CLAUDE.md` (the demand-task paragraph in "Architecture", the `demand_backtest.py` bullet in "Commands", the "Explanations" bullet)
- Modify: `docs/superpowers/specs/2026-09-05-demand-similar-day-reference-design.md` (§4.2, §4.1, §6)
- Modify: `docs/research/demand/R-004-prior-year-load-lag.md` (E-002 "Code or pull request")

- [ ] **Step 1: CLAUDE.md**

In the `demand_backtest.py` command bullet, extend the strategy list: after `lightgbm_msm_popw_daytype = …`, add "`lightgbm_msm_popw_daytype_simday` = that + `similar_day_demand_kwh` (research `demand/R-004` E-002; needs the でんき予報 hourly load of `fct_area_power_usage_hourly`, `dim_date.holiday_degree`, and the population-weighted MSM forecast and JMA observations of temperature, humidity and rain — the E-002 run also logs `similar_day_selection.csv` / `similar_day_retrieval.csv` and four `similar_day_*` metrics through the `diagnostics` hook)".

In the Architecture demand-task paragraph, after the sentence on the removed `lightgbm_msm_popw_daytype_lag1y`, add: "`lightgbm_msm_popw_daytype_simday` (`LightGbmMsmPopWeightedDayTypeSimilarDayStrategy`, research `demand/R-004` E-002, 2026-09-05) = the baseline + `similar_day_demand_kwh`: `tasks/demand/similar_day.py`'s `SimilarDaySelector` scores the 61 days D − 364 ± 30 with a softmax-weighted distance over seven parts (calendar days from D − 364; the 24-h RMSE of D's population-weighted MSM forecast against the candidate's population-weighted observation for temperature, humidity and rain; |Δ| of `dim_date`'s days since / until a named holiday and of `holiday_degree`), fits the weights once per run by `scipy.optimize.least_squares` on the pairs up to the first forecast day's cutoff (Park, Song and Kwon 2020 Eq. 1–3), picks the nearest day and joins its hourly load ÷ 2 per period; days whose window starts before 2016-04-01 or that lack a forecast profile are unscorable (first scorable day 2019-04-01). The strategy's `diagnostics` returns the selection and the retrieval check (selected vs D − 364 vs oracle load difference)."

In the "Explanations" bullet (or a new bullet after it), one sentence: "`ForecastStrategy.diagnostics(history, run)` (default `{}`) returns per-run frames the backtest scripts log as `<stem>.csv` after publishing; a strategy may log metrics inside it."

Add to "Gotchas": "`scipy` is a declared dependency since 2026-09-05 (`scipy.*` is mypy-ignored like `shap.*`)."

- [ ] **Step 2: Spec deviations**

§4.2: replace the two frame bullets with the implemented shape — "Forecast, target side: a new frame `AreaWeatherForecast` (`forecast_temperature_c`, `forecast_relative_humidity_pct`, `forecast_precipitation_mm`, nullable) from `load_area_weather_forecast_population_weighted`; its `temperature_forecast()` is the parent's `AreaTemperatureForecast`, so that frame and its loader are unchanged" and "Observed, candidate side: `AreaObservedWeather` keyed `obs_date × hour_ending` like `AreaTemperature`". §4.1: "computed in pandas with a forward and a backward fill over the spine". §6: unchanged. Add a line under the status: "Implemented 2026-09-05 per `docs/superpowers/plans/2026-09-05-demand-similar-day-reference.md`; deviations noted in §4.1–4.2."

- [ ] **Step 3: Verify and commit**

Run: `just lint && just mypy && just test`

```bash
git add CLAUDE.md docs/superpowers/specs/2026-09-05-demand-similar-day-reference-design.md docs/research/demand/R-004-prior-year-load-lag.md
git commit -m "docs(demand): the similar-day strategy, its selector and the diagnostics hook

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: E-002 run, research record, PR

**Files:**
- Modify: `docs/research/demand/R-004-prior-year-load-lag.md` (E-002 results, interpretation, reading against the decision rule; decision stays Pending for the researcher)
- Modify: `docs/research/demand/README.md` (E-002 row summary)

- [ ] **Step 1: Run E-002 in the devcontainer** (a main-session background task; the baseline run `0a6b8a5560d445d5b9705bde99cf13ae` is compared as run)

```bash
just python scripts/demand_backtest.py --strategy lightgbm_msm_popw_daytype_simday --area tokyo --start-date 2024-08-18 --end-date 2026-08-17
```

Expected: the log ends with `MAE=… kWh  MAPE=…%` and the run id. Record `n_days_skipped` (should be 0), `n_refits`, the four `similar_day_*` metrics and the `similar_day_weights` param.

- [ ] **Step 2: Rebuild the marts and compare**

```bash
just dbt build --select +fct_demand_forecast_accuracy +fct_demand_forecast_contribution
just python scripts/compare_demand_runs.py --baseline 0a6b8a5560d445d5b9705bde99cf13ae --candidate <run_id> --mae-by-month-png docs/research/demand/assets/R-004-E-002-mae-by-month.png
```

Expected: the markdown comparison (overall MAE / MAPE / bias, day part, day type, month, season, band, top-10 %, the daily paired comparison with its bootstrap CI). Also summarise the retrieval check from the run's `similar_day_retrieval.csv` (download with `mlflow.artifacts.download_artifacts`): mean selected / D − 364 / oracle load difference, the share better than D − 364, and the selected-rank distribution.

- [ ] **Step 3: Record the results**

Fill E-002's Results table (Overall MAE, holidays, the O-002 days 2025-08-12 / 2026-08-12, the O-003 days 2025-02-11 / 2026-02-11, bias), the retrieval summary and the fitted weights; write the Interpretation and a "Reading against the decision rule" paragraph in the same shape as E-001's; leave `**Decision:** Pending` — the researcher decides. Update the README index row's E-002 clause with the headline numbers, and E-002's Execution with the run id and the PR number.

```bash
git add docs/research/demand/R-004-prior-year-load-lag.md docs/research/demand/README.md docs/research/demand/assets/R-004-E-002-mae-by-month.png
git commit -m "docs(research): R-004 E-002 results — the learned similar-day reference load

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 4: Open the PR and run the review loop**

`git push -u origin feature/demand-similar-day-reference`, then `gh pr create` with title `feat(demand): learned similar-day reference load (R-004 E-002)` and a body with *Why* / *What* / *Proof* (the E-002 numbers, `just test` 100 %, `just lint`, `just mypy`, the full `just dbt build`), ending with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`; `gh pr edit <n> --add-assignee hankehly --add-label enhancement --add-label documentation`. Then the Codex → Copilot loop of CLAUDE.md until both are clean and CI is green on the current head; report the PR as ready. The researcher merges.

---

## Self-review

- Spec coverage: decisions 1–11 → Tasks 5 (1, 7), 3/8 (2), 4 (3), 4/8 (4, 10), 4 (5), 5 (6), 8 (8), 3 (9), 10 (11); §4.1 → Tasks 1, 3; §4.2 → 3, 4; §4.3 → 4; §4.4–4.5 → 5; §4.6–4.8 → 6, 8; §5 → 8; §6 → 7; §7 → 5 (scipy), 9; §8 → 1, 3–6; §9 → 10; §11 → every task's tests plus Task 10's devcontainer run.
- Type consistency: `SimilarDaySelector(calendar, weather_forecast, weather_observed, hourly_load, *, center_lag_days, half_width_days)` is the constructor everywhere; `SimilarDayWeights.as_params()` keys are the ones asserted in Tasks 5 and 8; `load_area_weather_forecast_population_weighted` returns `PopulationWeightedWeatherForecast(forecast, census_year, n_stations)` as used in Task 8's `build_strategy`.
