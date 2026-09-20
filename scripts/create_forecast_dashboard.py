"""Create or update the forecast-analysis Superset dashboards.

One dashboard per forecasting task — "Spot Price Forecast Analysis" and
"Demand Forecast Analysis" — each described by a :class:`DashboardSpec` in
``DASHBOARDS`` and built (idempotently, matched by name) via the Superset
REST API, so everything is reproducible from the repo after a
``docker compose down -v``:

- seven virtual datasets per dashboard: ``<task>_forecast_analysis`` — the
  task's forecast accuracy mart joined to dim_area / dim_half_hour /
  dim_date, plus presentation columns (``run_label``, actual-value bands, day
  types, the day's Explain link) — ``<task>_forecast_explanation`` — the
  contribution fact, one row per period x component, joined to the accuracy
  mart, the selection's features ranked by mean |contribution| (the ten
  largest named, the rest one ``Other features`` group) —
  ``<task>_forecast_explanation_period`` — the same SQL, with no rows until
  the Period filter has a value — ``<task>_forecast_contribution_summary`` —
  the run-level contribution summary fact, one row per run x feature with its
  rank by mean |SHAP| — ``<task>_forecast_comparison`` — the accuracy mart
  self-joined, the Run filter's run against the Baseline filter's, both
  pinned in the SQL with Jinja — ``<task>_forecast_explanation_comparison`` —
  the contribution fact self-joined the same way on the periods both runs
  explained, one row per period x component of either run — and
  ``<task>_forecast_importance`` — the permutation feature importance fact
  joined to dim_area, one row per feature x repeat (run grain), with the
  feature's rank by ΔMAE and its mean |SHAP|
- the pins: a filter on text the dataset SQL builds (the run label, the day
  label) cannot reach the parquet scan, so the analysis, explanation, period
  and both comparison datasets also read the Run filter's value with Jinja
  and pin the fact's own ``run_id``; the explanation and period datasets pin
  the Day and the Period the same way. A pin only makes the scan smaller:
  Superset still applies its own filter to the rows that come back
- charts on three tabs, each built by its ``build_<tab>_tab`` function:
  **Accuracy** — KPI tiles (MAE, bias, RMSE, RMSE/MAE, WAPE, P90), error
  structure (bars + heatmaps + day-type slices), calibration & distribution
  (actual-value-band MAE, calibration curve, error histogram), drilldown
  (worst days, each with an Explain link that opens the Explanation tab on
  that run and day, and the 30-minute detail of forecast, actual and error) —
  **Explanation**, in three sub-tabs so that only the open one queries:
  *Day overview* — the waterfall of the selection's ten largest mean
  per-period contributions plus ``Other features``, the same groups stacked
  by period with the forecast and the actual, both relative to the base, as
  lines on the same axis, the Feature filter's feature over the day (its
  value as a line, its contribution as bars, one chart each) and the table of
  every feature; *Single period* — base / forecast / actual / net-effect
  tiles and the table of feature values and contributions, empty until a
  Period is picked; *Feature importance* — the run, not the Day: the top-20
  permutation importance bars (ΔMAE per feature when its column is shuffled
  across the run), the top-20 mean |SHAP| bars and the importance table of
  every feature — and
  **Compare** — delta KPI tiles coloured by sign (ΔMAE, ΔMAE %, Δ|bias|,
  ΔWAPE), matched coverage / days / share of days lower / median daily ΔMAE,
  diverging Better / Worse bars of ΔMAE % by segment, ΔMAE % heatmaps, daily
  ΔMAE bars, the cumulative error reduction, most-improved / most-worsened
  day tables (with the same Explain link), the explanation vs baseline
  (Δ base / Δ net effect / Δ forecast tiles, the waterfall of per-component
  contribution deltas and its table), and a three-line 30-minute detail
- the dashboard, with five single-select native filters: a required Run
  (every chart); an optional Day (cascading from Run), scoped to the
  Explanation tab's Day overview and Single period sub-tabs and the Compare
  tab's explanation-vs-baseline section; an optional Period (cascading from
  Run and Day), scoped to those two sub-tabs; a required Feature (cascading
  from Run, opening on the run's feature with the largest mean |SHAP|),
  scoped to the two feature-by-period charts; and a required Baseline scoped
  to the Compare tab. A day click in a day table cross-filters the 30-minute
  detail charts and the explanation-vs-baseline section, never the
  Explanation tab: its day comes from the Day filter alone, which the Explain
  links set. The 30-minute detail charts carry their own data-zoom slider for
  navigating the backtest window

The two dashboards share chart names (a chart is identified by its name
*within its dataset*), differing only where the quantity shows through: the
unit, number formats, and the two actual-value-level charts.

Every chart labels a feature by its expression (``LAG(demand_kwh, 2d)``), read
from ``pma_curated.dim_feature`` in the dataset SQL; the facts store the column
name. After the dashboards come the feature-value datasets and the **Feature
Catalogue** dashboard: one searchable table of every feature under its
expression, over the same dimension.

Run inside the devcontainer (needs the compose network):

    python scripts/create_forecast_dashboard.py                 # every dashboard
    python scripts/create_forecast_dashboard.py --task demand   # one of them
    python scripts/create_forecast_dashboard.py --task demand --baseline-run 0a6b8a55

Environment: ``SUPERSET_URL`` (default ``http://superset:8088``),
``SUPERSET_ADMIN_USER`` (``admin``), ``SUPERSET_ADMIN_PASSWORD`` (``admin``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import string
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests
from loguru import logger

from power_market_analytics.tasks.demand import TASK as DEMAND_TASK
from power_market_analytics.tasks.spot_price import TASK as SPOT_PRICE_TASK

SUPERSET_URL = os.environ.get("SUPERSET_URL", "http://superset:8088")
ADMIN_USER = os.environ.get("SUPERSET_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("SUPERSET_ADMIN_PASSWORD", "admin")

DATABASE_NAME = "Spark Thriftserver"

# Superset's Flask-Limiter allows 50 requests per second per client (a global
# limit); the builder's chart loops burst past it, so a 429 is retried after
# the server's Retry-After pause (1 s when absent), this many times.
RATE_LIMIT_RETRIES = 5

# The Run / Baseline filters' option text and the datasets' run_label:
# published_at | area | strategy | run_id prefix (newest first when sorted
# descending). One definition, formatted with the fact's alias ``f`` and
# dim_area's alias ``a``, so every dataset and the default-run query build the
# label identically — a filter value only matches a chart's rows when they do.
RUN_LABEL_SQL = """\
concat(
    date_format({f}.published_at, 'yyyy-MM-dd HH:mm'),
    ' | ', {a}.area_code,
    ' | ', {f}.strategy,
    ' | ', substring({f}.run_id, 1, 8)
  )"""

# The label is text the dataset SQL builds, and Spark cannot push a filter on
# built text into the parquet scan: a chart filtered on run_label alone reads
# the whole fact (20 M contribution rows on 2026-09-20). So the datasets also
# pin the run on the fact's own run_id, read off the label's tail — the label
# ends with the run id's first RUN_ID_PREFIX_LENGTH characters, and a prefix
# ``like`` does reach the scan. Superset still applies the label filter to the
# rows that come back, so a pin can only make the scan smaller; it never
# decides which rows a chart shows.
RUN_ID_PREFIX_LENGTH = 8
RUN_FILTER_JINJA = "{% set run = filter_values('run_label') %}"


def run_pin_sql(variable: str, column: str) -> str:
    """The Jinja-templated predicate pinning ``column`` to the run a filter's label names.

    Parameters
    ----------
    variable : str
        The Jinja variable holding the filter's values (``filter_values(...)``);
        the caller guards the predicate with ``{% if <variable> %}``.
    column : str
        The fact's ``run_id`` column, qualified as the SQL needs it.

    Returns
    -------
    str
        ``<column> like '<the label's last RUN_ID_PREFIX_LENGTH characters>%'``.
    """
    tail = f"{variable}[0][-{RUN_ID_PREFIX_LENGTH}:]"
    return f"{column} like '{{{{ {tail} | replace(\"'\", \"''\") }}}}%'"


# The day tables' Explain link. Superset has no click-to-navigate, so a table row
# cannot open another tab, and a cross-filter would arrive after the explanation
# dataset has ranked the selection's features: the top ten would be the run's,
# not the day's. So each day row carries a link to the dashboard itself with the
# Run and Day native filters set — the URL's ``native_filters`` parameter, a rison
# of both filters' state — and the Explanation tab's layout key as the anchor,
# which opens that tab. The form was checked by hand in Superset 6.1; ``{run}``
# and ``{day}`` stand for the two values.
# The Compare tab is built but not placed, at the researcher's word on
# 2026-09-20: it takes 29 s where Accuracy takes 4 and Explanation 7, because it
# keeps its charts on one tab and reads two self-joined datasets, and its delta
# waterfall still draws a bar per component. Set this True to put it back — the
# tab's builder, its datasets and its charts are all still here, and a rebuild
# then places them again. While it is False the Baseline filter goes with it,
# and so does the day tables' drill into the explanation-vs-baseline section.
BUILD_COMPARE_TAB = False

EXPLANATION_TAB_KEY = "TAB-1"

#: The day tables' link column, and the heading it wears (the column's own name
#: would be the heading otherwise, and ``explain_link`` is not a word for a reader).
EXPLAIN_LINK_COLUMN = "explain_link"
EXPLAIN_LINK_COLUMN_CONFIG = {EXPLAIN_LINK_COLUMN: {"customColumnName": "Explain"}}

#: The same for the column every feature table is grouped by: a table of features
#: heads that column "Feature", not ``feature_expression``.
FEATURE_COLUMN_CONFIG = {"feature_expression": {"customColumnName": "Feature"}}

EXPLAIN_LINK_RISON = (
    "(NATIVE_FILTER-run:(extraFormData:(filters:!((col:run_label,op:IN,val:!('{run}')))),"
    "filterState:(label:'{run}',validateStatus:!f,value:!('{run}')),"
    "id:NATIVE_FILTER-run,ownState:()),"
    "NATIVE_FILTER-day:(extraFormData:(filters:!((col:trade_date_label,op:IN,val:!('{day}')))),"
    "filterState:(label:'{day}',validateStatus:!f,value:!('{day}')),"
    "id:NATIVE_FILTER-day,ownState:()))"
)


# Every apostrophe of the link's rison is written ``chr(39)``, never as an
# escaped-quote literal. Superset rewrites a virtual dataset's SQL on its way to
# Spark, and that rewrite turns an escaped-quote literal into an empty string:
# the rison came out with no quotes at all, so the filters it carried matched
# nothing and every link opened the Explanation tab unfiltered. Checked against
# the running Superset (6.1) through SQL Lab, the same query two ways:
# ``concat('val:!(', chr(39), 'X', chr(39), ')')`` gives ``val:!('X')``, and the
# escaped-literal form gives ``val:!(X)``.
SQL_QUOTE = "chr(39)"


def rison_escape_sql(expression: str) -> str:
    """SQL escaping a string for the inside of a quoted rison string.

    Parameters
    ----------
    expression : str
        A SQL string expression.

    Returns
    -------
    str
        ``expression`` with ``!`` doubled, then ``'`` written ``!'`` (rison's
        escape character is ``!``, so it goes first).
    """
    return f"replace(replace({expression}, '!', '!!'), {SQL_QUOTE}, concat('!', {SQL_QUOTE}))"


def rison_literal_sql(text: str) -> list[str]:
    """``text`` as ``concat`` arguments, each apostrophe its own ``SQL_QUOTE``.

    Parameters
    ----------
    text : str
        Literal text of the rison.

    Returns
    -------
    list of str
        SQL expressions which concatenated give ``text``; empty for empty text.
    """
    pieces = []
    for i, chunk in enumerate(text.split("'")):
        if i:
            pieces.append(SQL_QUOTE)
        if chunk:
            pieces.append(f"'{chunk}'")
    return pieces


def explain_link_sql(slug: str, run_label: str, day_label: str) -> str:
    """SQL for a day row's Explain link: an HTML anchor to the Explanation tab of that
    run and day.

    Parameters
    ----------
    slug : str
        The dashboard's URL slug.
    run_label : str
        SQL string expression of the row's run label (the Run filter's value).
    day_label : str
        SQL string expression of the row's delivery day, ``yyyy-MM-dd`` (the
        Day filter's value).

    Returns
    -------
    str
        ``concat('<a href="…?native_filters=', url_encode(<the rison>),
        '#TAB-1">Explain</a>')``. The rison is a ``concat`` of
        ``EXPLAIN_LINK_RISON``'s text as SQL literals, its apostrophes as
        ``SQL_QUOTE``, around the two values, each escaped for rison.
    """
    values = {"{run}": rison_escape_sql(run_label), "{day}": rison_escape_sql(day_label)}
    pieces: list[str] = []
    for piece in re.split(r"(\{run\}|\{day\})", EXPLAIN_LINK_RISON):
        pieces.extend([values[piece]] if piece in values else rison_literal_sql(piece))
    return (
        f"concat('<a href=\"/superset/dashboard/{slug}/?native_filters=', "
        f"url_encode(concat({', '.join(pieces)})), "
        f"'#{EXPLANATION_TAB_KEY}\">Explain</a>')"
    )


# A feature's label on every chart: its expression from the feature dimension
# (LAG(demand_kwh, 2d)), or the stored name where the dimension has no row (the
# SHAP base, time_code). The facts keep the column name; only the label reads
# the dimension. One definition, formatted with the dimension's alias ``e`` and
# the column holding the name, next to the left join that brings the dimension in.
FEATURE_DIMENSION = "pma_curated.dim_feature"
FEATURE_EXPRESSION_SQL = "coalesce({e}.feature_expression, {name})"
FEATURE_JOIN_SQL = "left join " + FEATURE_DIMENSION + " {e} on {e}.feature_name = {name}"

#: The every-vintage feature-value dataset (one for both tasks); the as-of
#: datasets are ``<task>_feature_values``.
FEATURE_VALUES_ALL_DATASET = "feature_values_all"

# Shared select list of the feature-value datasets: calendar / delivery-period
# / area context around one row of fct_feature_value (a period, a feature and
# a vintage). The as-of dataset adds the issue time and keeps one vintage per
# period and feature; the every-vintage dataset keeps them all.
FEATURE_VALUES_SELECT_TEMPLATE = """\
select
  f.trade_date,
  timestampadd(MINUTE, p.start_minute_of_day, timestamp(f.trade_date)) as trade_datetime,
  year(f.trade_date) as year,
  month(f.trade_date) as month,
  f.time_code,
  p.hour_of_day,
  p.day_part,
  p.is_daytime,
  d.day_name,
  case
    when d.is_holiday then 'Holiday'
    when d.is_weekend then 'Weekend'
    else 'Weekday'
  end as day_type,
  d.is_weekend,
  d.is_holiday,
  d.is_business_day,
  a.area_code,
  a.area_name_en,
  f.feature_view,
  f.feature_name,
  {feature_expression} as feature_expression,
  f.feature_ref,
  f.feature_value,
  f.is_categorical,
  f.available_at,
  f.published_at{extra_columns}
from {source} f
join pma_curated.dim_area a on f.area_code = a.area_code
join pma_curated.dim_half_hour p on f.time_code = p.time_code
join pma_curated.dim_date d on f.trade_date = d.date_key
{feature_join}{where}
"""
#: The feature-value datasets' label and dimension join, over fct_feature_value's name.
FEATURE_VALUES_LABEL = {
    "feature_expression": FEATURE_EXPRESSION_SQL.format(e="e", name="f.feature_name"),
    "feature_join": FEATURE_JOIN_SQL.format(e="e", name="f.feature_name"),
}

# The as-of dataset: the newest vintage of every period and feature that was
# public by the task's issue time, the row Feast serves a backtest; ties on
# available_at go to the newest published, Feast's rule.
FEATURE_VALUES_ASOF_SQL_TEMPLATE = """\
with asof as (
  select
    f.*,
    {issue_time} as issue_time,
    row_number() over (
      partition by f.area_code, f.trade_date, f.time_code, f.feature_ref
      order by f.available_at desc, f.published_at desc
    ) as vintage_rank
  from pma_curated.fct_feature_value f
  where f.available_at <= {issue_time}
)
""" + FEATURE_VALUES_SELECT_TEMPLATE.format(
    extra_columns=",\n  f.issue_time",
    source="asof",
    where="\nwhere f.vintage_rank = 1",
    **FEATURE_VALUES_LABEL,
)

FEATURE_VALUES_ALL_SQL = FEATURE_VALUES_SELECT_TEMPLATE.format(
    extra_columns="", source="pma_curated.fct_feature_value", where="", **FEATURE_VALUES_LABEL
)

# (column_name, generic type, is temporal) of the every-vintage dataset, in
# select order; the as-of dataset appends issue_time.
FEATURE_VALUES_ALL_COLUMNS = (
    ("trade_date", "DATE", True),
    ("trade_datetime", "TIMESTAMP", True),
    ("year", "BIGINT", False),
    ("month", "BIGINT", False),
    ("time_code", "INT", False),
    ("hour_of_day", "INT", False),
    ("day_part", "STRING", False),
    ("is_daytime", "BOOLEAN", False),
    ("day_name", "STRING", False),
    ("day_type", "STRING", False),
    ("is_weekend", "BOOLEAN", False),
    ("is_holiday", "BOOLEAN", False),
    ("is_business_day", "BOOLEAN", False),
    ("area_code", "STRING", False),
    ("area_name_en", "STRING", False),
    ("feature_view", "STRING", False),
    ("feature_name", "STRING", False),
    ("feature_expression", "STRING", False),
    ("feature_ref", "STRING", False),
    ("feature_value", "DOUBLE", False),
    ("is_categorical", "BOOLEAN", False),
    ("available_at", "TIMESTAMP", True),
    ("published_at", "TIMESTAMP", True),
)
FEATURE_VALUES_ASOF_COLUMNS = (*FEATURE_VALUES_ALL_COLUMNS, ("issue_time", "TIMESTAMP", True))


def issue_time_sql(issue_offset: pd.Timedelta) -> str:
    """The task's issue time of a delivery day as SQL over ``f.trade_date``.

    Parameters
    ----------
    issue_offset : pandas.Timedelta
        The issue time relative to D 00:00, ``TaskSpec.issue_offset`` (a whole
        number of minutes; 09:30 on D-1 is -870).

    Returns
    -------
    str
        ``timestampadd(MINUTE, <minutes>, timestamp(f.trade_date))``.

    Raises
    ------
    ValueError
        If the offset is not a whole number of minutes.
    """
    minutes = issue_offset / pd.Timedelta(minutes=1)
    if minutes != int(minutes):
        raise ValueError(f"issue offset {issue_offset} is not a whole number of minutes")
    return f"timestampadd(MINUTE, {int(minutes)}, timestamp(f.trade_date))"


# Shared skeleton of every task's virtual dataset: calendar / delivery-period
# / area context, the run label, then the task's value and error columns
# (in the task's display unit) and the unit-free percentage errors. The Run
# filter's run is pinned on run_id when the filter has a value (see
# RUN_ID_PREFIX_LENGTH); the Run and Baseline filters' own option queries carry
# no value, so they still list every run. explain_link is the Worst days
# table's link to the Explanation tab of the row's run and day (explain_link_sql).
DATASET_SQL_TEMPLATE = """\
{run_filter_jinja}
select
  f.date_key,
  f.trade_datetime,
  year(f.date_key) as year,
  month(f.date_key) as month,
  f.time_code,
  p.hour_of_day,
  p.day_part,
  {day_part_hours_sql} as day_part_hours,
  p.is_daytime,
  d.fiscal_year,
  d.day_name,
  concat(d.day_of_week_iso, ' ', substring(d.day_name, 1, 3)) as day_of_week,
  case
    when d.is_holiday then 'Holiday'
    when d.is_weekend then 'Weekend'
    else 'Weekday'
  end as day_type,
  {day_type_share_sql} as day_type_share,
  d.is_weekend,
  d.is_holiday,
  d.is_business_day,
  a.area_code,
  a.area_name_en,
  f.run_id,
  {run_label_sql} as run_label,
  {run_label_sql} as baseline_run_label,
  f.strategy,
  f.published_at,
  f.forecast_issued_ts,
  f.horizon_hours,
  {explain_link_sql} as explain_link,
{value_columns_sql}
  f.pct_error,
  f.abs_pct_error
