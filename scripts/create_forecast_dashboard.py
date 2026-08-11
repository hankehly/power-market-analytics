"""Create or update the "Spot Price Forecast Analysis" Superset dashboard.

Builds (idempotently, matched by name) everything the dashboard needs via the
Superset REST API, so the whole thing is reproducible from the repo after a
``docker compose down -v``:

- virtual dataset ``spot_price_forecast_analysis`` — the forecast accuracy
  mart joined to dim_area / dim_delivery_period / dim_date, plus a
  ``run_label`` column for the run picker
- three charts: MAE heatmaps (year x time code, year x month) and a
  forecast-vs-actual detail line at the 30-minute grain
- the dashboard, with a required single-select Run filter (all charts) and a
  delivery-date range filter (detail chart only; the heatmaps always show the
  full window)

Run inside the devcontainer (needs the compose network):

    python scripts/create_forecast_dashboard.py

Environment: ``SUPERSET_URL`` (default ``http://superset:8088``),
``SUPERSET_ADMIN_USER`` (``admin``), ``SUPERSET_ADMIN_PASSWORD`` (``admin``).
"""

from __future__ import annotations

import json
import os

import requests

SUPERSET_URL = os.environ.get("SUPERSET_URL", "http://superset:8088")
ADMIN_USER = os.environ.get("SUPERSET_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("SUPERSET_ADMIN_PASSWORD", "admin")

DATABASE_NAME = "Spark Thriftserver"
DATASET_NAME = "spot_price_forecast_analysis"
DASHBOARD_TITLE = "Spot Price Forecast Analysis"
DASHBOARD_SLUG = "spot-price-forecast-analysis"

CHART_OVERALL_MAE = "Overall MAE"
CHART_MAE_YEAR = "MAE by year"
CHART_MAE_TIME_CODE = "MAE by time code"
CHART_HEAT_TIME_CODE = "MAE by year and time code"
CHART_HEAT_MONTH = "MAE by year and month"
CHART_DETAIL = "Forecast vs actual (30-min detail)"

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
            csrf=False,
        )["access_token"]
        self.session.headers["Authorization"] = f"Bearer {token}"
        self.session.headers["Referer"] = self.base_url
        csrf = self._get_json("/api/v1/security/csrf_token/")["result"]
        self.session.headers["X-CSRFToken"] = csrf

    def _get_json(self, path: str, params: dict | None = None) -> dict:
        r = self.session.get(f"{self.base_url}{path}", params=params)
        r.raise_for_status()
        return r.json()

    def _post_json(self, path: str, payload: dict, csrf: bool = True) -> dict:
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
        {"column_name": name, "type": dtype, "is_dttm": is_dttm, "groupby": True, "filterable": True}
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


def big_number_params(dataset_id: int) -> dict:
    """Params for the overall-MAE stat tile.

    Parameters
    ----------
    dataset_id : int

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "big_number_total",
        "metric": avg_metric("abs_error_jpy_kwh", "MAE"),
        "adhoc_filters": [],
        "subheader": "JPY/kWh, all delivery periods",
        "header_font_size": 0.4,
        "subheader_font_size": 0.125,
        "y_axis_format": ",.3f",
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
        Dataset column for the x axis (``year`` or ``time_code``).

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
        "metrics": [avg_metric("abs_error_jpy_kwh", "MAE (JPY/kWh)")],
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

    Sequential single-hue ramp (blues): the metric encodes magnitude only.

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
        "metric": avg_metric("abs_error_jpy_kwh", "MAE (JPY/kWh)"),
        "adhoc_filters": [],
        "row_limit": 10000,
        "sort_x_axis": "alpha_asc",
        "sort_y_axis": "alpha_asc",
        "normalize_across": "heatmap",
        "legend_type": "continuous",
        "show_legend": True,
        # Single-hue sequential ramp ("Dark blues"): MAE encodes magnitude only.
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


def detail_params(dataset_id: int) -> dict:
    """Params for the forecast-vs-actual line chart at the 30-minute grain.

    Defaults to the last month of data; the dashboard's date filter (scoped to
    this chart only) zooms to any day.

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
        # Default window; echarts charts take the time range from a
        # TEMPORAL_RANGE adhoc filter, not the legacy time_range field. The
        # dashboard's date filter overrides it.
        "adhoc_filters": [
            {
                "expressionType": "SIMPLE",
                "clause": "WHERE",
                "subject": "trade_datetime",
                "operator": "TEMPORAL_RANGE",
                "comparator": "Last month",
            }
        ],
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
        "time_range": "Last month",
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


