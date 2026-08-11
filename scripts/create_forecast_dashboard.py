"""Create or update the "Spot Price Forecast Analysis" Superset dashboard.

Builds (idempotently, matched by name) everything the dashboard needs via the
Superset REST API, so the whole thing is reproducible from the repo after a
``docker compose down -v``:

- virtual dataset ``spot_price_forecast_analysis`` — the forecast accuracy
  mart joined to dim_area / dim_delivery_period / dim_date, plus
  presentation columns (``run_label``, price bands, day types)
- charts in four sections: KPI tiles (MAE, bias, RMSE, RMSE/MAE, WAPE, P90),
  error structure (bars + heatmaps + day-type slices), calibration &
  distribution (price-band MAE, calibration curve, error histogram), and
  runs & drilldown (run leaderboard, worst days, 30-minute detail)
- the dashboard, with a required single-select Run filter (all charts except
  the cross-run leaderboard); the 30-minute detail chart carries its own
  data-zoom slider for navigating the backtest window

Run inside the devcontainer (needs the compose network):

    python scripts/create_forecast_dashboard.py

Environment: ``SUPERSET_URL`` (default ``http://superset:8088``),
``SUPERSET_ADMIN_USER`` (``admin``), ``SUPERSET_ADMIN_PASSWORD`` (``admin``).
"""

from __future__ import annotations

import json
import os
import re

import requests
from loguru import logger

