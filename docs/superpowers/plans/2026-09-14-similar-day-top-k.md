# Paper-style similar days Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the demand task's single year-ago similar day with the Park, Song and Kwon (2020) blended pool: ranks 1–3 and an inverse-distance weighted mean, a same-holiday reference on special days, and the old `similar_day_demand_kwh` retired.

**Architecture:** The walk-forward job (`scripts/fit_similar_day.py` → `tasks/demand/similar_day.py` selector → `tasks/demand/similar_day_feature.py` records) keeps its shape: one fit per weekly cutoff, one row per scoring run × area × day × period in `pma_ml.similar_day`, passed through `stg_ml__similar_day` and the `ftr_period_similar_day` mart to Feast. The selector's window becomes a two-window pool with per-pair eligibility rules; a ranking frame feeds four feature columns; special days with an in-window same-name match take that day instead.

**Tech Stack:** pandas/numpy DomainFrames, scipy (unchanged fit), PySpark write-back, dbt 1.11 on Spark (enforced contracts, dbt_utils tests, unit tests), Feast views generated from the manifest, MLflow, pytest with a 100 % coverage gate.

**Spec:** `docs/superpowers/specs/2026-09-14-similar-day-top-k-design.md` (read it in full before any task).

## Global Constraints

- Pool: recent lags 2 … 31, year-ago lags 335 … 394 (inclusive), one ranking. Calendar part = the lag in days. Ties go to the smaller lag.
- A candidate counts only if it is not a `dim_date.is_holiday` day and the latest `available_at` of its 24 hourly loads is ≤ the target's issue time (`target + TASK.issue_offset` = 09:30 on D − 1).
- Training pairs: the same candidate rules, and the target is not a special day.
- Top k = 3: `similar_day_rank1_demand_kwh`, `similar_day_rank2_demand_kwh`, `similar_day_rank3_demand_kwh`, `wavg_similar_day_top3_demand_kwh`.
- Weights `w_r = (1/d_r) / Σ_s (1/d_s)`; any zero distance → the zero-distance days share the weight equally, the rest 0; missing ranks are skipped; sums are taken one rank at a time in rank order.
- Special day (`dim_date.is_holiday`): if last year's day with the same `holiday_name_ja` lies 335 … 394 days back, that day R is the reference (`similar_day_method = 'same_holiday'`): rank 1 = mean = R's hourly load / 2; ranks 2–3, distances, `similar_day_n_candidates`, `similar_day_fit_cutoff` null; `available_at` = R's load availability. Otherwise the special day is ranked like any day (`'similarity'`).
- Feature expressions (byte-exact):
  - `SIMILAR_DAY(power_usage_demand_kwh, gap=(2d, 335d), window=(30, 60), rank=1, holidays=last_year) / 2` (and `rank=2`, `rank=3`)
  - `SIMILAR_DAY_MEAN(power_usage_demand_kwh, gap=(2d, 335d), window=(30, 60), k=3, weight=inverse_distance, holidays=last_year) / 2`
- Retired row (byte-exact expression): `similar_day_demand_kwh,"SIMILAR_DAY(power_usage_demand_kwh, gap=334d, window=61) / 2",false,"…retired on 2026-09-14 for the paper-style pool."`
- Retrieval check compares the pick with D − 7 (`lag_7_rank`, `lag_7_load_difference`, metrics `similar_day_load_difference_lag_7`, `similar_day_share_better_than_lag_7`).
- No pool flags on the script; `--window-half-width-days` is removed.
- Generated files (`power_market_analytics/features/views.py`, `dbt/models/curated/fct_feature_value.sql`, `dbt/models/curated/dim_feature.sql`) are never hand-edited: run `just feature-views` after any mart YAML change (needs `cd dbt && uv run dbt deps` once in this worktree).
- Every run in this worktree: `uv run …` from the worktree root (it has its own `.venv`); dbt host-side with `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt …`. Never `just dbt` / `just python` (those exec in the main checkout). No test may touch the real warehouse.
- Repo rules that apply: NumPy docstrings; `merge` with `how=`, `on=`, `validate=`; DomainFrame wrappers at boundaries; plain-language docs; 100 % coverage; every dbt model enforced contract + key uniqueness test; generic test args under `arguments:`; Spark SQL decimal literals — use `cast(1 as double)`; prefer `=` over `<>` in dbt expressions (a boolean `<>` on nulls once hung the thriftserver).
- Expected numbers below for recounted tests were derived from the fixture rules by hand. If a test disagrees, re-derive from the fixture rules before changing an expectation; never copy a failing output into an expectation without an independent derivation.

## Design decisions the spec left open (locked here)

1. **Pool type.** `SimilarDayPool(windows: tuple[tuple[int, int], ...])`, frozen, validated (non-empty; each `1 <= newest <= oldest`; windows ascending and non-overlapping). `lags` = concatenated inclusive ranges, int64, ascending. `year_ago` = `windows[-1]`. `as_param()` = `"2-31,335-394"`. `SIMILAR_DAY_POOL = SimilarDayPool(((2, 31), (335, 394)))`.
2. **Special candidates are filtered per pair** in `SimilarDaySelector._pairs`, not dropped from `_candidates`, so `first_candidate_day` (and every date derived from it) does not move.
3. **Special targets** are dropped only from `_all_training_pairs`. Scoring pairs keep special targets, so ranked special days get pools.
4. **The issue offset** comes from `power_market_analytics.tasks.demand.TASK.issue_offset` (no import cycle). `issue_times` stays in `similar_day_feature.py`.
5. **`DayCalendar`** gains `is_holiday` (`bool`) and `holiday_name_ja` (`object`, null on ordinary days, excluded from `non_null_cols`). It checks a name is present exactly on holiday rows and that no (calendar year, name) repeats among holidays. `load_day_calendar` maps the name with `case when d.is_holiday then d.holiday_name_ja end`.
6. **Ranking frame.** `SimilarDayRanking` (grain `trade_date × rank`; `rank` int64, `reference_date`, `reference_lag_days` int64, `distance` float64) with `with_weights()` returning the frame plus a `weight` column from `inverse_distance_weights`. Both `build_feature_records` and the ranking CSV use it, so there is one weight implementation.
7. **Special-day frame.** `SpecialDayReferences` (grain `trade_date`; `holiday_name_ja` object, `last_year_date` datetime (NaT when none), `last_year_lag_days` float64 (NaN when none), `takes_reference` bool) built by `special_day_references(calendar, days, pool)` over the special days among `days`; property `same_holiday_days` → `DatetimeIndex`.
8. **`WalkForwardScoring`** fields: `selection`, `ranking`, `special_days`, `fit_cutoff`, `fits`, `cutoffs_without_fit=…`. `selection`, `ranking` and `fit_cutoff` cover ranked days only; `special_days` covers every special day in the scored span (issue time ≥ first cutoff).
9. **Selector surface.** `SimilarDaySelector.calendar` (the `DayCalendar`) and `.pool` are public. `select_and_rank(days, k)` scores once and returns `(SimilarDaySelection, SimilarDayRanking)`; `select(days)` and `rank(days, k)` wrap it.
10. **Load availability** in a row's `available_at` is per day (latest of the reference day's 24 hours), for both kinds of row.
11. **`similar_day_n_candidates`** is `float64` in the pandas frame (NaN on same-holiday rows) and published as `int`; NaN doubles are converted to SQL nulls at publish (`F.when(F.isnan(c), None)`), as `forecasting/publish.py` does.
12. **"same_holiday only on a special day"** (spec §8) is enforced by construction in `build_feature_records` (only `SpecialDayReferences` rows with `takes_reference` produce `same_holiday`) and tested there; the records frame has no holiday column to check it.
13. **Script counts.** `n_days_scored`, `first_day_scored`, `last_day_scored` cover every published day (ranked + same-holiday). New: `n_days_ranked`, `n_special_days_ranked`, `n_days_same_holiday`, `similar_day_top_k`; `similar_day_pool` comes from `selector.as_params()`. `fits.n_days_scored` sums to `n_days_ranked`.
14. **Column order** everywhere (records schema, publish DDL, `raw/ml.yml`, staging, mart YAML, conftest mart DDL): `area_code, trade_date, time_code, [run_id | similar_day_run_id], similar_day_rank1_demand_kwh, similar_day_rank2_demand_kwh, similar_day_rank3_demand_kwh, wavg_similar_day_top3_demand_kwh, similar_day_rank1_reference_date, similar_day_rank2_reference_date, similar_day_rank3_reference_date, similar_day_rank1_distance, similar_day_rank2_distance, similar_day_rank3_distance, similar_day_n_candidates, similar_day_fit_cutoff, similar_day_method, available_at, published_at`.

## File structure

| File | Change |
|---|---|
| `power_market_analytics/tasks/demand/frames.py` | `DayCalendar` gains two columns and checks |
| `power_market_analytics/tasks/demand/datasets.py` | `load_day_calendar` selects the name |
| `power_market_analytics/tasks/demand/similar_day.py` | pool, eligibility, ranking, weights, special days, lag 7 |
| `power_market_analytics/tasks/demand/similar_day_feature.py` | scoring, records, publish |
| `scripts/fit_similar_day.py` | params, counts, artifacts; flag removed |
| `power_market_analytics/tasks/demand/presets.py` | `SIMILAR_DAY_FEATURE` → rank 1 |
| `dbt/models/raw/ml.yml`, `dbt/models/staging/stg_ml__similar_day.{sql,yml}`, `dbt/models/features/ftr_period_similar_day.{sql,yml}`, `dbt/seeds/retired_features.csv` | new columns, tests, tags, retired row |
| generated: `views.py`, `fct_feature_value.sql`, `dim_feature.sql` | `just feature-views` |
| `tests/conftest.py` | `dim_date.holiday_name_ja`; the similar-day mart rows and helpers |
| `tests/test_demand_frames.py`, `tests/test_demand_datasets.py`, `tests/test_demand_similar_day.py`, `tests/test_demand_similar_day_feature.py`, `tests/test_fit_similar_day_script.py`, `tests/test_demand_presets.py`, `tests/test_demand_strategies.py`, `tests/test_demand_scripts.py`, `tests/test_feature_views.py`, `tests/test_feature_value_fact.py` | updated/new tests |
| `CLAUDE.md`, `docs/Feature-Naming.md`, `docs/superpowers/README.md`, the spec header | docs |

