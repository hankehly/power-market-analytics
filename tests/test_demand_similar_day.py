"""Tests for the learned similar-day selector (R-004 E-002)."""

from __future__ import annotations

import math
from collections.abc import Collection

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
    SimilarDayRetrieval,
    SimilarDaySelection,
    SimilarDaySelector,
    SimilarDayTrainingPairs,
    SimilarDayWeights,
    fit_similar_day_weights,
    join_similar_day_load,
    load_difference,
    retrieval_metrics,
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
    return (
        10.0
        + 12.0 * math.sin(2 * math.pi * (doy - 100) / 365)
        + 4.0 * math.sin(2 * math.pi * (hour - 9) / 24)
    )


def humidity_at(day: pd.Timestamp, hour: int) -> float:
    return 60.0 + 10.0 * math.cos(2 * math.pi * hour / 24) + (day.dayofyear % 7)


def rain_at(day: pd.Timestamp, hour: int) -> float:
    return 1.0 if day.dayofyear % 9 == 0 and 12 <= hour <= 15 else 0.0


def load_at(day: pd.Timestamp, hour: int) -> float:
    weekend = -5_000_000.0 if day.dayofweek >= 5 or day in HOLIDAYS else 0.0
    return (
        30_000_000.0
        - 8_000_000.0 * math.cos(2 * math.pi * hour / 24)
        + weekend
        + 1_000.0 * (day - HISTORY_DAYS[0]).days
    )


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


def make_forecast(
    days=FORECAST_DAYS, *, drop: Collection[tuple[pd.Timestamp, int]] = ()
) -> AreaWeatherForecast:
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


