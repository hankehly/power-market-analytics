"""Tests for the task-agnostic matched comparison (forecasting/compare.py).

Every helper here is exercised on hand-built frames whose errors are constant
within simple regions, so each expected cell is derived by hand. The task
coupling is only the ``TaskSpec``'s actual / forecast column names, so the
helpers that read a value column are checked against both tasks.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.forecasting.compare import (
    DAY_PARTS,
    DailyPairedComparison,
    SegmentComparison,
    assert_matched,
    comparison,
    daily_paired_comparison,
    fmt,
    matched_rows,
    paired_to_markdown,
    run_errors_from_pandas,
    segment_bias,
    segment_mae,
    segment_mape,
    segment_overall,
    to_markdown,
    uncommon_days,
)
from power_market_analytics.tasks.demand import TASK as DEMAND_TASK
from power_market_analytics.tasks.spot_price import TASK as SPOT_TASK

BASE = "base"
CAND = "cand"
DAY_1 = pd.Timestamp("2024-01-31")
DAY_2 = pd.Timestamp("2024-02-01")


class MinimalRunErrors(DomainFrame):
    """The smallest frame the shared helpers accept, in the demand task's columns."""

    schema = {
        "run_id": "object",
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "day_part": "object",
        "actual_demand_kwh": "float64",
        "forecast_demand_kwh": "float64",
    }
    keys = ["run_id", "trade_date", "time_code"]
    non_null_cols = ["day_part", "actual_demand_kwh", "forecast_demand_kwh"]


class SpotRunErrors(DomainFrame):
    """The same, in the spot task's columns."""

    schema = {
        "run_id": "object",
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "day_part": "object",
        "actual_price_jpy_kwh": "float64",
        "forecast_price_jpy_kwh": "float64",
    }
    keys = ["run_id", "trade_date", "time_code"]
    non_null_cols = ["day_part", "actual_price_jpy_kwh", "forecast_price_jpy_kwh"]


def build(frame_cls, actual_col: str, forecast_col: str, *, time_codes=(1, 48)):
    """Two runs x two days x ``time_codes``: baseline off by +2, candidate by +1."""
    rows = []
    for run_id, error in ((BASE, 2.0), (CAND, 1.0)):
        for day in (DAY_1, DAY_2):
            for tc in time_codes:
                rows.append(
                    {
                        "run_id": run_id,
                        "trade_date": day,
                        "time_code": tc,
                        "day_part": "Daytime" if tc > 24 else "Overnight",
                        actual_col: 10.0,
                        forecast_col: 10.0 + error,
                    }
                )
    return frame_cls.from_df(pd.DataFrame(rows).astype({"time_code": "int64"}))


def demand_errors(**kwargs):
    return build(MinimalRunErrors, "actual_demand_kwh", "forecast_demand_kwh", **kwargs)


def spot_errors(**kwargs):
    return build(SpotRunErrors, "actual_price_jpy_kwh", "forecast_price_jpy_kwh", **kwargs)


