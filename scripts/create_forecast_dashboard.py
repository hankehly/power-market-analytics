"""Create or update the forecast-analysis Superset dashboards.

One dashboard per forecasting task — "Spot Price Forecast Analysis" and
"Demand Forecast Analysis" — each described by a :class:`DashboardSpec` in
``DASHBOARDS`` and built (idempotently, matched by name) via the Superset
REST API, so everything is reproducible from the repo after a
``docker compose down -v``:

- two virtual datasets per dashboard: ``<task>_forecast_analysis`` — the
  task's forecast accuracy mart joined to dim_area / dim_delivery_period /
  dim_date, plus presentation columns (``run_label``, actual-value bands, day
  types) — and ``<task>_forecast_explanation`` — the contribution fact, one
  row per period x component, joined to the accuracy mart
- charts on two tabs: **Accuracy** — KPI tiles (MAE, bias, RMSE, RMSE/MAE,
  WAPE, P90), error structure (bars + heatmaps + day-type slices),
  calibration & distribution (actual-value-band MAE, calibration curve, error
  histogram), runs & drilldown (run leaderboard, worst days, 30-minute
  detail) — and **Explanation (SHAP)** — base / forecast / actual /
  net-effect tiles, the waterfall of mean per-period feature contributions,
  the component table, and the contributions by period — stacked bars with the
  forecast and the actual, both relative to the base, as lines on the same axis
- the dashboard, with a required single-select Run filter (all charts except
  the cross-run leaderboard) plus an optional Day filter scoped to the
  Explanation tab (cascading from Run); the 30-minute detail chart carries
  its own data-zoom slider for navigating the backtest window

The two dashboards share chart names (a chart is identified by its name
*within its dataset*), differing only where the quantity shows through: the
unit, number formats, and the two actual-value-level charts.

Run inside the devcontainer (needs the compose network):

    python scripts/create_forecast_dashboard.py                 # every dashboard
    python scripts/create_forecast_dashboard.py --task demand   # one of them

Environment: ``SUPERSET_URL`` (default ``http://superset:8088``),
``SUPERSET_ADMIN_USER`` (``admin``), ``SUPERSET_ADMIN_PASSWORD`` (``admin``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from typing import Any

import requests
from loguru import logger

SUPERSET_URL = os.environ.get("SUPERSET_URL", "http://superset:8088")
ADMIN_USER = os.environ.get("SUPERSET_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("SUPERSET_ADMIN_PASSWORD", "admin")

DATABASE_NAME = "Spark Thriftserver"

# Shared skeleton of every task's virtual dataset: calendar / delivery-period
# / area context, the run label, then the task's value and error columns
# (in the task's display unit) and the unit-free percentage errors.
DATASET_SQL_TEMPLATE = """\
select
  f.date_key,
  f.trade_datetime,
  year(f.date_key) as year,
  month(f.date_key) as month,
  f.time_code,
  p.hour_of_day,
  p.day_part,
  p.is_daytime,
  d.fiscal_year,
  d.day_name,
  concat(d.day_of_week_iso, ' ', substring(d.day_name, 1, 3)) as day_of_week,
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
  f.run_id,
  concat(
    date_format(f.published_at, 'yyyy-MM-dd HH:mm'),
    ' | ', a.area_code,
    ' | ', substring(f.run_id, 1, 8)
  ) as run_label,
  f.strategy,
  f.published_at,
  f.forecast_issued_ts,
  f.horizon_hours,
{value_columns_sql}
  f.pct_error,
  f.abs_pct_error