from {accuracy_table} f
join pma_curated.dim_area a on f.area_key = a.area_key
join pma_curated.dim_half_hour p on f.time_code = p.time_code
join pma_curated.dim_date d on f.date_key = d.date_key
{day_part_hours_join_sql}
{{% if run %}}where {run_pin}{{% endif %}}
"""

# (column_name, generic type, is temporal) for the shared head of the select
# list — kept in sync with DATASET_SQL_TEMPLATE so reruns can override stale
# column metadata after a SQL change. ``baseline_run_label`` repeats the label
# so the Baseline native filter can list the runs from this dataset.
COMMON_DATASET_COLUMNS = (
    ("date_key", "DATE", True),
    ("trade_datetime", "TIMESTAMP", True),
    ("year", "BIGINT", False),
    ("month", "BIGINT", False),
    ("time_code", "INT", False),
    ("hour_of_day", "INT", False),
    ("day_part", "STRING", False),
    ("day_part_hours", "STRING", False),
    ("is_daytime", "BOOLEAN", False),
    ("fiscal_year", "INT", False),
    ("day_name", "STRING", False),
    ("day_of_week", "STRING", False),
    ("day_type", "STRING", False),
    ("day_type_share", "STRING", False),
    ("is_weekend", "BOOLEAN", False),
    ("is_holiday", "BOOLEAN", False),
    ("is_business_day", "BOOLEAN", False),
    ("area_code", "STRING", False),
    ("area_name_en", "STRING", False),
    ("run_id", "STRING", False),
    ("run_label", "STRING", False),
    ("baseline_run_label", "STRING", False),
    ("strategy", "STRING", False),
    ("published_at", "TIMESTAMP", True),
    ("forecast_issued_ts", "TIMESTAMP", True),
    ("horizon_hours", "DOUBLE", False),
    ("explain_link", "STRING", False),
)

# The Explanation tab's overview charts draw the TOP_COMPONENTS features with the
# largest mean |contribution| in the selection and fold the rest into one
# OTHER_FEATURES group: a preset has a hundred features (e212: 104), and a bar or
# a stacked series per feature reads as noise. The All features table lists
# every one.
TOP_COMPONENTS = 10
OTHER_FEATURES = "Other features"

# A chart mark (a bar, a series, a legend entry) cannot carry a 130-character
# expression, so marks use the expression's head and tail around an ellipsis;
# the tail keeps what tells siblings apart (``…, rank=2) / 2``). Tables show the
# full expression, and a rank number beside both ties a mark to its table row.
SHORT_LABEL_HEAD = 34
SHORT_LABEL_TAIL = 21


def short_label_sql(expression: str) -> str:
    """SQL shortening ``expression`` for a chart mark: its head, an ellipsis, its tail.

    Parameters
    ----------
    expression : str
        A SQL string expression (the feature's expression).

    Returns
    -------
    str
        A ``case`` expression: ``expression`` as it is up to
        ``SHORT_LABEL_HEAD + SHORT_LABEL_TAIL + 1`` characters, else shortened
        to that length.
    """
    limit = SHORT_LABEL_HEAD + SHORT_LABEL_TAIL + 1
    return (
        f"case when length({expression}) > {limit} "
        f"then concat(substring({expression}, 1, {SHORT_LABEL_HEAD}), '…', "
        f"substring({expression}, -{SHORT_LABEL_TAIL})) else {expression} end"
    )


# The Feature filter's option text: the feature's rank by mean |SHAP| within its
# run, zero-padded so the list sorts by it and opens on the run's first feature,
# then the expression. One definition, formatted with the contribution summary's
# alias ``s`` and the expression: the filter lists the summary dataset's column
# and filters the explanation dataset's, so both must build the same text.
FEATURE_PICK_SQL = "concat(lpad(cast({s}.feature_rank as string), 3, '0'), ' ', {expression})"

# A bar per day part reads as a time of day, so the bar carries the hours it
# covers — nobody should have to look up what "Daytime" means. The range comes
# from dim_half_hour itself, through a four-row join, so it stays right if the
# day parts are ever redrawn and does not narrow to whatever periods a run
# happens to have scored.
DAY_PART_HOURS_JOIN_SQL = """\
join (
  select
    day_part,
    min(period_start_time) as day_part_start,
    max(period_end_time) as day_part_end
  from pma_curated.dim_half_hour
  group by day_part
) h on h.day_part = {p}.day_part"""
# The hours lead the label so the bars run in clock order: the chart sorts by
# its x axis, and the day part's name would sort Daytime, Evening, Morning,
# Overnight — a time of day out of time order.
DAY_PART_HOURS_SQL = "concat(h.day_part_start, '–', h.day_part_end, ' ', {p}.day_part)"


def day_type_share_sql(date_alias: str, run_column: str | None = None) -> str:
    """The day type with its share of the selection's periods, as one bar label.

    A bar per day type invites reading the three as equals, when a run is about
    two thirds weekday and under a tenth holiday, so the share rides on the
    label: chasing a holiday gain then shows its own weight.

    Parameters
    ----------
    date_alias : str
        Alias of ``dim_date`` in the query.
    run_column : str, optional
        Column the share is taken within, for a dataset holding several runs.
        None when the dataset already holds one selection.

    Returns
    -------
    str
        ``Weekday (65% of periods)`` as a SQL expression. The share is of the
        rows the dataset yields, before any filter Superset applies outside it.
    """
    case = (
        f"case when {date_alias}.is_holiday then 'Holiday' "
        f"when {date_alias}.is_weekend then 'Weekend' else 'Weekday' end"
    )
    within = f"partition by {run_column}, {case}" if run_column else f"partition by {case}"
    total = f"partition by {run_column}" if run_column else ""
    # cast(100 as double): a decimal literal would truncate the division
    share = f"cast(100 as double) * count(*) over ({within}) / count(*) over ({total})"
    return f"concat({case}, ' (', cast(cast(round({share}) as int) as string), '% of periods)')"


# Shared skeleton of every task's explanation dataset: the contribution fact
# (one row per period x component) with calendar / period / area context, the
# same run_label construction as the analysis dataset (so the Run filter selects
# both), then the task's value block — the contribution and, from the accuracy
# mart, the period's forecast and actual (repeated on each component row:
# AVG-only metrics).
#
# The Run, Day and Period filters are pinned inside the SQL (``pinned``), on the
# fact's own columns, so the scan is pruned (see RUN_ID_PREFIX_LENGTH); without
# a Run value it reads everything. ``ranked`` orders the selection's features by
# mean |contribution| — the selection is exactly the pinned rows, which is why
# the Explanation tab takes its day from the Day filter alone and not from a
# cross-filter, which would arrive after the ranking. ``grouped`` names each
# feature's group: its short label within the top TOP_COMPONENTS (with its rank
# appended should two shorten to the same text), else OTHER_FEATURES.
# component_group is the stacked chart's series, without a rank, so a feature
# keeps its colour when its rank changes; component_group_label carries the rank
# as a sortable prefix for the waterfall, which sorts by label.
#
# The run is pinned twice over, and here that is not belt and braces. The prefix
# ``like`` is what reaches the parquet scan, but ``ranked`` runs on whatever the
# predicate admits, so two runs sharing the label's eight characters would have
# their top ten taken over the pair — and Superset's own filter on the label
# arrives too late to undo it. Measured by widening the pin until it admitted 7
# runs: none of the true top ten kept its rank. The exact label, which needs the
# area join, restores the rule the other datasets keep — a pin only makes the
# scan smaller.
#
# ``$period_gate`` is empty here and, for the period dataset, a predicate that
# leaves no rows unless the Period filter has a value: Superset cannot hide a
# chart on a condition, so the Single period charts show "No data" instead.
# A string.Template ($name) because the SQL carries Jinja braces.
EXPLANATION_DATASET_SQL_TEMPLATE = string.Template("""\
{% set run = filter_values('run_label') %}
{% set day = filter_values('trade_date_label') %}
{% set period = filter_values('period_label') %}
with pinned as (
select c.*
from $contribution_table c
join pma_curated.dim_area a on c.area_key = a.area_key
where {% if run %}$run_pin and $run_label_sql = '{{ run[0] | replace("'", "''") }}'{% else %}1 = 1{% endif %}
  {% if day %}and c.date_key = date '{{ day[0] | replace("'", "''") }}'{% endif %}
  {% if period %}and c.time_code = {{ period[0][:2] | int }}{% endif %}$period_gate
),
ranked as (
select
  k.component,
  row_number() over (order by avg(abs(k.$contribution_fact_col)) desc, k.component) as selection_rank
from pinned k
where not k.is_base
group by k.component
),
labelled as (
select
  r.component,
  r.selection_rank,
  $short_label_sql as short_label
from ranked r
$ranked_feature_join_sql
),
grouped as (
select
  l.component,
  l.selection_rank,
  case
    when l.selection_rank > $top then '$other'
    when count(*) over (partition by l.selection_rank <= $top, l.short_label) > 1
      then concat(l.short_label, ' #', cast(l.selection_rank as string))
    else l.short_label
  end as component_group
from labelled l
)
select
  c.date_key,
  date_format(c.date_key, 'yyyy-MM-dd') as trade_date_label,
  c.trade_datetime,
  c.time_code,
  concat(lpad(cast(c.time_code as string), 2, '0'), ' ', p.period_start_time, '–', p.period_end_time) as period_label,
  p.hour_of_day,
  p.day_part,
  d.day_name,
  case
    when d.is_holiday then 'Holiday'
    when d.is_weekend then 'Weekend'
    else 'Weekday'
  end as day_type,
  a.area_code,
  a.area_name_en,
  c.run_id,
  $run_label_sql as run_label,
  c.strategy,
  c.published_at,
  c.component,
  $feature_expression_sql as feature_expression,
  c.component_order,
  c.is_base,
  g.selection_rank,
  case when c.is_base then 'base' else g.component_group end as component_group,
  case
    when c.is_base then '00 base'
    when g.selection_rank > $top then '$other_order $other'
    else concat(lpad(cast(g.selection_rank as string), 2, '0'), ' ', g.component_group)
  end as component_group_label,
  $feature_pick_sql as feature_pick,
  c.feature_value,
$explanation_value_columns_sql
from pinned c
join pma_curated.dim_area a on c.area_key = a.area_key
join pma_curated.dim_half_hour p on c.time_code = p.time_code
join pma_curated.dim_date d on c.date_key = d.date_key
left join grouped g on g.component = c.component
$feature_join_sql
left join $summary_table s
  on s.run_id = c.run_id
  and s.area_key = c.area_key
  and s.component = c.component
left join $accuracy_table f
  on c.run_id = f.run_id
  and c.date_key = f.date_key
  and c.time_code = f.time_code
  and c.area_key = f.area_key
""")

#: The period dataset's extra predicate of ``pinned``: no rows without a Period.
PERIOD_GATE_SQL = "\n  {% if not period %}and 1 = 0{% endif %}"

COMMON_EXPLANATION_COLUMNS = (
    ("date_key", "DATE", True),
    ("trade_date_label", "STRING", False),
    ("trade_datetime", "TIMESTAMP", True),
    ("time_code", "INT", False),
    ("period_label", "STRING", False),
    ("hour_of_day", "INT", False),
    ("day_part", "STRING", False),
    ("day_name", "STRING", False),
    ("day_type", "STRING", False),
    ("area_code", "STRING", False),
    ("area_name_en", "STRING", False),
    ("run_id", "STRING", False),
    ("run_label", "STRING", False),
    ("strategy", "STRING", False),
    ("published_at", "TIMESTAMP", True),
    ("component", "STRING", False),
    ("feature_expression", "STRING", False),
    ("component_order", "INT", False),
    ("is_base", "BOOLEAN", False),
    ("selection_rank", "INT", False),
    ("component_group", "STRING", False),
    ("component_group_label", "STRING", False),
    ("feature_pick", "STRING", False),
    ("feature_value", "DOUBLE", False),
)

# Shared skeleton of every task's importance dataset: the permutation feature
# importance fact (one row per run x feature x repeat) with the run label and
# area context. ``ranked`` orders each run's features by ΔMAE, largest first, so
# the bars can keep the top ones with a plain filter, and feature_short carries
# that rank before the short label, so no two bars share a name. The run-level
# contribution summary brings the feature's mean |SHAP| (the same on every
# repeat's row). No delivery-day axis: importance describes a run.
IMPORTANCE_DATASET_SQL_TEMPLATE = """\
with ranked as (
select
  i.run_id,
  i.area_key,
  i.feature,
  row_number() over (
    partition by i.run_id, i.area_key
    order by avg(i.{permuted_mae_fact_col}) - avg(i.{mae_fact_col}) desc, i.feature
  ) as importance_rank
from {importance_table} i
group by i.run_id, i.area_key, i.feature
)
select
  a.area_code,
  a.area_name_en,
  i.run_id,
  {run_label_sql} as run_label,
  i.strategy,
  i.published_at,
  i.feature,
  {feature_expression_sql} as feature_expression,
  i.feature_order,
  r.importance_rank,
  concat(lpad(cast(r.importance_rank as string), 3, '0'), ' ', {short_label_sql}) as feature_short,
  i.repeat_index,
  i.n_periods,
{importance_value_columns_sql},
  {mean_abs_contribution_sql}
from {importance_table} i
join ranked r
  on r.run_id = i.run_id
  and r.area_key = i.area_key
  and r.feature = i.feature
join pma_curated.dim_area a on i.area_key = a.area_key
{feature_join_sql}
left join {summary_table} s
  on s.run_id = i.run_id
  and s.area_key = i.area_key
  and s.component = i.feature
"""

COMMON_IMPORTANCE_COLUMNS = (
    ("area_code", "STRING", False),
    ("area_name_en", "STRING", False),
    ("run_id", "STRING", False),
    ("run_label", "STRING", False),
    ("strategy", "STRING", False),
    ("published_at", "TIMESTAMP", True),
    ("feature", "STRING", False),
    ("feature_expression", "STRING", False),
    ("feature_order", "INT", False),
    ("importance_rank", "INT", False),
    ("feature_short", "STRING", False),
    ("repeat_index", "INT", False),
    ("n_periods", "INT", False),
)

# Shared skeleton of every task's summary dataset: the run-level contribution
# summary (one row per run x feature, the base row left out) with the run label.
# It feeds the mean |SHAP| bars and the Feature filter's options; feature_short
# and feature_pick both start with the feature's rank by mean |SHAP|.
SUMMARY_DATASET_SQL_TEMPLATE = """\
select
  a.area_code,
  a.area_name_en,
  s.run_id,
  {run_label_sql} as run_label,
  s.strategy,
  s.published_at,
  s.component,
  {feature_expression_sql} as feature_expression,
  s.feature_rank,
  concat(lpad(cast(s.feature_rank as string), 3, '0'), ' ', {short_label_sql}) as feature_short,
  {feature_pick_sql} as feature_pick,
  s.n_periods,
{summary_value_columns_sql}
from {summary_table} s
join pma_curated.dim_area a on s.area_key = a.area_key
{feature_join_sql}
where not s.is_base
"""

COMMON_SUMMARY_COLUMNS = (
    ("area_code", "STRING", False),
    ("area_name_en", "STRING", False),
    ("run_id", "STRING", False),
    ("run_label", "STRING", False),
    ("strategy", "STRING", False),
    ("published_at", "TIMESTAMP", True),
    ("component", "STRING", False),
    ("feature_expression", "STRING", False),
    ("feature_rank", "INT", False),
    ("feature_short", "STRING", False),
    ("feature_pick", "STRING", False),
    ("n_periods", "BIGINT", False),
)

# Shared skeleton of every task's comparison dataset: the accuracy mart
# self-joined — the Run filter's run (candidate) against the Baseline filter's
# run — on delivery day x time code x area, one row per matched period.
#
# Superset renders this as Jinja (ENABLE_TEMPLATE_PROCESSING) per chart query:
# both runs are pinned inside the SQL from the native filters' values
# (filter_values), so the join touches two runs; Superset also applies the
# same two filters as an outer WHERE on run_label / baseline_run_label, which
# the rows satisfy. Without a value on either side the SQL yields no rows
# ("No data" on every chart rather than a misleading zero delta).
# candidate_periods = the candidate's count before the join (for the coverage
# tile); the daily_* columns are window averages over the day's matched
# periods, one constant per day, so tiles and tables can aggregate per day.
# is_first_matched_period marks one row per matched day, so a per-day metric
# (the median daily ΔMAE) can aggregate over days rather than over period rows.
# explain_link is the day tables' link to the Explanation tab of the candidate
# run and the row's day (explain_link_sql).
# A string.Template ($name) because the SQL carries Jinja braces.
COMPARISON_DATASET_SQL_TEMPLATE = string.Template("""\
{% set candidate = filter_values('run_label') %}
{% set baseline = filter_values('baseline_run_label') %}
with runs as (
select
  f.*,
  a.area_code,
  a.area_name_en,
  $run_label_sql as run_label
from $accuracy_table f
join pma_curated.dim_area a on f.area_key = a.area_key
),
candidate as (
select *, count(*) over () as candidate_periods
from runs
where {% if candidate %}run_label = '{{ candidate[0] | replace("'", "''") }}' and $candidate_pin{% else %}1 = 0{% endif %}
),
baseline as (
select *
from runs
where {% if baseline %}run_label = '{{ baseline[0] | replace("'", "''") }}' and $baseline_pin{% else %}1 = 0{% endif %}
),
matched as (
select
  c.date_key,
  c.trade_datetime,
  c.time_code,
  c.area_key,
  c.area_code,
  c.area_name_en,
  c.run_id,
  c.run_label,
  c.strategy,
  c.published_at,
  b.run_id as baseline_run_id,
  b.run_label as baseline_run_label,
  b.strategy as baseline_strategy,
  c.candidate_periods,
$comparison_value_columns_sql
from candidate c
join baseline b
  on b.date_key = c.date_key
  and b.time_code = c.time_code
  and b.area_key = c.area_key
)
select
  m.date_key,
  m.trade_datetime,
  year(m.date_key) as year,
  month(m.date_key) as month,
  m.time_code,
  p.hour_of_day,
  p.day_part,
  $day_part_hours_sql as day_part_hours,
  d.day_name,
  concat(d.day_of_week_iso, ' ', substring(d.day_name, 1, 3)) as day_of_week,
  case
    when d.is_holiday then 'Holiday'
    when d.is_weekend then 'Weekend'
    else 'Weekday'
  end as day_type,
  $day_type_share_sql as day_type_share,
  case when d.is_holiday then d.holiday_name_ja else '' end as holiday_name_ja,
  m.area_code,
  m.area_name_en,
  m.run_id,
  m.run_label,
  m.strategy,
  m.published_at,
  m.baseline_run_id,
  m.baseline_run_label,
  m.baseline_strategy,
  m.candidate_periods,
  $explain_link_sql as explain_link,
$value_select_sql
  avg(m.$abs_error_col) over (partition by m.date_key) as $daily_abs_error_col,
  avg(m.$baseline_abs_error_col) over (partition by m.date_key) as $daily_baseline_abs_error_col,
  avg(m.$delta_abs_error_col) over (partition by m.date_key) as $daily_delta_abs_error_col,
  row_number() over (partition by m.date_key order by m.time_code) = 1 as is_first_matched_period
from matched m
join pma_curated.dim_half_hour p on m.time_code = p.time_code
join pma_curated.dim_date d on m.date_key = d.date_key
$day_part_hours_join_sql
""")

COMMON_COMPARISON_COLUMNS = (
    ("date_key", "DATE", True),
    ("trade_datetime", "TIMESTAMP", True),
    ("year", "BIGINT", False),
    ("month", "BIGINT", False),
    ("time_code", "INT", False),
    ("hour_of_day", "INT", False),
    ("day_part", "STRING", False),
    ("day_part_hours", "STRING", False),
    ("day_name", "STRING", False),
    ("day_of_week", "STRING", False),
    ("day_type", "STRING", False),
    ("day_type_share", "STRING", False),
    ("holiday_name_ja", "STRING", False),
    ("area_code", "STRING", False),
    ("area_name_en", "STRING", False),
    ("run_id", "STRING", False),
    ("run_label", "STRING", False),
    ("strategy", "STRING", False),
    ("published_at", "TIMESTAMP", True),
    ("baseline_run_id", "STRING", False),
    ("baseline_run_label", "STRING", False),
    ("baseline_strategy", "STRING", False),
    ("candidate_periods", "BIGINT", False),
    ("explain_link", "STRING", False),
)

# Shared skeleton of every task's explanation-comparison dataset: the
# contribution fact self-joined — the Run filter's run (candidate) against the
# Baseline filter's run — on the periods both runs explained (one base row per
# period per run), one row per period x component of either run.
#
# Both runs are pinned by Jinja like the comparison dataset. A component only
# one run has still gets a row per period (the cross join with the union of
# both runs' components), with that run's contribution and the other side's
# 0 — a feature a model lacks contributes nothing — so its whole contribution
# is the delta; feature values stay null where the run lacks the feature.
# component_order keeps the candidate's feature order and pushes baseline-only
# components after it (100 + their order; the label pads to three digits).
# The filters' values are echoed as constant columns because Superset applies
# them as an outer WHERE on run_label / baseline_run_label, and no run column
# survives the joins for a component the candidate lacks.
EXPLANATION_COMPARISON_DATASET_SQL_TEMPLATE = string.Template("""\
{% set candidate = filter_values('run_label') %}
{% set baseline = filter_values('baseline_run_label') %}
with runs as (
select
  c.*,
  $run_label_sql as run_label
from $contribution_table c
join pma_curated.dim_area a on c.area_key = a.area_key
),
candidate as (
select *
from runs
where {% if candidate %}run_label = '{{ candidate[0] | replace("'", "''") }}' and $candidate_pin{% else %}1 = 0{% endif %}
),
baseline as (
select *
from runs
where {% if baseline %}run_label = '{{ baseline[0] | replace("'", "''") }}' and $baseline_pin{% else %}1 = 0{% endif %}
),
periods as (
select c.date_key, c.trade_datetime, c.time_code, c.area_key, c.run_id, b.run_id as baseline_run_id
from candidate c
join baseline b
  on b.date_key = c.date_key
  and b.time_code = c.time_code
  and b.area_key = c.area_key
where c.is_base and b.is_base
),
components as (
select distinct component from candidate
union
select distinct component from baseline
),
matched as (
select
  p.date_key,
  p.trade_datetime,
  p.time_code,
  p.area_key,
  k.component,
  coalesce(c.component_order, 100 + b.component_order) as component_order,
  coalesce(c.is_base, b.is_base) as is_base,
  c.feature_value,
  b.feature_value as baseline_feature_value,
$explanation_comparison_value_columns_sql
from periods p
cross join components k
left join candidate c
  on c.date_key = p.date_key
  and c.time_code = p.time_code
  and c.area_key = p.area_key
  and c.component = k.component
left join baseline b
  on b.date_key = p.date_key
  and b.time_code = p.time_code
  and b.area_key = p.area_key
  and b.component = k.component
left join $accuracy_table fc
  on fc.run_id = p.run_id
  and fc.date_key = p.date_key
  and fc.time_code = p.time_code
  and fc.area_key = p.area_key
left join $accuracy_table fb
  on fb.run_id = p.baseline_run_id
  and fb.date_key = p.date_key
  and fb.time_code = p.time_code
  and fb.area_key = p.area_key
)
select
  m.date_key,
  date_format(m.date_key, 'yyyy-MM-dd') as trade_date_label,
  m.trade_datetime,
  m.time_code,
  p.hour_of_day,
  p.day_part,
  d.day_name,
  case
    when d.is_holiday then 'Holiday'
    when d.is_weekend then 'Weekend'
    else 'Weekday'
  end as day_type,
  a.area_code,
  a.area_name_en,
  {% if candidate %}'{{ candidate[0] | replace("'", "''") }}'{% else %}cast(null as string){% endif %} as run_label,
  {% if baseline %}'{{ baseline[0] | replace("'", "''") }}'{% else %}cast(null as string){% endif %} as baseline_run_label,
  m.component,
  $feature_expression_sql as feature_expression,
  m.component_order,
  concat(lpad(cast(m.component_order as string), 3, '0'), ' ', $feature_expression_sql) as component_label,
  m.is_base,
  m.feature_value,
  m.baseline_feature_value,
$value_select_sql
from matched m
join pma_curated.dim_area a on m.area_key = a.area_key
join pma_curated.dim_half_hour p on m.time_code = p.time_code
join pma_curated.dim_date d on m.date_key = d.date_key
$feature_join_sql
""")

COMMON_EXPLANATION_COMPARISON_COLUMNS = (
    ("date_key", "DATE", True),
    ("trade_date_label", "STRING", False),
    ("trade_datetime", "TIMESTAMP", True),
    ("time_code", "INT", False),
    ("hour_of_day", "INT", False),
    ("day_part", "STRING", False),
    ("day_name", "STRING", False),
    ("day_type", "STRING", False),
    ("area_code", "STRING", False),
    ("area_name_en", "STRING", False),
    ("run_label", "STRING", False),
    ("baseline_run_label", "STRING", False),
    ("component", "STRING", False),
    ("feature_expression", "STRING", False),
    ("component_order", "INT", False),
    ("component_label", "STRING", False),
    ("is_base", "BOOLEAN", False),
    ("feature_value", "DOUBLE", False),
    ("baseline_feature_value", "DOUBLE", False),
)


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def avg_metric(column: str, label: str) -> dict:
    """Ad-hoc AVG metric definition for chart params.

    Parameters
    ----------
    column : str
        Dataset column to average.
    label : str
        Display label for the series / cell values.

    Returns
    -------
    dict
    """
    return {
        "expressionType": "SIMPLE",
        "column": {"column_name": column, "type": "DOUBLE"},
        "aggregate": "AVG",
        "label": label,
        "optionName": f"metric_avg_{column}",
    }


def sql_metric(expression: str, label: str, option_name: str | None = None) -> dict:
    """Ad-hoc SQL-expression metric definition for chart params.

    Parameters
    ----------
    expression : str
        Aggregate Spark SQL expression, e.g. ``sqrt(avg(power(x, 2)))``.
    label : str
        Display label.
    option_name : str, optional
        Identifier suffix (``metric_<option_name>``); defaults to the label's
        slug, which a label without ASCII letters — ``ΔMAE %`` — cannot supply
        uniquely.

    Returns
    -------
    dict
    """
    return {
        "expressionType": "SQL",
        "sqlExpression": expression,
        "label": label,
        "optionName": f"metric_{option_name or _slug(label)}",
    }


@dataclass(frozen=True)
class DashboardSpec:
    """Everything that distinguishes one task's forecast-analysis dashboard.

    Parameters
    ----------
    task : str
        Registry key and ``--task`` value (the forecasting task name).
    dataset_name, dashboard_title, dashboard_slug : str
        Superset names; charts are matched by name *within* the dataset.
    accuracy_table : str
        Fully qualified forecast accuracy mart the dataset reads.
    unit : str
        Display unit of the forecast quantity (labels, subheaders, axes).
    forecast_col, actual_col, error_col, abs_error_col : str
        *Dataset* columns for the forecast, the actual, the signed error and
        the absolute error — the mart columns as-is, or rescaled in
        ``value_columns_sql`` when the display unit differs from the mart's.
    value_columns_sql : str
        Task-specific block of the dataset select list (the forecast, the
        actual, the rounded actual for the calibration curve, the actual
        band, the signed error and the absolute error), already indented two
        spaces, every line comma-terminated.
    value_columns : tuple of (str, str, bool)
        Column metadata for that block, in select order.
    band_col, band_chart_title : str
        Actual-value band column and the title of its MAE bar chart.
    calibration_x_col, calibration_chart_title, calibration_x_title : str
        Rounded-actual column, chart title and x-axis title of the
        calibration curve.
    number_format : str
        d3 format for precise values (KPI tiles, table MAE/RMSE/bias).
    axis_format : str
        d3 format for MAE bar-chart y axes.
    calibration_x_format, calibration_y_format : str
        d3 formats for the calibration curve's axes (``~g`` is Superset's
        x-axis default; large values need an explicit format such as ``,.0f``
        or ``SMART_NUMBER`` to avoid ``1e+7``).
    worst_days_max_format : str
        d3 format for the worst-days table's ``Max |error|`` / ``Max actual``.
    explanation_dataset_name, contribution_table : str
        The explanation dataset and the contribution fact it reads.
    contribution_col : str
        The *dataset* column of a component's contribution (rescaled like the
        value columns).
    contribution_format : str
        Signed d3 format for contributions.
    explanation_value_columns_sql, explanation_value_columns : str, tuple of (str, str, bool)
        The value block — contribution, forecast, actual — two-space
        indented, the last line without a trailing comma.
    contribution_fact_col : str
        The contribution fact's own contribution column (``TaskSpec.contribution_col``),
        which the explanation dataset ranks the selection's features on.
    explanation_period_dataset_name : str
        The Single period sub-tab's dataset: the explanation SQL, with no rows
        unless the Period filter has a value.
    summary_dataset_name, summary_table : str
        The summary dataset and the run-level contribution summary fact it reads.
    summary_value_columns_sql, summary_value_columns : str, tuple of (str, str, bool)
        The summary dataset's value block — the mean contribution, then the mean
        absolute contribution (mean |SHAP|), rescaled like the value columns —
        two-space indented, the last line without a trailing comma, and its
        column metadata. The importance dataset selects the block's last line too.
    importance_mae_fact_col, importance_permuted_mae_fact_col : str
        The importance fact's own MAE columns (``TaskSpec.mae_col`` /
        ``permuted_mae_col``), which the importance dataset ranks the features on.
    comparison_dataset_name : str
        The comparison dataset (the accuracy mart self-joined, candidate vs baseline).
    comparison_value_columns_sql : str
        The ``matched`` CTE's value block — the candidate's (``c.``) and the
        baseline's (``b.``) forecast, the actual, the actual band, both signed
        errors, both absolute errors and their difference, in the display
        unit, named ``forecast_col`` / ``baseline_forecast_col`` / … /
        ``delta_abs_error_col``; two-space indented, last line without a
        trailing comma.
    comparison_value_columns : tuple of (str, str, bool)
        Column metadata for that block, in select order.
    importance_dataset_name, importance_table : str
        The importance dataset and the permutation-importance fact it reads.
    importance_mae_col, importance_permuted_mae_col : str
        The *dataset* columns of the run's MAE and of the MAE after shuffling
        a feature (rescaled like the value columns).
    importance_value_columns_sql, importance_value_columns : str, tuple of (str, str, bool)
        The importance dataset's value block and its column metadata.
    feature_values_dataset_name : str
        The task's as-of feature-value dataset (``<task>_feature_values``).
    issue_time_sql : str
        The task's issue time as SQL over ``f.trade_date`` (``issue_time_sql``),
        the as-of dataset's cutoff.
        The two-line value block — MAE, permuted MAE — two-space indented,
        the last line without a trailing comma, and its column metadata.
    """

    task: str
    dataset_name: str
    dashboard_title: str
    dashboard_slug: str
    accuracy_table: str
    unit: str
    forecast_col: str
    actual_col: str
    error_col: str
    abs_error_col: str
    value_columns_sql: str
    value_columns: tuple[tuple[str, str, bool], ...]
    band_col: str
    band_chart_title: str
    calibration_x_col: str
    calibration_chart_title: str
    calibration_x_title: str
    number_format: str
    axis_format: str
    calibration_x_format: str
    calibration_y_format: str
    worst_days_max_format: str
    explanation_dataset_name: str
    contribution_table: str
    contribution_col: str
    contribution_format: str
    explanation_value_columns_sql: str
    explanation_value_columns: tuple[tuple[str, str, bool], ...]
    comparison_dataset_name: str
    comparison_value_columns_sql: str
    comparison_value_columns: tuple[tuple[str, str, bool], ...]
    explanation_comparison_dataset_name: str
    explanation_comparison_value_columns_sql: str
    explanation_comparison_value_columns: tuple[tuple[str, str, bool], ...]
    importance_dataset_name: str
    importance_table: str
    importance_mae_col: str
    importance_permuted_mae_col: str
    importance_value_columns_sql: str
    importance_value_columns: tuple[tuple[str, str, bool], ...]
    feature_values_dataset_name: str
    issue_time_sql: str
    contribution_fact_col: str
    explanation_period_dataset_name: str
    summary_dataset_name: str
    summary_table: str
    summary_value_columns_sql: str
    summary_value_columns: tuple[tuple[str, str, bool], ...]
    importance_mae_fact_col: str
    importance_permuted_mae_fact_col: str

    @property
    def feature_values_sql(self) -> str:
        """The as-of feature-value dataset's SQL: one vintage per period and feature,
        the newest public by the task's issue time."""
        return FEATURE_VALUES_ASOF_SQL_TEMPLATE.format(issue_time=self.issue_time_sql)

    @property
    def dataset_sql(self) -> str:
        """The virtual dataset's SQL: the shared template around this task's columns."""
        return DATASET_SQL_TEMPLATE.format(
            run_filter_jinja=RUN_FILTER_JINJA,
            value_columns_sql=self.value_columns_sql,
            accuracy_table=self.accuracy_table,
            run_label_sql=RUN_LABEL_SQL.format(f="f", a="a"),
            run_pin=run_pin_sql("run", "f.run_id"),
            day_part_hours_sql=DAY_PART_HOURS_SQL.format(p="p"),
            day_part_hours_join_sql=DAY_PART_HOURS_JOIN_SQL.format(p="p"),
            day_type_share_sql=day_type_share_sql("d", run_column="f.run_id"),
            explain_link_sql=explain_link_sql(
                self.dashboard_slug,
                RUN_LABEL_SQL.format(f="f", a="a"),
                "date_format(f.date_key, 'yyyy-MM-dd')",
            ),
        )

    @property
    def dataset_columns(self) -> list[tuple[str, str, bool]]:
        """(column_name, generic type, is temporal) for every dataset column, in select order."""
        return [
            *COMMON_DATASET_COLUMNS,
            *self.value_columns,
            ("pct_error", "DOUBLE", False),
            ("abs_pct_error", "DOUBLE", False),
        ]

    @property
    def signed_number_format(self) -> str:
        """``number_format`` with an explicit sign (bias)."""
        return "+" + self.number_format

    @property
    def mae_metric(self) -> dict:
        return avg_metric(self.abs_error_col, f"MAE ({self.unit})")

    @property
    def bias_metric(self) -> dict:
        return avg_metric(self.error_col, "Bias")

    @property
    def error_metric(self) -> dict:
        """The signed error as a series of the 30-minute detail (the bias metric, named
        for a line)."""
        return avg_metric(self.error_col, "Error (forecast − actual)")

    @property
    def rmse_metric(self) -> dict:
        return sql_metric(f"sqrt(avg(power({self.error_col}, 2)))", "RMSE")

    @property
    def rmse_mae_metric(self) -> dict:
        return sql_metric(
            f"sqrt(avg(power({self.error_col}, 2))) / avg({self.abs_error_col})", "RMSE/MAE"
        )

    @property
    def wape_metric(self) -> dict:
        return sql_metric(f"sum({self.abs_error_col}) / sum({self.actual_col})", "WAPE")

    @property
    def p90_metric(self) -> dict:
        return sql_metric(f"percentile({self.abs_error_col}, 0.90)", "P90 abs error")

    def _explanation_sql(self, period_gate: str) -> str:
        """The explanation SQL around this task's value block, with ``period_gate``
        appended to the pinned rows' predicate."""
        expression = FEATURE_EXPRESSION_SQL.format(e="e", name="c.component")
        ranked_expression = FEATURE_EXPRESSION_SQL.format(e="e", name="r.component")
        return EXPLANATION_DATASET_SQL_TEMPLATE.substitute(
            explanation_value_columns_sql=self.explanation_value_columns_sql,
            contribution_table=self.contribution_table,
            contribution_fact_col=self.contribution_fact_col,
            accuracy_table=self.accuracy_table,
            summary_table=self.summary_table,
            run_pin=run_pin_sql("run", "c.run_id"),
            period_gate=period_gate,
            run_label_sql=RUN_LABEL_SQL.format(f="c", a="a"),
            feature_expression_sql=expression,
            feature_join_sql=FEATURE_JOIN_SQL.format(e="e", name="c.component"),
            short_label_sql=short_label_sql(ranked_expression),
            ranked_feature_join_sql=FEATURE_JOIN_SQL.format(e="e", name="r.component"),
            feature_pick_sql=FEATURE_PICK_SQL.format(s="s", expression=expression),
            top=TOP_COMPONENTS,
            other=OTHER_FEATURES,
            other_order=TOP_COMPONENTS + 1,
        )

    @property
    def explanation_dataset_sql(self) -> str:
        """The explanation dataset's SQL: the shared template around this task's value block."""
        return self._explanation_sql(period_gate="")

    @property
    def explanation_period_dataset_sql(self) -> str:
        """The period dataset's SQL: the explanation SQL, with no rows unless the Period
        filter has a value."""
        return self._explanation_sql(period_gate=PERIOD_GATE_SQL)

    @property
    def summary_dataset_sql(self) -> str:
        """The summary dataset's SQL: the shared template around this task's value block."""
        expression = FEATURE_EXPRESSION_SQL.format(e="e", name="s.component")
        return SUMMARY_DATASET_SQL_TEMPLATE.format(
            summary_value_columns_sql=self.summary_value_columns_sql,
            summary_table=self.summary_table,
            run_label_sql=RUN_LABEL_SQL.format(f="s", a="a"),
            feature_expression_sql=expression,
            feature_join_sql=FEATURE_JOIN_SQL.format(e="e", name="s.component"),
            short_label_sql=short_label_sql(expression),
            feature_pick_sql=FEATURE_PICK_SQL.format(s="s", expression=expression),
        )

    @property
    def summary_dataset_columns(self) -> list[tuple[str, str, bool]]:
        """(column_name, generic type, is temporal) for every summary column, in select order."""
        return [*COMMON_SUMMARY_COLUMNS, *self.summary_value_columns]

    @property
    def mean_abs_contribution_col(self) -> str:
        """The *dataset* column of a feature's mean |SHAP| over its run (the summary
        value block's last column; the importance dataset carries it too)."""
        return self.summary_value_columns[-1][0]

    @property
    def explanation_dataset_columns(self) -> list[tuple[str, str, bool]]:
        """(column_name, generic type, is temporal) for every explanation column, in select order."""
        return [*COMMON_EXPLANATION_COLUMNS, *self.explanation_value_columns]

    @property
    def baseline_forecast_col(self) -> str:
        """The baseline run's forecast column of the comparison dataset."""
        return f"baseline_{self.forecast_col}"

    @property
    def baseline_error_col(self) -> str:
        """The baseline run's signed error column of the comparison dataset."""
        return f"baseline_{self.error_col}"

    @property
    def baseline_abs_error_col(self) -> str:
        """The baseline run's absolute error column of the comparison dataset."""
        return f"baseline_{self.abs_error_col}"

    @property
    def delta_abs_error_col(self) -> str:
        """Candidate absolute error − baseline absolute error, per period."""
        return f"delta_{self.abs_error_col}"

    @property
    def daily_abs_error_col(self) -> str:
        """The candidate's daily MAE, repeated on each of the day's rows."""
        return f"daily_{self.abs_error_col}"

    @property
    def daily_baseline_abs_error_col(self) -> str:
        """The baseline's daily MAE, repeated on each of the day's rows."""
        return f"daily_baseline_{self.abs_error_col}"

    @property
    def daily_delta_abs_error_col(self) -> str:
        """Candidate daily MAE − baseline daily MAE, repeated on each of the day's rows."""
        return f"daily_delta_{self.abs_error_col}"

    @property
    def comparison_dataset_sql(self) -> str:
        """The comparison dataset's SQL: the shared Jinja template around this task's block."""
        return COMPARISON_DATASET_SQL_TEMPLATE.substitute(
            run_label_sql=RUN_LABEL_SQL.format(f="f", a="a"),
            candidate_pin=run_pin_sql("candidate", "run_id"),
            baseline_pin=run_pin_sql("baseline", "run_id"),
            day_part_hours_sql=DAY_PART_HOURS_SQL.format(p="p"),
            day_part_hours_join_sql=DAY_PART_HOURS_JOIN_SQL.format(p="p"),
            day_type_share_sql=day_type_share_sql("d"),
            accuracy_table=self.accuracy_table,
            comparison_value_columns_sql=self.comparison_value_columns_sql,
            explain_link_sql=explain_link_sql(
                self.dashboard_slug, "m.run_label", "date_format(m.date_key, 'yyyy-MM-dd')"
            ),
            value_select_sql="\n".join(
                f"  m.{name}," for name, _, _ in self.comparison_value_columns
            ),
            abs_error_col=self.abs_error_col,
            baseline_abs_error_col=self.baseline_abs_error_col,
            delta_abs_error_col=self.delta_abs_error_col,
            daily_abs_error_col=self.daily_abs_error_col,
            daily_baseline_abs_error_col=self.daily_baseline_abs_error_col,
            daily_delta_abs_error_col=self.daily_delta_abs_error_col,
        )

    @property
    def comparison_dataset_columns(self) -> list[tuple[str, str, bool]]:
        """(column_name, generic type, is temporal) for every comparison column, in select order."""
        return [
            *COMMON_COMPARISON_COLUMNS,
            *self.comparison_value_columns,
            (self.daily_abs_error_col, "DOUBLE", False),
            (self.daily_baseline_abs_error_col, "DOUBLE", False),
            (self.daily_delta_abs_error_col, "DOUBLE", False),
            ("is_first_matched_period", "BOOLEAN", False),
        ]

    @property
    def baseline_contribution_col(self) -> str:
        """The baseline run's contribution column of the explanation-comparison dataset."""
        return f"baseline_{self.contribution_col}"

    @property
    def delta_contribution_col(self) -> str:
        """Candidate contribution − baseline contribution, per period and component."""
        return f"delta_{self.contribution_col}"

    @property
    def explanation_comparison_dataset_sql(self) -> str:
        """The explanation-comparison dataset's SQL: the shared Jinja template around this
        task's value block."""
        return EXPLANATION_COMPARISON_DATASET_SQL_TEMPLATE.substitute(
            run_label_sql=RUN_LABEL_SQL.format(f="c", a="a"),
            candidate_pin=run_pin_sql("candidate", "run_id"),
            baseline_pin=run_pin_sql("baseline", "run_id"),
            feature_expression_sql=FEATURE_EXPRESSION_SQL.format(e="e", name="m.component"),
            feature_join_sql=FEATURE_JOIN_SQL.format(e="e", name="m.component"),
            contribution_table=self.contribution_table,
            accuracy_table=self.accuracy_table,
            explanation_comparison_value_columns_sql=self.explanation_comparison_value_columns_sql,
            value_select_sql=",\n".join(
                f"  m.{name}" for name, _, _ in self.explanation_comparison_value_columns
            ),
        )

    @property
    def explanation_comparison_dataset_columns(self) -> list[tuple[str, str, bool]]:
        """(column_name, generic type, is temporal) for every explanation-comparison column,
        in select order."""
        return [*COMMON_EXPLANATION_COMPARISON_COLUMNS, *self.explanation_comparison_value_columns]

    @property
    def contribution_metric(self) -> dict:
        """Mean contribution per period of the selection (SHAP is additive, so the
        per-period mean is a valid decomposition of the mean forecast)."""
        return avg_metric(self.contribution_col, f"Contribution ({self.unit})")

    @property
    def grouped_contribution_metric(self) -> dict:
        """Mean per period of a component group's total contribution.

        ``Other features`` groups many features, whose contributions add up
        within a period, so the group needs the sum over its rows divided by the
        selection's periods. For a group of one feature that is the plain
        average, ``contribution_metric``.
        """
        return sql_metric(
            f"sum({self.contribution_col}) / count(distinct trade_datetime)",
            f"Contribution ({self.unit})",
            option_name="grouped_contribution",
        )

    @property
    def base_value_metric(self) -> dict:
        return sql_metric(f"avg(case when is_base then {self.contribution_col} end)", "Base value")

    def _minus_base_metric(self, column: str, label: str) -> dict:
        """Ad-hoc metric ``avg(column) − base`` over the selection.

        The base is read off the ``is_base`` row of the same period, so a chart
        query using this metric must keep that row — never combine it with
        ``NOT_BASE_FILTER``.

        Parameters
        ----------
        column : str
            Explanation-dataset column to average (the forecast or the actual).
        label : str
            Display label of the metric (the series name in legends).

        Returns
        -------
        dict
            A ``sql_metric`` definition.
        """
        return sql_metric(
            f"avg({column}) - avg(case when is_base then {self.contribution_col} end)", label
        )

    @property
    def net_effect_metric(self) -> dict:
        """forecast − base = the sum of the feature contributions (the waterfall's Total)."""
        return self._minus_base_metric(self.forecast_col, "Net feature effect")

    @property
    def forecast_minus_base_metric(self) -> dict:
        """The net effect again, labelled for the by-period chart's legend: the line the
        stacked feature contributions sum to."""
        return self._minus_base_metric(self.forecast_col, "Forecast − base")

    @property
    def actual_minus_base_metric(self) -> dict:
        """actual − base: the actual in the contributions' base-relative frame, so its
        distance from ``forecast_minus_base_metric`` is the period's error."""
        return self._minus_base_metric(self.actual_col, "Actual − base")

    # -- importance dataset (permutation feature importance, one row per feature x repeat)

    @property
    def importance_dataset_sql(self) -> str:
        """The importance dataset's SQL: the shared template around this task's value block."""
        expression = FEATURE_EXPRESSION_SQL.format(e="e", name="i.feature")
        return IMPORTANCE_DATASET_SQL_TEMPLATE.format(
            importance_value_columns_sql=self.importance_value_columns_sql,
            mean_abs_contribution_sql=self.summary_value_columns_sql.splitlines()[-1].strip(),
            importance_table=self.importance_table,
            summary_table=self.summary_table,
            mae_fact_col=self.importance_mae_fact_col,
            permuted_mae_fact_col=self.importance_permuted_mae_fact_col,
            run_label_sql=RUN_LABEL_SQL.format(f="i", a="a"),
            feature_expression_sql=expression,
            feature_join_sql=FEATURE_JOIN_SQL.format(e="e", name="i.feature"),
            short_label_sql=short_label_sql(expression),
        )

    @property
    def importance_dataset_columns(self) -> list[tuple[str, str, bool]]:
        """(column_name, generic type, is temporal) for every importance column, in select order."""
        return [
            *COMMON_IMPORTANCE_COLUMNS,
            *self.importance_value_columns,
            self.summary_value_columns[-1],
        ]

    @property
    def importance_mae_metric(self) -> dict:
        """The run's MAE (the same on every row, so the average is the value)."""
        return avg_metric(self.importance_mae_col, f"MAE ({self.unit})")

    @property
    def permuted_mae_metric(self) -> dict:
        """Mean over the repeats of the MAE after shuffling the feature."""
        return avg_metric(self.importance_permuted_mae_col, f"Permuted MAE ({self.unit})")

    @property
    def importance_delta_sql(self) -> str:
        """Permuted MAE − MAE, mean over the selection's repeats (aggregate expression)."""
        return f"avg({self.importance_permuted_mae_col}) - avg({self.importance_mae_col})"

    @property
    def importance_metric(self) -> dict:
        return sql_metric(
            self.importance_delta_sql, f"ΔMAE ({self.unit})", option_name="importance_delta_mae"
        )

    @property
    def importance_pct_metric(self) -> dict:
        """ΔMAE as a percentage of the MAE; null (not an ANSI error) when the MAE is zero."""
        return sql_metric(
            f"100 * try_divide({self.importance_delta_sql}, avg({self.importance_mae_col}))",
            "Importance %",
            option_name="importance_pct",
        )

    @property
    def importance_std_metric(self) -> dict:
        """Population std of the permuted MAE over the repeats (scikit-learn's importances_std)."""
        return sql_metric(
            f"stddev_pop({self.importance_permuted_mae_col})",
            f"Std over repeats ({self.unit})",
            option_name="importance_std",
        )

    @property
    def mean_abs_shap_metric(self) -> dict:
        """A feature's mean |contribution| over its run: attribution next to the dependence
        the permutation bars show.

        Read off the summary and importance datasets, where the value is already
        a mean per feature (the same on every row of the feature), so the
        average returns it.
        """
        return sql_metric(
            f"avg({self.mean_abs_contribution_col})",
            f"Mean |SHAP| ({self.unit})",
            option_name="mean_abs_shap",
        )

    # -- comparison dataset (candidate = the Run filter's run, baseline = the Baseline filter's)

    @property
    def delta_band_chart_title(self) -> str:
        """The band chart's title on the Compare tab (ΔMAE % instead of MAE)."""
        return self.band_chart_title.replace("MAE by", "ΔMAE % by", 1)

    @property
    def delta_mae_sql(self) -> str:
        """Candidate MAE − baseline MAE over the selection (aggregate expression)."""
        return f"avg({self.abs_error_col}) - avg({self.baseline_abs_error_col})"

    @property
    def delta_mae_pct_sql(self) -> str:
        """The MAE change relative to the baseline, in percent (aggregate expression)."""
        return f"100 * ({self.delta_mae_sql}) / avg({self.baseline_abs_error_col})"

    @property
    def baseline_mae_metric(self) -> dict:
        return avg_metric(self.baseline_abs_error_col, f"Baseline MAE ({self.unit})")

    @property
    def candidate_mae_metric(self) -> dict:
        return avg_metric(self.abs_error_col, f"Candidate MAE ({self.unit})")

    @property
    def delta_mae_metric(self) -> dict:
        return sql_metric(self.delta_mae_sql, "ΔMAE", option_name="delta_mae")

    @property
    def delta_mae_pct_metric(self) -> dict:
        return sql_metric(self.delta_mae_pct_sql, "ΔMAE %", option_name="delta_mae_pct")

    @property
    def delta_abs_bias_metric(self) -> dict:
        """|candidate bias| − |baseline bias|: negative = the candidate is less biased."""
        return sql_metric(
            f"abs(avg({self.error_col})) - abs(avg({self.baseline_error_col}))",
            "Δ|bias|",
            option_name="delta_abs_bias",
        )

    @property
    def delta_wape_metric(self) -> dict:
        return sql_metric(
            f"sum({self.abs_error_col}) / sum({self.actual_col})"
            f" - sum({self.baseline_abs_error_col}) / sum({self.actual_col})",
            "ΔWAPE",
            option_name="delta_wape",
        )

    @property
    def matched_coverage_metric(self) -> dict:
        """Share of the candidate's periods the baseline also scored (1 = same window)."""
        return sql_metric("count(*) / max(candidate_periods)", "Matched coverage")

    @property
    def matched_days_metric(self) -> dict:
        return sql_metric("count(distinct date_key)", "Matched days")

    @property
    def days_candidate_lower_metric(self) -> dict:
        """Share of matched days on which the candidate's daily MAE is lower."""
        return sql_metric(
            f"count(distinct case when {self.daily_delta_abs_error_col} < 0 then date_key end)"
            " / count(distinct date_key)",
            "Days candidate lower",
        )

    @property
    def median_daily_delta_metric(self) -> dict:
        """Median over matched days of (candidate daily MAE − baseline daily MAE)."""
        return sql_metric(
            f"percentile(case when is_first_matched_period then {self.daily_delta_abs_error_col} end, 0.5)",
            "Median daily ΔMAE",
            option_name="median_daily_delta_mae",
        )

    @property
    def error_reduction_metric(self) -> dict:
        """Σ baseline |error| − Σ candidate |error| over the selection (positive = gain)."""
        return sql_metric(
            f"sum({self.baseline_abs_error_col}) - sum({self.abs_error_col})", "Error reduction"
        )

    def better_worse_metrics(self, expression: str) -> list[dict]:
        """The two series of a diverging bar: ``expression`` split by sign.

        Stacked, the negative part ("Better") and the positive part ("Worse")
        draw one bar per x value below or above zero, each in its own colour
        (``LABEL_COLORS``).

        Parameters
        ----------
        expression : str
            Signed aggregate expression, e.g. ``delta_mae_pct_sql``.

        Returns
        -------
        list of dict
            ``[Better, Worse]`` metric definitions.
        """
        return [
            sql_metric(f"least({expression}, 0)", "Better"),
            sql_metric(f"greatest({expression}, 0)", "Worse"),
        ]

    # -- explanation-comparison dataset (contribution deltas, candidate − baseline)

    @property
    def delta_contribution_metric(self) -> dict:
        """Mean per period of candidate contribution − baseline contribution (per component)."""
        return avg_metric(self.delta_contribution_col, f"Δ contribution ({self.unit})")

    @property
    def baseline_contribution_metric(self) -> dict:
        return avg_metric(self.baseline_contribution_col, f"Baseline contribution ({self.unit})")

    @property
    def candidate_contribution_metric(self) -> dict:
        return avg_metric(self.contribution_col, f"Candidate contribution ({self.unit})")

    @property
    def delta_base_value_metric(self) -> dict:
        """Candidate base value − baseline base value, mean per period."""
        return sql_metric(
            f"avg(case when is_base then {self.delta_contribution_col} end)",
            "Δ base value",
            option_name="delta_base_value",
        )

    @property
    def delta_net_effect_metric(self) -> dict:
        """Σ over features of the contribution delta, per period (= Δ forecast − Δ base)."""
        return sql_metric(
            f"sum(case when not is_base then {self.delta_contribution_col} end)"
            " / count(distinct date_key, time_code)",
            "Δ net feature effect",
            option_name="delta_net_feature_effect",
        )

    @property
    def delta_forecast_metric(self) -> dict:
        """Candidate forecast − baseline forecast, mean per period of the selection."""
        return sql_metric(
            f"avg({self.forecast_col}) - avg({self.baseline_forecast_col})",
            "Δ forecast",
            option_name="delta_forecast",
        )