SUPERSET_URL = os.environ.get("SUPERSET_URL", "http://superset:8088")
ADMIN_USER = os.environ.get("SUPERSET_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("SUPERSET_ADMIN_PASSWORD", "admin")

DATABASE_NAME = "Spark Thriftserver"
DATASET_NAME = "spot_price_forecast_analysis"
DASHBOARD_TITLE = "Spot Price Forecast Analysis"
DASHBOARD_SLUG = "spot-price-forecast-analysis"

DATASET_SQL = """\
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
  f.abs_error_jpy_kwh,
  f.pct_error,
  f.abs_pct_error
from pma_curated.fct_spot_price_forecast_accuracy f
join pma_curated.dim_area a on f.area_key = a.area_key
join pma_curated.dim_delivery_period p on f.time_code = p.time_code
join pma_curated.dim_date d on f.date_key = d.date_key
"""

# (column_name, generic type, is temporal) — kept in sync with DATASET_SQL so
# reruns can override stale column metadata after a SQL change.
DATASET_COLUMNS = [
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
    ("forecast_price_jpy_kwh", "DOUBLE", False),
    ("actual_price_jpy_kwh", "DOUBLE", False),
    ("actual_price_round_jpy", "INT", False),
    ("actual_price_band", "STRING", False),
    ("error_jpy_kwh", "DOUBLE", False),
    ("abs_error_jpy_kwh", "DOUBLE", False),
    ("pct_error", "DOUBLE", False),
    ("abs_pct_error", "DOUBLE", False),
]


class SupersetClient:
    """Thin authenticated wrapper over the Superset REST API.

    Parameters
    ----------
    base_url : str
        Superset root URL, e.g. ``http://superset:8088``.
    username, password : str
        Credentials for the ``db`` auth provider.
    """

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
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

    def find_one(self, resource: str, column: str, value: str) -> int | None:
        """Return the id of the first ``resource`` row where column == value.

        Parameters
        ----------
        resource : str
            API resource, e.g. ``dataset``, ``chart``, ``dashboard``.
        column, value : str
            Equality filter, e.g. ``table_name`` / ``slice_name`` / a title.

        Returns
        -------
        int or None
        """
        q = f"(filters:!((col:{column},opr:eq,value:'{value}')),page_size:100)"
        result = self._get_json(f"/api/v1/{resource}/", params={"q": q})["result"]
        return result[0]["id"] if result else None


def upsert_dataset(client: SupersetClient, database_id: int) -> int:
    """Create or update the virtual dataset and return its id.

    Parameters
    ----------
    client : SupersetClient
    database_id : int
        Superset id of the Spark Thriftserver connection.

    Returns
    -------
    int
    """
    columns = [
        {
            "column_name": name,
            "type": dtype,
            "is_dttm": is_dttm,
            "groupby": True,
            "filterable": True,
        }
        for name, dtype, is_dttm in DATASET_COLUMNS
    ]
    dataset_id = client.find_one("dataset", "table_name", DATASET_NAME)
    if dataset_id is None:
        dataset_id = client._post_json(
            "/api/v1/dataset/",
            {"database": database_id, "table_name": DATASET_NAME, "sql": DATASET_SQL},
        )["id"]
    client._put_json(
        f"/api/v1/dataset/{dataset_id}",
        {"sql": DATASET_SQL, "main_dttm_col": "trade_datetime", "columns": columns},
        params={"override_columns": "true"},
    )
    return dataset_id


def latest_run_label(client: SupersetClient, database_id: int) -> str | None:
    """Newest run's label, for the Run filter's on-load default.

    ``defaultToFirstItem`` only stages the value (charts render unfiltered,
    mixing runs, until Apply is clicked); an explicit default applies on page
    load. Must build the label exactly like the dataset's ``run_label``.

    Parameters
    ----------
    client : SupersetClient
    database_id : int

    Returns
    -------
    str or None
        None when the query fails or the mart is empty (falls back to
        ``defaultToFirstItem``).
    """
    sql = """\
select
  concat(
    date_format(f.published_at, 'yyyy-MM-dd HH:mm'),
    ' | ', a.area_code,
    ' | ', substring(f.run_id, 1, 8)
  ) as run_label
from pma_curated.fct_spot_price_forecast_accuracy f
join pma_curated.dim_area a on f.area_key = a.area_key
order by f.published_at desc
limit 1
"""
    try:
        result = client._post_json(
            "/api/v1/sqllab/execute/",
            {"database_id": database_id, "sql": sql, "runAsync": False},
        )
        return result["data"][0]["run_label"]
    except (requests.HTTPError, KeyError, IndexError):
        return None


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


MAE_METRIC = avg_metric("abs_error_jpy_kwh", "MAE (JPY/kWh)")
BIAS_METRIC = avg_metric("error_jpy_kwh", "Bias")
RMSE_METRIC = sql_metric("sqrt(avg(power(error_jpy_kwh, 2)))", "RMSE")
RMSE_MAE_METRIC = sql_metric(
    "sqrt(avg(power(error_jpy_kwh, 2))) / avg(abs_error_jpy_kwh)", "RMSE/MAE"
)
WAPE_METRIC = sql_metric("sum(abs_error_jpy_kwh) / sum(actual_price_jpy_kwh)", "WAPE")
P90_METRIC = sql_metric("percentile(abs_error_jpy_kwh, 0.90)", "P90 abs error")


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


def bar_params(dataset_id: int, x_axis: str) -> dict:
    """Params for a single-series MAE bar chart over ``x_axis``.

    One series, so no legend (the title names it).

    Parameters
    ----------
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
        "metrics": [MAE_METRIC],
        "groupby": [],
        "adhoc_filters": [],
        "order_desc": False,
        "row_limit": 1000,
        "show_legend": False,
        "rich_tooltip": True,
        "y_axis_format": ",.2f",
        "y_axis_title": "JPY/kWh",
        "y_axis_title_margin": 30,
        "truncateYAxis": False,
        "color_scheme": "supersetColors",
        "x_axis_time_format": "smart_date",
        "extra_form_data": {},
    }


def heatmap_params(dataset_id: int, x_axis: str) -> dict:
    """Params for a MAE heatmap (year on y, ``x_axis`` on x).

    Sequential single-hue ramp ("Dark blues"): the metric encodes magnitude
    only.

    Parameters
    ----------
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
        "metric": MAE_METRIC,
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


def calibration_params(dataset_id: int) -> dict:
    """Params for the calibration curve: mean forecast per actual price level.

    Mean actual per rounded actual price is (by construction) the y = x
    reference, so systematic under/over-forecast at any price level shows as
    the gap between the two series — the standard conditional-bias view.

    Parameters
    ----------
    dataset_id : int

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "echarts_timeseries_scatter",
        "x_axis": "actual_price_round_jpy",
        "time_grain_sqla": None,
        "x_axis_sort": "actual_price_round_jpy",
        "x_axis_sort_asc": True,
        "metrics": [
            avg_metric("forecast_price_jpy_kwh", "Mean forecast"),
            avg_metric("actual_price_jpy_kwh", "Mean actual (y = x reference)"),
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
        "x_axis_title": "Actual price (JPY/kWh, rounded)",
        "x_axis_title_margin": 30,
        "y_axis_format": ",.1f",
        "y_axis_title": "Forecast (JPY/kWh)",
        "y_axis_title_margin": 30,
        "truncateYAxis": False,
        "color_scheme": "supersetColors",
        "extra_form_data": {},
    }


def histogram_params(dataset_id: int) -> dict:
    """Params for the signed-error histogram.

    Parameters
    ----------
    dataset_id : int

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "histogram_v2",
        "column": "error_jpy_kwh",
        "bins": 60,
        "normalize": False,
        "cumulative": False,
        "groupby": [],
        "adhoc_filters": [],
        "row_limit": 100000,
        "show_legend": False,
        "show_value": False,
        "x_axis_title": "Signed error (JPY/kWh; + = over-forecast)",
        "y_axis_title": "Delivery periods",
        "color_scheme": "supersetColors",
        "extra_form_data": {},
    }