from {accuracy_table} f
join pma_curated.dim_area a on f.area_key = a.area_key
join pma_curated.dim_delivery_period p on f.time_code = p.time_code
join pma_curated.dim_date d on f.date_key = d.date_key
"""

# (column_name, generic type, is temporal) for the shared head of the select
# list — kept in sync with DATASET_SQL_TEMPLATE so reruns can override stale
# column metadata after a SQL change.
COMMON_DATASET_COLUMNS = (
    ("date_key", "DATE", True),
    ("trade_datetime", "TIMESTAMP", True),
    ("year", "BIGINT", False),
    ("month", "BIGINT", False),
    ("time_code", "INT", False),
    ("hour_of_day", "INT", False),
    ("day_part", "STRING", False),
    ("is_daytime", "BOOLEAN", False),
    ("fiscal_year", "INT", False),
    ("day_name", "STRING", False),
    ("day_of_week", "STRING", False),
    ("day_type", "STRING", False),
    ("is_weekend", "BOOLEAN", False),
    ("is_holiday", "BOOLEAN", False),
    ("is_business_day", "BOOLEAN", False),
    ("area_code", "STRING", False),
    ("area_name_en", "STRING", False),
    ("run_id", "STRING", False),
    ("run_label", "STRING", False),
    ("strategy", "STRING", False),
    ("published_at", "TIMESTAMP", True),
    ("forecast_issued_ts", "TIMESTAMP", True),
    ("horizon_hours", "DOUBLE", False),
)

# Shared skeleton of every task's explanation dataset: the contribution fact
# (one row per period x component) with calendar / period / area context,
# the same run_label construction as the analysis dataset (so the Run filter
# selects both), sortable Day / component labels, then the task's
# value block — the contribution and, from the accuracy mart, the period's
# forecast and actual (repeated on each component row: AVG-only metrics).
EXPLANATION_DATASET_SQL_TEMPLATE = """\
select
  c.date_key,
  date_format(c.date_key, 'yyyy-MM-dd') as trade_date_label,
  c.trade_datetime,
  c.time_code,
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
  concat(
    date_format(c.published_at, 'yyyy-MM-dd HH:mm'),
    ' | ', a.area_code,
    ' | ', substring(c.run_id, 1, 8)
  ) as run_label,
  c.strategy,
  c.published_at,
  c.component,
  c.component_order,
  concat(lpad(cast(c.component_order as string), 2, '0'), ' ', c.component) as component_label,
  c.is_base,
  c.feature_value,
{explanation_value_columns_sql}
from {contribution_table} c
join pma_curated.dim_area a on c.area_key = a.area_key
join pma_curated.dim_delivery_period p on c.time_code = p.time_code
join pma_curated.dim_date d on c.date_key = d.date_key
left join {accuracy_table} f
  on c.run_id = f.run_id
  and c.date_key = f.date_key
  and c.time_code = f.time_code
  and c.area_key = f.area_key
"""

COMMON_EXPLANATION_COLUMNS = (
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
    ("run_id", "STRING", False),
    ("run_label", "STRING", False),
    ("strategy", "STRING", False),
    ("published_at", "TIMESTAMP", True),
    ("component", "STRING", False),
    ("component_order", "INT", False),
    ("component_label", "STRING", False),
    ("is_base", "BOOLEAN", False),
    ("feature_value", "DOUBLE", False),
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


def sql_metric(expression: str, label: str) -> dict:
    """Ad-hoc SQL-expression metric definition for chart params.

    Parameters
    ----------
    expression : str
        Aggregate Spark SQL expression, e.g. ``sqrt(avg(power(x, 2)))``.
    label : str
        Display label.

    Returns
    -------
    dict
    """
    return {
        "expressionType": "SQL",
        "sqlExpression": expression,
        "label": label,
        "optionName": f"metric_{_slug(label)}",
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

    @property
    def dataset_sql(self) -> str:
        """The virtual dataset's SQL: the shared template around this task's columns."""
        return DATASET_SQL_TEMPLATE.format(
            value_columns_sql=self.value_columns_sql,
            accuracy_table=self.accuracy_table,
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

    @property
    def explanation_dataset_sql(self) -> str:
        """The explanation dataset's SQL: the shared template around this task's value block."""
        return EXPLANATION_DATASET_SQL_TEMPLATE.format(
            explanation_value_columns_sql=self.explanation_value_columns_sql,
            contribution_table=self.contribution_table,
            accuracy_table=self.accuracy_table,
        )

    @property
    def explanation_dataset_columns(self) -> list[tuple[str, str, bool]]:
        """(column_name, generic type, is temporal) for every explanation column, in select order."""
        return [*COMMON_EXPLANATION_COLUMNS, *self.explanation_value_columns]

    @property
    def contribution_metric(self) -> dict:
        """Mean contribution per period of the selection (SHAP is additive, so the
        per-period mean is a valid decomposition of the mean forecast)."""
        return avg_metric(self.contribution_col, f"Contribution ({self.unit})")

    @property
    def base_value_metric(self) -> dict:
        return sql_metric(f"avg(case when is_base then {self.contribution_col} end)", "Base value")

    def _minus_base_metric(self, column: str, label: str) -> dict:
        """``avg(column) − base`` over the selection, the base read off the ``is_base`` row
        of the same period — so the metric needs that row: never combine it with
        ``NOT_BASE_FILTER``."""
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
    """

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

    def _get_json(self, path: str, params: dict | None = None) -> dict:
        r = self.session.get(f"{self.base_url}{path}", params=params)
        r.raise_for_status()
        return r.json()

    def _post_json(self, path: str, payload: dict) -> dict:
        r = self.session.post(f"{self.base_url}{path}", json=payload)
        r.raise_for_status()
        return r.json()

    def _put_json(self, path: str, payload: dict, params: dict | None = None) -> dict:
        r = self.session.put(f"{self.base_url}{path}", json=payload, params=params)
        r.raise_for_status()
        return r.json()

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
        q = f"(filters:!({rison_filters}),page_size:100)"
        result = self._get_json(f"/api/v1/{resource}/", params={"q": q})["result"]
        return result[0]["id"] if result else None