SPOT_PRICE = DashboardSpec(
    task="spot_price",
    dataset_name="spot_price_forecast_analysis",
    dashboard_title="Spot Price Forecast Analysis",
    dashboard_slug="spot-price-forecast-analysis",
    accuracy_table="pma_curated.fct_spot_price_forecast_accuracy",
    unit="JPY/kWh",
    forecast_col="forecast_price_jpy_kwh",
    actual_col="actual_price_jpy_kwh",
    error_col="error_jpy_kwh",
    abs_error_col="abs_error_jpy_kwh",
    value_columns_sql="""\
  f.forecast_price_jpy_kwh,
  f.actual_price_jpy_kwh,
  cast(round(f.actual_price_jpy_kwh, 0) as int) as actual_price_round_jpy,
  case
    when f.actual_price_jpy_kwh is null then null
    when f.actual_price_jpy_kwh < 5 then '00-05'
    when f.actual_price_jpy_kwh < 10 then '05-10'
    when f.actual_price_jpy_kwh < 15 then '10-15'
    when f.actual_price_jpy_kwh < 20 then '15-20'
    when f.actual_price_jpy_kwh < 30 then '20-30'
    when f.actual_price_jpy_kwh < 50 then '30-50'
    else '50+'
  end as actual_price_band,
  f.error_jpy_kwh,
  f.abs_error_jpy_kwh,""",
    value_columns=(
        ("forecast_price_jpy_kwh", "DOUBLE", False),
        ("actual_price_jpy_kwh", "DOUBLE", False),
        ("actual_price_round_jpy", "INT", False),
        ("actual_price_band", "STRING", False),
        ("error_jpy_kwh", "DOUBLE", False),
        ("abs_error_jpy_kwh", "DOUBLE", False),
    ),
    band_col="actual_price_band",
    band_chart_title="MAE by actual price band",
    calibration_x_col="actual_price_round_jpy",
    calibration_chart_title="Calibration: forecast vs actual price level",
    calibration_x_title="Actual price (JPY/kWh, rounded)",
    number_format=",.3f",
    axis_format=",.2f",
    calibration_x_format="~g",
    calibration_y_format=",.1f",
    worst_days_max_format=",.2f",
    explanation_dataset_name="spot_price_forecast_explanation",
    contribution_table="pma_curated.fct_spot_price_forecast_contribution",
    contribution_col="contribution_price_jpy_kwh",
    contribution_format="+,.3f",
    explanation_value_columns_sql="""\
  c.contribution_price_jpy_kwh,
  f.forecast_price_jpy_kwh,
  f.actual_price_jpy_kwh""",
    explanation_value_columns=(
        ("contribution_price_jpy_kwh", "DOUBLE", False),
        ("forecast_price_jpy_kwh", "DOUBLE", False),
        ("actual_price_jpy_kwh", "DOUBLE", False),
    ),
    comparison_dataset_name="spot_price_forecast_comparison",
    comparison_value_columns_sql="""\
  c.forecast_price_jpy_kwh,
  b.forecast_price_jpy_kwh as baseline_forecast_price_jpy_kwh,
  c.actual_price_jpy_kwh,
  case
    when c.actual_price_jpy_kwh is null then null
    when c.actual_price_jpy_kwh < 5 then '00-05'
    when c.actual_price_jpy_kwh < 10 then '05-10'
    when c.actual_price_jpy_kwh < 15 then '10-15'
    when c.actual_price_jpy_kwh < 20 then '15-20'
    when c.actual_price_jpy_kwh < 30 then '20-30'
    when c.actual_price_jpy_kwh < 50 then '30-50'
    else '50+'
  end as actual_price_band,
  c.error_jpy_kwh,
  b.error_jpy_kwh as baseline_error_jpy_kwh,
  c.abs_error_jpy_kwh,
  b.abs_error_jpy_kwh as baseline_abs_error_jpy_kwh,
  c.abs_error_jpy_kwh - b.abs_error_jpy_kwh as delta_abs_error_jpy_kwh""",
    comparison_value_columns=(
        ("forecast_price_jpy_kwh", "DOUBLE", False),
        ("baseline_forecast_price_jpy_kwh", "DOUBLE", False),
        ("actual_price_jpy_kwh", "DOUBLE", False),
        ("actual_price_band", "STRING", False),
        ("error_jpy_kwh", "DOUBLE", False),
        ("baseline_error_jpy_kwh", "DOUBLE", False),
        ("abs_error_jpy_kwh", "DOUBLE", False),
        ("baseline_abs_error_jpy_kwh", "DOUBLE", False),
        ("delta_abs_error_jpy_kwh", "DOUBLE", False),
    ),
    explanation_comparison_dataset_name="spot_price_forecast_explanation_comparison",
    explanation_comparison_value_columns_sql="""\
  coalesce(c.contribution_price_jpy_kwh, 0) as contribution_price_jpy_kwh,
  coalesce(b.contribution_price_jpy_kwh, 0) as baseline_contribution_price_jpy_kwh,
  coalesce(c.contribution_price_jpy_kwh, 0) - coalesce(b.contribution_price_jpy_kwh, 0) as delta_contribution_price_jpy_kwh,
  fc.forecast_price_jpy_kwh,
  fb.forecast_price_jpy_kwh as baseline_forecast_price_jpy_kwh,
  fc.actual_price_jpy_kwh""",
    explanation_comparison_value_columns=(
        ("contribution_price_jpy_kwh", "DOUBLE", False),
        ("baseline_contribution_price_jpy_kwh", "DOUBLE", False),
        ("delta_contribution_price_jpy_kwh", "DOUBLE", False),
        ("forecast_price_jpy_kwh", "DOUBLE", False),
        ("baseline_forecast_price_jpy_kwh", "DOUBLE", False),
        ("actual_price_jpy_kwh", "DOUBLE", False),
    ),
    feature_values_dataset_name="spot_price_feature_values",
    issue_time_sql=issue_time_sql(SPOT_PRICE_TASK.issue_offset),
    contribution_fact_col=SPOT_PRICE_TASK.contribution_col,
    explanation_period_dataset_name="spot_price_forecast_explanation_period",
    summary_dataset_name="spot_price_forecast_contribution_summary",
    summary_table="pma_curated.fct_spot_price_forecast_contribution_summary",
    summary_value_columns_sql="""\
  s.mean_contribution_price_jpy_kwh,
  s.mean_abs_contribution_price_jpy_kwh""",
    summary_value_columns=(
        ("mean_contribution_price_jpy_kwh", "DOUBLE", False),
        ("mean_abs_contribution_price_jpy_kwh", "DOUBLE", False),
    ),
    importance_mae_fact_col=SPOT_PRICE_TASK.mae_col,
    importance_permuted_mae_fact_col=SPOT_PRICE_TASK.permuted_mae_col,
    importance_dataset_name="spot_price_forecast_importance",
    importance_table="pma_curated.fct_spot_price_forecast_importance",
    importance_mae_col="mae_price_jpy_kwh",
    importance_permuted_mae_col="permuted_mae_price_jpy_kwh",
    importance_value_columns_sql="""\
  i.mae_price_jpy_kwh,
  i.permuted_mae_price_jpy_kwh""",
    importance_value_columns=(
        ("mae_price_jpy_kwh", "DOUBLE", False),
        ("permuted_mae_price_jpy_kwh", "DOUBLE", False),
    ),
)