def leaderboard_params(dataset_id: int) -> dict:
    """Params for the cross-run leaderboard table (best MAE first).

    Excluded from the Run filter so all runs stay visible side by side.

    Parameters
    ----------
    dataset_id : int

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "table",
        "query_mode": "aggregate",
        "groupby": ["run_label", "strategy"],
        "metrics": [
            sql_metric("count(*)", "Periods"),
            MAE_METRIC,
            BIAS_METRIC,
            RMSE_METRIC,
            WAPE_METRIC,
        ],
        "adhoc_filters": [],
        "timeseries_limit_metric": MAE_METRIC,
        "order_desc": False,
        "row_limit": 100,
        "server_page_length": 10,
        "table_timestamp_format": "smart_date",
        "column_config": {
            "Periods": {"d3NumberFormat": ",d"},
            "MAE (JPY/kWh)": {"d3NumberFormat": ",.3f"},
            "Bias": {"d3NumberFormat": "+,.3f"},
            "RMSE": {"d3NumberFormat": ",.3f"},
            "WAPE": {"d3NumberFormat": ".1%"},
        },
        "extra_form_data": {},
    }


def worst_days_params(dataset_id: int) -> dict:
    """Params for the worst-days drill table (highest daily MAE first).

    Parameters
    ----------
    dataset_id : int

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "table",
        "query_mode": "aggregate",
        "groupby": ["date_key", "day_of_week", "day_type"],
        "metrics": [
            MAE_METRIC,
            BIAS_METRIC,
            sql_metric("max(abs_error_jpy_kwh)", "Max |error|"),
            sql_metric("max(actual_price_jpy_kwh)", "Max actual"),
        ],
        "adhoc_filters": [],
        "timeseries_limit_metric": MAE_METRIC,
        "order_desc": True,
        "row_limit": 20,
        "server_page_length": 20,
        "table_timestamp_format": "%Y-%m-%d",
        "column_config": {
            "MAE (JPY/kWh)": {"d3NumberFormat": ",.3f"},
            "Bias": {"d3NumberFormat": "+,.3f"},
            "Max |error|": {"d3NumberFormat": ",.2f"},
            "Max actual": {"d3NumberFormat": ",.2f"},
        },
        "extra_form_data": {},
    }


def detail_params(dataset_id: int) -> dict:
    """Params for the forecast-vs-actual line chart at the 30-minute grain.

    Loads the whole backtest window; the data-zoom slider (``zoomable``)
    navigates from the full five years down to a single day.

    Parameters
    ----------
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
            avg_metric("forecast_price_jpy_kwh", "Forecast"),
            avg_metric("actual_price_jpy_kwh", "Actual"),
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
        "y_axis_title": "JPY/kWh",
        "y_axis_title_margin": 30,
        "truncateYAxis": False,
        "color_scheme": "supersetColors",
        "comparison_type": "values",
        "annotation_layers": [],
        "time_range": "No filter",
        "extra_form_data": {},
    }


def upsert_chart(client: SupersetClient, name: str, dataset_id: int, params: dict) -> int:
    """Create or update a chart by name and return its id.

    Parameters
    ----------
    client : SupersetClient
    name : str
        ``slice_name`` to match on.
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
    chart_id = client.find_one("chart", "slice_name", name)
    if chart_id is None:
        return client._post_json("/api/v1/chart/", payload)["id"]
    client._put_json(f"/api/v1/chart/{chart_id}", payload)
    return chart_id