def upsert_dataset(
    client: SupersetClient,
    database_id: int,
    name: str,
    sql: str,
    columns: list[tuple[str, str, bool]],
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
        {"sql": sql, "main_dttm_col": "trade_datetime", "columns": column_payload},
        params={"override_columns": "true"},
    )
    return dataset_id


def latest_run(
    client: SupersetClient, database_id: int, spec: DashboardSpec
) -> tuple[str, str] | None:
    """Newest run's label and its last delivery day, for the filters' on-load defaults.

    ``defaultToFirstItem`` only stages a value (charts render unfiltered until
    Apply is clicked); an explicit default applies on page load. The label
    must be built exactly like the datasets' ``run_label``; the day like the
    explanation dataset's ``trade_date_label``.

    Parameters
    ----------
    client : SupersetClient
    database_id : int
    spec : DashboardSpec

    Returns
    -------
    tuple of (str, str) or None
        ``(run_label, last_day)``; None when the query fails or the mart is
        empty (both filters then fall back to ``defaultToFirstItem``).
    """
    sql = f"""\
select
  concat(
    date_format(f.published_at, 'yyyy-MM-dd HH:mm'),
    ' | ', a.area_code,
    ' | ', substring(f.run_id, 1, 8)
  ) as run_label,
  date_format(max(f.date_key), 'yyyy-MM-dd') as last_day
from {spec.accuracy_table} f
join pma_curated.dim_area a on f.area_key = a.area_key
group by f.run_id, f.published_at, a.area_code
order by f.published_at desc
limit 1
"""
    try:
        result = client._post_json(
            "/api/v1/sqllab/execute/",
            {"database_id": database_id, "sql": sql, "runAsync": False},
        )
        row = result["data"][0]
        return row["run_label"], row["last_day"]
    except (requests.HTTPError, KeyError, IndexError):
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