# Demand is 30分kWh as the TSOs publish it and as the mart stores it (Tokyo
# ≈ 9–30 GWh per half hour, Kansai ≈ 5–14 GWh); the dataset rescales it to
# MWh for display (errors in the hundreds, levels in the tens of thousands),
# so plain thousands-separated d3 formats work and the actual is banded in
# fixed 2,000-MWh bins (zero-padded ``10000-12000`` … so the string sort is
# numeric), which suit any area; the calibration curve rounds the actual to
# 1,000 MWh.
DEMAND = DashboardSpec(
    task="demand",
    dataset_name="demand_forecast_analysis",
    dashboard_title="Demand Forecast Analysis",
    dashboard_slug="demand-forecast-analysis",
    accuracy_table="pma_curated.fct_demand_forecast_accuracy",
    unit="MWh",
    forecast_col="forecast_demand_mwh",
    actual_col="actual_demand_mwh",
    error_col="error_mwh",
    abs_error_col="abs_error_mwh",
    value_columns_sql="""\
  f.forecast_demand_kwh / 1000 as forecast_demand_mwh,
  f.actual_demand_kwh / 1000 as actual_demand_mwh,
  cast(round(f.actual_demand_kwh / 1000, -3) as int) as actual_demand_round_mwh,
  case
    when f.actual_demand_kwh is null then null
    else concat(
      lpad(cast(cast(floor(f.actual_demand_kwh / 2000000) * 2000 as int) as string), 5, '0'),
      '-',
      lpad(cast(cast(floor(f.actual_demand_kwh / 2000000) * 2000 + 2000 as int) as string), 5, '0')
    )
  end as actual_demand_band,
  f.error_kwh / 1000 as error_mwh,
  f.abs_error_kwh / 1000 as abs_error_mwh,""",
    value_columns=(
        ("forecast_demand_mwh", "DOUBLE", False),
        ("actual_demand_mwh", "DOUBLE", False),
        ("actual_demand_round_mwh", "INT", False),
        ("actual_demand_band", "STRING", False),
        ("error_mwh", "DOUBLE", False),
        ("abs_error_mwh", "DOUBLE", False),
    ),
    band_col="actual_demand_band",
    band_chart_title="MAE by actual demand band",
    calibration_x_col="actual_demand_round_mwh",
    calibration_chart_title="Calibration: forecast vs actual demand level",
    calibration_x_title="Actual demand (MWh, rounded to 1,000 MWh)",
    number_format=",.1f",
    axis_format=",.0f",
    calibration_x_format=",.0f",
    calibration_y_format=",.0f",
    worst_days_max_format=",.0f",
    explanation_dataset_name="demand_forecast_explanation",
    contribution_table="pma_curated.fct_demand_forecast_contribution",
    contribution_col="contribution_mwh",
    contribution_format="+,.0f",
    explanation_value_columns_sql="""\
  c.contribution_demand_kwh / 1000 as contribution_mwh,
  f.forecast_demand_kwh / 1000 as forecast_demand_mwh,
  f.actual_demand_kwh / 1000 as actual_demand_mwh""",
    explanation_value_columns=(
        ("contribution_mwh", "DOUBLE", False),
        ("forecast_demand_mwh", "DOUBLE", False),
        ("actual_demand_mwh", "DOUBLE", False),
    ),
    comparison_dataset_name="demand_forecast_comparison",
    comparison_value_columns_sql="""\
  c.forecast_demand_kwh / 1000 as forecast_demand_mwh,
  b.forecast_demand_kwh / 1000 as baseline_forecast_demand_mwh,
  c.actual_demand_kwh / 1000 as actual_demand_mwh,
  case
    when c.actual_demand_kwh is null then null
    else concat(
      lpad(cast(cast(floor(c.actual_demand_kwh / 2000000) * 2000 as int) as string), 5, '0'),
      '-',
      lpad(cast(cast(floor(c.actual_demand_kwh / 2000000) * 2000 + 2000 as int) as string), 5, '0')
    )
  end as actual_demand_band,
  c.error_kwh / 1000 as error_mwh,
  b.error_kwh / 1000 as baseline_error_mwh,
  c.abs_error_kwh / 1000 as abs_error_mwh,
  b.abs_error_kwh / 1000 as baseline_abs_error_mwh,
  (c.abs_error_kwh - b.abs_error_kwh) / 1000 as delta_abs_error_mwh""",
    comparison_value_columns=(
        ("forecast_demand_mwh", "DOUBLE", False),
        ("baseline_forecast_demand_mwh", "DOUBLE", False),
        ("actual_demand_mwh", "DOUBLE", False),
        ("actual_demand_band", "STRING", False),
        ("error_mwh", "DOUBLE", False),
        ("baseline_error_mwh", "DOUBLE", False),
        ("abs_error_mwh", "DOUBLE", False),
        ("baseline_abs_error_mwh", "DOUBLE", False),
        ("delta_abs_error_mwh", "DOUBLE", False),
    ),
    explanation_comparison_dataset_name="demand_forecast_explanation_comparison",
    explanation_comparison_value_columns_sql="""\
  coalesce(c.contribution_demand_kwh, 0) / 1000 as contribution_mwh,
  coalesce(b.contribution_demand_kwh, 0) / 1000 as baseline_contribution_mwh,
  (coalesce(c.contribution_demand_kwh, 0) - coalesce(b.contribution_demand_kwh, 0)) / 1000 as delta_contribution_mwh,
  fc.forecast_demand_kwh / 1000 as forecast_demand_mwh,
  fb.forecast_demand_kwh / 1000 as baseline_forecast_demand_mwh,
  fc.actual_demand_kwh / 1000 as actual_demand_mwh""",
    explanation_comparison_value_columns=(
        ("contribution_mwh", "DOUBLE", False),
        ("baseline_contribution_mwh", "DOUBLE", False),
        ("delta_contribution_mwh", "DOUBLE", False),
        ("forecast_demand_mwh", "DOUBLE", False),
        ("baseline_forecast_demand_mwh", "DOUBLE", False),
        ("actual_demand_mwh", "DOUBLE", False),
    ),
    feature_values_dataset_name="demand_feature_values",
    issue_time_sql=issue_time_sql(DEMAND_TASK.issue_offset),
    contribution_fact_col=DEMAND_TASK.contribution_col,
    explanation_period_dataset_name="demand_forecast_explanation_period",
    summary_dataset_name="demand_forecast_contribution_summary",
    summary_table="pma_curated.fct_demand_forecast_contribution_summary",
    summary_value_columns_sql="""\
  s.mean_contribution_demand_kwh / 1000 as mean_contribution_mwh,
  s.mean_abs_contribution_demand_kwh / 1000 as mean_abs_contribution_mwh""",
    summary_value_columns=(
        ("mean_contribution_mwh", "DOUBLE", False),
        ("mean_abs_contribution_mwh", "DOUBLE", False),
    ),
    importance_mae_fact_col=DEMAND_TASK.mae_col,
    importance_permuted_mae_fact_col=DEMAND_TASK.permuted_mae_col,
    importance_dataset_name="demand_forecast_importance",
    importance_table="pma_curated.fct_demand_forecast_importance",
    importance_mae_col="mae_mwh",
    importance_permuted_mae_col="permuted_mae_mwh",
    importance_value_columns_sql="""\
  i.mae_demand_kwh / 1000 as mae_mwh,
  i.permuted_mae_demand_kwh / 1000 as permuted_mae_mwh""",
    importance_value_columns=(
        ("mae_mwh", "DOUBLE", False),
        ("permuted_mae_mwh", "DOUBLE", False),
    ),
)

DASHBOARDS: dict[str, DashboardSpec] = {spec.task: spec for spec in (SPOT_PRICE, DEMAND)}


def _rison_value(value: str | int) -> str:
    """Rison literal for an equality-filter value: ints bare, strings single-quoted."""
    return str(value) if isinstance(value, int) else f"'{value}'"


class SupersetClient:
    """Thin authenticated wrapper over the Superset REST API.

    Parameters
    ----------
    base_url : str
        Superset root URL, e.g. ``http://superset:8088``.
    username, password : str
        Credentials for the ``db`` auth provider.
    session : requests.Session, optional
        HTTP session to issue every request through (the login included);
        a fresh ``requests.Session()`` when omitted. Injectable for tests.

    Rate-limited answers (429) are retried after the server's Retry-After
    pause, up to ``RATE_LIMIT_RETRIES`` times.
    """

    #: Rows a listing asks for, and so the largest batch a lookup by id sends.
    PAGE_SIZE = 100

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else requests.Session()
        token = self._post_json(
            "/api/v1/security/login",
            {"username": username, "password": password, "provider": "db", "refresh": True},
        )["access_token"]
        self.session.headers["Authorization"] = f"Bearer {token}"
        self.session.headers["Referer"] = self.base_url
        csrf = self._get_json("/api/v1/security/csrf_token/")["result"]
        self.session.headers["X-CSRFToken"] = csrf

    def _json(self, send: Callable[[], requests.Response]) -> dict:
        """Send a request, retrying Superset's 429 rate-limit answers, and return the JSON.

        Parameters
        ----------
        send : callable
            Issues the request and returns the response; called again after
            each rate-limited answer.

        Returns
        -------
        dict

        Raises
        ------
        requests.HTTPError
            On any non-2xx answer, a 429 on the last attempt included.
        """
        response = send()
        for _ in range(RATE_LIMIT_RETRIES):
            if response.status_code != 429:
                break
            pause = float(response.headers.get("Retry-After", 1))
            logger.info("Superset rate limit (429); retrying in {} s", pause)
            time.sleep(pause)
            response = send()
        response.raise_for_status()
        return response.json()

    def _get_json(self, path: str, params: dict | None = None) -> dict:
        return self._json(lambda: self.session.get(f"{self.base_url}{path}", params=params))

    def _post_json(self, path: str, payload: dict) -> dict:
        return self._json(lambda: self.session.post(f"{self.base_url}{path}", json=payload))

    def _put_json(self, path: str, payload: dict, params: dict | None = None) -> dict:
        return self._json(
            lambda: self.session.put(f"{self.base_url}{path}", json=payload, params=params)
        )

    def find_one(self, resource: str, **filters: str | int) -> int | None:
        """Return the id of the first ``resource`` row matching every filter.

        Parameters
        ----------
        resource : str
            API resource, e.g. ``dataset``, ``chart``, ``dashboard``.
        **filters : str or int
            Equality filters, e.g. ``table_name="x"`` or
            ``slice_name="MAE by year", datasource_id=3`` (ANDed).

        Returns
        -------
        int or None
        """
        rison_filters = ",".join(
            f"(col:{column},opr:eq,value:{_rison_value(value)})"
            for column, value in filters.items()
        )
        q = f"(filters:!({rison_filters}),page_size:{self.PAGE_SIZE})"
        result = self._get_json(f"/api/v1/{resource}/", params={"q": q})["result"]
        return result[0]["id"] if result else None

    def dashboards_of_charts(self, chart_ids: list[int]) -> dict[int, list[int]]:
        """The dashboards each of these charts is on.

        ``charts_of_dashboard`` answers only for one dashboard's charts, and a
        build also places charts that are on none of its own — a new one, or one
        a person moved to a dashboard of theirs, which ``upsert_chart`` finds
        again by name within its dataset.

        Parameters
        ----------
        chart_ids : list of int

        Returns
        -------
        dict of int to list of int
            Chart id → the ids of every dashboard it is attached to. A chart the
            API does not return is absent.
        """
        links: dict[int, list[int]] = {}
        for start in range(0, len(chart_ids), self.PAGE_SIZE):
            batch = chart_ids[start : start + self.PAGE_SIZE]
            ids = ",".join(str(chart_id) for chart_id in batch)
            q = f"(filters:!((col:id,opr:in,value:!({ids}))),page_size:{self.PAGE_SIZE})"
            for row in self._get_json("/api/v1/chart/", params={"q": q})["result"]:
                links[row["id"]] = [d["id"] for d in row.get("dashboards") or []]
        return links

    def charts_of_dashboard(self, dashboard_id: int) -> dict[int, list[int]]:
        """Every chart attached to a dashboard, with the dashboards each one is on.

        Parameters
        ----------
        dashboard_id : int

        Returns
        -------
        dict of int to list of int
            Chart id → the ids of every dashboard it is attached to. The link
            is many-to-many, and writing it replaces the whole list, so a
            caller that changes one dashboard needs the others.
        """
        q = (
            "(filters:!((col:dashboards,opr:rel_m_m,"
            f"value:{dashboard_id})),page_size:{self.PAGE_SIZE})"
        )
        result = self._get_json("/api/v1/chart/", params={"q": q})["result"]
        return {row["id"]: [d["id"] for d in row.get("dashboards") or []] for row in result}