def build_position_json(sections: list[dict]) -> dict:
    """Dashboard layout: sections of rows, each section optionally headed.

    Parameters
    ----------
    sections : list of dict
        Each ``{"header": str | None, "rows": [[(chart_id, name, width,
        height), ...], ...]}``. Widths within a row should sum to 12; height
        is in dashboard grid units (~8 px each).

    Returns
    -------
    dict
    """
    position = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
        "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": DASHBOARD_TITLE}},
    }
    for s, section in enumerate(sections):
        if section["header"]:
            header_key = f"HEADER-{s}"
            position["GRID_ID"]["children"].append(header_key)
            position[header_key] = {
                "type": "HEADER",
                "id": header_key,
                "children": [],
                "parents": ["ROOT_ID", "GRID_ID"],
                "meta": {
                    "text": section["header"],
                    "headerSize": "MEDIUM_HEADER",
                    "background": "BACKGROUND_TRANSPARENT",
                },
            }
        for r, row in enumerate(section["rows"]):
            row_key = f"ROW-{s}-{r}"
            position["GRID_ID"]["children"].append(row_key)
            position[row_key] = {
                "type": "ROW",
                "id": row_key,
                "children": [],
                "parents": ["ROOT_ID", "GRID_ID"],
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
            }
            for chart_id, name, width, height in row:
                chart_key = f"CHART-{chart_id}"
                position[row_key]["children"].append(chart_key)
                position[chart_key] = {
                    "type": "CHART",
                    "id": chart_key,
                    "children": [],
                    "parents": ["ROOT_ID", "GRID_ID", row_key],
                    "meta": {
                        "chartId": chart_id,
                        "width": width,
                        "height": height,
                        "sliceName": name,
                    },
                }
    return position


def build_native_filters(
    dataset_id: int,
    run_excluded: list[int],
    default_run_label: str | None,
) -> list[dict]:
    """Native filter configuration: the Run picker.

    Parameters
    ----------
    dataset_id : int
        Dataset the run_label filter reads its values from.
    run_excluded : list of int
        Charts the Run filter must NOT apply to (the cross-run leaderboard).
    default_run_label : str or None
        Explicit on-load default for the Run filter; None falls back to
        ``defaultToFirstItem`` (which needs one manual Apply click).

    Returns
    -------
    list of dict
    """
    if default_run_label is None:
        run_default_mask = {"extraFormData": {}, "filterState": {}}
    else:
        run_default_mask = {
            "extraFormData": {
                "filters": [{"col": "run_label", "op": "IN", "val": [default_run_label]}]
            },
            "filterState": {"value": [default_run_label], "label": default_run_label},
        }
    return [
        {
            "id": "NATIVE_FILTER-run",
            "name": "Run",
            "filterType": "filter_select",
            "targets": [{"column": {"name": "run_label"}, "datasetId": dataset_id}],
            "defaultDataMask": run_default_mask,
            "controlValues": {
                "multiSelect": False,
                "enableEmptyFilter": True,
                "defaultToFirstItem": default_run_label is None,
                "inverseSelection": False,
                "searchAllOptions": False,
                "sortAscending": False,
            },
            "cascadeParentIds": [],
            "scope": {"rootPath": ["ROOT_ID"], "excluded": run_excluded},
            "type": "NATIVE_FILTER",
            "description": "published_at | area | run_id prefix (newest first)",
        },
    ]