def bar_params(spec: DashboardSpec, dataset_id: int, x_axis: str) -> dict:
    """Params for a single-series MAE bar chart over ``x_axis``.

    One series, so no legend (the title names it).

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
    x_axis : str
        Dataset column for the x axis.

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


def leaderboard_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the cross-run leaderboard table (best MAE first).

    Excluded from the Run filter so all runs stay visible side by side.

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
        "groupby": ["run_label", "strategy"],
        "metrics": [
            sql_metric("count(*)", "Periods"),
            mae,
            spec.bias_metric,
            spec.rmse_metric,
            spec.wape_metric,
        ],
        "adhoc_filters": [],
        "timeseries_limit_metric": mae,
        "order_desc": False,
        "row_limit": 100,
        "server_page_length": 10,
        "table_timestamp_format": "smart_date",
        "column_config": {
            "Periods": {"d3NumberFormat": ",d"},
            mae["label"]: {"d3NumberFormat": spec.number_format},
            "Bias": {"d3NumberFormat": spec.signed_number_format},
            "RMSE": {"d3NumberFormat": spec.number_format},
            "WAPE": {"d3NumberFormat": ".1%"},
        },
        "extra_form_data": {},
    }


def worst_days_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the worst-days drill table (highest daily MAE first).

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
        "groupby": ["date_key", "day_of_week", "day_type"],
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
        "column_config": {
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
    navigates from the full window down to a single day.

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


def waterfall_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the SHAP waterfall of the selected day / period.

    One bar per model feature in the model's feature order (the chart sorts
    by x-axis label, hence ``component_label``'s zero-padded order prefix),
    each the mean contribution per period of the selection; the Total bar is
    forecast − base. The base row is filtered out (see ``NOT_BASE_FILTER``).

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
        "x_axis": "component_label",
        "time_grain_sqla": None,
        "groupby": [],
        "metric": spec.contribution_metric,
        "adhoc_filters": [NOT_BASE_FILTER],
        "row_limit": 100,
        "show_value": True,
        "show_legend": True,
        "increase_label": "Pushes forecast up",
        "decrease_label": "Pushes forecast down",
        "show_total": True,
        "total_label": "Net effect",
        "x_axis_label": "Component (model feature order)",
        "x_axis_time_format": "smart_date",
        "x_ticks_layout": "auto",
        "y_axis_label": spec.unit,
        "y_axis_format": spec.axis_format,
        "extra_form_data": {},
    }


def feature_table_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the component table: order, mean feature value, mean contribution.

    Keeps the base row (order 00, no feature value), so the contribution
    column sums to the forecast; sorted by ``Order`` to read in the model's
    feature order.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The explanation dataset.

    Returns
    -------
    dict
    """
    order = sql_metric("min(component_order)", "Order")
    contribution = spec.contribution_metric
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "table",
        "query_mode": "aggregate",
        "groupby": ["component_label"],
        "metrics": [order, sql_metric("avg(feature_value)", "Feature value"), contribution],
        "adhoc_filters": [],
        "timeseries_limit_metric": order,
        "order_desc": False,
        "row_limit": 100,
        "server_page_length": 20,
        "table_timestamp_format": "smart_date",
        "column_config": {
            "Order": {"d3NumberFormat": ",d"},
            "Feature value": {"d3NumberFormat": ",.2~f"},
            contribution["label"]: {"d3NumberFormat": spec.contribution_format},
        },
        "extra_form_data": {},
    }