def upsert_dataset(
    client: SupersetClient,
    database_id: int,
    name: str,
    sql: str,
    columns: list[tuple[str, str, bool]],
    *,
    main_dttm_col: str | None = "trade_datetime",
) -> int:
    """Create or update a virtual dataset and return its id.

    Parameters
    ----------
    client : SupersetClient
    database_id : int
        Superset id of the Spark Thriftserver connection.
    name : str
        ``table_name`` of the virtual dataset (matched on reruns).
    sql : str
        The dataset SQL.
    columns : list of (str, str, bool)
        (column_name, generic type, is temporal) for every output column, in
        select order; overrides any stale column metadata on reruns.
    main_dttm_col : str or None, optional
        The dataset's main temporal column: ``trade_datetime`` for the
        period-grain datasets, ``published_at`` for the run-grain importance
        and summary datasets, None for the feature catalogue, which has no
        time axis.

    Returns
    -------
    int
    """
    column_payload = [
        {"column_name": col, "type": dtype, "is_dttm": is_dttm, "groupby": True, "filterable": True}
        for col, dtype, is_dttm in columns
    ]
    dataset_id = client.find_one("dataset", table_name=name)
    if dataset_id is None:
        dataset_id = client._post_json(
            "/api/v1/dataset/", {"database": database_id, "table_name": name, "sql": sql}
        )["id"]
    client._put_json(
        f"/api/v1/dataset/{dataset_id}",
        {"sql": sql, "main_dttm_col": main_dttm_col, "columns": column_payload},
        params={"override_columns": "true"},
    )
    return dataset_id


# One row per run of the task's mart, newest first, with the window that makes
# a pair comparable (area, first / last delivery day, period count). Unbounded:
# both the --baseline-run override and the matched-window rule must see every run.
RUNS_SQL_TEMPLATE = """\
select
  f.run_id,
  {run_label_sql} as run_label,
  a.area_code,
  date_format(min(f.date_key), 'yyyy-MM-dd') as first_day,
  date_format(max(f.date_key), 'yyyy-MM-dd') as last_day,
  count(*) as periods
from {accuracy_table} f
join pma_curated.dim_area a on f.area_key = a.area_key
group by f.run_id, f.strategy, f.published_at, a.area_code
order by f.published_at desc
"""

# One row per run: the feature its contributions rank first, as the Feature
# filter lists it. Every run, so the default run can be looked up without
# putting its id in the SQL.
TOP_FEATURE_SQL_TEMPLATE = """\
select
  s.run_id,
  {feature_pick_sql} as feature_pick
from {summary_table} s
{feature_join_sql}
where s.feature_rank = 1
"""


@dataclass(frozen=True)
class RunDefaults:
    """The native filters' on-load values.

    Parameters
    ----------
    run_label : str
        The newest run's label — the Run filter's default.
    last_day : str
        That run's last delivery day, ``yyyy-MM-dd`` — the Day filter's default.
    baseline_run_label : str or None
        The Baseline filter's default; None when no run qualifies.
    feature_pick : str or None
        That run's first-ranked feature by mean |SHAP| — the Feature filter's
        default; None when the run has no contributions.
    """

    run_label: str
    last_day: str
    baseline_run_label: str | None
    feature_pick: str | None


def run_defaults(
    client: SupersetClient,
    database_id: int,
    spec: DashboardSpec,
    baseline_run: str | None = None,
) -> RunDefaults | None:
    """The filters' on-load defaults from the mart's runs.

    ``defaultToFirstItem`` only stages a value (charts render unfiltered
    until Apply is clicked); an explicit default applies on page load. The
    labels are built by ``RUN_LABEL_SQL`` exactly like the datasets'
    ``run_label``; the day like the explanation dataset's ``trade_date_label``.

    Parameters
    ----------
    client : SupersetClient
    database_id : int
    spec : DashboardSpec
    baseline_run : str, optional
        ``run_id`` (or a prefix of it) of the run the Baseline filter opens
        on. Without it, or when no run matches (logged as a warning), the
        default is the newest *other* run with the same area, first day, last
        day and period count as the newest run.

    Returns
    -------
    RunDefaults or None
        None when the query fails or the mart is empty (every filter then
        falls back to its ``defaultToFirstItem`` setting).
    """

    def query(sql: str) -> list[dict]:
        return client._post_json(
            "/api/v1/sqllab/execute/",
            {"database_id": database_id, "sql": sql, "runAsync": False},
        )["data"]

    try:
        rows = query(
            RUNS_SQL_TEMPLATE.format(
                run_label_sql=RUN_LABEL_SQL.format(f="f", a="a"),
                accuracy_table=spec.accuracy_table,
            )
        )
        newest = rows[0]
        run_label, last_day, run_id = newest["run_label"], newest["last_day"], newest["run_id"]
        baseline_label = _default_baseline(rows, baseline_run)
    except (requests.HTTPError, KeyError, IndexError):
        return None
    # The feature default is fetched outside the guard: its query reads another
    # table, and its failure must not cost the defaults above.
    return RunDefaults(run_label, last_day, baseline_label, _top_feature(query, spec, run_id))


def _top_feature(
    query: Callable[[str], list[dict]], spec: DashboardSpec, run_id: str
) -> str | None:
    """The run's first-ranked feature as the Feature filter lists it.

    Parameters
    ----------
    query : callable
        Runs SQL and returns the rows.
    spec : DashboardSpec
    run_id : str
        The default run.

    Returns
    -------
    str or None
        None when the summary cannot be read — a warehouse that has never
        published contributions has no such table — or when the run has no
        contributions. The other defaults stand either way; the Feature filter
        then stages its first option instead of applying it.
    """
    expression = FEATURE_EXPRESSION_SQL.format(e="e", name="s.component")
    try:
        rows = query(
            TOP_FEATURE_SQL_TEMPLATE.format(
                summary_table=spec.summary_table,
                feature_pick_sql=FEATURE_PICK_SQL.format(s="s", expression=expression),
                feature_join_sql=FEATURE_JOIN_SQL.format(e="e", name="s.component"),
            )
        )
        return {row["run_id"]: row["feature_pick"] for row in rows}.get(run_id)
    except (requests.HTTPError, KeyError):
        return None


def _default_baseline(rows: list[dict], baseline_run: str | None) -> str | None:
    """The Baseline filter's default label from the runs query's rows.

    Parameters
    ----------
    rows : list of dict
        The runs, newest first (``RUNS_SQL_TEMPLATE`` columns).
    baseline_run : str or None
        Requested ``run_id`` or prefix; None for the matched-window rule.

    Returns
    -------
    str or None
        The requested run's label; else the newest other run with the newest
        run's area and window; None when there is none.
    """
    if baseline_run is not None:
        for row in rows:
            if row["run_id"].startswith(baseline_run):
                return row["run_label"]
        logger.warning(
            "--baseline-run {}: no such run in the mart; using the matched-window rule",
            baseline_run,
        )
    newest = rows[0]
    window = (newest["area_code"], newest["first_day"], newest["last_day"], newest["periods"])
    for row in rows[1:]:
        if (row["area_code"], row["first_day"], row["last_day"], row["periods"]) == window:
            return row["run_label"]
    return None


def big_number_params(dataset_id: int, metric: dict, subheader: str, number_format: str) -> dict:
    """Params for a KPI stat tile.

    Parameters
    ----------
    dataset_id : int
    metric : dict
        Ad-hoc metric definition.
    subheader : str
        Small caption under the number (include units).
    number_format : str
        d3 number format, e.g. ``,.3f`` or ``.1%``.

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "big_number_total",
        "metric": metric,
        "adhoc_filters": [],
        "subheader": subheader,
        "header_font_size": 0.3,
        "subheader_font_size": 0.125,
        "y_axis_format": number_format,
        "time_format": "smart_date",
        "extra_form_data": {},
    }


def bar_params(
    spec: DashboardSpec, dataset_id: int, x_axis: str, *, label_rotation: int = 0
) -> dict:
    """Params for a single-series MAE bar chart over ``x_axis``.

    One series, so no legend (the title names it).

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
    x_axis : str
        Dataset column for the x axis.
    label_rotation : int, optional
        Degrees to turn the x axis labels by. A label that carries its hours or
        its share does not fit a third of a row flat, and Superset answers that
        by dropping the labels that collide and cutting the rest short.

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "echarts_timeseries_bar",
        "x_axis": x_axis,
        "time_grain_sqla": None,
        "x_axis_sort": x_axis,
        "x_axis_sort_asc": True,
        "xAxisLabelRotation": label_rotation,
        "metrics": [spec.mae_metric],
        "groupby": [],
        "adhoc_filters": [],
        "order_desc": False,
        "row_limit": 1000,
        "show_legend": False,
        "rich_tooltip": True,
        "y_axis_format": spec.axis_format,
        "y_axis_title": spec.unit,
        "y_axis_title_margin": 30,
        "truncateYAxis": False,
        "color_scheme": "supersetColors",
        "x_axis_time_format": "smart_date",
        "extra_form_data": {},
    }


def heatmap_params(spec: DashboardSpec, dataset_id: int, x_axis: str) -> dict:
    """Params for a MAE heatmap (year on y, ``x_axis`` on x).

    Sequential single-hue ramp ("Dark blues"): the metric encodes magnitude
    only.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
    x_axis : str
        Dataset column for the x axis (``time_code`` or ``month``).

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "heatmap_v2",
        "x_axis": x_axis,
        "groupby": "year",
        "metric": spec.mae_metric,
        "adhoc_filters": [],
        "row_limit": 10000,
        "sort_x_axis": "alpha_asc",
        "sort_y_axis": "alpha_asc",
        "normalize_across": "heatmap",
        "legend_type": "continuous",
        "show_legend": True,
        "linear_color_scheme": "dark_blue",
        "xscale_interval": -1,
        "yscale_interval": -1,
        "value_bounds": [None, None],
        "y_axis_format": "SMART_NUMBER",
        "x_axis_time_format": "smart_date",
        "show_values": False,
        "show_percentage": False,
        "time_range": "No filter",
        "extra_form_data": {},
    }


def calibration_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the calibration curve: mean forecast per actual level.

    Mean actual per rounded actual is (by construction) the y = x reference,
    so systematic under/over-forecast at any level shows as the gap between
    the two series — the standard conditional-bias view.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "echarts_timeseries_scatter",
        "x_axis": spec.calibration_x_col,
        "time_grain_sqla": None,
        "x_axis_sort": spec.calibration_x_col,
        "x_axis_sort_asc": True,
        "metrics": [
            avg_metric(spec.forecast_col, "Mean forecast"),
            avg_metric(spec.actual_col, "Mean actual (y = x reference)"),
        ],
        "groupby": [],
        "adhoc_filters": [],
        "order_desc": False,
        "row_limit": 10000,
        "markerSize": 5,
        "show_legend": True,
        "legendType": "scroll",
        "legendOrientation": "top",
        "rich_tooltip": True,
        "tooltipTimeFormat": "smart_date",
        "x_axis_time_format": "smart_date",
        "x_axis_title": spec.calibration_x_title,
        "x_axis_title_margin": 30,
        "x_axis_number_format": spec.calibration_x_format,
        "y_axis_format": spec.calibration_y_format,
        "y_axis_title": f"Forecast ({spec.unit})",
        "y_axis_title_margin": 30,
        "truncateYAxis": False,
        "color_scheme": "supersetColors",
        "extra_form_data": {},
    }


def histogram_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the signed-error histogram.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "histogram_v2",
        "column": spec.error_col,
        "bins": 60,
        "normalize": False,
        "cumulative": False,
        "groupby": [],
        "adhoc_filters": [],
        "row_limit": 100000,
        "show_legend": False,
        "show_value": False,
        "x_axis_title": f"Signed error ({spec.unit}; + = over-forecast)",
        "y_axis_title": "Delivery periods",
        "color_scheme": "supersetColors",
        "extra_form_data": {},
    }


def worst_days_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the worst-days drill table (highest daily MAE first).

    The first column is the day's Explain link (``explain_link_sql``), so the
    table renders HTML.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int

    Returns
    -------
    dict
    """
    mae = spec.mae_metric
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "table",
        "query_mode": "aggregate",
        "groupby": ["explain_link", "date_key", "day_of_week", "day_type"],
        "metrics": [
            mae,
            spec.bias_metric,
            sql_metric(f"max({spec.abs_error_col})", "Max |error|"),
            sql_metric(f"max({spec.actual_col})", "Max actual"),
        ],
        "adhoc_filters": [],
        "timeseries_limit_metric": mae,
        "order_desc": True,
        "row_limit": 20,
        "server_page_length": 20,
        "table_timestamp_format": "%Y-%m-%d",
        "allow_render_html": True,
        "column_config": {
            **EXPLAIN_LINK_COLUMN_CONFIG,
            mae["label"]: {"d3NumberFormat": spec.number_format},
            "Bias": {"d3NumberFormat": spec.signed_number_format},
            "Max |error|": {"d3NumberFormat": spec.worst_days_max_format},
            "Max actual": {"d3NumberFormat": spec.worst_days_max_format},
        },
        "extra_form_data": {},
    }


def detail_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the forecast-vs-actual line chart at the 30-minute grain.

    Loads the whole backtest window; the data-zoom slider (``zoomable``)
    navigates from the full window down to a single day. The signed error is a
    third line on the same axis: the axis already starts at zero
    (``truncateYAxis: False``) and the unit is the same, so the hover popup
    lists the error without a second scale.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "echarts_timeseries_line",
        "x_axis": "trade_datetime",
        "time_grain_sqla": None,
        "x_axis_sort_asc": True,
        "metrics": [
            avg_metric(spec.forecast_col, "Forecast"),
            avg_metric(spec.actual_col, "Actual"),
            spec.error_metric,
        ],
        "groupby": [],
        "adhoc_filters": [],
        "zoomable": True,
        "order_desc": False,
        "row_limit": 100000,
        "seriesType": "line",
        "opacity": 0.2,
        "markerEnabled": False,
        "markerSize": 6,
        "show_legend": True,
        "legendType": "scroll",
        "legendOrientation": "top",
        "only_total": True,
        "show_value": False,
        "rich_tooltip": True,
        # the hover lists forecast, actual and error; adding them up means nothing
        "showTooltipTotal": False,
        "tooltipTimeFormat": "smart_date",
        "x_axis_time_format": "smart_date",
        "y_axis_format": "SMART_NUMBER",
        "y_axis_title": spec.unit,
        "y_axis_title_margin": 30,
        "truncateYAxis": False,
        "color_scheme": "supersetColors",
        "comparison_type": "values",
        "annotation_layers": [],
        "time_range": "No filter",
        "extra_form_data": {},
    }


# Ad-hoc WHERE clause that drops the base row: the waterfall and the stacked
# bars show feature contributions only (Superset's value axis always includes
# zero, so a base bar ~30x the contributions would flatten them; the base is
# a KPI tile instead).
NOT_BASE_FILTER = {
    "expressionType": "SQL",
    "sqlExpression": "not is_base",
    "clause": "WHERE",
    "filterOptionName": "filter_not_is_base",
}

# The x axis the three by-period charts share, so they stack in line. Not
# ``time_code``: that is a model feature as well as a column, and Superset pivots
# a series per feature onto an index named after the axis — a series called
# ``time_code`` then fails the whole chart with "cannot insert time_code, already
# exists" (seen on the Tokyo e212 run, where the feature ranks second on some
# days). ``period_label`` is the dataset's own column, zero-padded so it sorts by
# time, and it reads the clock rather than a code.
BY_PERIOD_X_AXIS = "period_label"

# The importance and mean |SHAP| bars draw the TOP_FEATURE_BARS largest features;
# their tables list every one. Each cut is an ad-hoc WHERE clause on a rank the
# dataset computes (importance_rank by ΔMAE, feature_rank by mean |SHAP|), so it
# does not depend on how Superset orders a row-limited query.
TOP_FEATURE_BARS = 20
TOP_IMPORTANCE_FILTER = {
    "expressionType": "SQL",
    "sqlExpression": f"importance_rank <= {TOP_FEATURE_BARS}",
    "clause": "WHERE",
    "filterOptionName": "filter_top_importance",
}
TOP_MEAN_ABS_SHAP_FILTER = {
    "expressionType": "SQL",
    "sqlExpression": f"feature_rank <= {TOP_FEATURE_BARS}",
    "clause": "WHERE",
    "filterOptionName": "filter_top_mean_abs_shap",
}


# Fixed series colours (dashboard ``label_colors``): the comparison charts
# name roles, not runs, so the same colour means the same thing for any pair.
# Blue / orange is a cool-warm pair that survives colour-vision deficiency; the
# delta tiles and the delta heatmaps' blue-white-yellow scheme put "better" on
# the same blue pole. "Actual" also recolours the Accuracy tab's detail line.
# The Explanation tab's Other features group is grey, so the ten named features
# stand out.
#
# The detail chart's error is drawn in nothing at all. It belongs in the hover,
# which lists every series of the chart, but a third line over the forecast and
# the actual only distracts, and Superset offers no way to put a metric in the
# tooltip alone. A transparent series is the one lever it does offer; that
# chart pins its axis at zero so the undrawn negative values cannot stretch it.
INVISIBLE = "rgba(0, 0, 0, 0)"
LABEL_COLORS = {
    "Candidate": "#1FA8C9",
    "Baseline": "#B2B2B2",
    "Actual": "#222222",
    "Better": "#1FA8C9",
    "Worse": "#FF7F44",
    "Error (forecast − actual)": INVISIBLE,
    OTHER_FEATURES: "#B2B2B2",
}

# ΔMAE % colour-scale bounds of the delta heatmaps, symmetric so white = no change.
DELTA_HEATMAP_BOUND_PCT = 30


def delta_big_number_params(
    dataset_id: int, metric: dict, subheader: str, number_format: str
) -> dict:
    """Params for a signed-delta KPI tile: the value turns blue below zero, orange above.

    Parameters
    ----------
    dataset_id : int
    metric : dict
        Ad-hoc metric definition; its label names the result column the
        conditional formatting reads.
    subheader : str
        Small caption under the number (include units and the sign's meaning).
    number_format : str
        d3 number format, e.g. ``+,.1f``.

    Returns
    -------
    dict
    """
    params = big_number_params(dataset_id, metric, subheader, number_format)
    params["conditional_formatting"] = [
        {
            "colorScheme": LABEL_COLORS["Better"],
            "column": metric["label"],
            "operator": "<",
            "targetValue": 0,
        },
        {
            "colorScheme": LABEL_COLORS["Worse"],
            "column": metric["label"],
            "operator": ">",
            "targetValue": 0,
        },
    ]
    return params


def _delta_bar_params(
    spec: DashboardSpec,
    dataset_id: int,
    x_axis: str,
    *,
    expression: str,
    y_axis_title: str,
    y_axis_format: str,
    zoomable: bool,
) -> dict:
    """Params for a diverging bar of a signed delta over ``x_axis``.

    Two stacked series, Better (≤ 0) and Worse (≥ 0), so each bar hangs
    below or rises above zero in its own colour; the legend names them.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The comparison dataset.
    x_axis : str
        Comparison-dataset column for the x axis.
    expression : str
        Signed aggregate expression the bar splits by sign (``least`` /
        ``greatest`` of it against 0).
    y_axis_title, y_axis_format : str
        Axis title and d3 format of the delta.
    zoomable : bool
        Whether to add the data-zoom slider (the daily bars over the window).

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "echarts_timeseries_bar",
        "x_axis": x_axis,
        "time_grain_sqla": None,
        "x_axis_sort": x_axis,
        "x_axis_sort_asc": True,
        "metrics": spec.better_worse_metrics(expression),
        "groupby": [],
        "adhoc_filters": [],
        "stack": "Stack",
        "zoomable": zoomable,
        "order_desc": False,
        "row_limit": 10000,
        "show_legend": True,
        "legendType": "scroll",
        "legendOrientation": "top",
        "rich_tooltip": True,
        "tooltipTimeFormat": "smart_date",
        "y_axis_format": y_axis_format,
        "y_axis_title": y_axis_title,
        "y_axis_title_margin": 30,
        "truncateYAxis": False,
        "color_scheme": "supersetColors",
        "x_axis_time_format": "smart_date",
        "extra_form_data": {},
    }


def delta_bar_params(spec: DashboardSpec, dataset_id: int, x_axis: str) -> dict:
    """Params for the ΔMAE % (candidate vs baseline) diverging bar over a segment axis.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The comparison dataset.
    x_axis : str
        Comparison-dataset column for the x axis (``day_part``, ``time_code``, …).

    Returns
    -------
    dict
    """
    return _delta_bar_params(
        spec,
        dataset_id,
        x_axis,
        expression=spec.delta_mae_pct_sql,
        y_axis_title="ΔMAE % vs baseline",
        y_axis_format="+,.1f",
        zoomable=False,
    )


def daily_delta_bar_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the daily ΔMAE (in the unit) diverging bar over the window, zoomable.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The comparison dataset.

    Returns
    -------
    dict
    """
    return _delta_bar_params(
        spec,
        dataset_id,
        "date_key",
        expression=spec.delta_mae_sql,
        y_axis_title=f"Daily ΔMAE ({spec.unit}) vs baseline",
        y_axis_format="+" + spec.axis_format,
        zoomable=True,
    )