---

### Task 1: `DayCalendar` carries the holiday flag and name

**Files:**
- Modify: `power_market_analytics/tasks/demand/frames.py` (class `DayCalendar`, ~L174-205)
- Modify: `power_market_analytics/tasks/demand/datasets.py` (`load_day_calendar`, ~L263-333)
- Modify: `tests/conftest.py` (`HOLIDAYS_2024_SPRING` ~L153-161: add a parallel `HOLIDAY_NAMES_2024_SPRING`; `curated_warehouse` `dates` frame ~L809-828 and its DDL ~L1054-1059)
- Test: `tests/test_demand_frames.py` (`calendar()` ~L112-125, `TestDayCalendar`), `tests/test_demand_datasets.py` (`TestLoadDayCalendar` ~L117-163)
- Also: `tests/test_demand_similar_day.py` `make_calendar` must emit the two columns (the selector tests import it), so this task updates `make_calendar` minimally: `is_holiday = day in HOLIDAYS`, `holiday_name_ja = HOLIDAY_NAMES.get(day)` with a new module constant `HOLIDAY_NAMES` (2023-01-09 成人の日, 2023-03-21 春分の日, 2023-05-03 憲法記念日, 2024-01-08 成人の日, 2024-03-20 春分の日, 2024-04-29 昭和の日).

**Interfaces:**
- Produces: `DayCalendar.schema == {"trade_date": "datetime64[ns]", "is_holiday": "bool", "holiday_name_ja": "object", "days_since_holiday": "int64", "days_until_holiday": "int64", "holiday_degree": "float64"}`; `DayCalendar.non_null_cols` excludes `holiday_name_ja`.
- Produces: `tests.test_demand_similar_day.HOLIDAY_NAMES: dict[pd.Timestamp, str]`; `tests.conftest.HOLIDAY_NAMES_2024_SPRING: dict[pd.Timestamp, str]` (2024-03-20 春分の日, 04-29 昭和の日, 05-03 憲法記念日, 05-04 みどりの日, 05-05 こどもの日, 05-06 こどもの日（振替休日）).

- [ ] **Step 1: Failing frame tests** in `tests/test_demand_frames.py`. Update `calendar()` to add `"is_holiday": [False, True]` and `"holiday_name_ja": [None, "春分の日"]`, update the schema-order assertion to the new order, and add:

```python
    def test_a_name_repeated_within_a_calendar_year_is_rejected(self):
        df = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2024-01-08", "2024-03-20"]),
                "is_holiday": [True, True],
                "holiday_name_ja": ["成人の日", "成人の日"],
                "days_since_holiday": np.array([0, 0], dtype="int64"),
                "days_until_holiday": np.array([0, 0], dtype="int64"),
                "holiday_degree": [1.0, 1.0],
            }
        )
        with pytest.raises(ValueError, match="holiday_name_ja repeats within a calendar year"):
            DayCalendar.from_df(df)

    def test_the_same_name_in_two_years_is_accepted(self):
        df = calendar(
            trade_date=pd.to_datetime(["2023-03-21", "2024-03-20"]),
            is_holiday=[True, True],
            holiday_name_ja=["春分の日", "春分の日"],
            days_since_holiday=np.array([0, 0], dtype="int64"),
            days_until_holiday=np.array([0, 0], dtype="int64"),
            holiday_degree=[1.0, 1.0],
        )
        assert len(DayCalendar.from_df(df)) == 2

    def test_a_holiday_without_a_name_is_rejected(self):
        with pytest.raises(ValueError, match="a name exactly on holidays"):
            DayCalendar.from_df(calendar(holiday_name_ja=[None, None]))

    def test_a_named_ordinary_day_is_rejected(self):
        with pytest.raises(ValueError, match="a name exactly on holidays"):
            DayCalendar.from_df(calendar(holiday_name_ja=["平日", "春分の日"]))
```

- [ ] **Step 2: Run to fail.** `uv run pytest tests/test_demand_frames.py -q --no-cov` → FAIL (missing columns / no checks).

- [ ] **Step 3: Implement `DayCalendar`.**

```python
class DayCalendar(DomainFrame):
    """The holiday attributes of every ``dim_date`` day the similar-day selector reads.

    ``is_holiday`` and ``holiday_name_ja`` are ``dim_date``'s; the name is null on
    ordinary days and unique within a calendar year among holidays, so the same
    holiday can be found a year back. ``days_since_holiday`` / ``days_until_holiday``
    count calendar days to the nearest holiday (0 on a holiday);
    ``holiday_degree`` is ``dim_date.holiday_degree``.

    Grain: (trade_date).
    """

    schema = {
        "trade_date": "datetime64[ns]",
        "is_holiday": "bool",
        "holiday_name_ja": "object",
        "days_since_holiday": "int64",
        "days_until_holiday": "int64",
        "holiday_degree": "float64",
    }
    keys = ["trade_date"]
    non_null_cols = [col for col in schema if col not in ("trade_date", "holiday_name_ja")]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        # keep the two existing checks (distances >= 0, holiday_degree levels) unchanged, then:
        named = df["holiday_name_ja"].notna()
        if (named != df["is_holiday"]).any():
            raise ValueError(f"{cls.__name__}: holiday_name_ja must carry a name exactly on holidays")
        holidays = df.loc[df["is_holiday"], ["trade_date", "holiday_name_ja"]]
        repeated = holidays.assign(year=holidays["trade_date"].dt.year).duplicated(
            ["year", "holiday_name_ja"], keep=False
        )
        if repeated.any():
            first = holidays[repeated].iloc[0]
            raise ValueError(
                f"{cls.__name__}: holiday_name_ja repeats within a calendar year: "
                f"{first['trade_date'].year} {first['holiday_name_ja']}"
            )
```

- [ ] **Step 4: `load_day_calendar`.** SQL becomes `select d.date_key as trade_date, d.is_holiday, case when d.is_holiday then d.holiday_name_ja end as holiday_name_ja, d.holiday_degree from pma_curated.dim_date d`; add `"is_holiday": "bool"` to the `astype` dict; docstring names both columns. Keep the "no named holiday" raise before any use of the name.

- [ ] **Step 5: conftest `dim_date`.** Add `HOLIDAY_NAMES_2024_SPRING` next to `HOLIDAYS_2024_SPRING` (keep the tuple). In `dates` add `"holiday_name_ja": [HOLIDAY_NAMES_2024_SPRING.get(day, "Not Applicable") for day in CALENDAR_DAYS]` directly after `is_holiday`, and add `holiday_name_ja string` in the same position of the DDL string.

- [ ] **Step 6: datasets tests.** In `TestLoadDayCalendar.test_attributes_per_day` the expected column list gains `is_holiday`, `holiday_name_ja` (schema order, index `trade_date`); assert `by_day.loc["2024-04-29", "is_holiday"]` is `True` with name `"昭和の日"`, `by_day.loc["2024-05-06", "holiday_name_ja"] == "こどもの日（振替休日）"`, `by_day.loc["2024-04-10", "holiday_name_ja"]` is null, dtype of `is_holiday` is `bool`. In `test_dim_date_without_a_holiday_raises` add `"holiday_name_ja": [None] * 5` to the mocked frame.

- [ ] **Step 7: `make_calendar`.** In `tests/test_demand_similar_day.py` add `HOLIDAY_NAMES` and emit `"is_holiday": day in HOLIDAYS` and `"holiday_name_ja": HOLIDAY_NAMES.get(day)` in each row; cast `is_holiday` to `bool`. Keep `HOLIDAYS` a tuple.

- [ ] **Step 8: Run.** `uv run pytest tests/test_demand_frames.py tests/test_demand_datasets.py tests/test_demand_similar_day.py -q --no-cov` → PASS.

- [ ] **Step 9: Commit.** `git commit -m "feat(demand): DayCalendar carries is_holiday and a per-year unique holiday name"` (+ Co-Authored-By trailer).

---

### Task 2: The selector's pool, eligibility rules and calendar part

**Files:**
- Modify: `power_market_analytics/tasks/demand/similar_day.py`
- Test: `tests/test_demand_similar_day.py`

**Interfaces:**
- Consumes: `DayCalendar` (Task 1).
- Produces:
  - `SimilarDayPool(windows: tuple[tuple[int, int], ...])` with `.lags -> np.ndarray` (int64), `.year_ago -> tuple[int, int]`, `.as_param() -> str`.
  - `SIMILAR_DAY_POOL: SimilarDayPool`, `SIMILAR_DAY_TOP_K: int = 3`, `SIMILAR_DAY_BASELINE_LAG_DAYS: int = 7`.
  - `SimilarDaySelector(calendar, weather_forecast, weather_observed, hourly_load, *, pool: SimilarDayPool = SIMILAR_DAY_POOL, fit_window_days: int = SIMILAR_DAY_FIT_WINDOW_DAYS)`; public attributes `calendar: DayCalendar`, `pool: SimilarDayPool`, `fit_window_days`, `first_candidate_day`, `hourly_load_span`; property `lags` returns `pool.lags`.
  - Removed: `SIMILAR_DAY_CENTER_LAG_DAYS`, `SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS`, `center_lag_days`, `half_width_days`.

- [ ] **Step 1: Failing pool tests** (new `TestSimilarDayPool` in `tests/test_demand_similar_day.py`; update `TestConstants`):