def contribution_by_period_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the by-period chart: each feature's contribution stacked over the day's
    48 periods, with the forecast and the actual — both relative to the base — as lines.

    A Superset Mixed Chart. Query A stacks the mean contribution per ``time_code``
    of each feature (the base row filtered out, see ``NOT_BASE_FILTER``, so the
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
        "x_axis": "time_code",
        "time_grain_sqla": None,
        # Query A: one stacked bar per feature
        "metrics": [spec.contribution_metric],
        "groupby": ["component_label"],
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


def build_position_json(spec: DashboardSpec, tabs: list[dict]) -> dict:
    """Dashboard layout: top-level tabs, each a list of optionally headed sections of rows.

    Emits the shape Superset itself writes for a tabbed dashboard: the tabs
    container replaces the grid as the root's child (the grid stays, empty)
    and every component carries its full ``parents`` chain — the frontend
    resolves native-filter and cross-filter scopes through those chains.

    Parameters
    ----------
    spec : DashboardSpec
        Supplies the dashboard header text.
    tabs : list of dict
        Each ``{"title": str, "sections": [...]}``; a section is
        ``{"header": str | None, "rows": [[(chart_id, name, width, height),
        ...], ...]}``. Widths within a row should sum to 12; height is in
        dashboard grid units (~8 px each).

    Returns
    -------
    dict
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
        "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": spec.dashboard_title}},
    }
    for t, tab in enumerate(tabs):
        tab_key = f"TAB-{t}"
        position[tabs_key]["children"].append(tab_key)
        position[tab_key] = {
            "type": "TAB",
            "id": tab_key,
            "children": [],
            "parents": ["ROOT_ID", tabs_key],
            "meta": {"text": tab["title"], "defaultText": "Tab title", "placeholder": "Tab title"},
        }
        parents = ["ROOT_ID", tabs_key, tab_key]
        for s, section in enumerate(tab["sections"]):
            if section["header"]:
                header_key = f"HEADER-{t}-{s}"
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
                row_key = f"ROW-{t}-{s}-{r}"
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
            "defaultToFirstItem": default is None and default_to_first,
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
    dataset_id: int,
    run_excluded: list[int],
    default_run_label: str | None,
    explanation_dataset_id: int,
    day_excluded: list[int],
    default_day_label: str | None,
) -> list[dict]:
    """Native filter configuration: Run (whole dashboard), then Day (the
    Explanation tab only).

    Parameters
    ----------
    dataset_id : int
        Analysis dataset the Run filter reads ``run_label`` from.
    run_excluded : list of int
        Charts the Run filter must NOT apply to (the cross-run leaderboard).
    default_run_label : str or None
        Explicit on-load run; None falls back to ``defaultToFirstItem``.
    explanation_dataset_id : int
        Explanation dataset the Day filter reads its values from.
    day_excluded : list of int
        Charts outside the Day filter's scope (everything on the Accuracy
        tab).
    default_day_label : str or None
        Explicit on-load day (the default run's last delivery day).

    Returns
    -------
    list of dict
    """
    return [
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
            description="published_at | area | run_id prefix (newest first)",
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
                "Delivery day explained (empty = the run's mean decomposition); clear Day, "
                "or pick the same day, before following a Worst days click — the two filters "
                "combine"
            ),
        ),
    ]


def build_chart_configuration(worst_days: int, in_scope: list[int], excluded: list[int]) -> dict:
    """Per-chart cross-filter scopes: clicking a Worst-days row selects that day
    on the Explanation tab (and in the 30-minute detail chart) only.

    Parameters
    ----------
    worst_days : int
        The Worst-days table's chart id (the emitter).
    in_scope : list of int
        Charts that receive its cross-filter.
    excluded : list of int
        Every other chart on the dashboard.

    Returns
    -------
    dict
        ``json_metadata["chart_configuration"]``.
    """
    return {
        str(worst_days): {
            "id": worst_days,
            "crossFilters": {
                "scope": {"rootPath": ["ROOT_ID"], "excluded": excluded},
                "chartsInScope": in_scope,
            },
        }
    }


def upsert_dashboard(
    client: SupersetClient,
    spec: DashboardSpec,
    position: dict,
    native_filters: list[dict],
    chart_configuration: dict,
) -> int:
    """Create or update the dashboard and return its id.

    Parameters
    ----------
    client : SupersetClient
    spec : DashboardSpec
        Supplies the title and slug.
    position : dict
        ``position_json`` layout from :func:`build_position_json`.
    native_filters : list of dict
        ``native_filter_configuration`` from :func:`build_native_filters`.
    chart_configuration : dict
        Per-chart cross-filter scopes from :func:`build_chart_configuration`.

    Returns
    -------
    int
    """
    dashboard_id = client.find_one("dashboard", dashboard_title=spec.dashboard_title)
    if dashboard_id is None:
        dashboard_id = client._post_json(
            "/api/v1/dashboard/",
            {"dashboard_title": spec.dashboard_title, "slug": spec.dashboard_slug},
        )["id"]
    json_metadata = {
        "native_filter_configuration": native_filters,
        "cross_filters_enabled": True,
        "chart_configuration": chart_configuration,
        "color_scheme": "",
        "expanded_slices": {},
        "label_colors": {},
        "refresh_frequency": 0,
        "timed_refresh_immune_slices": [],
    }
    client._put_json(
        f"/api/v1/dashboard/{dashboard_id}",
        {
            "dashboard_title": spec.dashboard_title,
            "slug": spec.dashboard_slug,
            "position_json": json.dumps(position),
            "json_metadata": json.dumps(json_metadata),
            "published": True,
        },
    )
    return dashboard_id


def attach_charts(client: SupersetClient, dashboard_id: int, chart_ids: list[int]) -> None:
    """Link charts to the dashboard (position_json alone is not enough).

    Parameters
    ----------
    client : SupersetClient
    dashboard_id : int
    chart_ids : list of int
    """
    for chart_id in chart_ids:
        client._put_json(f"/api/v1/chart/{chart_id}", {"dashboards": [dashboard_id]})


def build_dashboard(client: SupersetClient, database_id: int, spec: DashboardSpec) -> int:
    """Build or refresh one task's dashboard end to end and return its id.

    Parameters
    ----------
    client : SupersetClient
    database_id : int
        Superset id of the Spark Thriftserver connection.
    spec : DashboardSpec

    Returns
    -------
    int
    """
    dataset_id = upsert_dataset(
        client, database_id, spec.dataset_name, spec.dataset_sql, spec.dataset_columns
    )
    logger.info("dataset {}: id={}", spec.dataset_name, dataset_id)
    explanation_id = upsert_dataset(
        client,
        database_id,
        spec.explanation_dataset_name,
        spec.explanation_dataset_sql,
        spec.explanation_dataset_columns,
    )
    logger.info("dataset {}: id={}", spec.explanation_dataset_name, explanation_id)

    def chart(name: str, params: dict, on: int = dataset_id) -> int:
        chart_id = upsert_chart(client, name, on, params)
        logger.info("chart {}: {}", chart_id, name)
        return chart_id

    unit, fmt = spec.unit, spec.number_format

    # KPI tiles
    kpi_mae = chart("Overall MAE", big_number_params(dataset_id, spec.mae_metric, unit, fmt))
    kpi_bias = chart(
        "Bias (mean error)",
        big_number_params(
            dataset_id, spec.bias_metric, f"{unit}; + = over-forecast", spec.signed_number_format
        ),
    )
    kpi_rmse = chart("RMSE", big_number_params(dataset_id, spec.rmse_metric, unit, fmt))
    kpi_ratio = chart(
        "RMSE / MAE",
        big_number_params(dataset_id, spec.rmse_mae_metric, ">1.3 = spike-heavy errors", ",.2f"),
    )
    kpi_wape = chart(
        "WAPE", big_number_params(dataset_id, spec.wape_metric, "Σ|error| / Σ actual", ".1%")
    )
    kpi_p90 = chart("P90 abs error", big_number_params(dataset_id, spec.p90_metric, unit, fmt))

    # Error structure
    mae_year = chart("MAE by year", bar_params(spec, dataset_id, "year"))
    mae_tc = chart("MAE by time code", bar_params(spec, dataset_id, "time_code"))
    heat_tc = chart("MAE by year and time code", heatmap_params(spec, dataset_id, "time_code"))
    heat_month = chart("MAE by year and month", heatmap_params(spec, dataset_id, "month"))
    mae_dow = chart("MAE by day of week", bar_params(spec, dataset_id, "day_of_week"))
    mae_daypart = chart("MAE by day part", bar_params(spec, dataset_id, "day_part"))
    mae_daytype = chart("MAE by day type", bar_params(spec, dataset_id, "day_type"))

    # Calibration & distribution
    mae_band = chart(spec.band_chart_title, bar_params(spec, dataset_id, spec.band_col))
    calibration = chart(spec.calibration_chart_title, calibration_params(spec, dataset_id))
    histogram = chart("Error distribution", histogram_params(spec, dataset_id))

    # Runs & drilldown
    leaderboard = chart("Run leaderboard", leaderboard_params(spec, dataset_id))
    worst_days = chart("Worst days", worst_days_params(spec, dataset_id))
    detail = chart("Forecast vs actual (30-min detail)", detail_params(spec, dataset_id))

    # Explanation (SHAP): the contribution fact, filtered by Run + Day
    per_period = f"{unit}; mean per period"
    kpi_base = chart(
        "Base value",
        big_number_params(
            explanation_id,
            spec.base_value_metric,
            f"{unit}; model expected value, mean per period",
            fmt,
        ),
        explanation_id,
    )
    kpi_forecast = chart(
        "Forecast (selection)",
        big_number_params(
            explanation_id, avg_metric(spec.forecast_col, "Forecast"), per_period, fmt
        ),
        explanation_id,
    )
    kpi_actual = chart(
        "Actual (selection)",
        big_number_params(explanation_id, avg_metric(spec.actual_col, "Actual"), per_period, fmt),
        explanation_id,
    )
    kpi_net = chart(
        "Net feature effect",
        big_number_params(
            explanation_id,
            spec.net_effect_metric,
            f"{unit}; forecast − base",
            spec.signed_number_format,
        ),
        explanation_id,
    )
    waterfall = chart("SHAP waterfall", waterfall_params(spec, explanation_id), explanation_id)
    feature_table = chart(
        "Feature values & contributions", feature_table_params(spec, explanation_id), explanation_id
    )
    by_period = chart(
        "Contributions by period",
        contribution_by_period_params(spec, explanation_id),
        explanation_id,
    )

    accuracy_sections: list[dict[str, Any]] = [
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
                    (mae_dow, "MAE by day of week", 4, 36),
                    (mae_daypart, "MAE by day part", 4, 36),
                    (mae_daytype, "MAE by day type", 4, 36),
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
            "header": "Runs & drilldown",
            "rows": [
                [(leaderboard, "Run leaderboard", 12, 26)],
                [(worst_days, "Worst days", 12, 40)],
                [(detail, "Forecast vs actual (30-min detail)", 12, 60)],
            ],
        },
    ]
    explanation_sections: list[dict[str, Any]] = [
        {
            "header": None,
            "rows": [
                [
                    (kpi_base, "Base value", 3, 24),
                    (kpi_forecast, "Forecast (selection)", 3, 24),
                    (kpi_actual, "Actual (selection)", 3, 24),
                    (kpi_net, "Net feature effect", 3, 24),
                ],
                [
                    (waterfall, "SHAP waterfall", 8, 46),
                    (feature_table, "Feature values & contributions", 4, 46),
                ],
                [(by_period, "Contributions by period", 12, 44)],
            ],
        },
    ]
    tabs = [
        {"title": "Accuracy", "sections": accuracy_sections},
        {"title": "Explanation (SHAP)", "sections": explanation_sections},
    ]
    analysis_charts = [
        kpi_mae,
        kpi_bias,
        kpi_rmse,
        kpi_ratio,
        kpi_wape,
        kpi_p90,
        mae_year,
        mae_tc,
        heat_tc,
        heat_month,
        mae_dow,
        mae_daypart,
        mae_daytype,
        mae_band,
        calibration,
        histogram,
        leaderboard,
        worst_days,
        detail,
    ]
    explanation_charts = [
        kpi_base,
        kpi_forecast,
        kpi_actual,
        kpi_net,
        waterfall,
        feature_table,
        by_period,
    ]
    all_charts = [*analysis_charts, *explanation_charts]
    cross_filter_targets = [detail, *explanation_charts]
    chart_configuration = build_chart_configuration(
        worst_days,
        in_scope=cross_filter_targets,
        excluded=[c for c in analysis_charts if c != detail],
    )

    latest = latest_run(client, database_id, spec)
    default_run, default_day = (None, None) if latest is None else latest
    logger.info("default run: {} (last day: {})", default_run, default_day)
    dashboard_id = upsert_dashboard(
        client,
        spec,
        build_position_json(spec, tabs),
        build_native_filters(
            dataset_id,
            run_excluded=[leaderboard],
            default_run_label=default_run,
            explanation_dataset_id=explanation_id,
            day_excluded=analysis_charts,
            default_day_label=default_day,
        ),
        chart_configuration,
    )
    attach_charts(client, dashboard_id, all_charts)
    logger.info("dashboard: id={}", dashboard_id)
    logger.info("open: http://localhost:8088/superset/dashboard/{}/", spec.dashboard_slug)
    return dashboard_id


def main(argv: list[str] | None = None) -> None:
    """Build or refresh the selected dashboards end to end.

    Parameters
    ----------
    argv : list of str, optional
        Command-line arguments (``--url``, ``--user``, ``--password``, each
        defaulting to the corresponding ``SUPERSET_*`` environment value;
        ``--task``, repeatable, one of ``DASHBOARDS`` — every dashboard when
        omitted). ``None`` reads ``sys.argv``.
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
    args = parser.parse_args(argv)

    client = SupersetClient(args.url, args.user, args.password)

    database_id = client.find_one("database", database_name=DATABASE_NAME)
    if database_id is None:
        raise SystemExit(
            f"Database connection {DATABASE_NAME!r} not found — register the "
            "Spark Thriftserver connection in the Superset UI first."
        )

    for task in args.task or list(DASHBOARDS):
        build_dashboard(client, database_id, DASHBOARDS[task])


if __name__ == "__main__":
    main()