def delta_heatmap_params(spec: DashboardSpec, dataset_id: int, x_axis: str) -> dict:
    """Params for a ΔMAE % heatmap (year on y, ``x_axis`` on x) on a diverging scale.

    ``blue_white_yellow`` with bounds ±``DELTA_HEATMAP_BOUND_PCT``: white is
    "no change", blue better, yellow worse.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The comparison dataset.
    x_axis : str
        ``time_code`` or ``month``.

    Returns
    -------
    dict
    """
    return {
        **heatmap_params(spec, dataset_id, x_axis),
        "metric": spec.delta_mae_pct_metric,
        "linear_color_scheme": "blue_white_yellow",
        "value_bounds": [-DELTA_HEATMAP_BOUND_PCT, DELTA_HEATMAP_BOUND_PCT],
        "y_axis_format": "+,.1f",
    }


def cumulative_reduction_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the running total of the error reduction over the window.

    Per day, Σ baseline |error| − Σ candidate |error|, accumulated
    (``rolling_type: cumsum``): a steady slope is a broad gain, a few steps a
    gain concentrated in a few days.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The comparison dataset.

    Returns
    -------
    dict
    """
    return {
        **detail_params(spec, dataset_id),
        "x_axis": "date_key",
        "metrics": [spec.error_reduction_metric],
        "rolling_type": "cumsum",
        "row_limit": 10000,
        "show_legend": False,
        "y_axis_format": spec.axis_format,
        "y_axis_title": f"{spec.unit}; Σ (baseline |error| − candidate |error|)",
    }


def ranked_days_params(spec: DashboardSpec, dataset_id: int, *, improved: bool) -> dict:
    """Params for the most-improved (or most-worsened) days table.

    The first column is the day's Explain link (``explain_link_sql``), so the
    table renders HTML.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The comparison dataset.
    improved : bool
        True = lowest daily ΔMAE first (the candidate's biggest gains);
        False = highest first.

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "table",
        "query_mode": "aggregate",
        "groupby": ["explain_link", "date_key", "day_of_week", "day_type", "holiday_name_ja"],
        "metrics": [
            spec.baseline_mae_metric,
            spec.candidate_mae_metric,
            spec.delta_mae_metric,
            spec.delta_mae_pct_metric,
        ],
        "adhoc_filters": [],
        "timeseries_limit_metric": spec.delta_mae_metric,
        "order_desc": not improved,
        "row_limit": 10,
        "server_page_length": 10,
        "table_timestamp_format": "%Y-%m-%d",
        "allow_render_html": True,
        "column_config": {
            **EXPLAIN_LINK_COLUMN_CONFIG,
            spec.baseline_mae_metric["label"]: {"d3NumberFormat": spec.number_format},
            spec.candidate_mae_metric["label"]: {"d3NumberFormat": spec.number_format},
            "ΔMAE": {"d3NumberFormat": spec.signed_number_format},
            "ΔMAE %": {"d3NumberFormat": "+,.1f"},
        },
        "extra_form_data": {},
    }


def comparison_detail_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the 30-minute detail with the actual, the candidate and the baseline.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The comparison dataset.

    Returns
    -------
    dict
    """
    return {
        **detail_params(spec, dataset_id),
        "metrics": [
            avg_metric(spec.actual_col, "Actual"),
            avg_metric(spec.forecast_col, "Candidate"),
            avg_metric(spec.baseline_forecast_col, "Baseline"),
        ],
    }


def delta_waterfall_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the waterfall of contribution deltas, candidate − baseline, per component.

    The Explanation tab's waterfall on the explanation-comparison dataset: one
    bar per component of either run (candidate feature order, baseline-only
    components last), each the mean per period of the contribution delta; the
    Total bar is Δ net effect. The base row is filtered out like the plain
    waterfall (Δ base value is a tile). It keeps every component — that dataset
    ranks nothing, so there is no top ten and no ``Other features`` here — and
    the row limit is high enough that none is dropped silently.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The explanation-comparison dataset.

    Returns
    -------
    dict
    """
    return {
        **waterfall_params(spec, dataset_id),
        "x_axis": "component_label",
        "metric": spec.delta_contribution_metric,
        "increase_label": "Higher than baseline",
        "decrease_label": "Lower than baseline",
        "total_label": "Δ net effect",
        "x_axis_label": "Component (candidate feature order; baseline-only features last)",
    }


def delta_feature_table_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the component table of both runs' contributions and their delta.

    Keeps the base row (order 000), so each contribution column sums to its
    run's forecast; sorted by ``Order`` (the candidate's feature order,
    baseline-only components last).

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The explanation-comparison dataset.

    Returns
    -------
    dict
    """
    order = sql_metric("min(component_order)", "Order")
    baseline = spec.baseline_contribution_metric
    candidate = spec.candidate_contribution_metric
    delta = spec.delta_contribution_metric
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "table",
        "query_mode": "aggregate",
        "groupby": ["component_label"],
        "metrics": [order, baseline, candidate, delta],
        "adhoc_filters": [],
        "timeseries_limit_metric": order,
        "order_desc": False,
        "row_limit": 1000,
        "server_page_length": 20,
        "table_timestamp_format": "smart_date",
        "column_config": {
            "Order": {"d3NumberFormat": ",d"},
            baseline["label"]: {"d3NumberFormat": spec.contribution_format},
            candidate["label"]: {"d3NumberFormat": spec.contribution_format},
            delta["label"]: {"d3NumberFormat": spec.contribution_format},
        },
        "extra_form_data": {},
    }


def waterfall_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the SHAP waterfall of the selected day / period.

    One bar per feature of the selection's top ``TOP_COMPONENTS`` by mean
    |contribution|, largest first, then one ``Other features`` bar (the chart
    sorts by x-axis label, hence ``component_group_label``'s zero-padded rank
    prefix), each the mean per period of the group's total contribution; the
    Total bar is forecast − base. The base row is filtered out (see
    ``NOT_BASE_FILTER``).

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The explanation dataset.

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "waterfall",
        "x_axis": "component_group_label",
        "time_grain_sqla": None,
        "groupby": [],
        "metric": spec.grouped_contribution_metric,
        "adhoc_filters": [NOT_BASE_FILTER],
        "row_limit": 1000,
        "show_value": True,
        "show_legend": True,
        "increase_label": "Pushes forecast up",
        "decrease_label": "Pushes forecast down",
        "show_total": True,
        "total_label": "Net effect",
        "x_axis_label": "Feature (largest mean |contribution| first)",
        "x_axis_time_format": "smart_date",
        "x_ticks_layout": "auto",
        "y_axis_label": spec.unit,
        "y_axis_format": spec.axis_format,
        "extra_form_data": {},
    }


def feature_table_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the Single period table: every feature's value and contribution.

    One row per feature under its full expression, the largest |contribution|
    first; the metric that orders the rows is not a column. Keeps the base row
    (no feature value), so the contribution column sums to the forecast. With a
    hundred features the table pages 25 rows at a time, with a search box.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The period dataset.

    Returns
    -------
    dict
    """
    contribution = spec.contribution_metric
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "table",
        "query_mode": "aggregate",
        "groupby": ["feature_expression"],
        "metrics": [sql_metric("avg(feature_value)", "Feature value"), contribution],
        "adhoc_filters": [],
        "timeseries_limit_metric": sql_metric(
            f"max(abs({spec.contribution_col}))", "|Contribution|", option_name="abs_contribution"
        ),
        "order_desc": True,
        "row_limit": 1000,
        "server_pagination": False,
        "page_length": 25,
        "include_search": True,
        "table_timestamp_format": "smart_date",
        "column_config": {
            **FEATURE_COLUMN_CONFIG,
            "Feature value": {"d3NumberFormat": ",.2~f"},
            contribution["label"]: {"d3NumberFormat": spec.contribution_format},
        },
        "extra_form_data": {},
    }


def all_features_table_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the All features table: every feature of the selection, by rank.

    Where what ``Other features`` hides is read: the feature's rank in the
    selection (the waterfall's), its full expression, its mean contribution, its
    mean |contribution| and that mean's share of the column total (a percent
    metric). The base row is filtered out. 25 rows a page, with a search box.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The explanation dataset.

    Returns
    -------
    dict
    """
    rank = sql_metric("min(selection_rank)", "Rank")
    contribution = spec.contribution_metric
    mean_abs = sql_metric(
        f"avg(abs({spec.contribution_col}))",
        f"Mean |contribution| ({spec.unit})",
        option_name="mean_abs_contribution",
    )
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "table",
        "query_mode": "aggregate",
        "groupby": ["feature_expression"],
        "metrics": [rank, contribution, mean_abs],
        "percent_metrics": [mean_abs],
        "adhoc_filters": [NOT_BASE_FILTER],
        "timeseries_limit_metric": rank,
        "order_desc": False,
        "row_limit": 1000,
        "server_pagination": False,
        "page_length": 25,
        "include_search": True,
        "table_timestamp_format": "smart_date",
        "column_config": {
            **FEATURE_COLUMN_CONFIG,
            "Rank": {"d3NumberFormat": ",d"},
            contribution["label"]: {"d3NumberFormat": spec.contribution_format},
            mean_abs["label"]: {"d3NumberFormat": spec.number_format},
        },
        "extra_form_data": {},
    }


def feature_value_by_period_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the Feature filter's feature as a line over the day's 48 periods: its value.

    The mean value per ``time_code`` of the one feature the Feature filter
    picks (the base row, which has no value, filtered out). Its own chart and
    its own axis, above ``feature_contribution_by_period_params`` at the same
    width so the time codes line up: a feature's unit is not the forecast's. The
    axis is truncated — a feature's level (30 °C, 20 GWh) is far from zero, and
    the shape over the day is what is read.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The explanation dataset.

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "echarts_timeseries_line",
        "x_axis": BY_PERIOD_X_AXIS,
        "time_grain_sqla": None,
        "x_axis_sort_asc": True,
        "metrics": [sql_metric("avg(feature_value)", "Feature value")],
        "groupby": [],
        "adhoc_filters": [NOT_BASE_FILTER],
        "order_desc": False,
        "row_limit": 10000,
        "seriesType": "line",
        "markerEnabled": True,
        "markerSize": 6,
        "show_legend": False,
        "rich_tooltip": True,
        "y_axis_format": "SMART_NUMBER",
        "y_axis_title": "Feature value",
        # a feature's level runs to millions, so its labels are wide
        "y_axis_title_margin": 60,
        "truncateYAxis": True,
        "y_axis_bounds": [None, None],
        "color_scheme": "supersetColors",
        "x_axis_time_format": "smart_date",
        "extra_form_data": {},
    }


def feature_contribution_by_period_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the Feature filter's feature as bars over the day's 48 periods: its
    contribution.

    The mean contribution per ``time_code`` of the one feature the Feature
    filter picks, in the forecast's unit on an axis through zero — the
    ``bar_params`` chart over ``time_code`` with the contribution for the MAE.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The explanation dataset.

    Returns
    -------
    dict
    """
    return {
        **bar_params(spec, dataset_id, BY_PERIOD_X_AXIS),
        "metrics": [spec.contribution_metric],
        "adhoc_filters": [NOT_BASE_FILTER],
        "row_limit": 10000,
    }


def _horizontal_bar_params(
    dataset_id: int,
    *,
    x_axis: str,
    metric: dict,
    adhoc_filters: list[dict],
    y_axis_format: str,
    y_axis_title: str,
) -> dict:
    """Params for a single-metric horizontal bar chart sorted by that metric.

    Ascending order on a horizontal bar draws the largest value on top.

    Parameters
    ----------
    dataset_id : int
    x_axis : str
        Category column (one bar each).
    metric : dict
        The bar length.
    adhoc_filters : list of dict
    y_axis_format, y_axis_title : str
        Format and title of the value axis.

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "echarts_timeseries_bar",
        "orientation": "horizontal",
        "x_axis": x_axis,
        "time_grain_sqla": None,
        "x_axis_sort": metric["label"],
        "x_axis_sort_asc": True,
        "metrics": [metric],
        "groupby": [],
        "adhoc_filters": adhoc_filters,
        "order_desc": True,
        "row_limit": 1000,
        "show_legend": False,
        "rich_tooltip": True,
        "y_axis_format": y_axis_format,
        "y_axis_title": y_axis_title,
        "y_axis_title_margin": 30,
        "truncateYAxis": False,
        "color_scheme": "supersetColors",
        "x_axis_time_format": "smart_date",
        "extra_form_data": {},
    }


def importance_bar_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the permutation-importance bars: ΔMAE per feature, largest on top.

    The ``TOP_FEATURE_BARS`` features with the largest ΔMAE
    (``TOP_IMPORTANCE_FILTER``), each bar named by its rank and short label
    (``feature_short``); the table below lists every feature.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The importance dataset.

    Returns
    -------
    dict
    """
    return _horizontal_bar_params(
        dataset_id,
        x_axis="feature_short",
        metric=spec.importance_metric,
        adhoc_filters=[TOP_IMPORTANCE_FILTER],
        y_axis_format=spec.axis_format,
        y_axis_title=f"ΔMAE ({spec.unit}) when the feature is shuffled",
    )


def mean_abs_shap_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the mean |SHAP| bars per feature, largest on top.

    The ``TOP_FEATURE_BARS`` features with the largest mean |SHAP| over the run
    (``TOP_MEAN_ABS_SHAP_FILTER``), read off the run-level summary dataset — a
    row per feature, the base left out — instead of adding up the run's
    contribution rows on every view.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The summary dataset.

    Returns
    -------
    dict
    """
    return _horizontal_bar_params(
        dataset_id,
        x_axis="feature_short",
        metric=spec.mean_abs_shap_metric,
        adhoc_filters=[TOP_MEAN_ABS_SHAP_FILTER],
        y_axis_format=spec.axis_format,
        y_axis_title=f"Mean |SHAP| ({spec.unit})",
    )


def importance_table_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the importance table: MAE, permuted MAE, ΔMAE, its std, importance %,
    mean |SHAP|.

    Every feature under its full expression, the largest ΔMAE first, 25 rows a
    page, with a search box.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The importance dataset.

    Returns
    -------
    dict
    """
    mae, permuted, delta, std, pct, mean_abs_shap = (
        spec.importance_mae_metric,
        spec.permuted_mae_metric,
        spec.importance_metric,
        spec.importance_std_metric,
        spec.importance_pct_metric,
        spec.mean_abs_shap_metric,
    )
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "table",
        "query_mode": "aggregate",
        "groupby": ["feature_expression"],
        "metrics": [mae, permuted, delta, std, pct, mean_abs_shap],
        "adhoc_filters": [],
        "timeseries_limit_metric": delta,
        "order_desc": True,
        "row_limit": 1000,
        "server_pagination": False,
        "page_length": 25,
        "include_search": True,
        "table_timestamp_format": "smart_date",
        "column_config": {
            **FEATURE_COLUMN_CONFIG,
            mae["label"]: {"d3NumberFormat": spec.number_format},
            permuted["label"]: {"d3NumberFormat": spec.number_format},
            delta["label"]: {"d3NumberFormat": spec.signed_number_format},
            std["label"]: {"d3NumberFormat": spec.number_format},
            pct["label"]: {"d3NumberFormat": "+.1f"},
            mean_abs_shap["label"]: {"d3NumberFormat": spec.number_format},
        },
        "extra_form_data": {},
    }


def contribution_by_period_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the by-period chart: the contributions stacked over the day's 48
    periods, with the forecast and the actual — both relative to the base — as lines.

    A Superset Mixed Chart. Query A stacks, per ``time_code``, the mean contribution
    of each of the selection's top ``TOP_COMPONENTS`` features and of the
    ``Other features`` group (``component_group``; the grouped metric, because the
    group is a sum; the base row filtered out, see ``NOT_BASE_FILTER``, so the
    bars sit around zero at the contributions' scale). Query B draws two lines on
    the *same* y-axis: ``Forecast − base`` — the signed sum of the bars, which a
    stack of mixed signs has no visible edge for — and ``Actual − base``; the
    vertical gap between the lines is the period's error. Query B is unfiltered:
    both metrics read the period's base off its base row.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The explanation dataset.

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "mixed_timeseries",
        "x_axis": BY_PERIOD_X_AXIS,
        "time_grain_sqla": None,
        # Query A: one stacked series per top feature, and one for the rest
        "metrics": [spec.grouped_contribution_metric],
        "groupby": ["component_group"],
        "adhoc_filters": [NOT_BASE_FILTER],
        "order_desc": False,
        "row_limit": 10000,
        "seriesType": "bar",
        "stack": True,
        "area": False,
        "show_value": False,
        "markerEnabled": False,
        "markerSize": 6,
        "yAxisIndex": 0,
        # Query B: the forecast and the actual relative to the base, as lines
        "metrics_b": [spec.forecast_minus_base_metric, spec.actual_minus_base_metric],
        "groupby_b": [],
        "adhoc_filters_b": [],
        "order_desc_b": False,
        "row_limit_b": 10000,
        "seriesTypeB": "line",
        "stackB": False,
        "areaB": False,
        "show_valueB": False,
        "markerEnabledB": True,
        "markerSizeB": 6,
        "yAxisIndexB": 0,
        # Chart options
        "show_legend": True,
        "legendType": "scroll",
        "legendOrientation": "top",
        "rich_tooltip": True,
        "tooltipTimeFormat": "smart_date",
        "y_axis_format": spec.axis_format,
        "y_axis_title": spec.unit,
        "y_axis_title_margin": 30,
        "truncateYAxis": False,
        "color_scheme": "supersetColors",
        "x_axis_time_format": "smart_date",
        "extra_form_data": {},
    }


def upsert_chart(client: SupersetClient, name: str, dataset_id: int, params: dict) -> int:
    """Create or update a chart (matched by name within its dataset) and return its id.

    An update also clears the chart's saved ``query_context`` (see the inline
    note) so the repo's ``params`` are the only definition that survives.

    Parameters
    ----------
    client : SupersetClient
    name : str
        ``slice_name`` to match on — together with ``dataset_id``, since the
        dashboards share chart names.
    dataset_id : int
    params : dict
        Chart form data; serialized into the saved chart.

    Returns
    -------
    int
    """
    payload = {
        "slice_name": name,
        "datasource_id": dataset_id,
        "datasource_type": "table",
        "viz_type": params["viz_type"],
        "params": json.dumps(params),
    }
    chart_id = client.find_one("chart", slice_name=name, datasource_id=dataset_id)
    if chart_id is None:
        return client._post_json("/api/v1/chart/", payload)["id"]
    # Reset any query context saved from Explore: it snapshots the dataset
    # columns of its day, and the chart-data API (thumbnails, alerts, MCP)
    # replays it — stale after a dataset SQL change. The dashboard itself
    # renders from ``params``.
    client._put_json(f"/api/v1/chart/{chart_id}", {**payload, "query_context": None})
    return chart_id


def _tab_component(key: str, title: str, parents: list[str]) -> dict[str, Any]:
    """One ``TAB`` layout component, top-level or nested, without children yet.

    Parameters
    ----------
    key : str
        The component's layout key (``TAB-<t>`` or ``TAB-<t>-<u>``).
    title : str
        The tab title.
    parents : list of str
        The chain of keys above the tab, root first.

    Returns
    -------
    dict
    """
    return {
        "type": "TAB",
        "id": key,
        "children": [],
        "parents": parents,
        "meta": {"text": title, "defaultText": "Tab title", "placeholder": "Tab title"},
    }


def _add_sections(
    position: dict[str, Any], tab_key: str, parents: list[str], numbering: str, sections: list[dict]
) -> None:
    """Add a tab's sections (headers, rows and their charts) to ``position``, in place.

    Parameters
    ----------
    position : dict
        The layout being built; the tab ``tab_key`` is already in it.
    tab_key : str
        The tab (or sub-tab) the sections go under.
    parents : list of str
        The full chain of keys down to and including ``tab_key``.
    numbering : str
        What the header and row keys carry before their own numbers: ``<t>`` for
        a top-level tab, ``<t>-<u>`` for a sub-tab, so keys never collide.
    sections : list of dict
        ``{"header": str | None, "rows": [[(chart_id, name, width, height), ...], ...]}``.
    """
    for s, section in enumerate(sections):
        if section["header"]:
            header_key = f"HEADER-{numbering}-{s}"
            position[tab_key]["children"].append(header_key)
            position[header_key] = {
                "type": "HEADER",
                "id": header_key,
                "children": [],
                "parents": parents,
                "meta": {
                    "text": section["header"],
                    "headerSize": "MEDIUM_HEADER",
                    "background": "BACKGROUND_TRANSPARENT",
                },
            }
        for r, row in enumerate(section["rows"]):
            row_key = f"ROW-{numbering}-{s}-{r}"
            position[tab_key]["children"].append(row_key)
            position[row_key] = {
                "type": "ROW",
                "id": row_key,
                "children": [],
                "parents": parents,
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
            }
            for chart_id, name, width, height in row:
                chart_key = f"CHART-{chart_id}"
                position[row_key]["children"].append(chart_key)
                position[chart_key] = {
                    "type": "CHART",
                    "id": chart_key,
                    "children": [],
                    "parents": [*parents, row_key],
                    "meta": {
                        "chartId": chart_id,
                        "width": width,
                        "height": height,
                        "sliceName": name,
                    },
                }


def build_position_json(title: str, tabs: list[dict]) -> dict:
    """Dashboard layout: top-level tabs, each a list of optionally headed sections of rows,
    or a row of sub-tabs that each hold such a list.

    Emits the shape Superset itself writes for a tabbed dashboard: the tabs
    container replaces the grid as the root's child (the grid stays, empty)
    and every component carries its full ``parents`` chain — the frontend
    resolves native-filter and cross-filter scopes through those chains. A tab
    with sub-tabs ``TAB-<t>`` has one child, a nested tabs container
    ``TABS-<t>``, whose children are the sub-tabs ``TAB-<t>-<u>``; Superset
    queries only the charts of the open sub-tab.

    Parameters
    ----------
    title : str
        The dashboard header text.
    tabs : list of dict
        Each ``{"title": str, "sections": [...]}`` or ``{"title": str,
        "subtabs": [{"title": str, "sections": [...]}, ...]}``; a section is
        ``{"header": str | None, "rows": [[(chart_id, name, width, height),
        ...], ...]}``. Widths within a row should sum to 12; height is in
        dashboard grid units (~8 px each).

    Returns
    -------
    dict

    Raises
    ------
    ValueError
        If the first tab has sub-tabs: its container's key, ``TABS-0``, is the
        root container's.
    """
    tabs_key = "TABS-0"
    position: dict[str, Any] = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": [tabs_key]},
        tabs_key: {
            "type": "TABS",
            "id": tabs_key,
            "children": [],
            "parents": ["ROOT_ID"],
            "meta": {},
        },
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
        "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": title}},
    }
    for t, tab in enumerate(tabs):
        tab_key = f"TAB-{t}"
        position[tabs_key]["children"].append(tab_key)
        position[tab_key] = _tab_component(tab_key, tab["title"], ["ROOT_ID", tabs_key])
        parents = ["ROOT_ID", tabs_key, tab_key]
        if "subtabs" not in tab:
            _add_sections(position, tab_key, parents, str(t), tab["sections"])
            continue
        subtabs_key = f"TABS-{t}"
        if subtabs_key == tabs_key:
            raise ValueError(f"the first tab cannot have sub-tabs: {tabs_key} is the root's key")
        position[tab_key]["children"].append(subtabs_key)
        position[subtabs_key] = {
            "type": "TABS",
            "id": subtabs_key,
            "children": [],
            "parents": parents,
            "meta": {},
        }
        for u, subtab in enumerate(tab["subtabs"]):
            subtab_key = f"TAB-{t}-{u}"
            position[subtabs_key]["children"].append(subtab_key)
            position[subtab_key] = _tab_component(
                subtab_key, subtab["title"], [*parents, subtabs_key]
            )
            _add_sections(
                position,
                subtab_key,
                [*parents, subtabs_key, subtab_key],
                f"{t}-{u}",
                subtab["sections"],
            )
    return position