def build_position_json(rows: list[list[tuple[int, str, int, int]]]) -> dict:
    """Dashboard layout: rows of charts.

    Parameters
    ----------
    rows : list of list of (chart_id, slice_name, width, height)
        In display order; widths within a row should sum to 12; height is in
        dashboard grid units (~8 px each).

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
    for i, row in enumerate(rows):
        row_key = f"ROW-{i}"
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
                "meta": {"chartId": chart_id, "width": width, "height": height, "sliceName": name},
            }
    return position


def build_native_filters(
    dataset_id: int, full_window_chart_ids: list[int], default_run_label: str | None
) -> list[dict]:
    """Native filter configuration: Run picker + detail date range.

    Parameters
    ----------
    dataset_id : int
        Dataset the run_label filter reads its values from.
    full_window_chart_ids : list of int
        Charts the date filter must NOT apply to (they always show the whole
        backtest window).
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
            "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
            "type": "NATIVE_FILTER",
            "description": "published_at | area | run_id prefix (newest first)",
        },
        {
            "id": "NATIVE_FILTER-dates",
            "name": "Delivery date range",
            "filterType": "filter_time",
            "targets": [],
            "defaultDataMask": {"extraFormData": {}, "filterState": {}},
            "controlValues": {},
            "cascadeParentIds": [],
            "scope": {"rootPath": ["ROOT_ID"], "excluded": full_window_chart_ids},
            "type": "NATIVE_FILTER",
            "description": "Zoom the 30-min detail chart (heatmaps always show all years).",
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
    print(f"dataset {DATASET_NAME}: id={dataset_id}")

    overall = upsert_chart(client, CHART_OVERALL_MAE, dataset_id, big_number_params(dataset_id))
    mae_year = upsert_chart(client, CHART_MAE_YEAR, dataset_id, bar_params(dataset_id, "year"))
    mae_tc = upsert_chart(
        client, CHART_MAE_TIME_CODE, dataset_id, bar_params(dataset_id, "time_code")
    )
    heat_tc = upsert_chart(
        client, CHART_HEAT_TIME_CODE, dataset_id, heatmap_params(dataset_id, "time_code")
    )
    heat_month = upsert_chart(
        client, CHART_HEAT_MONTH, dataset_id, heatmap_params(dataset_id, "month")
    )
    detail = upsert_chart(client, CHART_DETAIL, dataset_id, detail_params(dataset_id))
    print(
        f"charts: overall={overall}, mae_year={mae_year}, mae_time_code={mae_tc}, "
        f"heat_time_code={heat_tc}, heat_month={heat_month}, detail={detail}"
    )

    position = build_position_json(
        [
            [
                (overall, CHART_OVERALL_MAE, 3, 40),
                (mae_year, CHART_MAE_YEAR, 4, 40),
                (mae_tc, CHART_MAE_TIME_CODE, 5, 40),
            ],
            [(heat_tc, CHART_HEAT_TIME_CODE, 12, 56)],
            [(heat_month, CHART_HEAT_MONTH, 12, 56)],
            [(detail, CHART_DETAIL, 12, 64)],
        ]
    )
    all_charts = [overall, mae_year, mae_tc, heat_tc, heat_month, detail]
    default_run = latest_run_label(client, database_id)
    print(f"default run: {default_run}")
    dashboard_id = upsert_dashboard(
        client,
        position,
        build_native_filters(
            dataset_id, [overall, mae_year, mae_tc, heat_tc, heat_month], default_run
        ),
    )
    attach_charts(client, dashboard_id, all_charts)
    print(f"dashboard: id={dashboard_id}")
    print(f"open: http://localhost:8088/superset/dashboard/{DASHBOARD_SLUG}/")


if __name__ == "__main__":
    main()