def upsert_dashboard(client: SupersetClient, position: dict, native_filters: list[dict]) -> int:
    """Create or update the dashboard and return its id.

    Parameters
    ----------
    client : SupersetClient
    position : dict
        ``position_json`` layout from :func:`build_position_json`.
    native_filters : list of dict
        ``native_filter_configuration`` from :func:`build_native_filters`.

    Returns
    -------
    int
    """
    dashboard_id = client.find_one("dashboard", "dashboard_title", DASHBOARD_TITLE)
    if dashboard_id is None:
        dashboard_id = client._post_json(
            "/api/v1/dashboard/", {"dashboard_title": DASHBOARD_TITLE, "slug": DASHBOARD_SLUG}
        )["id"]
    json_metadata = {
        "native_filter_configuration": native_filters,
        "cross_filters_enabled": True,
        "chart_configuration": {},
        "color_scheme": "",
        "expanded_slices": {},
        "label_colors": {},
        "refresh_frequency": 0,
        "timed_refresh_immune_slices": [],
    }
    client._put_json(
        f"/api/v1/dashboard/{dashboard_id}",
        {
            "dashboard_title": DASHBOARD_TITLE,
            "slug": DASHBOARD_SLUG,
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


def main() -> None:
    client = SupersetClient(SUPERSET_URL, ADMIN_USER, ADMIN_PASSWORD)

    database_id = client.find_one("database", "database_name", DATABASE_NAME)
    if database_id is None:
        raise SystemExit(
            f"Database connection {DATABASE_NAME!r} not found — register the "
            "Spark Thriftserver connection in the Superset UI first."
        )

    dataset_id = upsert_dataset(client, database_id)
    logger.info("dataset {}: id={}", DATASET_NAME, dataset_id)

    def chart(name: str, params: dict) -> int:
        chart_id = upsert_chart(client, name, dataset_id, params)
        logger.info("chart {}: {}", chart_id, name)
        return chart_id

    # KPI tiles
    kpi_mae = chart("Overall MAE", big_number_params(dataset_id, MAE_METRIC, "JPY/kWh", ",.3f"))
    kpi_bias = chart(
        "Bias (mean error)",
        big_number_params(dataset_id, BIAS_METRIC, "JPY/kWh; + = over-forecast", "+,.3f"),
    )
    kpi_rmse = chart("RMSE", big_number_params(dataset_id, RMSE_METRIC, "JPY/kWh", ",.3f"))
    kpi_ratio = chart(
        "RMSE / MAE",
        big_number_params(dataset_id, RMSE_MAE_METRIC, ">1.3 = spike-heavy errors", ",.2f"),
    )
    kpi_wape = chart(
        "WAPE", big_number_params(dataset_id, WAPE_METRIC, "Σ|error| / Σ actual", ".1%")
    )
    kpi_p90 = chart("P90 abs error", big_number_params(dataset_id, P90_METRIC, "JPY/kWh", ",.3f"))

    # Error structure
    mae_year = chart("MAE by year", bar_params(dataset_id, "year"))
    mae_tc = chart("MAE by time code", bar_params(dataset_id, "time_code"))
    heat_tc = chart("MAE by year and time code", heatmap_params(dataset_id, "time_code"))
    heat_month = chart("MAE by year and month", heatmap_params(dataset_id, "month"))
    mae_dow = chart("MAE by day of week", bar_params(dataset_id, "day_of_week"))
    mae_daypart = chart("MAE by day part", bar_params(dataset_id, "day_part"))
    mae_daytype = chart("MAE by day type", bar_params(dataset_id, "day_type"))

    # Calibration & distribution
    mae_band = chart("MAE by actual price band", bar_params(dataset_id, "actual_price_band"))
    calibration = chart(
        "Calibration: forecast vs actual price level", calibration_params(dataset_id)
    )
    histogram = chart("Error distribution", histogram_params(dataset_id))

    # Runs & drilldown
    leaderboard = chart("Run leaderboard", leaderboard_params(dataset_id))
    worst_days = chart("Worst days", worst_days_params(dataset_id))
    detail = chart("Forecast vs actual (30-min detail)", detail_params(dataset_id))

    sections = [
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
                    (mae_band, "MAE by actual price band", 5, 42),
                    (calibration, "Calibration: forecast vs actual price level", 7, 42),
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
    all_charts = [
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

    default_run = latest_run_label(client, database_id)
    logger.info("default run: {}", default_run)
    dashboard_id = upsert_dashboard(
        client,
        build_position_json(sections),
        build_native_filters(
            dataset_id,
            run_excluded=[leaderboard],
            default_run_label=default_run,
        ),
    )
    attach_charts(client, dashboard_id, all_charts)
    logger.info("dashboard: id={}", dashboard_id)
    logger.info("open: http://localhost:8088/superset/dashboard/{}/", DASHBOARD_SLUG)


if __name__ == "__main__":
    main()