def make_observed(
    days=HISTORY_DAYS, *, null_hours: Collection[tuple[pd.Timestamp, int]] = ()
) -> AreaObservedWeather:
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
                make_calendar(),
                make_forecast(),
                make_observed(),
                make_hourly_load(),
                center_lag_days=10,
                half_width_days=10,
            )

    def test_no_candidates_is_rejected(self):
        with pytest.raises(ValueError, match="no candidate days"):
            SimilarDaySelector(
                make_calendar(),
                make_forecast(),
                make_observed(pd.date_range("2022-01-01", "2022-01-05")),
                make_hourly_load(),
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
            np.mean(
                [
                    (temperature_at(D, h) + 0.5 - temperature_at(D_MINUS_364, h)) ** 2
                    for h in range(1, 25)
                ]
            )
        )
        expected_h = math.sqrt(
            np.mean(
                [(humidity_at(D, h) - 2.0 - humidity_at(D_MINUS_364, h)) ** 2 for h in range(1, 25)]
            )
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
            assert row[col] == pytest.approx(
                abs(calendar.loc[D, col] - calendar.loc[D_MINUS_364, col])
            )

    def test_a_candidate_missing_an_observed_hour_is_left_out(self):
        selector = SimilarDaySelector(
            make_calendar(),
            make_forecast(),
            make_observed(null_hours={(D_MINUS_364, 5)}),
            make_hourly_load(),
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
        t, c = D - pd.Timedelta(days=14), D_MINUS_364 - pd.Timedelta(days=14)
        row = pairs.df.set_index(["target_date", "candidate_date"]).loc[(t, c)]
        expected = np.mean(
            [abs(load_at(t, h) - load_at(c, h)) / load_at(t, h) for h in range(1, 25)]
        )
        assert row["load_difference"] == pytest.approx(expected)

    def test_no_pairs_before_the_first_scorable_day(self, selector):
        assert len(selector.training_pairs(pd.Timestamp("2024-01-31"))) == 0


def planted_pairs(
    n: int = 400, seed: int = 0
) -> tuple[SimilarDayTrainingPairs, np.ndarray, float, float]:
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
        np.testing.assert_allclose(
            fitted.distance(pairs), (pairs.df["load_difference"] - beta) / alpha, atol=1e-3
        )

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
        assert str(params["similar_day_weights"]).startswith("calendar_days=0.30")
        assert params["similar_day_fit_from"] == "2024-02-07"
        assert params["similar_day_fit_n_pairs"] == 400

    def test_too_few_pairs(self):
        pairs, _, _, _ = planted_pairs(n=MIN_FIT_PAIRS - 1)
        with pytest.raises(
            ValueError, match=f"{MIN_FIT_PAIRS - 1} training pairs; at least {MIN_FIT_PAIRS}"
        ):
            fit_similar_day_weights(pairs)

    def test_all_parts_zero_fits_the_mean(self):
        days = pd.date_range("2024-02-07", periods=MIN_FIT_PAIRS, freq="D")
        df = pd.DataFrame(
            np.zeros((MIN_FIT_PAIRS, 7)), columns=list(SIMILAR_DAY_COMPONENTS)
        ).assign(
            target_date=days,
            candidate_date=days - pd.Timedelta(days=364),
            load_difference=np.arange(MIN_FIT_PAIRS) / 10,
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
        fresh = SimilarDaySelector(
            make_calendar(), make_forecast(), make_observed(), make_hourly_load()
        )
        with pytest.raises(RuntimeError, match="not fitted"):
            fresh.weights

    def test_fit_once_and_reuse(self):
        fresh = SimilarDaySelector(
            make_calendar(), make_forecast(), make_observed(), make_hourly_load()
        )
        first = fresh.ensure_fitted(pd.Timestamp("2024-03-31"))
        assert fresh.weights is first
        assert fresh.ensure_fitted(pd.Timestamp("2024-04-15")) is first
        assert first.fit_through == pd.Timestamp("2024-03-31")
        assert first.n_pairs == len(fresh.training_pairs(pd.Timestamp("2024-03-31")))

    def test_fit_without_pairs_raises(self, selector):
        with pytest.raises(
            ValueError, match="no training pairs with a target day on or before 2024-01-31"
        ):
            selector.fit(pd.Timestamp("2024-01-31"))


@pytest.fixture(scope="module")
def fitted(selector) -> SimilarDaySelector:
    selector.ensure_fitted(pd.Timestamp("2024-03-31"))
    return selector


def hand_weights(**shares: float) -> SimilarDayWeights:
    weights = np.array([shares.get(c, 0.0) for c in SIMILAR_DAY_COMPONENTS])
    return SimilarDayWeights(
        components=SIMILAR_DAY_COMPONENTS,
        weights=weights / weights.sum(),
        scales=np.ones(7),
        alpha=1.0,
        beta=0.0,
        n_pairs=8,
        n_targets=8,
        fit_from=pd.Timestamp("2024-02-07"),
        fit_through=pd.Timestamp("2024-02-14"),
        fit_rmse=0.0,
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
        # A calendar-days-only distance is |lag - 364|: D - 364 wins outright, and without
        # it lags 363 and 365 tie at 1, both one day from the centre, so the earlier wins.
        selector = SimilarDaySelector(
            make_calendar(), make_forecast(), make_observed(), make_hourly_load()
        )
        selector._weights = hand_weights(calendar_days=1.0)
        assert selector.select([D]).df.iloc[0]["reference_date"] == D_MINUS_364
        without_centre = SimilarDaySelector(
            make_calendar(),
            make_forecast(),
            make_observed(null_hours={(D_MINUS_364, 1)}),
            make_hourly_load(),
        )
        without_centre._weights = hand_weights(calendar_days=1.0)
        row = without_centre.select([D]).df.iloc[0]
        assert row["reference_date"] == D - pd.Timedelta(days=365)
        assert np.isnan(row["lag_364_rank"])
        assert row["n_candidates"] == 60

    def test_nothing_scorable_gives_an_empty_frame(self, fitted):
        selection = fitted.select([pd.Timestamp("2023-12-31")])
        assert len(selection) == 0
        assert list(selection.df.columns) == list(SimilarDaySelection.schema)

    def test_select_before_fit_raises(self):
        fresh = SimilarDaySelector(
            make_calendar(), make_forecast(), make_observed(), make_hourly_load()
        )
        with pytest.raises(RuntimeError, match="not fitted"):
            fresh.select([D])


class TestJoinSimilarDayLoad:
    def test_hourly_load_of_the_reference_halved_per_period(self, fitted):
        selection = fitted.select([D])
        reference = selection.df.iloc[0]["reference_date"]
        points = pd.DataFrame(
            {
                "trade_date": [D] * 48 + [pd.Timestamp("2023-12-31")] * 2,
                "time_code": list(range(1, 49)) + [1, 2],
            }
        )
        points["time_code"] = points["time_code"].astype("int64")
        joined = join_similar_day_load(points, selection, make_hourly_load())
        assert list(joined.columns) == ["trade_date", "time_code", SIMILAR_DAY_FEATURE]
        expected = [load_at(reference, (tc + 1) // 2) / PERIODS_PER_HOUR for tc in range(1, 49)]
        assert joined[SIMILAR_DAY_FEATURE].head(48).tolist() == pytest.approx(expected)
        assert joined[SIMILAR_DAY_FEATURE].tail(2).isna().all()

    def test_custom_name(self, fitted):
        selection = fitted.select([D])
        points = pd.DataFrame({"trade_date": [D], "time_code": np.array([1], dtype="int64")})
        assert "ref_kwh" in join_similar_day_load(
            points, selection, make_hourly_load(), name="ref_kwh"
        )


class TestRetrieval:
    def test_outcomes_per_forecast_day(self, fitted):
        days = [D, D + pd.Timedelta(days=1), pd.Timestamp("2024-04-30")]  # 04-30: no calendar row
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
            make_calendar(),
            make_forecast(),
            make_observed(null_hours={(D_MINUS_364, 1)}),
            make_hourly_load(),
        )
        selector.ensure_fitted(pd.Timestamp("2024-03-31"))
        retrieval = selector.retrieval(selector.select([D]))
        assert np.isnan(retrieval.df.iloc[0]["lag_364_load_difference"])

    def test_days_without_a_known_load_are_left_out(self):
        # A forecast day after the hourly load ends: selectable, not checkable.
        beyond = SimilarDaySelector(
            make_calendar(),
            make_forecast(),
            make_observed(),
            make_hourly_load(pd.date_range("2023-01-01", "2024-04-09")),
        )
        beyond.ensure_fitted(pd.Timestamp("2024-03-31"))
        retrieval = beyond.retrieval(beyond.select([D]))
        assert len(retrieval) == 0
        assert list(retrieval.df.columns) == list(SimilarDayRetrieval.schema)


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