def _select_filter(
    filter_id: str,
    name: str,
    column: str,
    dataset_id: int,
    *,
    excluded: list[int],
    default: str | None,
    default_to_first: bool,
    required: bool,
    sort_ascending: bool,
    cascade_parent_ids: list[str],
    description: str,
) -> dict:
    """One single-select native filter on ``column`` of ``dataset_id``.

    Parameters
    ----------
    filter_id, name : str
        Stable id (``NATIVE_FILTER-…``) and the label shown in the filter bar.
    column : str
        Dataset column the filter reads its values from and filters on; a
        chart on another dataset is filtered too when that dataset has a
        column of the same name.
    dataset_id : int
    excluded : list of int
        Charts the filter must NOT apply to.
    default : str or None
        Explicit on-load value; None falls back to ``default_to_first``.
    default_to_first : bool
        Stage the first option when there is no explicit default.
    required : bool
        Whether a value must be selected (``enableEmptyFilter``).
    sort_ascending : bool
    cascade_parent_ids : list of str
        Filters whose selection restricts this filter's options.
    description : str

    Returns
    -------
    dict
    """
    default_mask: dict[str, Any]
    if default is None:
        default_mask = {"extraFormData": {}, "filterState": {}}
    else:
        default_mask = {
            "extraFormData": {"filters": [{"col": column, "op": "IN", "val": [default]}]},
            "filterState": {"value": [default], "label": default},
        }
    return {
        "id": filter_id,
        "name": name,
        "filterType": "filter_select",
        "targets": [{"column": {"name": column}, "datasetId": dataset_id}],
        "defaultDataMask": default_mask,
        "controlValues": {
            "multiSelect": False,
            "enableEmptyFilter": required,
            # Set alongside an explicit default, not instead of it. The default
            # applies on load, which staging alone does not; the setting is what
            # re-resolves a cascading filter when its parent changes, and without
            # it the old value survives into a parent that has no row for it —
            # the Feature filter kept one run's first feature after a switch to
            # a run that ranks another first, and both its charts came back
            # empty. Checked against the running Superset, both ways.
            "defaultToFirstItem": default_to_first,
            "inverseSelection": False,
            "searchAllOptions": False,
            "sortAscending": sort_ascending,
        },
        "cascadeParentIds": cascade_parent_ids,
        "scope": {"rootPath": ["ROOT_ID"], "excluded": excluded},
        "type": "NATIVE_FILTER",
        "description": description,
    }


def build_native_filters(
    *,
    dataset_id: int,
    run_excluded: list[int],
    default_run_label: str | None,
    explanation_dataset_id: int,
    day_excluded: list[int],
    default_day_label: str | None,
    period_excluded: list[int],
    summary_dataset_id: int,
    feature_excluded: list[int],
    default_feature_label: str | None,
    baseline_excluded: list[int],
    default_baseline_label: str | None,
    with_baseline: bool = True,
) -> list[dict]:
    """Native filter configuration: Run (whole dashboard), Day and Period (the
    Explanation tab's per-selection charts; Day also the explanation-vs-baseline
    section), Feature (the two feature-by-period charts), Baseline (the Compare
    tab only).

    Parameters
    ----------
    dataset_id : int
        Analysis dataset the Run and Baseline filters read their options
        from (``run_label`` / its alias ``baseline_run_label``).
    run_excluded : list of int
        Charts the Run filter must NOT apply to.
    default_run_label : str or None
        Explicit on-load run; None falls back to ``defaultToFirstItem``.
    explanation_dataset_id : int
        Explanation dataset the Day and Period filters read their values from.
    day_excluded : list of int
        Charts outside the Day filter's scope (everything but the Day overview
        and Single period sub-tabs and the explanation-vs-baseline section).
    default_day_label : str or None
        Explicit on-load day (the default run's last delivery day).
    period_excluded : list of int
        Charts outside the Period filter's scope (everything but the Day
        overview and Single period sub-tabs).
    summary_dataset_id : int
        Summary dataset the Feature filter reads its options from: a row per
        feature of the run, so the list is cheap and complete. The explanation
        dataset builds the same ``feature_pick`` text (``FEATURE_PICK_SQL``),
        which is how the value filters its charts.
    feature_excluded : list of int
        Charts outside the Feature filter's scope (everything but
        ``FEATURE_BY_PERIOD_CHART_NAMES``).
    default_feature_label : str or None
        Explicit on-load feature — the default run's first by mean |SHAP|.
        None leaves the filter to stage the first option, and its two charts
        then average every feature until someone clicks Apply.
    baseline_excluded : list of int
        Charts outside the Baseline filter's scope (everything but the
        Compare tab — on the analysis dataset the alias column would
        otherwise empty every chart).
    default_baseline_label : str or None
        Explicit on-load baseline; None leaves the filter empty (no first-item
        fallback: the Compare tab shows "No data" until a baseline is picked).

    with_baseline : bool, optional
        Whether to build the Baseline filter. False while the Compare tab is
        not placed: its only charts are that tab's, and on the analysis dataset
        the alias column would otherwise empty every chart it reached.

    Returns
    -------
    list of dict
        ``[run, day, period, feature]``, and ``baseline`` last when it is built.
    """
    filters = [
        _select_filter(
            "NATIVE_FILTER-run",
            "Run",
            "run_label",
            dataset_id,
            excluded=run_excluded,
            default=default_run_label,
            default_to_first=True,
            required=True,
            sort_ascending=False,
            cascade_parent_ids=[],
            description="published_at | area | strategy | run_id prefix (newest first)",
        ),
        _select_filter(
            "NATIVE_FILTER-day",
            "Day",
            "trade_date_label",
            explanation_dataset_id,
            excluded=day_excluded,
            default=default_day_label,
            default_to_first=True,
            required=False,
            sort_ascending=False,
            cascade_parent_ids=["NATIVE_FILTER-run"],
            description=(
                "Delivery day explained (empty = the run's mean decomposition); "
                "the Explain links of the day tables set it"
            ),
        ),
        _select_filter(
            "NATIVE_FILTER-period",
            "Period",
            "period_label",
            explanation_dataset_id,
            excluded=period_excluded,
            default=None,
            default_to_first=False,
            required=False,
            sort_ascending=True,
            cascade_parent_ids=["NATIVE_FILTER-run", "NATIVE_FILTER-day"],
            description=(
                "Half-hour period (empty = the whole day); "
                "the Single period tab stays empty until one is picked"
            ),
        ),
        _select_filter(
            "NATIVE_FILTER-feature",
            "Feature",
            "feature_pick",
            summary_dataset_id,
            excluded=feature_excluded,
            default=default_feature_label,
            default_to_first=True,
            required=True,
            sort_ascending=True,
            cascade_parent_ids=["NATIVE_FILTER-run"],
            description=(
                "The feature the two Feature … by period charts draw; "
                "listed by mean |SHAP| rank, so it opens on the run's first"
            ),
        ),
    ]
    if not with_baseline:
        return filters
    return [
        *filters,
        _select_filter(
            "NATIVE_FILTER-baseline",
            "Baseline",
            "baseline_run_label",
            dataset_id,
            excluded=baseline_excluded,
            default=default_baseline_label,
            default_to_first=False,
            required=True,
            sort_ascending=False,
            cascade_parent_ids=[],
            description=(
                "Reference run; the Compare tab shows the Run (candidate) against it over "
                "the periods both runs scored"
            ),
        ),
    ]


def build_chart_configuration(emitters: dict[int, list[int]], all_charts: list[int]) -> dict:
    """Per-chart cross-filter scopes: clicking a row of an emitter table
    selects that day on its target charts only.

    Parameters
    ----------
    emitters : dict of int to list of int
        Emitter chart id → the charts that receive its cross-filter.
    all_charts : list of int
        Every chart on the dashboard; those not targeted (the emitter
        included) are excluded from its scope.

    Returns
    -------
    dict
        ``json_metadata["chart_configuration"]``.
    """
    return {
        str(emitter): {
            "id": emitter,
            "crossFilters": {
                "scope": {
                    "rootPath": ["ROOT_ID"],
                    "excluded": [c for c in all_charts if c not in targets],
                },
                "chartsInScope": targets,
            },
        }
        for emitter, targets in emitters.items()
    }


def upsert_dashboard(
    client: SupersetClient,
    title: str,
    slug: str,
    position: dict,
    native_filters: list[dict],
    chart_configuration: dict,
) -> int:
    """Create or update the dashboard and return its id.

    Parameters
    ----------
    client : SupersetClient
    title, slug : str
        The dashboard title (matched on reruns) and URL slug.
    position : dict
        ``position_json`` layout from :func:`build_position_json`.
    native_filters : list of dict
        ``native_filter_configuration`` from :func:`build_native_filters`.
    chart_configuration : dict
        Per-chart cross-filter scopes from :func:`build_chart_configuration`.
        ``label_colors`` pins the comparison roles' colours (``LABEL_COLORS``).

    Returns
    -------
    int
    """
    dashboard_id = client.find_one("dashboard", dashboard_title=title)
    if dashboard_id is None:
        dashboard_id = client._post_json(
            "/api/v1/dashboard/", {"dashboard_title": title, "slug": slug}
        )["id"]
    json_metadata = {
        "native_filter_configuration": native_filters,
        "cross_filters_enabled": True,
        "chart_configuration": chart_configuration,
        "color_scheme": "",
        "expanded_slices": {},
        "label_colors": LABEL_COLORS,
        "refresh_frequency": 0,
        "timed_refresh_immune_slices": [],
    }
    client._put_json(
        f"/api/v1/dashboard/{dashboard_id}",
        {
            "dashboard_title": title,
            "slug": slug,
            "position_json": json.dumps(position),
            "json_metadata": json.dumps(json_metadata),
            "published": True,
        },
    )
    return dashboard_id


def attach_charts(
    client: SupersetClient,
    dashboard_id: int,
    chart_ids: list[int],
    attached: dict[int, list[int]],
) -> None:
    """Link charts to the dashboard (position_json alone is not enough).

    Parameters
    ----------
    client : SupersetClient
    dashboard_id : int
    chart_ids : list of int
    attached : dict of int to list of int
        What each chart is attached to already (``charts_of_dashboard``). The
        write replaces the whole many-to-many list, so every other dashboard a
        chart is on goes back with it; a chart this map does not know is new,
        and lands on this dashboard alone.
    """
    for chart_id in chart_ids:
        dashboards = sorted({*attached.get(chart_id, []), dashboard_id})
        client._put_json(f"/api/v1/chart/{chart_id}", {"dashboards": dashboards})


def detach_stale_charts(
    client: SupersetClient,
    dashboard_id: int,
    chart_ids: list[int],
    attached: dict[int, list[int]],
) -> list[int]:
    """Unlink the charts an earlier build left on the dashboard, and return their ids.

    Superset renders every chart attached to a dashboard, appending the ones
    the layout does not place to the foot of the first tab — so a chart this
    build no longer makes (a dropped one, or one that moved to another dataset
    and is therefore a new chart) would still show, and may even show an error
    where its dataset has since lost a column it reads.

    Parameters
    ----------
    client : SupersetClient
    dashboard_id : int
    chart_ids : list of int
        Every chart this build placed; the rest are detached.
    attached : dict of int to list of int
        What each chart is attached to already (``charts_of_dashboard``).

    Returns
    -------
    list of int
        The detached charts, in the order Superset listed them. The charts
        themselves are kept, and so is every other dashboard they are on —
        the link is many-to-many and the write replaces the whole list, so a
        chart someone reused elsewhere must come back with that one still on
        it.
    """
    placed = set(chart_ids)
    stale = [chart_id for chart_id in attached if chart_id not in placed]
    for chart_id in stale:
        others = [d for d in attached[chart_id] if d != dashboard_id]
        client._put_json(f"/api/v1/chart/{chart_id}", {"dashboards": others})
    return stale


# A chart factory: (chart name, params, dataset id) -> chart id. build_dashboard
# binds one to its client so the tab builders below stay free of HTTP.
ChartFactory = Callable[[str, dict, int], int]

# The Compare tab's charts on the explanation-comparison dataset: they read a
# Day like the Explanation tab (in the Day filter's scope) and are what a day
# click in that tab's day tables drills into, unlike the rest of the tab.
EXPLANATION_VS_BASELINE_CHART_NAMES = (
    "Δ base value vs baseline",
    "Δ net feature effect vs baseline",
    "Δ forecast vs baseline",
    "SHAP waterfall vs baseline",
    "Contributions vs baseline",
)

# The Explanation tab's run-level charts (its Feature importance sub-tab):
# outside the Day and Period filters, because importance describes the whole run.
RUN_LEVEL_CHART_NAMES = (
    "Permutation importance",
    "Mean |SHAP| by feature",
    "Feature importance table",
)
# The two Day overview charts of one feature over the day, stacked at the same
# width so their time codes line up: the Feature filter's whole scope.
FEATURE_BY_PERIOD_CHART_NAMES = (
    "Feature value by period",
    "Feature contribution by period",
)
IMPORTANCE_SECTION_HEADER = (
    "Feature importance — ΔMAE when a feature is shuffled across the run's periods "
    "(the run, not the Day); correlated features share importance"
)


@dataclass(frozen=True)
class DashboardTab:
    """One top-level tab: its charts by name, in creation order, and its layout.

    Parameters
    ----------
    title : str
        The tab title.
    charts : dict of str to int
        Chart name → Superset chart id, in creation order.
    sections : list of dict
        ``build_position_json`` sections: ``{"header": str | None, "rows":
        [[(chart_id, name, width, height), ...], ...]}``; empty for a tab with
        sub-tabs.
    subtabs : list of dict, optional
        ``build_position_json`` sub-tabs, ``{"title": str, "sections": [...]}``
        each, for a tab that lays its charts out under sub-tabs.
    """

    title: str
    charts: dict[str, int]
    sections: list[dict[str, Any]]
    subtabs: list[dict[str, Any]] | None = None

    @property
    def chart_ids(self) -> list[int]:
        """The tab's chart ids in creation order."""
        return list(self.charts.values())

    @property
    def layout(self) -> dict[str, Any]:
        """The tab as ``build_position_json`` takes it."""
        if self.subtabs is not None:
            return {"title": self.title, "subtabs": self.subtabs}
        return {"title": self.title, "sections": self.sections}


def _chart_adder(
    chart: ChartFactory, charts: dict[str, int], dataset_id: int
) -> Callable[[str, dict], int]:
    """An ``add(name, params)`` that creates a chart on ``dataset_id`` through ``chart``
    and records its id under ``name`` in ``charts``.

    Parameters
    ----------
    chart : ChartFactory
    charts : dict of str to int
        The tab's chart registry, filled in creation order.
    dataset_id : int
        The dataset every chart added through this function reads.

    Returns
    -------
    callable
    """

    def add(name: str, params: dict) -> int:
        charts[name] = chart(name, params, dataset_id)
        return charts[name]

    return add


def build_accuracy_tab(chart: ChartFactory, spec: DashboardSpec, dataset_id: int) -> DashboardTab:
    """Create the Accuracy tab's charts on the analysis dataset and lay them out.

    Parameters
    ----------
    chart : ChartFactory
    spec : DashboardSpec
    dataset_id : int
        The analysis dataset.

    Returns
    -------
    DashboardTab
    """
    charts: dict[str, int] = {}
    add = _chart_adder(chart, charts, dataset_id)
    unit, fmt = spec.unit, spec.number_format

    # KPI tiles
    kpi_mae = add("Overall MAE", big_number_params(dataset_id, spec.mae_metric, unit, fmt))
    kpi_bias = add(
        "Bias (mean error)",
        big_number_params(
            dataset_id, spec.bias_metric, f"{unit}; + = over-forecast", spec.signed_number_format
        ),
    )
    kpi_rmse = add("RMSE", big_number_params(dataset_id, spec.rmse_metric, unit, fmt))
    kpi_ratio = add(
        "RMSE / MAE",
        big_number_params(dataset_id, spec.rmse_mae_metric, ">1.3 = spike-heavy errors", ",.2f"),
    )
    kpi_wape = add(
        "WAPE", big_number_params(dataset_id, spec.wape_metric, "Σ|error| / Σ actual", ".1%")
    )
    kpi_p90 = add("P90 abs error", big_number_params(dataset_id, spec.p90_metric, unit, fmt))

    # Error structure
    mae_year = add("MAE by year", bar_params(spec, dataset_id, "year"))
    mae_tc = add("MAE by time code", bar_params(spec, dataset_id, "time_code"))
    heat_tc = add("MAE by year and time code", heatmap_params(spec, dataset_id, "time_code"))
    heat_month = add("MAE by year and month", heatmap_params(spec, dataset_id, "month"))
    mae_dow = add("MAE by day of week", bar_params(spec, dataset_id, "day_of_week"))
    # the hours each day part covers, and each day type's share of the run, ride
    # on the bar labels: a bar per category otherwise reads as an equal weight
    mae_daypart = add(
        "MAE by day part", bar_params(spec, dataset_id, "day_part_hours", label_rotation=45)
    )
    mae_daytype = add(
        "MAE by day type", bar_params(spec, dataset_id, "day_type_share", label_rotation=45)
    )

    # Calibration & distribution
    mae_band = add(spec.band_chart_title, bar_params(spec, dataset_id, spec.band_col))
    calibration = add(spec.calibration_chart_title, calibration_params(spec, dataset_id))
    histogram = add("Error distribution", histogram_params(spec, dataset_id))

    # Drilldown
    worst_days = add("Worst days", worst_days_params(spec, dataset_id))
    detail = add("Forecast vs actual (30-min detail)", detail_params(spec, dataset_id))

    sections: list[dict[str, Any]] = [
        {
            "header": None,
            "rows": [
                [
                    (kpi_mae, "Overall MAE", 2, 24),
                    (kpi_bias, "Bias (mean error)", 2, 24),
                    (kpi_rmse, "RMSE", 2, 24),
                    (kpi_ratio, "RMSE / MAE", 2, 24),
                    (kpi_wape, "WAPE", 2, 24),
                    (kpi_p90, "P90 abs error", 2, 24),
                ]
            ],
        },
        {
            "header": "Error structure",
            "rows": [
                [(mae_year, "MAE by year", 4, 36), (mae_tc, "MAE by time code", 8, 36)],
                [(heat_tc, "MAE by year and time code", 12, 50)],
                [(heat_month, "MAE by year and month", 12, 46)],
                [
                    (mae_dow, "MAE by day of week", 4, 44),
                    (mae_daypart, "MAE by day part", 4, 44),
                    (mae_daytype, "MAE by day type", 4, 44),
                ],
            ],
        },
        {
            "header": "Calibration & distribution",
            "rows": [
                [
                    (mae_band, spec.band_chart_title, 5, 42),
                    (calibration, spec.calibration_chart_title, 7, 42),
                ],
                [(histogram, "Error distribution", 12, 38)],
            ],
        },
        {
            "header": "Drilldown",
            "rows": [
                [(worst_days, "Worst days", 12, 40)],
                [(detail, "Forecast vs actual (30-min detail)", 12, 60)],
            ],
        },
    ]
    return DashboardTab("Accuracy", charts, sections)


def build_explanation_tab(
    chart: ChartFactory,
    spec: DashboardSpec,
    explanation_id: int,
    period_id: int,
    summary_id: int,
    importance_id: int,
) -> DashboardTab:
    """Create the Explanation tab's charts and lay them out under three sub-tabs.

    Superset queries only the open sub-tab's charts. **Day overview** reads the
    explanation dataset: the waterfall and the by-period stack of the
    selection's top features, the Feature filter's feature over the day (value,
    then contribution) and the table of every feature. **Single period** reads
    the period dataset, which has no rows until a Period is picked: the four
    tiles and the table of values and contributions. **Feature importance**
    describes the run: the permutation bars and the table on the importance
    dataset, the mean |SHAP| bars on the summary dataset.

    Parameters
    ----------
    chart : ChartFactory
    spec : DashboardSpec
    explanation_id : int
        The explanation dataset.
    period_id : int
        The period dataset (the explanation SQL, gated on the Period filter).
    summary_id : int
        The summary dataset (the run-level contribution summary).
    importance_id : int
        The importance dataset.

    Returns
    -------
    DashboardTab
        With ``subtabs``; its ``sections`` are empty.
    """
    charts: dict[str, int] = {}
    unit, fmt = spec.unit, spec.number_format
    per_period = f"{unit}; mean per period"

    # Day overview
    add = _chart_adder(chart, charts, explanation_id)
    waterfall = add("SHAP waterfall", waterfall_params(spec, explanation_id))
    by_period = add("Contributions by period", contribution_by_period_params(spec, explanation_id))
    value_name, contribution_name = FEATURE_BY_PERIOD_CHART_NAMES
    feature_value = add(value_name, feature_value_by_period_params(spec, explanation_id))
    feature_contribution = add(
        contribution_name, feature_contribution_by_period_params(spec, explanation_id)
    )
    all_features = add("All features", all_features_table_params(spec, explanation_id))

    # Single period
    add_period = _chart_adder(chart, charts, period_id)
    kpi_base = add_period(
        "Base value",
        big_number_params(
            period_id,
            spec.base_value_metric,
            f"{unit}; model expected value, mean per period",
            fmt,
        ),
    )
    kpi_forecast = add_period(
        "Forecast (selection)",
        big_number_params(period_id, avg_metric(spec.forecast_col, "Forecast"), per_period, fmt),
    )
    kpi_actual = add_period(
        "Actual (selection)",
        big_number_params(period_id, avg_metric(spec.actual_col, "Actual"), per_period, fmt),
    )
    kpi_net = add_period(
        "Net feature effect",
        big_number_params(
            period_id,
            spec.net_effect_metric,
            f"{unit}; forecast − base",
            spec.signed_number_format,
        ),
    )
    feature_table = add_period(
        "Feature values & contributions", feature_table_params(spec, period_id)
    )

    # Feature importance
    add_importance = _chart_adder(chart, charts, importance_id)
    add_summary = _chart_adder(chart, charts, summary_id)
    bars = add_importance("Permutation importance", importance_bar_params(spec, importance_id))
    mean_shap = add_summary("Mean |SHAP| by feature", mean_abs_shap_params(spec, summary_id))
    table = add_importance("Feature importance table", importance_table_params(spec, importance_id))

    subtabs: list[dict[str, Any]] = [
        {
            "title": "Day overview",
            "sections": [
                {
                    "header": None,
                    "rows": [
                        [(waterfall, "SHAP waterfall", 12, 46)],
                        [(by_period, "Contributions by period", 12, 44)],
                        [(feature_value, value_name, 12, 28)],
                        [(feature_contribution, contribution_name, 12, 28)],
                        [(all_features, "All features", 12, 50)],
                    ],
                }
            ],
        },
        {
            "title": "Single period",
            "sections": [
                {
                    "header": "One period — pick a Period (and a Day) to fill this tab",
                    "rows": [
                        [
                            (kpi_base, "Base value", 3, 24),
                            (kpi_forecast, "Forecast (selection)", 3, 24),
                            (kpi_actual, "Actual (selection)", 3, 24),
                            (kpi_net, "Net feature effect", 3, 24),
                        ],
                        [(feature_table, "Feature values & contributions", 12, 60)],
                    ],
                }
            ],
        },
        {
            "title": "Feature importance",
            "sections": [
                {
                    "header": IMPORTANCE_SECTION_HEADER,
                    "rows": [
                        [(bars, "Permutation importance", 12, 60)],
                        [(mean_shap, "Mean |SHAP| by feature", 12, 60)],
                        [(table, "Feature importance table", 12, 50)],
                    ],
                }
            ],
        },
    ]
    return DashboardTab("Explanation", charts, [], subtabs)