def segment_frame(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(
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


# --------------------------------------------------------------------------- frames


class TestSegmentComparison:
    def test_accepts_nan_relative_change(self):
        table = SegmentComparison.from_df(segment_frame([("all", 4, 2.0, 1.0, -1.0, np.nan)]))
        assert len(table) == 1

    def test_rejects_null_baseline(self):
        with pytest.raises(ValueError, match="baseline"):
            SegmentComparison.from_df(segment_frame([("all", 4, np.nan, 1.0, -1.0, np.nan)]))

    def test_day_parts_are_the_canonical_order(self):
        assert DAY_PARTS == ("Overnight", "Morning", "Daytime", "Evening")


# --------------------------------------------------------------------------- matched rows


class TestMatchedRows:
    @pytest.mark.parametrize(
        "errors_of, task",
        [(demand_errors, DEMAND_TASK), (spot_errors, SPOT_TASK)],
        ids=["demand", "spot_price"],
    )
    def test_reads_the_value_columns_off_the_task_spec(self, errors_of, task):
        df = matched_rows(errors_of(), task=task, baseline_run_id=BASE, candidate_run_id=CAND)
        assert set(df["role"]) == {"baseline", "candidate"}
        # baseline forecast is actual + 2, candidate actual + 1.
        assert df.loc[df["role"] == "baseline", "error"].unique().tolist() == [2.0]
        assert df.loc[df["role"] == "candidate", "abs_error"].unique().tolist() == [1.0]

    def test_ignores_rows_of_other_runs(self):
        df = demand_errors().df
        third = df[df["run_id"] == BASE].assign(run_id="other")
        out = matched_rows(
            MinimalRunErrors.from_df(pd.concat([df, third], ignore_index=True)),
            task=DEMAND_TASK,
            baseline_run_id=BASE,
            candidate_run_id=CAND,
        )
        assert set(out["run_id"]) == {BASE, CAND}

    def test_common_days_drops_the_days_only_one_run_scored(self):
        df = demand_errors().df
        trimmed = MinimalRunErrors.from_df(
            df[~((df["run_id"] == CAND) & (df["trade_date"] == DAY_2))]
        )
        with pytest.raises(ValueError, match="Runs are not matched"):
            matched_rows(trimmed, task=DEMAND_TASK, baseline_run_id=BASE, candidate_run_id=CAND)
        out = matched_rows(
            trimmed,
            task=DEMAND_TASK,
            baseline_run_id=BASE,
            candidate_run_id=CAND,
            common_days=True,
        )
        assert set(out["trade_date"]) == {DAY_1}
        assert out["role"].value_counts().to_dict() == {"baseline": 2, "candidate": 2}

    def test_common_days_still_requires_every_period_of_a_common_day(self):
        df = demand_errors().df
        # One period of DAY_2 missing from the candidate: the day is common, the point is not.
        first = df[(df["run_id"] == CAND) & (df["trade_date"] == DAY_2)].index[0]
        trimmed = MinimalRunErrors.from_df(df.drop(index=first))
        with pytest.raises(ValueError, match=r"\{'left_only': 1\}"):
            matched_rows(
                trimmed,
                task=DEMAND_TASK,
                baseline_run_id=BASE,
                candidate_run_id=CAND,
                common_days=True,
            )


class TestUncommonDays:
    def test_lists_the_days_only_one_run_scored(self):
        df = demand_errors().df
        no_cand_day_2 = df[~((df["run_id"] == CAND) & (df["trade_date"] == DAY_2))]
        assert uncommon_days(no_cand_day_2, BASE, CAND) == {"baseline": [DAY_2], "candidate": []}
        no_base_day_1 = df[~((df["run_id"] == BASE) & (df["trade_date"] == DAY_1))]
        assert uncommon_days(no_base_day_1, BASE, CAND) == {"baseline": [], "candidate": [DAY_1]}

    def test_empty_when_both_scored_the_same_days(self):
        assert uncommon_days(demand_errors().df, BASE, CAND) == {"baseline": [], "candidate": []}


class TestAssertMatched:
    def test_passes_on_identical_points(self):
        assert assert_matched(demand_errors().df, BASE, CAND) is None

    def test_reports_only_the_non_zero_counts(self):
        # The categorical merge indicator would otherwise report "'both': 0" too.
        df = demand_errors().df
        trimmed = df[~((df["run_id"] == CAND) & (df["trade_date"] == DAY_2))]
        with pytest.raises(ValueError, match=r"Runs are not matched: \{'left_only': 2\} points"):
            assert_matched(trimmed, BASE, CAND)

    def test_reports_candidate_only_points(self):
        df = demand_errors().df
        trimmed = df[~((df["run_id"] == BASE) & (df["trade_date"] == DAY_1))]
        with pytest.raises(ValueError, match=r"\{'right_only': 2\}"):
            assert_matched(trimmed, BASE, CAND)

    def test_missing_run_raises(self):
        with pytest.raises(ValueError, match="Both runs must be present"):
            assert_matched(demand_errors().df, BASE, "nope")


# --------------------------------------------------------------------------- segments


class TestSegments:
    def test_overall_is_the_mean_absolute_error(self):
        df = matched_rows(
            demand_errors(), task=DEMAND_TASK, baseline_run_id=BASE, candidate_run_id=CAND
        )
        table = segment_overall(df)
        assert table.df["segment"].tolist() == ["all"]
        assert table.df["baseline"].tolist() == [2.0]
        assert table.df["candidate"].tolist() == [1.0]
        assert table.df["rel_change_pct"].tolist() == [-50.0]

    def test_mae_by_segment_follows_the_given_order(self):
        df = matched_rows(
            demand_errors(), task=DEMAND_TASK, baseline_run_id=BASE, candidate_run_id=CAND
        )
        table = segment_mae(df, df["day_part"], order=DAY_PARTS)
        # Only the two parts present, in canonical order (Overnight before Daytime).
        assert table.df["segment"].tolist() == ["Overnight", "Daytime"]

    def test_mae_without_an_order_sorts_lexically(self):
        df = matched_rows(
            demand_errors(), task=DEMAND_TASK, baseline_run_id=BASE, candidate_run_id=CAND
        )
        table = segment_mae(df, df["day_part"])
        assert table.df["segment"].tolist() == ["Daytime", "Overnight"]

    def test_bias_is_the_signed_error_overall_and_daytime(self):
        df = matched_rows(
            demand_errors(), task=DEMAND_TASK, baseline_run_id=BASE, candidate_run_id=CAND
        )
        table = segment_bias(df)
        assert table.df["segment"].tolist() == ["all", "Daytime"]
        assert table.df["baseline"].tolist() == [2.0, 2.0]
        # Bias has no meaningful relative change.
        assert table.df["rel_change_pct"].isna().all()

    def test_mape_is_over_points_with_a_positive_actual(self):
        df = matched_rows(
            demand_errors(), task=DEMAND_TASK, baseline_run_id=BASE, candidate_run_id=CAND
        )
        table = segment_mape(df, actual_col=DEMAND_TASK.actual_col)
        # |error| / actual = 2/10 and 1/10.
        assert table.df["baseline"].tolist() == [20.0]
        assert table.df["candidate"].tolist() == [10.0]

    def test_mape_ignores_zero_actuals(self):
        df = matched_rows(
            demand_errors(), task=DEMAND_TASK, baseline_run_id=BASE, candidate_run_id=CAND
        )
        df.loc[df["trade_date"] == DAY_1, "actual_demand_kwh"] = 0.0
        table = segment_mape(df, actual_col=DEMAND_TASK.actual_col)
        assert table.df["n"].tolist() == [2]


class TestComparison:
    def test_relative_change_is_the_percentage_of_the_baseline(self):
        table = comparison(
            n=pd.Series([10], index=["all"]),
            baseline=pd.Series([2.0], index=["all"]),
            candidate=pd.Series([1.5], index=["all"]),
            relative=True,
        )
        assert table.df["abs_change"].tolist() == [-0.5]
        assert table.df["rel_change_pct"].tolist() == [-25.0]

    def test_non_relative_leaves_the_percentage_null(self):
        table = comparison(
            n=pd.Series([10], index=["all"]),
            baseline=pd.Series([2.0], index=["all"]),
            candidate=pd.Series([1.5], index=["all"]),
            relative=False,
        )
        assert table.df["rel_change_pct"].isna().all()


# --------------------------------------------------------------------------- daily paired


class TestDailyPairedComparison:
    def test_hand_derived_statistics(self):
        paired = daily_paired_comparison(
            demand_errors(),
            task=DEMAND_TASK,
            baseline_run_id=BASE,
            candidate_run_id=CAND,
            top_days=1,
        )
        assert isinstance(paired, DailyPairedComparison)
        # Daily MAE 2.0 baseline, 1.0 candidate on both days -> diff -1.0 each.
        assert paired.n_days == 2
        assert paired.n_candidate_lower == 2
        assert paired.share_candidate_lower_pct == 100.0
        assert paired.mean_diff == -1.0
        assert paired.median_diff == -1.0
        assert paired.ci_low == -1.0
        assert paired.ci_high == -1.0
        # One of two equally improved days carries half the reduction.
        assert paired.top_days == 1
        assert paired.top_days_share_pct == 50.0

    def test_top_days_is_capped_at_the_number_of_days(self):
        paired = daily_paired_comparison(
            demand_errors(),
            task=DEMAND_TASK,
            baseline_run_id=BASE,
            candidate_run_id=CAND,
            top_days=99,
        )
        assert paired.top_days == 2

    def test_share_is_nan_without_a_net_reduction(self):
        # Candidate equals baseline: no reduction to attribute.
        df = demand_errors().df
        df.loc[df["run_id"] == CAND, "forecast_demand_kwh"] = 12.0
        paired = daily_paired_comparison(
            MinimalRunErrors.from_df(df),
            task=DEMAND_TASK,
            baseline_run_id=BASE,
            candidate_run_id=CAND,
        )
        assert np.isnan(paired.top_days_share_pct)

    def test_is_reproducible_for_a_seed(self):
        kwargs = dict(task=DEMAND_TASK, baseline_run_id=BASE, candidate_run_id=CAND, resamples=50)
        first = daily_paired_comparison(demand_errors(), seed=7, **kwargs)
        second = daily_paired_comparison(demand_errors(), seed=7, **kwargs)
        assert first == second

    def test_unmatched_points_raise(self):
        df = demand_errors().df
        dropped = df[~((df["run_id"] == BASE) & (df["trade_date"] == DAY_2))]
        with pytest.raises(ValueError, match="Runs are not matched"):
            daily_paired_comparison(
                MinimalRunErrors.from_df(dropped),
                task=DEMAND_TASK,
                baseline_run_id=BASE,
                candidate_run_id=CAND,
            )


# --------------------------------------------------------------------------- loading


class TestRunErrorsFromPandas:
    def test_missing_run_raises(self):
        pdf = demand_errors().df
        with pytest.raises(ValueError, match=r"No accuracy rows for run ids \['nope'\]"):
            run_errors_from_pandas(
                pdf,
                run_ids=[BASE, "nope"],
                frame_cls=MinimalRunErrors,
                float_cols=["actual_demand_kwh", "forecast_demand_kwh"],
            )

    def test_casts_the_declared_columns(self):
        pdf = demand_errors().df.astype({"actual_demand_kwh": "int64", "time_code": "int32"})
        errors = run_errors_from_pandas(
            pdf,
            run_ids=[BASE, CAND],
            frame_cls=MinimalRunErrors,
            float_cols=["actual_demand_kwh", "forecast_demand_kwh"],
        )
        assert errors.df["actual_demand_kwh"].dtype == "float64"
        assert errors.df["time_code"].dtype == "int64"


# --------------------------------------------------------------------------- rendering


class TestFmt:
    def test_uses_a_typographic_minus_and_thousands_separators(self):
        assert fmt(-1234.5, 1, signed=False) == "−1,234.5"

    def test_signed_adds_a_plus(self):
        assert fmt(1234.5, 1, signed=True) == "+1,234.5"


class TestToMarkdown:
    def test_renders_github_table(self):
        table = SegmentComparison.from_df(
            segment_frame(
                [
                    ("all", 1234, 2.0, 1.5, -0.5, -25.0),
                    ("Daytime", 40, 2.0, 1.3, -0.7, -35.0),
                ]
            )
        )
        assert to_markdown(table, metric="MAE", unit="JPY/kWh", decimals=3) == "\n".join(
            [
                "| Segment | n | Baseline MAE (JPY/kWh) | Candidate MAE (JPY/kWh) "
                "| Absolute change | Relative change |",
                "|---|---:|---:|---:|---:|---:|",
                "| all | 1,234 | 2.000 | 1.500 | −0.500 | −25.0 % |",
                "| Daytime | 40 | 2.000 | 1.300 | −0.700 | −35.0 % |",
            ]
        )

    def test_a_table_without_relative_changes_prints_signed_values(self):
        table = SegmentComparison.from_df(
            segment_frame([("all", 40, 2.0, -1.3, -3.3, np.nan)]),
        )
        assert to_markdown(table, metric="bias", unit="kWh", decimals=0).endswith(
            "| all | 40 | +2 | −1 | −3 | — |"
        )


class TestPairedToMarkdown:
    def test_renders_the_bullet_lines(self):
        paired = DailyPairedComparison(
            n_days=10,
            n_candidate_lower=7,
            share_candidate_lower_pct=70.0,
            mean_diff=-1500.0,
            median_diff=-1200.0,
            ci_low=-2000.0,
            ci_high=-1000.0,
            resamples=10_000,
            seed=0,
            top_days=3,
            top_days_share_pct=42.0,
        )
        text = paired_to_markdown(paired, unit="kWh")
        assert "candidate lower on 70.0 % of days (7 of 10)" in text
        assert "mean daily-MAE difference −1,500 kWh" in text
        assert "95 % bootstrap CI over days [−2,000, −1,000]" in text
        assert "the 3 most-improved day(s) account for 42 % of the total" in text

    def test_share_is_a_dash_when_undefined(self):
        paired = DailyPairedComparison(
            n_days=1,
            n_candidate_lower=0,
            share_candidate_lower_pct=0.0,
            mean_diff=0.0,
            median_diff=0.0,
            ci_low=0.0,
            ci_high=0.0,
            resamples=10,
            seed=0,
            top_days=1,
            top_days_share_pct=float("nan"),
        )
        assert "account for — of the total" in paired_to_markdown(paired)