```python
class TestSimilarDayPool:
    def test_the_papers_pool(self):
        assert SIMILAR_DAY_POOL.windows == ((2, 31), (335, 394))
        assert SIMILAR_DAY_POOL.lags.tolist() == [*range(2, 32), *range(335, 395)]
        assert SIMILAR_DAY_POOL.lags.dtype == "int64"
        assert SIMILAR_DAY_POOL.year_ago == (335, 394)
        assert SIMILAR_DAY_POOL.as_param() == "2-31,335-394"
        assert SIMILAR_DAY_TOP_K == 3

    @pytest.mark.parametrize(
        "windows",
        [(), ((0, 5),), ((5, 4),), ((2, 10), (10, 20)), ((335, 394), (2, 31))],
        ids=["empty", "newest-below-one", "reversed", "overlapping", "descending"],
    )
    def test_a_bad_pool_is_rejected(self, windows):
        with pytest.raises(ValueError, match="pool"):
            SimilarDayPool(windows)
```

In `TestConstants.test_values` replace the centre/half-width asserts with the pool/top-k/baseline asserts. In `TestSelectorSetup`: `test_window_and_candidates` → `selector.lags.tolist() == [*range(2, 32), *range(335, 395)]` (first candidate and load span unchanged); rewrite `test_bad_window_is_rejected` to keep only the `fit_window_days=0` half (pool validation now lives in `TestSimilarDayPool`).

- [ ] **Step 2: Failing eligibility tests** (`TestDifferences`):
  - `test_one_row_per_pool_day`: `len(selector.differences([D])) == 87`; candidate lags sorted by candidate date = `[*range(394, 334, -1)]` without 386 and 343, then `[*range(31, 1, -1)]` without 21. (Derivation: holidays 2024-03-20 at lag 21, 2023-03-21 at 386, 2023-05-03 at 343.)
  - `test_calendar_part_is_the_lag`: `calendar_days` equals the lag: 364.0 at `D_MINUS_364`, 2.0 at `D - 2d`, 394.0 at `D - 394d`.
  - `test_a_holiday_is_not_a_candidate`: `pd.Timestamp("2024-03-20") not in selector.differences([D]).df["candidate_date"].tolist()`.
  - `test_a_candidate_public_after_the_issue_time_is_left_out`: a selector with `make_hourly_load(late={D - pd.Timedelta(days=2)})` has 86 rows for D and no `D - 2d` candidate; with the default loads `D - 2d` is present.
  - `test_a_candidate_missing_an_observed_hour_is_left_out`: 86 rows (was 60).
  - The weather-RMSE and holiday-part tests stay (D − 364 is in the pool).

- [ ] **Step 3: Failing training-pair tests** (`TestTrainingPairs`):
  - `test_targets_whose_load_was_public_by_the_instant`: `training_pairs(2024-04-01)` holds 4,716 pairs over 53 targets; `2024-03-20` is not a target; no holiday is a candidate; the pair (2024-03-27, 2023-03-29) keeps its load difference.
  - `test_the_pairs_are_computed_once_and_sliced_by_availability`: unchanged (`first_fit_cutoff == HOLIDAYS[0] + 395 days`).
  - `test_a_late_load_joins_the_pairs_later`: `2 * 61` → `177`.
  - `test_the_fit_window_keeps_the_targets_of_the_days_before_the_cutoff`: `10 * 61` → `880`; `5 * 61` → `447`.
  - `test_first_fit_cutoff_waits_for_enough_pairs` and `test_first_fit_cutoff_counts_the_pairs_inside_the_fit_window`: replace `half_width_days=1` with `pool=SimilarDayPool(((363, 365),))`; first fit cutoff 2024-01-12 with 8 pairs (2 + 3 + 3; 2023-01-09 is a holiday), `narrow(2)` → None, `narrow(3)` cutoff == `narrow(730)` cutoff.
  - New `test_a_special_target_leaves_the_training_pairs`: `2024-03-20` never appears as `target_date` in `selector.training_pairs(pd.Timestamp("2024-04-30"))`.
  - New `test_a_training_candidate_public_after_its_targets_issue_time_is_left_out`: with `late={pd.Timestamp("2024-03-25")}`, the pair (2024-03-27, 2024-03-25) is absent and (2024-03-28, 2024-03-25) is present.

- [ ] **Step 4: Run to fail.** `uv run pytest tests/test_demand_similar_day.py -q --no-cov`.

- [ ] **Step 5: Implement.** Replace the two window constants:

```python
@dataclasses.dataclass(frozen=True)
class SimilarDayPool:
    """The candidate days of a delivery day, as inclusive windows of lags in days.

    Attributes
    ----------
    windows : tuple of (int, int)
        ``(newest, oldest)`` lag pairs, ascending and non-overlapping.
    """

    windows: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        if not self.windows:
            raise ValueError("similar-day pool must hold at least one window")
        previous_oldest = 0
        for newest, oldest in self.windows:
            if newest < 1 or newest > oldest:
                raise ValueError(f"similar-day pool window {(newest, oldest)} must satisfy 1 <= newest <= oldest")
            if newest <= previous_oldest:
                raise ValueError(f"similar-day pool windows {self.windows} must be ascending and not overlap")
            previous_oldest = oldest

    @property
    def lags(self) -> np.ndarray:
        """Every lag of the pool, ascending."""
        return np.concatenate([np.arange(n, o + 1, dtype="int64") for n, o in self.windows])

    @property
    def year_ago(self) -> tuple[int, int]:
        """The oldest window: where a special day's same-holiday reference must lie."""
        return self.windows[-1]

    def as_param(self) -> str:
        """The pool as an MLflow param, e.g. ``2-31,335-394``."""
        return ",".join(f"{n}-{o}" for n, o in self.windows)


#: The paper's pool (Park, Song and Kwon 2020, §3): the 30 recent days a
#: 09:30 D-1 issue time can see, and the 60 days around the same weekday one year back.
SIMILAR_DAY_POOL = SimilarDayPool(((2, 31), (335, 394)))
#: How many nearest days become features (the paper uses 3).
SIMILAR_DAY_TOP_K = 3
#: The retrieval check's plain reference: the same weekday one week back (the paper's PW model).
SIMILAR_DAY_BASELINE_LAG_DAYS = 7
```

In `SimilarDaySelector.__init__`: replace the window arguments and validation with `pool: SimilarDayPool = SIMILAR_DAY_POOL`; keep the fit-window check; set `self.calendar = calendar`, `self.pool = pool`; keep `self._calendar` and add `self._holidays = pd.DatetimeIndex(self._calendar.index[self._calendar["is_holiday"].to_numpy()])`; update the log line to print `pool.as_param()`. `lags` returns `self.pool.lags`. Import `TASK` from `power_market_analytics.tasks.demand`.

`_pairs`:

```python
    def _pairs(self, targets: pd.DatetimeIndex) -> pd.DataFrame:
        """Every (target, candidate) pair of the pool: the candidate has data, is not
        a special day, and its whole day's load was public by the target's issue time."""
        lags = self.lags
        pairs = pd.DataFrame(
            {
                "target_date": np.repeat(targets.to_numpy(), len(lags)),
                "lag_days": np.tile(lags, len(targets)),
            }
        )
        pairs["candidate_date"] = pairs["target_date"] - pd.to_timedelta(pairs["lag_days"], unit="D")
        pairs = pairs[
            pairs["candidate_date"].isin(self._candidates)
            & ~pairs["candidate_date"].isin(self._holidays)
        ]
        public_at = pairs["candidate_date"].map(self._load_available_at)
        issued = pairs["target_date"] + TASK.issue_offset
        return pairs[(public_at <= issued).to_numpy()].reset_index(drop=True)
```

`differences`: `"calendar_days": pairs["lag_days"].to_numpy().astype("float64")`. `_all_training_pairs`: `targets = targets[~targets.isin(self._holidays)]` before `differences(targets)`. Update docstrings (module, `DayPairDifferences`, `scorable_days`, `differences`, `_all_training_pairs`, `training_pairs`, `SimilarDaySelector`) from "window" to the pool and its rules. `as_params`: drop the two window keys, add `"similar_day_pool": self.pool.as_param()`.

- [ ] **Step 6: Run.** `uv run pytest tests/test_demand_similar_day.py -q --no-cov` → the pool, differences and training-pair tests PASS (selection/retrieval tests are rewritten in Task 3 and may still fail; confirm the only failures are in `TestSelect`, `TestRetrieval`, `TestRetrievalMetrics`, `TestPairFrames` lag_364 cases and `TestSelectorParams`).

- [ ] **Step 7: Commit.** `git commit -m "feat(demand): similar-day pool of 30 recent and 60 year-ago days with candidate rules"`.

---

### Task 3: Ranking, selection, retrieval against D − 7, weights and special-day references

**Files:**
- Modify: `power_market_analytics/tasks/demand/similar_day.py`
- Test: `tests/test_demand_similar_day.py`

**Interfaces:**
- Consumes: Task 2.
- Produces:
  - `SimilarDayRanking(DomainFrame)` schema `{"trade_date": dt, "rank": "int64", "reference_date": dt, "reference_lag_days": "int64", "distance": "float64"}`, keys `["trade_date", "rank"]`; `with_weights() -> pd.DataFrame` (adds `weight` float64).
  - `SimilarDaySelection` column `lag_7_rank` (replaces `lag_364_rank`); `SimilarDayRetrieval` column `lag_7_load_difference` (replaces `lag_364_load_difference`).
  - `SimilarDaySelector.select_and_rank(days, k=SIMILAR_DAY_TOP_K) -> tuple[SimilarDaySelection, SimilarDayRanking]`, `.select(days) -> SimilarDaySelection`, `.rank(days, k=SIMILAR_DAY_TOP_K) -> SimilarDayRanking`.
  - `inverse_distance_weights(distances: np.ndarray) -> np.ndarray`.
  - `SpecialDayReferences(DomainFrame)` schema `{"trade_date": dt, "holiday_name_ja": "object", "last_year_date": dt, "last_year_lag_days": "float64", "takes_reference": "bool"}`, keys `["trade_date"]`; property `same_holiday_days -> pd.DatetimeIndex`.
  - `special_day_references(calendar: DayCalendar, days: Iterable[pd.Timestamp], pool: SimilarDayPool) -> SpecialDayReferences`.
  - `retrieval_metrics` keys: `similar_day_load_difference_selected`, `similar_day_load_difference_lag_7`, `similar_day_load_difference_oracle`, `similar_day_share_better_than_lag_7`.