def build_compare_tab(
    chart: ChartFactory,
    spec: DashboardSpec,
    comparison_id: int,
    explanation_comparison_id: int,
) -> DashboardTab:
    """Create the Compare tab's charts and lay them out.

    The accuracy comparison charts read the comparison dataset; the
    ``EXPLANATION_VS_BASELINE_CHART_NAMES`` charts read the
    explanation-comparison dataset.

    Parameters
    ----------
    chart : ChartFactory
    spec : DashboardSpec
    comparison_id : int
        The comparison dataset (candidate vs baseline accuracy rows).
    explanation_comparison_id : int
        The explanation-comparison dataset (candidate vs baseline contributions).

    Returns
    -------
    DashboardTab
    """
    charts: dict[str, int] = {}
    add = _chart_adder(chart, charts, comparison_id)
    unit, fmt = spec.unit, spec.number_format
    signed = spec.signed_number_format

    cmp_base_mae = add(
        "Baseline MAE",
        big_number_params(
            comparison_id,
            spec.baseline_mae_metric,
            f"{unit}; the Baseline run, matched periods",
            fmt,
        ),
    )
    cmp_cand_mae = add(
        "Candidate MAE",
        big_number_params(
            comparison_id, spec.candidate_mae_metric, f"{unit}; the Run, matched periods", fmt
        ),
    )
    cmp_delta_mae = add(
        "ΔMAE vs baseline",
        delta_big_number_params(
            comparison_id, spec.delta_mae_metric, f"{unit}; − = candidate better", signed
        ),
    )
    cmp_delta_pct = add(
        "ΔMAE % vs baseline",
        delta_big_number_params(
            comparison_id, spec.delta_mae_pct_metric, "%; − = candidate better", "+,.1f"
        ),
    )
    cmp_delta_bias = add(
        "Δ|bias| vs baseline",
        delta_big_number_params(
            comparison_id, spec.delta_abs_bias_metric, f"{unit}; − = candidate less biased", signed
        ),
    )
    cmp_delta_wape = add(
        "ΔWAPE vs baseline",
        delta_big_number_params(
            comparison_id, spec.delta_wape_metric, "− = candidate better", "+.2%"
        ),
    )
    cmp_coverage = add(
        "Matched coverage",
        big_number_params(
            comparison_id,
            spec.matched_coverage_metric,
            "share of the candidate's periods the baseline also scored",
            ".1%",
        ),
    )
    cmp_days = add(
        "Matched days",
        big_number_params(
            comparison_id, spec.matched_days_metric, "delivery days both runs scored", ",d"
        ),
    )
    cmp_days_lower = add(
        "Days candidate lower",
        big_number_params(
            comparison_id,
            spec.days_candidate_lower_metric,
            "share of matched days with a lower daily MAE",
            ".1%",
        ),
    )
    cmp_median = add(
        "Median daily ΔMAE",
        delta_big_number_params(
            comparison_id, spec.median_daily_delta_metric, f"{unit}; − = candidate better", signed
        ),
    )
    cmp_tc = add("ΔMAE % by time code", delta_bar_params(spec, comparison_id, "time_code"))
    cmp_daypart = add("ΔMAE % by day part", delta_bar_params(spec, comparison_id, "day_part_hours"))
    cmp_daytype = add("ΔMAE % by day type", delta_bar_params(spec, comparison_id, "day_type_share"))
    cmp_dow = add("ΔMAE % by day of week", delta_bar_params(spec, comparison_id, "day_of_week"))
    cmp_band = add(
        spec.delta_band_chart_title, delta_bar_params(spec, comparison_id, spec.band_col)
    )
    cmp_year = add("ΔMAE % by year", delta_bar_params(spec, comparison_id, "year"))
    cmp_heat_month = add(
        "ΔMAE % by year and month", delta_heatmap_params(spec, comparison_id, "month")
    )
    cmp_heat_tc = add(
        "ΔMAE % by year and time code", delta_heatmap_params(spec, comparison_id, "time_code")
    )
    cmp_daily = add("Daily ΔMAE", daily_delta_bar_params(spec, comparison_id))
    cmp_cumulative = add(
        "Cumulative error reduction", cumulative_reduction_params(spec, comparison_id)
    )
    cmp_improved = add("Most improved days", ranked_days_params(spec, comparison_id, improved=True))
    cmp_worsened = add(
        "Most worsened days", ranked_days_params(spec, comparison_id, improved=False)
    )
    cmp_detail = add(
        "Candidate vs baseline vs actual (30-min detail)",
        comparison_detail_params(spec, comparison_id),
    )

    # Explanation vs baseline: the explanation-comparison dataset, filtered by
    # Run + Baseline + Day
    x_id = explanation_comparison_id
    add_x = _chart_adder(chart, charts, x_id)
    x_fmt = spec.contribution_format
    d_base, d_net, d_forecast, d_waterfall, d_table = EXPLANATION_VS_BASELINE_CHART_NAMES
    xp_base = add_x(
        d_base,
        delta_big_number_params(
            x_id,
            spec.delta_base_value_metric,
            f"{unit}; candidate − baseline, mean per period",
            x_fmt,
        ),
    )
    xp_net = add_x(
        d_net,
        delta_big_number_params(
            x_id, spec.delta_net_effect_metric, f"{unit}; Σ feature Δ per period", x_fmt
        ),
    )
    xp_forecast = add_x(
        d_forecast,
        delta_big_number_params(
            x_id, spec.delta_forecast_metric, f"{unit}; = Δ base + Δ net effect", x_fmt
        ),
    )
    xp_waterfall = add_x(d_waterfall, delta_waterfall_params(spec, x_id))
    xp_table = add_x(d_table, delta_feature_table_params(spec, x_id))

    sections: list[dict[str, Any]] = [
        {
            "header": None,
            "rows": [
                [
                    (cmp_base_mae, "Baseline MAE", 2, 24),
                    (cmp_cand_mae, "Candidate MAE", 2, 24),
                    (cmp_delta_mae, "ΔMAE vs baseline", 2, 24),
                    (cmp_delta_pct, "ΔMAE % vs baseline", 2, 24),
                    (cmp_delta_bias, "Δ|bias| vs baseline", 2, 24),
                    (cmp_delta_wape, "ΔWAPE vs baseline", 2, 24),
                ],
                [
                    (cmp_coverage, "Matched coverage", 3, 24),
                    (cmp_days, "Matched days", 3, 24),
                    (cmp_days_lower, "Days candidate lower", 3, 24),
                    (cmp_median, "Median daily ΔMAE", 3, 24),
                ],
            ],
        },
        {
            "header": "Where the candidate wins (ΔMAE % vs baseline)",
            "rows": [
                [(cmp_tc, "ΔMAE % by time code", 12, 36)],
                [
                    (cmp_daypart, "ΔMAE % by day part", 4, 36),
                    (cmp_daytype, "ΔMAE % by day type", 4, 36),
                    (cmp_dow, "ΔMAE % by day of week", 4, 36),
                ],
                [
                    (cmp_band, spec.delta_band_chart_title, 6, 36),
                    (cmp_year, "ΔMAE % by year", 6, 36),
                ],
                [(cmp_heat_month, "ΔMAE % by year and month", 12, 46)],
                [(cmp_heat_tc, "ΔMAE % by year and time code", 12, 50)],
            ],
        },
        {
            "header": "Day by day",
            "rows": [
                [(cmp_daily, "Daily ΔMAE", 12, 44)],
                [(cmp_cumulative, "Cumulative error reduction", 12, 40)],
                [(cmp_improved, "Most improved days", 12, 40)],
                [(cmp_worsened, "Most worsened days", 12, 40)],
            ],
        },
        {
            "header": "Explanation vs baseline (SHAP; mean per period of the Day, or of the run when Day is empty)",
            "rows": [
                [
                    (xp_base, d_base, 4, 24),
                    (xp_net, d_net, 4, 24),
                    (xp_forecast, d_forecast, 4, 24),
                ],
                [(xp_waterfall, d_waterfall, 12, 46)],
                [(xp_table, d_table, 12, 40)],
            ],
        },
        {
            "header": "Detail",
            "rows": [[(cmp_detail, "Candidate vs baseline vs actual (30-min detail)", 12, 60)]],
        },
    ]
    return DashboardTab("Compare", charts, sections)


def build_dashboard(
    client: SupersetClient,
    database_id: int,
    spec: DashboardSpec,
    baseline_run: str | None = None,
) -> int:
    """Build or refresh one task's dashboard end to end and return its id.

    Parameters
    ----------
    client : SupersetClient
    database_id : int
        Superset id of the Spark Thriftserver connection.
    spec : DashboardSpec
    baseline_run : str, optional
        ``run_id`` or prefix for the Baseline filter's default; see
        ``run_defaults``.

    Returns
    -------
    int
    """

    def dataset(
        name: str,
        sql: str,
        columns: list[tuple[str, str, bool]],
        main_dttm_col: str = "trade_datetime",
    ) -> int:
        dataset_id = upsert_dataset(
            client, database_id, name, sql, columns, main_dttm_col=main_dttm_col
        )
        logger.info("dataset {}: id={}", name, dataset_id)
        return dataset_id

    dataset_id = dataset(spec.dataset_name, spec.dataset_sql, spec.dataset_columns)
    explanation_id = dataset(
        spec.explanation_dataset_name,
        spec.explanation_dataset_sql,
        spec.explanation_dataset_columns,
    )
    explanation_period_id = dataset(
        spec.explanation_period_dataset_name,
        spec.explanation_period_dataset_sql,
        spec.explanation_dataset_columns,
    )
    summary_id = dataset(
        spec.summary_dataset_name,
        spec.summary_dataset_sql,
        spec.summary_dataset_columns,
        main_dttm_col="published_at",
    )
    comparison_id = dataset(
        spec.comparison_dataset_name, spec.comparison_dataset_sql, spec.comparison_dataset_columns
    )
    explanation_comparison_id = dataset(
        spec.explanation_comparison_dataset_name,
        spec.explanation_comparison_dataset_sql,
        spec.explanation_comparison_dataset_columns,
    )
    importance_id = dataset(
        spec.importance_dataset_name,
        spec.importance_dataset_sql,
        spec.importance_dataset_columns,
        main_dttm_col="published_at",
    )

    def chart(name: str, params: dict, on: int) -> int:
        chart_id = upsert_chart(client, name, on, params)
        logger.info("chart {}: {}", chart_id, name)
        return chart_id

    accuracy = build_accuracy_tab(chart, spec, dataset_id)
    explanation = build_explanation_tab(
        chart, spec, explanation_id, explanation_period_id, summary_id, importance_id
    )
    compare = (
        build_compare_tab(chart, spec, comparison_id, explanation_comparison_id)
        if BUILD_COMPARE_TAB
        else None
    )
    tabs = [accuracy, explanation, *([compare] if compare else [])]
    all_charts = [chart_id for tab in tabs for chart_id in tab.chart_ids]

    worst_days = accuracy.charts["Worst days"]
    detail = accuracy.charts["Forecast vs actual (30-min detail)"]
    explained_vs_baseline = (
        [compare.charts[name] for name in EXPLANATION_VS_BASELINE_CHART_NAMES] if compare else []
    )
    # The Explanation tab's charts of a selection (the Period filter's scope): its
    # Day overview and Single period sub-tabs, not the run-level Feature importance
    # one. The Day filter's scope adds the Compare tab's explanation-vs-baseline
    # section; the Feature filter's is the two feature-by-period charts.
    per_selection = [
        chart_id
        for name, chart_id in explanation.charts.items()
        if name not in RUN_LEVEL_CHART_NAMES
    ]
    explained = [*per_selection, *explained_vs_baseline]
    feature_by_period = [explanation.charts[name] for name in FEATURE_BY_PERIOD_CHART_NAMES]
    # A day click in a day table drills into the 30-minute detail charts and the
    # explanation-vs-baseline section, never into the Explanation tab: its dataset
    # ranks the features of the pinned Day, and a cross-filter arrives after that
    # ranking. The day tables' Explain links set the Day filter instead.
    emitters = {worst_days: [detail]}
    if compare:
        cmp_detail = compare.charts["Candidate vs baseline vs actual (30-min detail)"]
        emitters[worst_days] = [detail, cmp_detail, *explained_vs_baseline]
        for name in ("Most improved days", "Most worsened days"):
            emitters[compare.charts[name]] = [cmp_detail, *explained_vs_baseline]
    chart_configuration = build_chart_configuration(emitters, all_charts)

    defaults = run_defaults(client, database_id, spec, baseline_run)
    default_run = None if defaults is None else defaults.run_label
    default_day = None if defaults is None else defaults.last_day
    default_baseline = None if defaults is None else defaults.baseline_run_label
    default_feature = None if defaults is None else defaults.feature_pick
    logger.info(
        "defaults: run {} (last day {}), baseline {}, feature {}",
        default_run,
        default_day,
        default_baseline,
        default_feature,
    )
    dashboard_id = upsert_dashboard(
        client,
        spec.dashboard_title,
        spec.dashboard_slug,
        build_position_json(spec.dashboard_title, [tab.layout for tab in tabs]),
        build_native_filters(
            dataset_id=dataset_id,
            run_excluded=[],
            default_run_label=default_run,
            explanation_dataset_id=explanation_id,
            day_excluded=[c for c in all_charts if c not in explained],
            default_day_label=default_day,
            period_excluded=[c for c in all_charts if c not in per_selection],
            summary_dataset_id=summary_id,
            feature_excluded=[c for c in all_charts if c not in feature_by_period],
            default_feature_label=default_feature,
            # The Baseline arguments are worked out whether or not the tab is
            # built, and thrown away when it is not. That costs nothing — the
            # default comes from the query the Run default already needs — and
            # it is what keeps BUILD_COMPARE_TAB a one-line flip.
            baseline_excluded=[*accuracy.chart_ids, *explanation.chart_ids],
            default_baseline_label=default_baseline,
            with_baseline=bool(compare),
        ),
        chart_configuration,
    )
    # One read of the chart/dashboard links serves both writes, so neither drops
    # another dashboard a chart happens to be on.
    attached = client.charts_of_dashboard(dashboard_id)
    attach_charts(client, dashboard_id, all_charts, client.dashboards_of_charts(all_charts))
    detached = detach_stale_charts(client, dashboard_id, all_charts, attached)
    if detached:
        logger.info("detached {} chart(s) an earlier build left: {}", len(detached), detached)
    logger.info("dashboard: id={}", dashboard_id)
    logger.info("open: http://localhost:8088/superset/dashboard/{}/", spec.dashboard_slug)
    return dashboard_id


def build_feature_value_datasets(
    client: SupersetClient, database_id: int, specs: Iterable[DashboardSpec]
) -> dict[str, int]:
    """Register the feature-value datasets and return their ids by name.

    One as-of dataset per task (``spec.feature_values_dataset_name``: the
    newest vintage of every period and feature public by the task's issue
    time) and the shared every-vintage dataset ``feature_values_all``, all
    over ``pma_curated.fct_feature_value``. No dashboard reads them; they are
    the catalogue's browsing surface in Superset.

    Parameters
    ----------
    client : SupersetClient
    database_id : int
        Superset id of the Spark Thriftserver connection.
    specs : iterable of DashboardSpec
        The tasks whose as-of dataset to register.

    Returns
    -------
    dict of str to int
    """
    ids: dict[str, int] = {}
    for spec in specs:
        ids[spec.feature_values_dataset_name] = upsert_dataset(
            client,
            database_id,
            spec.feature_values_dataset_name,
            spec.feature_values_sql,
            list(FEATURE_VALUES_ASOF_COLUMNS),
        )
    ids[FEATURE_VALUES_ALL_DATASET] = upsert_dataset(
        client,
        database_id,
        FEATURE_VALUES_ALL_DATASET,
        FEATURE_VALUES_ALL_SQL,
        list(FEATURE_VALUES_ALL_COLUMNS),
    )
    for name, dataset_id in ids.items():
        logger.info("dataset {}: id={}", name, dataset_id)
    return ids


#: The feature catalogue: every feature by the name people read, one row each.
FEATURE_CATALOGUE_DATASET = "feature_catalogue"
FEATURE_CATALOGUE_TITLE = "Feature Catalogue"
FEATURE_CATALOGUE_SLUG = "feature-catalogue"
FEATURE_CATALOGUE_CHART = "Features"
FEATURE_CATALOGUE_SQL = f"""\
select
  feature_expression,
  feature_view,
  feature_name,
  feature_ref,
  grain,
  data_type,
  is_categorical,
  is_retired,
  feature_description
from {FEATURE_DIMENSION}
"""
FEATURE_CATALOGUE_COLUMNS = (
    ("feature_expression", "STRING", False),
    ("feature_view", "STRING", False),
    ("feature_name", "STRING", False),
    ("feature_ref", "STRING", False),
    ("grain", "STRING", False),
    ("data_type", "STRING", False),
    ("is_categorical", "BOOLEAN", False),
    ("is_retired", "BOOLEAN", False),
    ("feature_description", "STRING", False),
)


def feature_catalogue_params(dataset_id: int) -> dict:
    """Params for the catalogue table: one row per feature, expression first, with a search box.

    Rows sort by feature view, then expression, so a mart's features sit
    together; the search box matches any column, the expression included.

    Parameters
    ----------
    dataset_id : int
        The feature catalogue dataset.

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "table",
        "query_mode": "raw",
        "all_columns": [name for name, _, _ in FEATURE_CATALOGUE_COLUMNS],
        "order_by_cols": [
            json.dumps(["feature_view", True]),
            json.dumps(["feature_expression", True]),
        ],
        "adhoc_filters": [],
        "row_limit": 10000,
        "server_pagination": False,
        "page_length": 100,
        "include_search": True,
        "table_timestamp_format": "smart_date",
        # One line per feature: long text is cut at the column edge, not wrapped.
        "column_config": {
            name: {"truncateLongCells": True}
            for name, dtype, _ in FEATURE_CATALOGUE_COLUMNS
            if dtype == "STRING"
        },
        "extra_form_data": {},
    }


def build_feature_catalogue(client: SupersetClient, database_id: int) -> int:
    """Build or refresh the Feature Catalogue dashboard over ``dim_feature`` and return its id.

    The browsable list of every feature under its expression: the
    ``feature_catalogue`` dataset, one table chart and a one-tab dashboard.
    It does not depend on a task.

    Parameters
    ----------
    client : SupersetClient
    database_id : int
        Superset id of the Spark Thriftserver connection.

    Returns
    -------
    int
    """
    dataset_id = upsert_dataset(
        client,
        database_id,
        FEATURE_CATALOGUE_DATASET,
        FEATURE_CATALOGUE_SQL,
        list(FEATURE_CATALOGUE_COLUMNS),
        main_dttm_col=None,
    )
    logger.info("dataset {}: id={}", FEATURE_CATALOGUE_DATASET, dataset_id)
    chart_id = upsert_chart(
        client, FEATURE_CATALOGUE_CHART, dataset_id, feature_catalogue_params(dataset_id)
    )
    logger.info("chart {}: {}", chart_id, FEATURE_CATALOGUE_CHART)
    layout = {
        "title": FEATURE_CATALOGUE_CHART,
        "sections": [{"header": None, "rows": [[(chart_id, FEATURE_CATALOGUE_CHART, 12, 150)]]}],
    }
    dashboard_id = upsert_dashboard(
        client,
        FEATURE_CATALOGUE_TITLE,
        FEATURE_CATALOGUE_SLUG,
        build_position_json(FEATURE_CATALOGUE_TITLE, [layout]),
        [],
        {},
    )
    attach_charts(client, dashboard_id, [chart_id], client.dashboards_of_charts([chart_id]))
    logger.info("dashboard: id={}", dashboard_id)
    logger.info("open: http://localhost:8088/superset/dashboard/{}/", FEATURE_CATALOGUE_SLUG)
    return dashboard_id


def main(argv: list[str] | None = None) -> None:
    """Build or refresh the selected dashboards end to end, then the feature-value datasets
    and the Feature Catalogue.

    Parameters
    ----------
    argv : list of str, optional
        Command-line arguments (``--url``, ``--user``, ``--password``, each
        defaulting to the corresponding ``SUPERSET_*`` environment value;
        ``--task``, repeatable, one of ``DASHBOARDS`` — every dashboard when
        omitted; ``--baseline-run``, the Compare tab's Baseline filter
        default, passed to every dashboard built). ``None`` reads
        ``sys.argv``.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--url", default=SUPERSET_URL, help="Superset root URL")
    parser.add_argument("--user", default=ADMIN_USER, help="Superset admin username")
    parser.add_argument("--password", default=ADMIN_PASSWORD, help="Superset admin password")
    parser.add_argument(
        "--task",
        action="append",
        choices=list(DASHBOARDS),
        help="dashboard to build (repeatable); default: all of them",
    )
    parser.add_argument(
        "--baseline-run",
        default=None,
        help=(
            "run_id (or prefix) the Compare tab's Baseline filter opens on; default: the newest "
            "other run with the same area and window as the newest run. Does nothing while "
            "BUILD_COMPARE_TAB is False, which is how the tab ships today"
        ),
    )
    args = parser.parse_args(argv)

    client = SupersetClient(args.url, args.user, args.password)

    database_id = client.find_one("database", database_name=DATABASE_NAME)
    if database_id is None:
        raise SystemExit(
            f"Database connection {DATABASE_NAME!r} not found — register the "
            "Spark Thriftserver connection in the Superset UI first."
        )

    specs = [DASHBOARDS[task] for task in args.task or list(DASHBOARDS)]
    for spec in specs:
        build_dashboard(client, database_id, spec, baseline_run=args.baseline_run)
    build_feature_value_datasets(client, database_id, specs)
    build_feature_catalogue(client, database_id)


if __name__ == "__main__":
    main()
