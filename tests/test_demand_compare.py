"""Tests for the matched two-run demand comparison (tasks/demand/compare.py).

``compare_runs`` / ``daily_paired_comparison`` are exercised on a hand-built
``RunErrors`` frame whose errors are constant within simple regions, so every
cell is derived by hand in the comments; the synthetic ``pma_curated``
warehouse is only used for ``load_run_errors`` and the "not matched" path.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.tasks.demand.compare import (
    DAY_PARTS,
    DAY_TYPES,
    SEASONS,
    DailyPairedComparison,
    RunErrors,
    SegmentComparison,
    compare_runs,
    daily_paired_comparison,
    load_run_errors,
    paired_to_markdown,
    to_markdown,
)
from tests.conftest import (
    ACCURACY_DAYS,
    DEMAND_BASELINE_ERROR_KWH,
    DEMAND_BASELINE_RUN_ID,
    DEMAND_CANDIDATE_RUN_ID,
    DEMAND_UNMATCHED_RUN_ID,
    day_part,
    synthetic_demand,
)

# --------------------------------------------------------------------------- hand-built frame
#
# Two runs x two delivery days x 48 time codes.
#   * day 1 (2024-01-31, a weekday): actual 10,000,000 kWh everywhere
#   * day 2 (2024-02-01, tagged Holiday): actual 20,000,000 kWh everywhere
#   * baseline forecast  = actual + 2,000,000 on every row          -> abs 2.0 M, signed +2.0 M
#   * candidate forecast = actual +   500,000 for time codes 1..24  -> abs 0.5 M, signed +0.5 M
#                        = actual - 2,500,000 for time codes 25..48 -> abs 2.5 M, signed -2.5 M
#
# Day parts (dim_delivery_period): Overnight = tc 1..12, Morning = 13..16,
# Daytime = 17..36, Evening = 37..48. Both days are in Winter (Dec-Feb).

DAY_1 = pd.Timestamp("2024-01-31")
DAY_2 = pd.Timestamp("2024-02-01")
BASE = "base"
CAND = "cand"
ACTUALS = {DAY_1: 10_000_000.0, DAY_2: 20_000_000.0}
DAY_TYPE = {DAY_1: "Weekday", DAY_2: "Holiday"}


def candidate_error(time_code: int) -> float:
    return 500_000.0 if time_code <= 24 else -2_500_000.0


def build_run_errors() -> RunErrors:
    rows = []
    for run_id, error_of in ((BASE, lambda tc: 2_000_000.0), (CAND, candidate_error)):
        for day in (DAY_1, DAY_2):
            for tc in range(1, 49):
                rows.append(
                    {
                        "run_id": run_id,
                        "trade_date": day,
                        "time_code": tc,
                        "day_part": day_part(tc),
                        "day_type": DAY_TYPE[day],
                        "actual_demand_kwh": ACTUALS[day],
                        "forecast_demand_kwh": ACTUALS[day] + error_of(tc),
                    }
                )
    return RunErrors.from_df(pd.DataFrame(rows).astype({"time_code": "int64"}))


def assert_segments(table: SegmentComparison, rows: list[tuple]) -> None:
    """Compare a SegmentComparison against (segment, n, base, cand, abs, rel) rows."""
    expected = pd.DataFrame(
        rows, columns=["segment", "n", "baseline", "candidate", "abs_change", "rel_change_pct"]
    ).astype(
        {
            "n": "int64",
            "baseline": "float64",
            "candidate": "float64",
            "abs_change": "float64",
            "rel_change_pct": "float64",
        }
    )
    pd.testing.assert_frame_equal(table.df.reset_index(drop=True), expected, check_exact=False)


@pytest.fixture(scope="module")
def tables() -> dict[str, SegmentComparison]:
    return compare_runs(build_run_errors(), baseline_run_id=BASE, candidate_run_id=CAND)


class TestContracts:
    def test_run_errors_grain_and_non_null(self):
        assert RunErrors.keys == ["run_id", "trade_date", "time_code"]
        assert set(RunErrors.non_null_cols) == {
            "day_part",
            "day_type",
            "actual_demand_kwh",
            "forecast_demand_kwh",
        }
        assert DAY_PARTS == ("Overnight", "Morning", "Daytime", "Evening")
        assert DAY_TYPES == ("Weekday", "Weekend", "Holiday")
        assert SEASONS == (
            "Winter (Dec–Feb)",
            "Spring (Mar–May)",
            "Summer (Jun–Aug)",
            "Autumn (Sep–Nov)",
        )

    def test_segment_comparison_accepts_nan_relative_change(self):
        table = SegmentComparison.from_df(
            pd.DataFrame(
                {
                    "segment": ["all"],
                    "n": np.array([1], dtype="int64"),
                    "baseline": [1.0],
                    "candidate": [2.0],
                    "abs_change": [1.0],
                    "rel_change_pct": [np.nan],
                }
            )
        )
        assert len(table) == 1


class TestCompareRuns:
    def test_returns_the_sections(self, tables):
        assert list(tables) == [
            "overall",
            "mape",
            "bias",
            "day_part",
            "day_type",
            "month",
            "season",
            "demand_band",
            "demand_days",
        ]

    def test_overall_mae(self, tables):
        # candidate: (0.5 M x 24 + 2.5 M x 24) / 48 = 1.5 M per day.
        assert_segments(
            tables["overall"], [("all", 96, 2_000_000.0, 1_500_000.0, -500_000.0, -25.0)]
        )

    def test_mape_is_the_mean_absolute_percentage_error(self, tables):
        # baseline: 20 % on day 1, 10 % on day 2 -> 15 %.
        # candidate day 1: (5 % x 24 + 25 % x 24) / 48 = 15 %; day 2: 7.5 % -> 11.25 %.
        assert_segments(tables["mape"], [("all", 96, 15.0, 11.25, -3.75, -25.0)])

    def test_bias_is_the_mean_signed_error_overall_and_daytime(self, tables):
        # candidate daytime (tc 17..36): 8 x +0.5 M + 12 x -2.5 M = -26 M / 20 = -1.3 M.
        assert_segments(
            tables["bias"],
            [
                ("all", 96, 2_000_000.0, -1_000_000.0, -3_000_000.0, np.nan),
                ("Daytime", 40, 2_000_000.0, -1_300_000.0, -3_300_000.0, np.nan),
            ],
        )

    def test_day_part_in_canonical_order(self, tables):
        # Overnight tc 1..12 all +0.5 M; Morning 13..16 +0.5 M; Daytime 17..36: 8 x 0.5 M +
        # 12 x 2.5 M = 34 M / 20 = 1.7 M; Evening 37..48 all 2.5 M. Two days each.
        assert_segments(
            tables["day_part"],
            [
                ("Overnight", 24, 2_000_000.0, 500_000.0, -1_500_000.0, -75.0),
                ("Morning", 8, 2_000_000.0, 500_000.0, -1_500_000.0, -75.0),
                ("Daytime", 40, 2_000_000.0, 1_700_000.0, -300_000.0, -15.0),
                ("Evening", 24, 2_000_000.0, 2_500_000.0, 500_000.0, 25.0),
            ],
        )

    def test_day_type_only_lists_present_types_in_canonical_order(self, tables):
        assert_segments(
            tables["day_type"],
            [
                ("Weekday", 48, 2_000_000.0, 1_500_000.0, -500_000.0, -25.0),
                ("Holiday", 48, 2_000_000.0, 1_500_000.0, -500_000.0, -25.0),
            ],
        )

    def test_month_and_season(self, tables):
        assert_segments(
            tables["month"],
            [
                ("2024-01", 48, 2_000_000.0, 1_500_000.0, -500_000.0, -25.0),
                ("2024-02", 48, 2_000_000.0, 1_500_000.0, -500_000.0, -25.0),
            ],
        )
        assert_segments(
            tables["season"],
            [("Winter (Dec–Feb)", 96, 2_000_000.0, 1_500_000.0, -500_000.0, -25.0)],
        )

    def test_demand_band_is_2000_mwh_wide_on_the_actual(self, tables):
        assert_segments(
            tables["demand_band"],
            [
                ("10,000–12,000 MWh", 48, 2_000_000.0, 1_500_000.0, -500_000.0, -25.0),
                ("20,000–22,000 MWh", 48, 2_000_000.0, 1_500_000.0, -500_000.0, -25.0),
            ],
        )

    def test_demand_days_split_on_the_daily_mean_quantile(self, tables):
        # quantile 0.9 of the daily means [10 M, 20 M] = 19 M -> day 2 only.
        assert_segments(
            tables["demand_days"],
            [
                (
                    "top 10% demand days (daily mean >= 19,000 MWh)",
                    48,
                    2_000_000.0,
                    1_500_000.0,
                    -500_000.0,
                    -25.0,
                ),
                ("other 90% of days", 48, 2_000_000.0, 1_500_000.0, -500_000.0, -25.0),
            ],
        )

    def test_custom_band_and_quantile(self):
        tables = compare_runs(
            build_run_errors(),
            baseline_run_id=BASE,
            candidate_run_id=CAND,
            band_mwh=10_000,
            high_demand_quantile=0.5,
        )
        assert tables["demand_band"].df["segment"].tolist() == [
            "10,000–20,000 MWh",
            "20,000–30,000 MWh",
        ]
        assert tables["demand_days"].df["segment"].tolist() == [
            "top 50% demand days (daily mean >= 15,000 MWh)",
            "other 50% of days",
        ]

    def test_ignores_rows_of_other_runs(self):
        errors = build_run_errors()
        extra = errors.df[errors.df["run_id"] == BASE].assign(
            run_id="other", forecast_demand_kwh=lambda d: d["actual_demand_kwh"] + 9e9
        )
        with_extra = RunErrors.from_df(pd.concat([errors.df, extra], ignore_index=True))
        tables = compare_runs(with_extra, baseline_run_id=BASE, candidate_run_id=CAND)
        assert_segments(
            tables["overall"], [("all", 96, 2_000_000.0, 1_500_000.0, -500_000.0, -25.0)]
        )

    def test_missing_run_raises(self):
        with pytest.raises(ValueError, match="Both runs must be present"):
            compare_runs(build_run_errors(), baseline_run_id=BASE, candidate_run_id="nope")

    def test_unmatched_points_raise(self):
        errors = build_run_errors()
        dropped = errors.df[~((errors.df["run_id"] == CAND) & (errors.df["time_code"] == 48))]
        with pytest.raises(ValueError, match=r"Runs are not matched: \{'left_only': 2\}"):
            compare_runs(RunErrors.from_df(dropped), baseline_run_id=BASE, candidate_run_id=CAND)


class TestDailyPairedComparison:
    def test_hand_derived_statistics(self):
        # Daily MAE: baseline 2.0 M both days, candidate 1.5 M both days -> diff -0.5 M each.
        paired = daily_paired_comparison(
            build_run_errors(), baseline_run_id=BASE, candidate_run_id=CAND, top_days=1
        )
        assert isinstance(paired, DailyPairedComparison)
        assert paired.n_days == 2
        assert paired.n_candidate_lower == 2
        assert paired.share_candidate_lower_pct == 100.0
        assert paired.mean_diff == -500_000.0
        assert paired.median_diff == -500_000.0
        # Every resample averages identical values.
        assert (paired.ci_low, paired.ci_high) == (-500_000.0, -500_000.0)
        # Total abs-error reduction 2 x 48 x 0.5 M = 48 M; the best day carries 24 M.
        assert paired.top_days == 1
        assert paired.top_days_share_pct == 50.0
        assert (paired.resamples, paired.seed) == (10_000, 0)

    def test_bootstrap_is_reproducible_and_brackets_the_mean(self):
        errors = build_run_errors()
        # Make day 2 a worse day for the candidate so the daily differences differ.
        df = errors.df.copy()
        cand_day2 = (df["run_id"] == CAND) & (df["trade_date"] == DAY_2)
        df.loc[cand_day2, "forecast_demand_kwh"] = df.loc[cand_day2, "actual_demand_kwh"] + 3e6
        varied = RunErrors.from_df(df)
        a = daily_paired_comparison(
            varied, baseline_run_id=BASE, candidate_run_id=CAND, seed=1, resamples=500
        )
        b = daily_paired_comparison(
            varied, baseline_run_id=BASE, candidate_run_id=CAND, seed=1, resamples=500
        )
        # diffs: day 1 -0.5 M, day 2 +1.0 M -> mean +0.25 M, one of two days lower.
        assert a.mean_diff == 250_000.0
        assert a.n_candidate_lower == 1 and a.share_candidate_lower_pct == 50.0
        assert (a.resamples, a.seed) == (500, 1)
        assert (a.ci_low, a.ci_high) == (b.ci_low, b.ci_high)
        # Resample means of two values lie between them and bracket the mean.
        assert a.ci_low <= a.mean_diff <= a.ci_high
        assert -500_000.0 <= a.ci_low and a.ci_high <= 1_000_000.0

    def test_top_days_share_is_capped_at_the_number_of_days(self):
        paired = daily_paired_comparison(
            build_run_errors(), baseline_run_id=BASE, candidate_run_id=CAND, top_days=10
        )
        assert paired.top_days == 2
        assert paired.top_days_share_pct == 100.0

    def test_unmatched_points_raise(self):
        errors = build_run_errors()
        dropped = errors.df[~((errors.df["run_id"] == BASE) & (errors.df["trade_date"] == DAY_2))]
        with pytest.raises(ValueError, match="Runs are not matched"):
            daily_paired_comparison(
                RunErrors.from_df(dropped), baseline_run_id=BASE, candidate_run_id=CAND
            )


class TestMarkdown:
    def test_segment_table_renders_whole_kwh_and_signed_relative_change(self, tables):
        text = to_markdown(tables["day_part"], metric="MAE")
        lines = text.splitlines()
        assert lines[0] == (
            "| Segment | n | Baseline MAE (kWh) | Candidate MAE (kWh) | Absolute change | "
            "Relative change |"
        )
        assert lines[1] == "|---|---:|---:|---:|---:|---:|"
        assert lines[2] == "| Overnight | 24 | 2,000,000 | 500,000 | −1,500,000 | −75.0 % |"
        assert lines[5] == "| Evening | 24 | 2,000,000 | 2,500,000 | +500,000 | +25.0 % |"

    def test_percent_metric_and_nan_relative_change(self, tables):
        assert (
            to_markdown(tables["mape"], metric="MAPE", unit="%", decimals=2).splitlines()[2]
            == "| all | 96 | 15.00 | 11.25 | −3.75 | −25.0 % |"
        )
        bias = to_markdown(tables["bias"], metric="bias").splitlines()
        assert bias[2] == "| all | 96 | +2,000,000 | −1,000,000 | −3,000,000 | — |"

    def test_paired_summary_lines(self):
        paired = daily_paired_comparison(
            build_run_errors(), baseline_run_id=BASE, candidate_run_id=CAND, top_days=1
        )
        text = paired_to_markdown(paired)
        assert text.splitlines() == [
            "- candidate lower on 100.0 % of days (2 of 2)",
            "- mean daily-MAE difference −500,000 kWh; 95 % bootstrap CI over days "
            "[−500,000, −500,000] (10,000 resamples, seed 0)",
            "- median daily-MAE difference −500,000 kWh",
            "- the 1 most-improved day(s) account for 50 % of the total absolute-error reduction",
        ]


class TestLoadRunErrors:
    @pytest.fixture(scope="class")
    def errors(self, spark, curated_warehouse) -> RunErrors:
        return load_run_errors([DEMAND_BASELINE_RUN_ID, DEMAND_CANDIDATE_RUN_ID], spark=spark)

    def test_one_row_per_run_day_and_period(self, errors):
        assert len(errors) == 2 * len(ACCURACY_DAYS) * 48
        assert errors.df.dtypes.astype(str).to_dict() == RunErrors.schema
        assert errors.df["trade_date"].min() == pd.Timestamp("2024-04-10")
        assert errors.df["trade_date"].max() == pd.Timestamp("2024-04-30")

    def test_day_part_and_day_type_joined_from_the_dimensions(self, errors):
        by_tc = errors.df.drop_duplicates("time_code").set_index("time_code")["day_part"]
        assert (by_tc[1], by_tc[13], by_tc[17], by_tc[40]) == (
            "Overnight",
            "Morning",
            "Daytime",
            "Evening",
        )
        by_day = errors.df.drop_duplicates("trade_date").set_index("trade_date")["day_type"]
        assert by_day[pd.Timestamp("2024-04-10")] == "Weekday"  # Wednesday
        assert by_day[pd.Timestamp("2024-04-13")] == "Weekend"  # Saturday
        assert by_day[pd.Timestamp("2024-04-29")] == "Holiday"  # 昭和の日 (a Monday)
        assert by_day.value_counts().to_dict() == {"Weekday": 14, "Weekend": 6, "Holiday": 1}

    def test_values_match_the_fixture(self, errors):
        row = errors.df.set_index(["run_id", "trade_date", "time_code"])
        actual = float(synthetic_demand(pd.Timestamp("2024-04-10"), 1))
        assert (
            row.loc[(DEMAND_BASELINE_RUN_ID, pd.Timestamp("2024-04-10"), 1), "actual_demand_kwh"]
            == actual
        )
        assert (
            row.loc[(DEMAND_BASELINE_RUN_ID, pd.Timestamp("2024-04-10"), 1), "forecast_demand_kwh"]
            == actual + DEMAND_BASELINE_ERROR_KWH
        )
        assert (
            row.loc[(DEMAND_CANDIDATE_RUN_ID, pd.Timestamp("2024-04-10"), 2), "forecast_demand_kwh"]
            == float(synthetic_demand(pd.Timestamp("2024-04-10"), 2))
            - 0.25 * DEMAND_BASELINE_ERROR_KWH
        )

    def test_compare_runs_on_warehouse_rows(self, errors):
        # baseline +100,000 / -50,000 alternating -> MAE 75,000, bias +25,000;
        # candidate half of that; 21 days x 48 = 1,008 points.
        tables = compare_runs(
            errors, baseline_run_id=DEMAND_BASELINE_RUN_ID, candidate_run_id=DEMAND_CANDIDATE_RUN_ID
        )
        assert_segments(tables["overall"], [("all", 1008, 75_000.0, 37_500.0, -37_500.0, -50.0)])
        assert tables["bias"].df.set_index("segment").loc[
            "all", ["baseline", "candidate"]
        ].tolist() == [
            25_000.0,
            12_500.0,
        ]
        assert tables["day_type"].df["n"].tolist() == [14 * 48, 6 * 48, 48]
        assert tables["month"].df["segment"].tolist() == ["2024-04"]
        paired = daily_paired_comparison(
            errors, baseline_run_id=DEMAND_BASELINE_RUN_ID, candidate_run_id=DEMAND_CANDIDATE_RUN_ID
        )
        assert paired.n_days == 21 and paired.n_candidate_lower == 21

    def test_default_session_path(self, spark, curated_warehouse):
        assert len(load_run_errors([DEMAND_BASELINE_RUN_ID])) == 21 * 48

    def test_unknown_run_id_raises(self, spark, curated_warehouse):
        with pytest.raises(ValueError, match=r"No accuracy rows for run ids \['nope'\]"):
            load_run_errors([DEMAND_BASELINE_RUN_ID, "nope"], spark=spark)

    def test_unmatched_windows_raise(self, spark, curated_warehouse):
        errors = load_run_errors([DEMAND_BASELINE_RUN_ID, DEMAND_UNMATCHED_RUN_ID], spark=spark)
        with pytest.raises(ValueError, match=r"Runs are not matched: \{'left_only': 720\}"):
            compare_runs(
                errors,
                baseline_run_id=DEMAND_BASELINE_RUN_ID,
                candidate_run_id=DEMAND_UNMATCHED_RUN_ID,
            )
