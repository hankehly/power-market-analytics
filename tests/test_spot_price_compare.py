"""Tests for the matched two-run comparison (tasks/spot_price/compare.py).

``compare_runs`` is exercised on a hand-built ``RunErrors`` frame whose
errors are constant within simple regions, so every MAE / bias / n cell is
derived by hand in the comments; the synthetic ``pma_curated`` warehouse is
only used for ``load_run_errors`` and the "not matched" error path.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.tasks.spot_price.compare import (
    DAY_PARTS,
    RunErrors,
    SegmentComparison,
    _assert_matched,
    compare_runs,
    load_run_errors,
    to_markdown,
)
from tests.conftest import (
    ACCURACY_DAYS,
    BASELINE_RUN_ID,
    CANDIDATE_RUN_ID,
    UNMATCHED_RUN_ID,
    day_part,
    synthetic_price,
)

# --------------------------------------------------------------------------- hand-built frame
#
# Two runs x two delivery days x 48 time codes.
#   * day 1 (2024-01-31): actual 10.0 everywhere, OCCTO peak hour-ending 18
#   * day 2 (2024-02-01): actual 20.0 everywhere, no OCCTO forecast (NaN)
#   * baseline forecast  = actual + 2.0 on every row        -> abs 2.0, signed +2.0
#   * candidate forecast = actual + 0.5 for time codes 1..24 -> abs 0.5, signed +0.5
#                        = actual - 2.5 for time codes 25..48 -> abs 2.5, signed -2.5
#
# Day parts (dim_delivery_period): Overnight = tc 1..12, Morning = 13..16,
# Daytime = 17..36, Evening = 37..48.

DAY_1 = pd.Timestamp("2024-01-31")
DAY_2 = pd.Timestamp("2024-02-01")
BASE = "base"
CAND = "cand"


def candidate_error(time_code: int) -> float:
    return 0.5 if time_code <= 24 else -2.5


def build_run_errors(*, peak_hours: dict[pd.Timestamp, float] | None = None) -> RunErrors:
    """The frame described above; ``peak_hours`` overrides the OCCTO peak per day."""
    peaks = {DAY_1: 18.0, DAY_2: np.nan} if peak_hours is None else peak_hours
    actuals = {DAY_1: 10.0, DAY_2: 20.0}
    rows = []
    for run_id, error_of in ((BASE, lambda tc: 2.0), (CAND, candidate_error)):
        for day in (DAY_1, DAY_2):
            for tc in range(1, 49):
                rows.append(
                    {
                        "run_id": run_id,
                        "trade_date": day,
                        "time_code": tc,
                        "day_part": day_part(tc),
                        "max_demand_hour_ending": peaks[day],
                        "actual_price_jpy_kwh": actuals[day],
                        "forecast_price_jpy_kwh": actuals[day] + error_of(tc),
                    }
                )
    return RunErrors.from_df(
        pd.DataFrame(rows).astype({"time_code": "int64", "max_demand_hour_ending": "float64"})
    )


def segment_frame(rows: list[tuple]) -> pd.DataFrame:
    """Literal expected ``SegmentComparison`` contents (segment, n, base, cand, abs, rel)."""
    return pd.DataFrame(
        rows, columns=["segment", "n", "baseline", "candidate", "abs_change", "rel_change_pct"]
    ).astype({"n": "int64"})


def assert_segments(actual: SegmentComparison, expected_rows: list[tuple]) -> None:
    assert isinstance(actual, SegmentComparison)
    pd.testing.assert_frame_equal(
        actual.df.reset_index(drop=True), segment_frame(expected_rows), atol=1e-9, rtol=0
    )


# --------------------------------------------------------------------------- contracts


class TestRunErrorsContract:
    def test_accepts_nan_peak_hour(self):
        errors = build_run_errors()
        assert len(errors) == 2 * 2 * 48
        assert errors.df["max_demand_hour_ending"].isna().sum() == 2 * 48

    def test_rejects_null_day_part(self):
        df = build_run_errors().df.copy()
        df.loc[df.index[0], "day_part"] = None
        with pytest.raises(ValueError, match="RunErrors: column 'day_part' has 1 null values"):
            RunErrors.from_df(df)

    def test_rejects_duplicate_grain(self):
        df = build_run_errors().df
        with pytest.raises(ValueError, match="grain .* not unique"):
            RunErrors.from_df(pd.concat([df, df.head(1)], ignore_index=True))


class TestSegmentComparisonContract:
    def test_accepts_nan_relative_change(self):
        table = SegmentComparison.from_df(segment_frame([("all", 10, 1.0, 0.5, -0.5, np.nan)]))
        assert table.df["segment"].tolist() == ["all"]
        assert np.isnan(table.df["rel_change_pct"].iloc[0])

    def test_rejects_null_baseline(self):
        with pytest.raises(ValueError, match="column 'baseline' has 1 null values"):
            SegmentComparison.from_df(segment_frame([("all", 10, np.nan, 0.5, -0.5, -50.0)]))


# --------------------------------------------------------------------------- compare_runs


@pytest.fixture(scope="module")
def tables() -> dict[str, SegmentComparison]:
    return compare_runs(build_run_errors(), baseline_run_id=BASE, candidate_run_id=CAND)


class TestCompareRuns:
    def test_returns_the_six_sections(self, tables):
        assert list(tables) == ["overall", "day_part", "near_peak", "bias", "month", "price_band"]
        assert all(isinstance(t, SegmentComparison) for t in tables.values())

    def test_overall(self, tables):
        # 96 points per run; candidate abs sum per day = 24*0.5 + 24*2.5 = 72 -> 144/96 = 1.5
        assert_segments(tables["overall"], [("all", 96, 2.0, 1.5, -0.5, -25.0)])

    def test_day_part_in_canonical_order(self, tables):
        assert_segments(
            tables["day_part"],
            [
                # Overnight tc 1..12 (2 days x 12): candidate 0.5 throughout
                ("Overnight", 24, 2.0, 0.5, -1.5, -75.0),
                # Morning tc 13..16
                ("Morning", 8, 2.0, 0.5, -1.5, -75.0),
                # Daytime tc 17..36: 8 periods at 0.5 + 12 at 2.5 -> (4 + 30) / 20 = 1.7
                ("Daytime", 40, 2.0, 1.7, -0.3, -15.0),
                # Evening tc 37..48: candidate 2.5 throughout
                ("Evening", 24, 2.0, 2.5, 0.5, 25.0),
            ],
        )
        assert tables["day_part"].df["segment"].tolist() == list(DAY_PARTS)

    def test_day_part_only_lists_present_parts(self):
        errors = build_run_errors()
        no_evening = RunErrors.from_df(errors.df[errors.df["day_part"] != "Evening"])
        tables = compare_runs(no_evening, baseline_run_id=BASE, candidate_run_id=CAND)
        assert tables["day_part"].df["segment"].tolist() == ["Overnight", "Morning", "Daytime"]

    def test_near_peak_covers_six_periods_around_hour_ending_18(self, tables):
        # Hour-ending 18 = hour-of-day 17; +/-1 h -> hours 16..18 -> tc 33..38 on day 1
        # only (day 2 has no peak hour, NaN compares False). Candidate = 2.5 on all six.
        # Other periods: (144 - 6 * 2.5) / 90 = 129 / 90.
        assert_segments(
            tables["near_peak"],
            [
                ("within ±1 h of forecast peak hour", 6, 2.0, 2.5, 0.5, 25.0),
                ("other periods", 90, 2.0, 1.4333333333333, -0.5666666666667, -28.3333333333333),
            ],
        )

    def test_near_peak_membership_is_exactly_time_codes_33_to_38(self):
        # Make the six near-peak periods the only ones where the candidate is worse than
        # the baseline, so a wrong window shows up as a wrong candidate MAE.
        errors = build_run_errors(peak_hours={DAY_1: 18.0, DAY_2: 18.0})
        df = errors.df.copy()
        cand = df["run_id"] == CAND
        df.loc[cand, "forecast_price_jpy_kwh"] = df.loc[cand, "actual_price_jpy_kwh"] + np.where(
            df.loc[cand, "time_code"].between(33, 38), 9.0, 1.0
        )
        tables = compare_runs(RunErrors.from_df(df), baseline_run_id=BASE, candidate_run_id=CAND)
        assert_segments(
            tables["near_peak"],
            [
                ("within ±1 h of forecast peak hour", 12, 2.0, 9.0, 7.0, 350.0),
                ("other periods", 84, 2.0, 1.0, -1.0, -50.0),
            ],
        )

    def test_near_peak_hours_zero_is_the_peak_hour_only(self):
        # Hour-of-day 17 only -> tc 35, 36 on day 1 -> 2 periods per run.
        tables = compare_runs(
            build_run_errors(), baseline_run_id=BASE, candidate_run_id=CAND, near_peak_hours=0
        )
        segments = tables["near_peak"].df.set_index("segment")
        assert segments.index.tolist() == ["forecast peak hour only", "other periods"]
        assert segments.loc["forecast peak hour only", "n"] == 2
        assert segments.loc["forecast peak hour only", "candidate"] == 2.5
        assert segments.loc["other periods", "n"] == 94

    def test_bias_is_the_mean_signed_error(self, tables):
        # all: candidate (24*0.5 - 24*2.5) / 48 = -1.0; Daytime: (8*0.5 - 12*2.5) / 20 = -1.3
        assert_segments(
            tables["bias"],
            [
                ("all", 96, 2.0, -1.0, -3.0, np.nan),
                ("Daytime", 40, 2.0, -1.3, -3.3, np.nan),
            ],
        )

    def test_month_segments(self, tables):
        assert_segments(
            tables["month"],
            [
                ("2024-01", 48, 2.0, 1.5, -0.5, -25.0),
                ("2024-02", 48, 2.0, 1.5, -0.5, -25.0),
            ],
        )

    def test_price_band_default_quantile(self, tables):
        # Daily means 10 and 20; 0.9-quantile (linear) = 10 + 0.9 * 10 = 19.0 -> day 2 only.
        assert_segments(
            tables["price_band"],
            [
                ("top 10% price days (daily mean >= 19.00)", 48, 2.0, 1.5, -0.5, -25.0),
                ("other 90% of days", 48, 2.0, 1.5, -0.5, -25.0),
            ],
        )

    def test_price_band_custom_quantile(self):
        # 0.5-quantile of {10, 20} = 15.0; still only day 2 is at or above it.
        tables = compare_runs(
            build_run_errors(),
            baseline_run_id=BASE,
            candidate_run_id=CAND,
            high_price_quantile=0.5,
        )
        assert tables["price_band"].df["segment"].tolist() == [
            "top 50% price days (daily mean >= 15.00)",
            "other 50% of days",
        ]

    def test_price_band_uses_daily_mean_not_row_prices(self):
        # Spike one period of day 1 so its daily mean (10 + 480/48 = 20) matches day 2's:
        # both days now sit at the threshold and land in the top band.
        errors = build_run_errors()
        df = errors.df.copy()
        spike = (df["trade_date"] == DAY_1) & (df["time_code"] == 1)
        df.loc[spike, "actual_price_jpy_kwh"] = 10.0 + 480.0
        df.loc[spike, "forecast_price_jpy_kwh"] = df.loc[spike, "forecast_price_jpy_kwh"] + 480.0
        tables = compare_runs(RunErrors.from_df(df), baseline_run_id=BASE, candidate_run_id=CAND)
        assert tables["price_band"].df["segment"].tolist() == [
            "top 10% price days (daily mean >= 20.00)"
        ]
        assert tables["price_band"].df["n"].tolist() == [96]

    def test_ignores_rows_of_other_runs(self):
        errors = build_run_errors()
        third = errors.df[errors.df["run_id"] == BASE].assign(
            run_id="third", forecast_price_jpy_kwh=lambda d: d["actual_price_jpy_kwh"] + 50.0
        )
        with_third = RunErrors.from_df(pd.concat([errors.df, third], ignore_index=True))
        tables = compare_runs(with_third, baseline_run_id=BASE, candidate_run_id=CAND)
        assert_segments(tables["overall"], [("all", 96, 2.0, 1.5, -0.5, -25.0)])

    def test_no_peak_hour_anywhere_raises(self):
        errors = build_run_errors(peak_hours={DAY_1: np.nan, DAY_2: np.nan})
        with pytest.raises(ValueError, match="No rows carry an OCCTO peak hour"):
            compare_runs(errors, baseline_run_id=BASE, candidate_run_id=CAND)

    def test_missing_run_raises(self):
        with pytest.raises(ValueError, match="Both runs must be present"):
            compare_runs(build_run_errors(), baseline_run_id=BASE, candidate_run_id="nope")

    def test_unmatched_points_raise(self):
        errors = build_run_errors()
        df = errors.df[~((errors.df["run_id"] == CAND) & (errors.df["trade_date"] == DAY_2))]
        with pytest.raises(
            ValueError,
            match=(
                r"Runs are not matched: \{'left_only': 48, 'right_only': 0, 'both': 0\} "
                r"points are not in both \(left_only = baseline only, right_only = candidate only\)"
            ),
        ):
            compare_runs(RunErrors.from_df(df), baseline_run_id=BASE, candidate_run_id=CAND)


class TestAssertMatched:
    def test_reports_candidate_only_points(self):
        df = build_run_errors().df
        # Drop the baseline's first day so 48 candidate points have no counterpart.
        trimmed = df[~((df["run_id"] == BASE) & (df["trade_date"] == DAY_1))]
        with pytest.raises(ValueError, match=r"\{'right_only': 48, 'left_only': 0, 'both': 0\}"):
            _assert_matched(trimmed, BASE, CAND)

    def test_passes_on_identical_points(self):
        assert _assert_matched(build_run_errors().df, BASE, CAND) is None


# --------------------------------------------------------------------------- to_markdown


class TestToMarkdown:
    def test_renders_github_table(self):
        table = SegmentComparison.from_df(
            segment_frame(
                [
                    ("all", 1234, 2.0, 1.5, -0.5, -25.0),
                    ("Daytime", 40, 2.0, -1.3, -3.3, np.nan),
                ]
            )
        )
        assert to_markdown(table, metric="MAE") == "\n".join(
            [
                "| Segment | n | Baseline MAE (JPY/kWh) | Candidate MAE (JPY/kWh) "
                "| Absolute change | Relative change |",
                "|---|---:|---:|---:|---:|---:|",
                "| all | 1,234 | 2.000 | 1.500 | -0.500 | -25.0% |",
                "| Daytime | 40 | 2.000 | -1.300 | -3.300 | — |",
            ]
        )

    def test_positive_changes_carry_a_plus_sign_and_unit_is_configurable(self):
        table = SegmentComparison.from_df(segment_frame([("x", 3, 1.0, 1.25, 0.25, 25.0)]))
        assert to_markdown(table, metric="bias", unit="pct") == "\n".join(
            [
                "| Segment | n | Baseline bias (pct) | Candidate bias (pct) "
                "| Absolute change | Relative change |",
                "|---|---:|---:|---:|---:|---:|",
                "| x | 3 | 1.000 | 1.250 | +0.250 | +25.0% |",
            ]
        )


# --------------------------------------------------------------------------- warehouse


@pytest.fixture(scope="module")
def errors(spark, curated_warehouse) -> RunErrors:
    return load_run_errors([BASELINE_RUN_ID, CANDIDATE_RUN_ID], spark=spark)


class TestLoadRunErrors:
    def test_one_row_per_run_day_and_period(self, errors):
        assert len(errors) == 2 * len(ACCURACY_DAYS) * 48
        assert errors.df.groupby("run_id").size().to_dict() == {
            BASELINE_RUN_ID: 21 * 48,
            CANDIDATE_RUN_ID: 21 * 48,
        }
        assert errors.df["trade_date"].min() == pd.Timestamp("2024-04-10")
        assert errors.df["trade_date"].max() == pd.Timestamp("2024-04-30")

    def test_dtypes_follow_the_contract(self, errors):
        assert errors.df.dtypes.astype(str).to_dict() == RunErrors.schema

    def test_day_part_joined_from_dim_delivery_period(self, errors):
        by_tc = errors.df.drop_duplicates("time_code").set_index("time_code")["day_part"]
        assert by_tc[1] == "Overnight"
        assert by_tc[12] == "Overnight"
        assert by_tc[13] == "Morning"
        assert by_tc[16] == "Morning"
        assert by_tc[17] == "Daytime"
        assert by_tc[20] == "Daytime"
        assert by_tc[36] == "Daytime"
        assert by_tc[40] == "Evening"

    def test_peak_hour_joined_from_occto_fact(self, errors):
        # occto rows: max_demand_hour_ending = 17 + i % 3 with i counted from 2024-04-01.
        by_day = errors.df.drop_duplicates("trade_date").set_index("trade_date")[
            "max_demand_hour_ending"
        ]
        assert by_day[pd.Timestamp("2024-04-10")] == 17.0  # i = 9
        assert by_day[pd.Timestamp("2024-04-11")] == 18.0  # i = 10
        assert by_day[pd.Timestamp("2024-04-12")] == 19.0  # i = 11
        assert errors.df["max_demand_hour_ending"].notna().all()

    def test_prices_match_the_fixture(self, errors):
        row = errors.df.set_index(["run_id", "trade_date", "time_code"])
        # synthetic_price(2024-04-10, 1): day_index 40, shape 12.0, weekday, wobble
        # (280 + 13) % 11 = 7 -> 12.7. Baseline error +1.0 on odd codes, candidate +0.5.
        assert (
            row.loc[(BASELINE_RUN_ID, pd.Timestamp("2024-04-10"), 1), "actual_price_jpy_kwh"]
            == 12.7
        )
        assert (
            row.loc[(BASELINE_RUN_ID, pd.Timestamp("2024-04-10"), 1), "forecast_price_jpy_kwh"]
            == 13.7
        )
        assert (
            row.loc[(CANDIDATE_RUN_ID, pd.Timestamp("2024-04-10"), 1), "forecast_price_jpy_kwh"]
            == 13.2
        )
        expected = [
            synthetic_price(d, tc) for d, tc in zip(errors.df["trade_date"], errors.df["time_code"])
        ]
        assert errors.df["actual_price_jpy_kwh"].tolist() == expected

    def test_default_session_path(self, spark, curated_warehouse):
        errors = load_run_errors([BASELINE_RUN_ID])
        assert len(errors) == 21 * 48
        assert set(errors.df["run_id"]) == {BASELINE_RUN_ID}

    def test_unknown_run_id_raises(self, spark, curated_warehouse):
        with pytest.raises(ValueError, match=r"No accuracy rows for run ids \['nope'\]"):
            load_run_errors([BASELINE_RUN_ID, "nope"], spark=spark)

    def test_compare_runs_on_warehouse_rows(self, errors):
        # baseline error +1.0 / -0.5 alternating -> MAE 0.75, bias +0.25;
        # candidate +0.5 / -0.25 -> MAE 0.375, bias +0.125; 21 days x 48 = 1008 points.
        tables = compare_runs(
            errors, baseline_run_id=BASELINE_RUN_ID, candidate_run_id=CANDIDATE_RUN_ID
        )
        assert_segments(tables["overall"], [("all", 1008, 0.75, 0.375, -0.375, -50.0)])
        assert_segments(
            tables["bias"],
            [
                ("all", 1008, 0.25, 0.125, -0.125, np.nan),
                ("Daytime", 420, 0.25, 0.125, -0.125, np.nan),
            ],
        )
        assert tables["month"].df["segment"].tolist() == ["2024-04"]

    def test_unmatched_windows_raise(self, spark, curated_warehouse):
        errors = load_run_errors([BASELINE_RUN_ID, UNMATCHED_RUN_ID], spark=spark)
        # baseline covers 21 days, the unmatched run 6 of them -> 15 x 48 baseline-only points
        with pytest.raises(
            ValueError,
            match=r"Runs are not matched: \{'left_only': 720, 'right_only': 0, 'both': 0\}",
        ):
            compare_runs(errors, baseline_run_id=BASELINE_RUN_ID, candidate_run_id=UNMATCHED_RUN_ID)