- [ ] **Step 1: Failing weight tests.**

```python
class TestInverseDistanceWeights:
    def test_hand_weights(self):
        w = inverse_distance_weights(np.array([[0.5, 1.0, 2.0]]))
        np.testing.assert_allclose(w, [[4 / 7, 2 / 7, 1 / 7]])

    def test_equal_distances_share_equally(self):
        np.testing.assert_allclose(inverse_distance_weights(np.array([[0.3, 0.3, 0.3]])), [[1 / 3] * 3])

    def test_a_zero_distance_takes_all_the_weight(self):
        np.testing.assert_allclose(inverse_distance_weights(np.array([[0.0, 0.0, 1.0]])), [[0.5, 0.5, 0.0]])

    def test_a_missing_rank_is_skipped(self):
        w = inverse_distance_weights(np.array([[1.0, 1.0, np.nan]]))
        np.testing.assert_allclose(w[:, :2], [[0.5, 0.5]])
        assert np.isnan(w[0, 2])

    def test_rows_are_independent_and_sum_to_one(self):
        w = inverse_distance_weights(np.array([[0.2, 0.4, 0.8], [1.0, np.nan, np.nan]]))
        np.testing.assert_allclose(np.nansum(w, axis=1), [1.0, 1.0])

    def test_a_day_without_any_rank_is_rejected(self):
        with pytest.raises(ValueError, match="no distance"):
            inverse_distance_weights(np.array([[np.nan, np.nan, np.nan]]))
```

- [ ] **Step 2: Failing special-day tests.** Add a builder `make_named_calendar(names: dict[str, str], start: str, end: str) -> DayCalendar` in the test module (days from `start` to `end`; `is_holiday` on the dict's dates; `days_since/until_holiday` computed from them; `holiday_degree` 1.0 on holidays and Sundays, 0.8 on Saturdays, else 0.0; rows outside the first..last holiday dropped as the loader does). Then:

```python
class TestSpecialDayReferences:
    def test_the_same_holiday_last_year_inside_the_window(self):
        calendar = make_named_calendar({"2025-01-13": "成人の日", "2026-01-12": "成人の日"}, "2025-01-01", "2026-01-31")
        refs = special_day_references(calendar, [pd.Timestamp("2026-01-12")], SIMILAR_DAY_POOL)
        row = refs.df.iloc[0]
        assert row["last_year_date"] == pd.Timestamp("2025-01-13")
        assert row["last_year_lag_days"] == 364.0
        assert bool(row["takes_reference"])
        assert refs.same_holiday_days.tolist() == [pd.Timestamp("2026-01-12")]

    def test_a_holiday_moved_outside_the_window_is_ranked(self):
        calendar = make_named_calendar({"2019-10-14": "スポーツの日", "2020-07-24": "スポーツの日"}, "2019-10-01", "2020-08-31")
        row = special_day_references(calendar, [pd.Timestamp("2020-07-24")], SIMILAR_DAY_POOL).df.iloc[0]
        assert row["last_year_lag_days"] == 284.0
        assert not bool(row["takes_reference"])

    def test_a_name_missing_last_year_is_ranked(self):
        calendar = make_named_calendar({"2019-02-11": "建国記念の日", "2020-02-23": "天皇誕生日（令和）"}, "2019-02-01", "2020-03-31")
        row = special_day_references(calendar, [pd.Timestamp("2020-02-23")], SIMILAR_DAY_POOL).df.iloc[0]
        assert pd.isna(row["last_year_date"]) and np.isnan(row["last_year_lag_days"])
        assert not bool(row["takes_reference"])

    def test_ordinary_days_and_days_outside_the_calendar_have_no_row(self):
        calendar = make_calendar()
        refs = special_day_references(calendar, [D, pd.Timestamp("2030-01-01"), pd.Timestamp("2024-03-20")], SIMILAR_DAY_POOL)
        assert refs.df["trade_date"].tolist() == [pd.Timestamp("2024-03-20")]

    def test_the_synthetic_calendar(self):
        refs = special_day_references(make_calendar(), [pd.Timestamp("2024-03-20"), HOLIDAYS[-1]], SIMILAR_DAY_POOL)
        assert refs.same_holiday_days.tolist() == [pd.Timestamp("2024-03-20")]
        assert refs.df.set_index("trade_date").loc[pd.Timestamp("2024-03-20"), "last_year_date"] == pd.Timestamp("2023-03-21")

    def test_the_window_comes_from_the_pool(self):
        calendar = make_named_calendar({"2025-01-13": "成人の日", "2026-01-12": "成人の日"}, "2025-01-01", "2026-01-31")
        refs = special_day_references(calendar, [pd.Timestamp("2026-01-12")], SimilarDayPool(((2, 31), (365, 400))))
        assert refs.same_holiday_days.empty
```

Frame tests (`TestPairFrames`): `SpecialDayReferences` rejects `takes_reference` without a `last_year_date` (`match="takes_reference needs last_year_date"`), a `last_year_date` on or after `trade_date`, and a lag that is not the date gap. `SimilarDayRanking` rejects: a reference on or after the day; a lag that is not the gap; ranks that do not run 1..n per day (`match="rank"`); distances decreasing by rank (`match="distance"`); a reference day repeated within a day (`match="repeat"`).

- [ ] **Step 3: Failing ranking/selection/retrieval tests** (`TestSelect`, `TestRetrieval`, `TestRetrievalMetrics`, `TestSelectorParams`, `TestPairFrames` renames):
  - `test_nearest_candidate`: the reference lag lies in 2..31 or 335..394; `n_candidates == 87`; `1 <= lag_7_rank <= 87`; distance equals the minimum of `weights.distance(differences([D]))`.
  - New `test_rank_returns_the_three_nearest`: `fitted.rank([D])` has ranks `[1, 2, 3]`, distances non-decreasing and equal to the three smallest distances of `differences([D])`, rank 1 equals `fitted.select([D])` (reference and distance).
  - Rewrite `test_tie_goes_to_the_smaller_lag`: with `hand_weights(calendar_days=1.0)` the pick is `D - 2d` (2024-04-08); with `hand_weights(holiday_degree=1.0)` (every weekday candidate at distance 0) the pick is also `D - 2d`; with `make_observed(null_hours={(D - pd.Timedelta(days=7), 1)})`, `lag_7_rank` is NaN and `n_candidates == 86`.
  - New `test_fewer_candidates_than_k`: `SimilarDaySelector(..., pool=SimilarDayPool(((364, 365),)))` fitted by hand weights → `rank([D], 3)` has ranks `[1, 2]`.
  - `rank` on nothing scorable → empty `SimilarDayRanking` with its schema; before a fit → `RuntimeError`.
  - `TestRetrieval`: `lag_7_load_difference == realised[D - 7d]`; `test_lag_7_is_nan_when_it_was_not_a_candidate` with `null_hours={(D - 7d, 1)}`.
  - `TestRetrievalMetrics`: column `lag_7_load_difference`, keys with `lag_7`, same values (0.1/3, 0.04, 0.02, 0.5).
  - `TestSelectorParams`: `params["similar_day_pool"] == "2-31,335-394"`; no `similar_day_center_lag_days` / `similar_day_window_half_width_days`; first selectable day stays `2024-02-07`.

- [ ] **Step 4: Run to fail.**

- [ ] **Step 5: Implement.**

```python
def inverse_distance_weights(distances: np.ndarray) -> np.ndarray:
    """Inverse-distance weights of each day's ranked references.

    ``w_r = (1 / d_r) / Σ_s (1 / d_s)`` over the ranks present; when a distance is
    0 the zero-distance ranks share the weight equally and the rest get 0. The
    denominator is summed one rank at a time, in rank order, so a re-run gives
    the same weights to the bit.

    Parameters
    ----------
    distances : numpy.ndarray
        Shape (days, k), non-decreasing along a row; NaN for a missing rank.

    Returns
    -------
    numpy.ndarray
        Shape (days, k); NaN where the rank is missing.

    Raises
    ------
    ValueError
        If a day has no distance at all.
    """
    d = np.asarray(distances, dtype="float64")
    present = ~np.isnan(d)
    if (~present.any(axis=1)).any():
        raise ValueError("inverse_distance_weights: a day has no distance")
    zero = present & (d == 0.0)
    has_zero = zero.any(axis=1)
    with np.errstate(divide="ignore"):
        inverse = np.where(present & ~zero, 1.0 / np.where(present & ~zero, d, 1.0), 0.0)
    inverse = np.where(has_zero[:, None], zero.astype("float64"), inverse)
    total = np.zeros(d.shape[0])
    for r in range(d.shape[1]):
        total = total + inverse[:, r]
    weights = inverse / total[:, None]
    return np.where(present, weights, np.nan)
```

`SimilarDayRanking` (docstring: the k nearest pool days of each ranked day under the fit that scored it; grain (trade_date, rank)); `_validate_extra`: `_check_reference_precedes`; lag equals gap; per day `rank` equals `1..n` (`groupby("trade_date")["rank"]` compared to `cumcount() + 1` after sorting by trade_date, rank); `distance` non-decreasing within a day (`groupby(...)["distance"].diff() < 0` → raise "distance must not decrease by rank"); `duplicated(["trade_date", "reference_date"])` → raise "a reference day repeats within a day". `with_weights`:

```python
    def with_weights(self) -> pd.DataFrame:
        """The ranking plus each rank's inverse-distance ``weight`` (see ``inverse_distance_weights``)."""
        df = self.df.sort_values(["trade_date", "rank"], ignore_index=True)
        if df.empty:
            return df.assign(weight=pd.Series(dtype="float64"))
        wide = df.pivot(index="trade_date", columns="rank", values="distance")
        weights = pd.DataFrame(inverse_distance_weights(wide.to_numpy()), index=wide.index, columns=wide.columns)
        long = weights.stack().rename("weight").reset_index()
        return df.merge(long, how="left", on=["trade_date", "rank"], validate="one_to_one")
```

Selector:

```python
    def _ranked(self, days: Iterable[pd.Timestamp]) -> pd.DataFrame:
        """Pool pairs with their distance and lag, nearest first per day; ties to the smaller lag."""
        diffs = self.differences(days)
        df = diffs.df.assign(distance=self.weights.distance(diffs))
        df = df.assign(lag_days=(df["target_date"] - df["candidate_date"]).dt.days)
        df = df.sort_values(["target_date", "distance", "lag_days"], kind="mergesort", ignore_index=True)
        return df.assign(rank=df.groupby("target_date").cumcount() + 1)

    def select_and_rank(self, days, k=SIMILAR_DAY_TOP_K):
        ranked = self._ranked(days)
        if ranked.empty:
            return (SimilarDaySelection.from_df(_empty(SimilarDaySelection)),
                    SimilarDayRanking.from_df(_empty(SimilarDayRanking)))
        top = ranked[ranked["rank"] <= k]
        ranking = SimilarDayRanking.from_df(pd.DataFrame({
            "trade_date": top["target_date"].to_numpy(),
            "rank": top["rank"].to_numpy(dtype="int64"),
            "reference_date": top["candidate_date"].to_numpy(),
            "reference_lag_days": top["lag_days"].to_numpy(dtype="int64"),
            "distance": top["distance"].to_numpy(dtype="float64"),
        }))
        best = top[top["rank"] == 1].set_index("target_date")
        min_rank = ranked.groupby("target_date")["distance"].rank(method="min")
        at_baseline = ranked.assign(min_rank=min_rank)
        at_baseline = at_baseline[at_baseline["lag_days"] == SIMILAR_DAY_BASELINE_LAG_DAYS].set_index("target_date")["min_rank"]
        counts = ranked.groupby("target_date").size()
        selection = SimilarDaySelection.from_df(pd.DataFrame({
            "trade_date": best.index,
            "reference_date": best["candidate_date"].to_numpy(),
            "distance": best["distance"].to_numpy(dtype="float64"),
            "reference_lag_days": best["lag_days"].to_numpy(dtype="int64"),
            "n_candidates": counts.loc[best.index].to_numpy(dtype="int64"),
            "lag_7_rank": at_baseline.reindex(best.index).to_numpy(dtype="float64"),
        }))
        return selection, ranking
```

(Add the NumPy docstrings; `select` returns `select_and_rank(days, 1)[0]`, `rank` returns `select_and_rank(days, k)[1]`; delete `_scored`'s centre gap; `retrieval` computes `scored` from `self._ranked(...)` and uses `lag_days == SIMILAR_DAY_BASELINE_LAG_DAYS`.)

```python
def special_day_references(calendar, days, pool):
    """The same-holiday reference of every special day among ``days``.

    For a ``calendar`` holiday D, last year's day with the same ``holiday_name_ja``
    is its reference when it lies in ``pool.year_ago`` days back; otherwise the day
    is ranked like any day. Days that are not holidays, or not in the calendar,
    get no row.
    ...
    """
    cal = calendar.df.set_index("trade_date")
    wanted = pd.DatetimeIndex(pd.to_datetime(list(days))).unique().sort_values()
    wanted = wanted[wanted.isin(cal.index)]
    special = cal.loc[wanted]
    special = special[special["is_holiday"]]
    holidays = cal[cal["is_holiday"]].reset_index()
    by_year_name = holidays.assign(year=holidays["trade_date"].dt.year).set_index(["year", "holiday_name_ja"])["trade_date"]
    rows = []
    newest, oldest = pool.year_ago
    for day, name in special["holiday_name_ja"].items():
        last = by_year_name.get((day.year - 1, name), pd.NaT)
        lag = float((day - last).days) if pd.notna(last) else np.nan
        rows.append({"trade_date": day, "holiday_name_ja": name, "last_year_date": last,
                     "last_year_lag_days": lag, "takes_reference": bool(pd.notna(last) and newest <= lag <= oldest)})
    frame = pd.DataFrame(rows) if rows else _empty(SpecialDayReferences)
    return SpecialDayReferences.from_df(frame.astype({"last_year_date": _DATE, "last_year_lag_days": "float64", "takes_reference": "bool"}))
```

`SpecialDayReferences._validate_extra`: `takes_reference` rows need `last_year_date`; `last_year_date` (where present) precedes `trade_date`; `last_year_lag_days` equals the gap where present. Property `same_holiday_days = pd.DatetimeIndex(df.loc[df["takes_reference"], "trade_date"])`.

Rename `lag_364_*` in the frames and `retrieval_metrics`; update their docstrings to D − 7.

- [ ] **Step 6: Run.** `uv run pytest tests/test_demand_similar_day.py -q --no-cov` → all PASS.

- [ ] **Step 7: Commit.** `git commit -m "feat(demand): rank the three nearest pool days, weigh them by inverse distance, find same-holiday references"`.

---

### Task 4: Walk-forward scoring and the feature records

**Files:**
- Modify: `power_market_analytics/tasks/demand/similar_day_feature.py`
- Test: `tests/test_demand_similar_day_feature.py`

**Interfaces:**
- Consumes: Tasks 2–3.
- Produces:
  - `WalkForwardScoring(selection, ranking, special_days, fit_cutoff, fits, cutoffs_without_fit=…)`.
  - `score_walk_forward(selector, days, *, refit_every_days=DEFAULT_REFIT_EVERY_DAYS, top_k=SIMILAR_DAY_TOP_K) -> WalkForwardScoring`.
  - Column sets: `RANK_LOAD_COLS = tuple(f"similar_day_rank{r}_demand_kwh" for r in 1..3)`, `RANK_DATE_COLS`, `RANK_DISTANCE_COLS`, `WEIGHTED_MEAN_COL = "wavg_similar_day_top3_demand_kwh"`, `METHOD_SIMILARITY = "similarity"`, `METHOD_SAME_HOLIDAY = "same_holiday"`.
  - `SimilarDayFeatureRecords` with the schema of decision 14 (dtypes: loads/distances/`similar_day_n_candidates` float64; dates datetime64[ns]; `similar_day_method` object; `run_id` object).
  - `build_feature_records(scoring, hourly_load, forecast, *, run_id, area_code, published_at)` (signature unchanged).
  - `publish_feature_records(records, spark=None) -> int` (signature unchanged).

- [ ] **Step 1: Failing scoring tests.**
  - `make_scoring(days=(D, OTHER), refs=((364, 0.5), (7, 1.0), (2, 2.0)), fit_cutoff=CUTOFF, same_holiday=())`: builds `SimilarDayRanking` rows per day from `refs` (lag, distance), the selection as rank 1 (`n_candidates` 87, `lag_7_rank` 2.0), `special_days` from `same_holiday` as a sequence of `(day, reference_day)` with `takes_reference=True`, `fit_cutoff` Series over the ranked days, `fits` frame.
  - `TestScoreWalkForward.test_every_day_is_scored_by_the_latest_fit_before_its_issue_time`: the ranked days are `2024-02-09 … 2024-04-29` without `2024-03-20` (80 days); `weekly.special_days.same_holiday_days == [2024-03-20]`; `2024-04-29` is in `weekly.selection` (a ranked special day); `fits["n_days_scored"].sum() == 80`; `fit_through == fit_cutoff - 1 day` except the `2024-03-21` fit whose `fit_through == 2024-03-19`; every ranked day has 3 ranks and rank 1 equals the selection's reference.
  - `test_a_days_choice_is_the_fits_own`: refit at `weekly.fit_cutoff[D]` → `select_and_rank([D], 3)` equals the stored selection row and ranking rows of D.
  - `test_a_fit_window_bounds_what_each_fit_sees`: per-fit `n_targets == [1, 8, 14, 14, 14, 14, 13, 13, 14, 14, 14, 14]` and `n_pairs == [88, 717, 1256, 1250, 1246, 1246, 1157, 1151, 1231, 1223, 1218, 1228]`; fit_through as above.
  - `test_a_narrow_window_waits_for_eight_public_pairs`: `pool=SimilarDayPool(((363, 365),))` instead of `half_width_days=1`.
  - `test_a_cutoff_without_enough_pairs_makes_no_fit`: last line → `len(records) == 48 * (len(scoring.selection) + len(scoring.special_days.same_holiday_days))`.
  - New `test_same_holiday_days_are_not_ranked`: `2024-03-20` absent from `weekly.ranking` and `weekly.fit_cutoff`.
  - New `test_only_same_holiday_days_still_score`: `score_walk_forward(make_selector(), [pd.Timestamp("2024-03-20")])` (the first fit cutoff comes from all training pairs, 2024-02-08; the one day is a same-holiday day) returns empty `selection` and `ranking` frames with their schemas, `fit_cutoff` empty, and `special_days.same_holiday_days == [2024-03-20]`, without an exception.

- [ ] **Step 2: Failing record tests.**
  - `test_48_rows_per_ranked_day_with_three_loads_and_their_weighted_mean`: columns in decision-14 order plus `run_id`; for D, `similar_day_rank{r}_demand_kwh == load_at(D - lag_r, (tc + 1) // 2) / 2` for lags (364, 7, 2); `wavg == (4/7·L1 + 2/7·L2 + 1/7·L3)` (halved loads) to `rel=1e-12`; distances (0.5, 1.0, 2.0); `n_candidates == 87.0`; method `"similarity"`; `available_at == forecast_available_at(D)`; `fit_cutoff == CUTOFF`.
  - `test_a_same_holiday_days_rows`: `make_scoring(days=(D,), same_holiday=((pd.Timestamp("2024-03-20"), pd.Timestamp("2023-03-21")),))` → 48 rows for 2024-03-20 with rank 1 == wavg == `load_at(2023-03-21, h) / 2`; ranks 2–3, their dates, all distances, `n_candidates`, `fit_cutoff` null; `rank1_reference_date == 2023-03-21`; method `"same_holiday"`; `available_at == 2023-03-22 00:00` (the fixture's load availability); no forecast needed (pass a forecast without 2024-03-20).
  - `test_a_ranked_special_day`: `make_scoring(days=(HOLIDAYS[-1],))` → method `"similarity"`, three ranks.
  - `test_fewer_than_three_ranks`: refs `((364, 0.5), (7, 1.0))` → rank 3 load/date/distance null, wavg = `(2/3·L1 + 1/3·L2)`.
  - `test_a_zero_distance_takes_all_the_weight`: refs `((364, 0.0), (7, 1.0), (2, 2.0))` → wavg == rank-1 load.
  - `test_the_latest_ranked_load_sets_the_availability`: rank 3 at lag 2 with `late={D - 2d}` → `available_at == D` (day + 2 days = 2024-04-10 00:00 > the forecast's 2024-04-09 01:00).
  - `test_a_fit_after_the_forecast_sets_the_availability`: unchanged logic.
  - `test_a_similar_day_without_a_load_is_rejected`: a rank-2 reference with no load → `"48 period(s) have no load on their similar day"`; a same-holiday R with no load → same message.
  - `test_a_day_without_a_forecast_is_rejected`: ranked days only (unchanged message).
  - `test_an_empty_scoring_is_rejected`: no ranked and no same-holiday day → `"no scored day to publish"`.
  - `test_the_frame_checks_its_rows` (one `pytest.raises` per case, each mutating one column of a valid frame): reference on/after the day (`"must precede trade_date"`); a load ≤ 0 (`"must be positive"`); `time_code` 49; fit cutoff after the issue time; `available_at` before the fit cutoff; two run ids; unknown method (`"similar_day_method"`); same-holiday row with a distance (`"same_holiday"`); ranked row without `n_candidates` (`"similarity"`); distances decreasing (`"distance"`); a null rank 2 with a non-null rank 3 (`"null rank"`); repeated reference dates (`"repeat"`); wavg outside [min, max] (`"weighted mean"`); a load present with its date missing (`"reference_date"`).
  - `TestPublishFeatureRecords.test_creates_the_partitioned_table_and_writes_the_rows`: include a same-holiday day; column types: loads/wavg/distances double, dates date, `similar_day_n_candidates` int, `similar_day_fit_cutoff` timestamp, `similar_day_method` string; `spark.sql(f"select count(*) from {FEATURE_TABLE} where run_id = 'score-1' and similar_day_method = 'same_holiday' and similar_day_rank2_distance is null and similar_day_n_candidates is null and similar_day_fit_cutoff is null").first()[0] == 48`; and no `isnan` doubles remain.
  - The publish tests' warehouse table may pre-exist from an earlier test with old columns only within one session of old code — tests create it fresh per session, so nothing to drop; if a session-scoped table from `test_fit_similar_day_script` exists first, it is created by the new code too.

- [ ] **Step 3: Run to fail.**

- [ ] **Step 4: Implement scoring.**

```python
@dataclasses.dataclass(frozen=True)
class WalkForwardScoring:
    """The similar days of every scored day, each chosen by the latest fit before it.

    Attributes
    ----------
    selection : SimilarDaySelection
        Rank 1 of every ranked day (any day without a same-holiday reference).
    ranking : SimilarDayRanking
        The up to ``top_k`` nearest pool days of every ranked day.
    special_days : SpecialDayReferences
        Every special day of the scored span (issue time on or after the first
        cutoff), with its same-holiday reference or none.
    fit_cutoff : pandas.Series
        The cutoff of the fit that ranked each ranked day, indexed by ``trade_date``.
    fits : pandas.DataFrame
        One row per fit (unchanged columns; ``n_days_scored`` counts ranked days).
    cutoffs_without_fit : pandas.DatetimeIndex
        Cutoffs with too few public pairs; the previous fit served on.
    """

    selection: SimilarDaySelection
    ranking: SimilarDayRanking
    special_days: SpecialDayReferences
    fit_cutoff: pd.Series
    fits: pd.DataFrame
    cutoffs_without_fit: pd.DatetimeIndex = dataclasses.field(default_factory=lambda: pd.DatetimeIndex([]))
```

In `score_walk_forward`, after computing `first`, `scorable`, `issued` and the existing raise:

```python
    span = scorable[issued >= first]
    special = special_day_references(selector.calendar, span, selector.pool)
    ranked_days = span[~span.isin(special.same_holiday_days)]
    ranked_issued = issue_times(ranked_days)
    cutoffs = pd.date_range(first, issued.max(), freq=step)
    selections, rankings = [], []
    for k, cutoff in enumerate(cutoffs):
        served = ranked_issued >= cutoff
        if k + 1 < len(cutoffs):
            served &= ranked_issued < cutoffs[k + 1]
        # fit or skip exactly as today
        selection, ranking = selector.select_and_rank(ranked_days[served], top_k)
        n_served[-1] += len(selection)
        if not selection.df.empty:
            selections.append(selection.df.assign(fit_cutoff=fitted[-1][0]))
            rankings.append(ranking.df)
    scored = pd.concat(selections, ignore_index=True) if selections else _empty(SimilarDaySelection).assign(fit_cutoff=pd.Series(dtype="datetime64[ns]"))
    ranked = pd.concat(rankings, ignore_index=True) if rankings else _empty(SimilarDayRanking)
```

Return `WalkForwardScoring(selection=SimilarDaySelection.from_df(scored.drop(columns="fit_cutoff")), ranking=SimilarDayRanking.from_df(ranked), special_days=special, fit_cutoff=scored.set_index("trade_date")["fit_cutoff"], fits=..., cutoffs_without_fit=...)`. Keep the logging (add ranked/same-holiday counts). Import `_empty` is private: add a small local helper `_empty_frame(frame_cls)` in this module instead of importing a private name.

- [ ] **Step 5: Implement the records.** Column sets as module constants (decision 14). `SimilarDayFeatureRecords.schema` built from them; `non_null_cols = ["similar_day_rank1_demand_kwh", WEIGHTED_MEAN_COL, "similar_day_rank1_reference_date", "similar_day_method", "available_at", "published_at", "run_id"]`. `_validate_extra` implements every check listed in Step 2 (vectorised; nulls handled explicitly; the wavg check `wavg >= min_load * (1 - 1e-9)` and `wavg <= max_load * (1 + 1e-9)` over the non-null rank loads).

`build_feature_records`:

```python
    ranked = scoring.ranking.with_weights()
    references = scoring.special_days.df[scoring.special_days.df["takes_reference"]]
    if ranked.empty and references.empty:
        raise ValueError("no scored day to publish")
    load = hourly_load.df.rename(columns={"load_date": "reference_date"})
    day_available_at = hourly_load.df.groupby("load_date")["available_at"].max()
    periods = pd.DataFrame({"time_code": np.arange(1, N_PERIODS + 1, dtype="int64")})
    periods["hour_ending"] = (periods["time_code"] + 1) // 2
    frames = []
    if not ranked.empty:
        frames.append(_ranked_rows(scoring, ranked, load, day_available_at, forecast, periods))
    if not references.empty:
        frames.append(_same_holiday_rows(references, load, day_available_at, periods))
    rows = pd.concat(frames, ignore_index=True)
    # assign area_code, published_at, run_id; cast dates; sort by trade_date, time_code
    return SimilarDayFeatureRecords.from_df(rows)
```

`_ranked_rows`: for each rank r = 1..SIMILAR_DAY_TOP_K, take `ranked[ranked["rank"] == r]` as `(trade_date, reference_date_r, distance_r, weight_r)`; start from the ranked days × periods (`merge(how="cross")`), left-merge each rank's columns on `trade_date` (`validate="many_to_one"`), then left-merge the hourly load on `(reference_date_r, hour_ending)` (`validate="many_to_one"`); raise `"{n} period(s) have no load on their similar day, e.g. …"` where a reference date is present but its load is missing (count over all ranks). Weighted mean, one rank at a time:

```python
    total = np.zeros(len(rows))
    for r in range(1, SIMILAR_DAY_TOP_K + 1):
        term = rows[f"weight_{r}"].to_numpy() * rows[f"load_{r}"].to_numpy()
        total = total + np.where(np.isnan(term), 0.0, term)
    rows[WEIGHTED_MEAN_COL] = total / PERIODS_PER_HOUR
```

Rank loads = `load_r / PERIODS_PER_HOUR`. Load availability = row-wise max over ranks of `reference_date_r.map(day_available_at)`; forecast availability per `trade_date` (missing → the existing `"day(s) have no forecast availability"` raise); `fit_cutoff = trade_date.map(scoring.fit_cutoff)`; `n_candidates = trade_date.map(scoring.selection.df.set_index("trade_date")["n_candidates"]).astype("float64")`; `available_at = max(forecast, fit_cutoff, load availability)`; method `METHOD_SIMILARITY`.

`_same_holiday_rows`: references × periods; merge R's load; missing → the same load raise; rank 1 = wavg = load / 2; rank 2–3 loads/dates, distances, `n_candidates`, `fit_cutoff` null (float NaN / NaT); `rank1_reference_date = last_year_date`; `available_at = last_year_date.map(day_available_at)`; method `METHOD_SAME_HOLIDAY`.

`publish_feature_records`: DDL and select generated from the schema in order (`double` for float64 except `similar_day_n_candidates` → `int`; `date` for the trade/reference dates; `timestamp` for fit cutoff, available_at, published_at; `string`, `int` for time_code), each float column wrapped `F.when(F.isnan(F.col(c)), None).otherwise(F.col(c))` before its cast, `run_id` last.

Update the module and function docstrings (four features, same-holiday rows, both `available_at` rules).

- [ ] **Step 6: Run.** `uv run pytest tests/test_demand_similar_day_feature.py tests/test_demand_similar_day.py -q --no-cov` → PASS.

- [ ] **Step 7: Commit.** `git commit -m "feat(demand): write ranks 1-3, their weighted mean and same-holiday rows to pma_ml.similar_day"`.

---

### Task 5: The fit script

**Files:**
- Modify: `scripts/fit_similar_day.py`
- Test: `tests/test_fit_similar_day_script.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces: params `similar_day_pool` (via `selector.as_params()`), `similar_day_top_k`, `n_days_scored`, `first_day_scored`, `last_day_scored`, `n_days_ranked`, `n_special_days_ranked`, `n_days_same_holiday`; artifacts `similar_day_ranking.csv`, `similar_day_special_days.csv` (columns of `SpecialDayReferences` plus `similar_day_method`); metrics with `lag_7`.

- [ ] **Step 1: Failing script tests.**
  - `RETRIEVAL_METRICS` with the two `lag_7` names.
  - `test_walks_forward_weekly_and_publishes_the_rows`: params `first_fit_cutoff '2024-02-08 00:00:00'`, `last_fit_cutoff '2024-04-25 00:00:00'`, `n_fits '12'`, `first_day_scored '2024-02-09'`, `last_day_scored '2024-04-29'`, `n_days_scored '81'`, `n_days_ranked '80'`, `n_special_days_ranked '1'`, `n_days_same_holiday '1'`, `similar_day_pool '2-31,335-394'`, `similar_day_top_k '3'`, `similar_day_fit_window_days '730'`; no `similar_day_center_lag_days` or `similar_day_window_half_width_days`. `similar_day_selection.csv` and `similar_day_retrieval.csv` list ranked days only (`2024-03-20` absent). `similar_day_ranking.csv` has 240 rows, ranks 1..3 per day and weights summing to 1 per day (`rel=1e-12`). `similar_day_special_days.csv` has `2024-03-20` (`春分の日`, `2023-03-21`, `365.0`, `same_holiday`) and `2024-04-29` (`昭和の日`, empty date and lag, `similarity`). Published rows `== 48 * 81`. Row (2024-04-10, time_code 7): `similar_day_rank1_reference_date` equals the selection's reference, `similar_day_rank1_demand_kwh == load_at(ref, 4) / 2`, `similar_day_method == "similarity"`, `available_at == forecast_available_at(day)`. Row (2024-03-20, time_code 7): rank 1 == wavg == `load_at(2023-03-21, 4) / 2`, method `same_holiday`, `similar_day_fit_cutoff` null, `available_at == 2023-03-22 00:00`.
  - Delete `test_window_half_width_reaches_the_selector`; add `test_the_pool_has_no_flag`: `main(["--window-half-width-days", "10"])` raises `SystemExit`.
  - The row-count assertions in `test_cadence_reaches_the_job`, `test_fit_window_reaches_the_selector`, `test_scored_days_without_a_load_yet_log_no_metrics` stay `48 * n_days_scored` (now all published days).

- [ ] **Step 2: Run to fail.**

- [ ] **Step 3: Implement.** Keep `calendar = load_day_calendar()`; `SimilarDaySelector(calendar, weather.forecast, observed.weather, hourly_load, fit_window_days=args.fit_window_days)`; remove the flag, its import and its selector argument. After `build_feature_records`:

```python
        published_days = pd.DatetimeIndex(records.df["trade_date"].drop_duplicates()).sort_values()
        special = scoring.special_days.df
        mlflow.log_params({
            ...,
            "n_days_scored": len(published_days),
            "first_day_scored": str(published_days[0].date()),
            "last_day_scored": str(published_days[-1].date()),
            "n_days_ranked": len(scoring.selection),
            "n_special_days_ranked": int((~special["takes_reference"]).sum()),
            "n_days_same_holiday": int(special["takes_reference"].sum()),
            "similar_day_top_k": SIMILAR_DAY_TOP_K,
            **selector.as_params(),
        })
        log_dataframe(scoring.ranking.with_weights(), "similar_day_ranking.csv")
        log_dataframe(
            special.assign(similar_day_method=np.where(special["takes_reference"], METHOD_SAME_HOLIDAY, METHOD_SIMILARITY)),
            "similar_day_special_days.csv",
        )
```

Update the module docstring (pool, four features, same-holiday rows, artifacts, metrics) and the final log lines.

- [ ] **Step 4: Run.** `uv run pytest tests/test_fit_similar_day_script.py -q --no-cov` → PASS.

- [ ] **Step 5: Commit.** `git commit -m "feat(demand): fit script logs the ranking, special days and lag-7 retrieval"`.

---

### Task 6: dbt models, the retired feature, presets and the feature store

**Files:**
- Modify: `dbt/models/raw/ml.yml` (source `similar_day`), `dbt/models/staging/stg_ml__similar_day.sql` and `.yml`, `dbt/models/features/ftr_period_similar_day.sql` and `.yml`, `dbt/seeds/retired_features.csv`
- Regenerate: `power_market_analytics/features/views.py`, `dbt/models/curated/fct_feature_value.sql`, `dbt/models/curated/dim_feature.sql`
- Modify: `power_market_analytics/tasks/demand/presets.py`
- Modify: `tests/conftest.py` (`similar_day_load` and new helpers, `_write_feature_marts` similar-day rows and DDL, docstrings)
- Test: `tests/test_demand_presets.py`, `tests/test_demand_strategies.py`, `tests/test_demand_scripts.py`, `tests/test_feature_views.py`, `tests/test_feature_value_fact.py`

**Interfaces:**
- Consumes: Task 4's column order.
- Produces: `SIMILAR_DAY_FEATURE = "ftr_period_similar_day:similar_day_rank1_demand_kwh"`; `tests.conftest.SIMILAR_DAY_SAME_HOLIDAY = pd.Timestamp("2024-04-29")` and helpers `similar_day_reference(day: pd.Timestamp, rank: int) -> pd.Timestamp | None`, `similar_day_rank_load(day, time_code, rank) -> float | None`, `similar_day_mean(day, time_code) -> float`; `similar_day_load(day, time_code)` stays rank 1.

- [ ] **Step 1: dbt source, staging, mart.**
  - `ml.yml`: rewrite the table description (pool, ranks and mean, same-holiday rule, guard sentence kept) and the column entries in decision-14 order (`run_id` first); descriptions only, no tests.
  - `stg_ml__similar_day.sql`: the new column list in both branches (typed nulls: loads/mean/distances `double`, dates `date`, `similar_day_n_candidates int`, `similar_day_fit_cutoff timestamp`, `similar_day_method string`).
  - `stg_ml__similar_day.yml`: contract for every column; keep the grain test and key `not_null`s; `not_null` + `accepted_range` (min 0, `inclusive: false`) on `similar_day_rank1_demand_kwh` and `wavg_similar_day_top3_demand_kwh`; `not_null` on `similar_day_rank1_reference_date` and `similar_day_method` with `accepted_values` `[similarity, same_holiday]`; conditional `not_null` with `config: where:` on ranks 2 and 3 (`"similar_day_method = 'similarity' and similar_day_n_candidates >= 2"` / `>= 3`) for load, date and distance, on `similar_day_rank1_distance`, `similar_day_n_candidates` and `similar_day_fit_cutoff` (`"similar_day_method = 'similarity'"`); `accepted_range` min 0 on distances, min 1 on `similar_day_n_candidates`. Model-level `dbt_utils.expression_is_true` tests:

```yaml
      - dbt_utils.expression_is_true:
          name: stg_ml__similar_day_same_holiday_rows_carry_one_reference
          arguments:
            expression: >
              similar_day_method = 'similarity' or (
                similar_day_rank2_demand_kwh is null and similar_day_rank3_demand_kwh is null
                and similar_day_rank2_reference_date is null and similar_day_rank3_reference_date is null
                and similar_day_rank1_distance is null and similar_day_rank2_distance is null
                and similar_day_rank3_distance is null and similar_day_n_candidates is null
                and similar_day_fit_cutoff is null
                and wavg_similar_day_top3_demand_kwh = similar_day_rank1_demand_kwh
                and datediff(trade_date, similar_day_rank1_reference_date) between 335 and 394
              )
      - dbt_utils.expression_is_true:
          name: stg_ml__similar_day_distances_do_not_decrease_by_rank
          arguments:
            expression: >
              similar_day_method = 'same_holiday' or (
                coalesce(similar_day_rank1_distance <= similar_day_rank2_distance, true)
                and coalesce(similar_day_rank2_distance <= similar_day_rank3_distance, true)
              )
      - dbt_utils.expression_is_true:
          name: stg_ml__similar_day_weighted_mean_recomputes
          arguments:
            expression: >
              similar_day_method = 'same_holiday'
              or similar_day_rank3_distance is null
              or least(similar_day_rank1_distance, similar_day_rank2_distance, similar_day_rank3_distance) = 0
              or abs(
                wavg_similar_day_top3_demand_kwh
                - (
                  similar_day_rank1_demand_kwh / similar_day_rank1_distance
                  + similar_day_rank2_demand_kwh / similar_day_rank2_distance
                  + similar_day_rank3_demand_kwh / similar_day_rank3_distance
                ) / (
                  cast(1 as double) / similar_day_rank1_distance
                  + cast(1 as double) / similar_day_rank2_distance
                  + cast(1 as double) / similar_day_rank3_distance
                )
              ) <= cast(1e-9 as double) * wavg_similar_day_top3_demand_kwh
```

  - `ftr_period_similar_day.sql`: the new columns (keep `run_id as similar_day_run_id`, `available_at`, `published_at`).
  - `ftr_period_similar_day.yml`: rewrite the model description (pool, rule, four features, untagged columns, both `available_at` rules, grain sentence kept); four tagged columns with the Global Constraints expressions (`feature: true`, `categorical: false`; `not_null` on rank 1 and the mean; descriptions say kWh per 30-minute period, halved from the でんき予報 hourly load, and when each is null); untagged `similar_day_rank{1,2,3}_reference_date` (date; `not_null` on rank 1), `similar_day_rank{1,2,3}_distance` (double), `similar_day_n_candidates` (int), `similar_day_fit_cutoff` (timestamp), `similar_day_method` (string, `not_null`); reword `similar_day_run_id` and `available_at`. Rewrite the unit test: given two runs (`score-1`, `score-2`) × one ranked row (2025-03-10 time_code 1, three ranks, method `similarity`) and one same-holiday row (2025-03-20 time_code 1, typed nulls, method `same_holiday`); expect the same four rows with `run_id` renamed `similar_day_run_id`.
  - `retired_features.csv`: append `similar_day_demand_kwh,"SIMILAR_DAY(power_usage_demand_kwh, gap=334d, window=61) / 2",false,"The chosen similar day's hourly load (fct_area_power_usage_hourly, kWh over the hour containing the period) halved, kWh per 30-minute period: the nearest day of D - 364 +- 30 (research demand/R-004 E-002); retired on 2026-09-14 for the paper-style pool."`

- [ ] **Step 2: Regenerate.** `cd dbt && uv run dbt deps && cd .. && just feature-views`. Check `git diff --stat` shows the three generated files, then `uv run python scripts/generate_feature_views.py --check` → exit 0. Run `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt parse` → no errors.

- [ ] **Step 3: Presets.** `SIMILAR_DAY_FEATURE = "ftr_period_similar_day:similar_day_rank1_demand_kwh"`; comment: rank 1 of the paper-style pool (same holiday last year on a special day); module docstring "six" → "seven"; the `LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY` comment: reference run `008868fe…` predates the rank-1 feature (2026-09-14) and no longer comes from this preset.

- [ ] **Step 4: conftest mart fixture.** Ranked days: ranks at lags (364, 7, 371) with distances (0.5, 1.0, 2.0), `n_candidates` 87, `fit_cutoff = day - 1d`, `available_at = day - 1d + 1h`, method `similarity`; the mean via `(4/7, 2/7, 1/7)` weights, summed in rank order. `SIMILAR_DAY_SAME_HOLIDAY = 2024-04-29`: R = 2023-04-29 (lag 366); rank 1 = mean = R's load / 2; ranks 2–3, dates, distances, `n_candidates`, `fit_cutoff` `None` (object dtype, as `nullable_column` does); `available_at = 2023-04-30 00:00`; method `same_holiday`. Helpers `similar_day_reference`, `similar_day_rank_load`, `similar_day_mean` (NumPy docstrings); `similar_day_load(day, tc)` returns rank 1 (R's load on the same-holiday day). Update the DDL string to decision-14 order. Update the `_write_feature_marts` docstring.

- [ ] **Step 5: Tests referencing the retired column.**
  - `tests/test_demand_presets.py`: `SIMDAY_COLUMNS[-1] = "similar_day_rank1_demand_kwh"`; `SIMILAR_DAY_FEATURE` expectation; dtype map key.
  - `tests/test_demand_strategies.py`: `SIMDAY_FEATURE_COLS[-1]`; `similar_day_demand_kwh` → `similar_day_rank1_demand_kwh` at the frame lookups; add in `test_similar_day_preset_reads_the_marts_selection` a check that `2024-04-29` time_code 1 equals `similar_day_load(SIMILAR_DAY_SAME_HOLIDAY, 1)` if the test's days cover it, otherwise leave coverage to the fact test.
  - `tests/test_demand_scripts.py`: `feature_refs` suffix, `lgbm_feature_cols`, contribution `component` → rank 1; rename the diagnostics literal `similar_day_share_better_than_lag_364` → `…_lag_7`.
  - `tests/test_feature_views.py`: the query tail `"time_code, similar_day_rank1_demand_kwh, similar_day_rank2_demand_kwh, similar_day_rank3_demand_kwh, wavg_similar_day_top3_demand_kwh, available_at, published_at from pma_features.ftr_period_similar_day"`; add a check that the view's field names are exactly those four with the four Global Constraints expressions.
  - `tests/test_feature_value_fact.py::test_a_mart_with_two_vintages_keeps_the_newest_published`: double the four feature columns in the older run; expect `len(subset) == 4 * mart.count()`; compare per `feature_name` with a null-safe equality (`pd.isna(a) & pd.isna(b) | (a == b)`).

- [ ] **Step 6: Run.** `uv run pytest tests/test_demand_presets.py tests/test_demand_strategies.py tests/test_demand_scripts.py tests/test_feature_views.py tests/test_feature_value_fact.py tests/test_features_presets.py tests/test_feature_store.py tests/test_generate_feature_views.py -q --no-cov` → PASS.

- [ ] **Step 7: Commit.** `git commit -m "feat(dbt)!: similar-day mart carries ranks 1-3 and their mean; retire similar_day_demand_kwh"` with a `BREAKING CHANGE:` footer (the mart's contract changes) and the Co-Authored-By trailer last.

---

### Task 7: Docs

**Files:** `CLAUDE.md`, `docs/Feature-Naming.md`, `docs/superpowers/README.md`, `docs/superpowers/specs/2026-09-14-similar-day-top-k-design.md` (header only).

- [ ] **Step 1: `docs/Feature-Naming.md`.** Rule 5: tuple `gap`/`window` for a pool of several windows (`gap=(2d, 335d), window=(30, 60)`); `rank`, `k`, `weight`, `holidays` after `halflife`; `holidays=last_year` = a holiday takes the same holiday last year, when that day lies in the year-ago window, instead of a ranked pick. The `SIMILAR_DAY` row becomes `SIMILAR_DAY(x, gap, window, rank, holidays)` with the rank-1 expression as example; a new `SIMILAR_DAY_MEAN(x, gap, window, k, weight, holidays)` row with the mean's expression. Note that `weight=inverse_distance` weighs days, unlike `MEAN(x, weight=population)`, which weighs stations.

- [ ] **Step 2: `CLAUDE.md`** (edit CLAUDE.md only; AGENTS.md is a symlink):
  - `just test` bullet: the `feature_marts` similar-day mart now has ranks 1–3 (lags 364, 7, 371), their mean and one same-holiday day (2024-04-29), instead of "picks D − 364".
  - `demand_backtest.py` bullet and the demand-task bullet: the seven simday presets read `ftr_period_similar_day:similar_day_rank1_demand_kwh` since 2026-09-14; their reference runs (`008868fe…`, `e3e3bd61…`, `a8da46c5…`, `f7153839…`, `9182d469…`, `a3fde7eb…`, `34707ed6…`, `d04e9d0c…`, `e6d6d4ef…`) predate the switch and no longer come from the presets; a comparison needs fresh baselines. Keep the historical numbers.
  - `fit_similar_day.py` bullet: the pool (D − 2 … D − 31 and D − 335 … D − 394, no special days, candidate load public by the issue time), the same-holiday rule, ranks 1–3 and the mean, the new params/counts/artifacts, the renamed metrics, no `--window-half-width-days`, "up to 90 candidates", the build selector `retired_features stg_ml__similar_day+ dim_feature`, dropping `pma_ml.similar_day` before the first run with new columns.
  - Feature-mart bullet: the four tagged and the untagged columns; `similar_day_demand_kwh` retired on 2026-09-14 (`retired_features` row).
  - Demand-task bullet: replace "D − 364 ± 30", "days from D − 364", "the chosen day's hourly load" with the pool, the lag as calendar part, the special-day rule, both `available_at` rules and the D − 7 retrieval check; drop "at least 334 days older".
  - The `pma_ml` gotcha: add that the 2026-09-14 column change dropped the table again.
- [ ] **Step 3: `docs/superpowers/README.md`.** Add the 2026-09-14 row at the top (title "Paper-style similar days — ranks 1-3, weighted mean, same-holiday references", spec and plan links).
- [ ] **Step 4: Spec header.** `Status: **approved** on 2026-09-14; implemented on branch feature/similar-day-top-k.`
- [ ] **Step 5: Check.** `uv run python scripts/check_docs_links.py` → "every relative Markdown link resolves".
- [ ] **Step 6: Commit.** `git commit -m "docs(demand): document the paper-style similar-day features"`.

---

### Task 8: Full verification

- [ ] `just test` → all pass, coverage 100 %.
- [ ] `just lint`, `just mypy`, `just docs-links` → pass.
- [ ] `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt parse` → no errors; `uv run python scripts/generate_feature_views.py --check` → 0.
- [ ] `git status` clean.

## Rollout (main session, after Task 8)

1. `docker compose --project-directory /Users/hankehly/Projects/power-market-analytics exec -T thriftserver /opt/spark/bin/beeline -u 'jdbc:hive2://localhost:10000/;auth=noSasl' -n admin -e "drop table if exists pma_ml.similar_day"`.
2. Run the job from the worktree in the devcontainer (background): `docker compose --project-directory /Users/hankehly/Projects/power-market-analytics exec -T -w /workspace/.claude/worktrees/similar-day-top-k -e PYTHONPATH=/workspace/.claude/worktrees/similar-day-top-k devcontainer python scripts/fit_similar_day.py --area tokyo`.
3. `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt build --select retired_features stg_ml__similar_day+ dim_feature`.
4. Backtest (background): the same `exec` form with `python scripts/demand_backtest.py --strategy lightgbm_msm_popw_daytype_simday --area tokyo`, then `dbt build --select +fct_demand_forecast_accuracy +fct_demand_forecast_contribution +fct_demand_forecast_importance`.
5. Collect the §14 proof from the job run's params and CSVs (days by rule; share of each rank's picks from each window; largest/smallest weight per day; three example days; retrieval metrics) and the backtest's run id and MAE.
