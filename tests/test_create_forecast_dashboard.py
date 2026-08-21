"""Tests for ``scripts/create_forecast_dashboard.py`` (Superset dashboard builder).

The only thing faked is the HTTP boundary: ``FakeSupersetSession`` is an
in-memory stand-in for ``requests.Session`` that emulates the handful of
Superset REST endpoints the script uses (login, CSRF, list-with-filters,
create, update, SQL Lab execute) and records every call. Everything else in
the script — the client, the dashboard specs, the param builders, the
layout/filter builders, ``build_dashboard`` and ``main`` — runs for real
against it.
"""

from __future__ import annotations

import dataclasses
import json
import re
from urllib.parse import urlsplit

import pytest
import requests

from tests.support import import_script

BASE = "http://superset.test:8088"
DEFAULT_LABEL = "2026-08-18 09:00 | tokyo | abcdef12"

# The exact rison the client must send for an equality lookup: one or more
# ``(col:NAME,opr:eq,value:VALUE)`` filters, strings quoted, ints bare.
FILTERS_Q_RE = re.compile(r"\(filters:!\((.*)\),page_size:100\)")
FILTER_RE = re.compile(r"\(col:(\w+),opr:eq,value:('[^']*'|\d+)\)")


def parse_filters(q: str) -> dict[str, str | int] | None:
    """Parse the ``q`` rison into ``{column: value}``; None if malformed."""
    m = FILTERS_Q_RE.fullmatch(q)
    if m is None:
        return None
    parts = FILTER_RE.findall(m.group(1))
    if ",".join(f"(col:{c},opr:eq,value:{v})" for c, v in parts) != m.group(1):
        return None
    return {c: v[1:-1] if v.startswith("'") else int(v) for c, v in parts}


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class FakeSupersetSession:
    """In-memory Superset: rows per resource, ids from one counter (10, 11, ...).

    ``calls`` records every request as ``(method, url, json, params)``.
    ``overrides`` maps ``(method, path)`` to a canned ``FakeResponse`` so a
    test can make any endpoint fail or return a specific body.
    """

    RESOURCES = ("database", "dataset", "chart", "dashboard")

    def __init__(self, *, sqllab: FakeResponse | None = None):
        self.headers: dict[str, str] = {}
        self.calls: list[tuple] = []
        self.rows: dict[str, dict[int, dict]] = {r: {} for r in self.RESOURCES}
        self.overrides: dict[tuple[str, str], FakeResponse] = {}
        self._next_id = 10
        if sqllab is not None:
            self.overrides[("POST", "/api/v1/sqllab/execute/")] = sqllab

    # -- test-side helpers -------------------------------------------------
    def seed(self, resource: str, **fields) -> int:
        row_id = fields.pop("id", None)
        if row_id is None:
            row_id = self._allocate()
        self.rows[resource][row_id] = {"id": row_id, **fields}
        return row_id

    def id_of(self, resource: str, column: str, value) -> int:
        (row_id,) = [i for i, row in self.rows[resource].items() if row.get(column) == value]
        return row_id

    def calls_after_login(self) -> list[tuple]:
        return self.calls[2:]

    def _allocate(self) -> int:
        row_id = self._next_id
        self._next_id += 1
        return row_id

    # -- requests.Session surface -----------------------------------------
    def get(self, url: str, params: dict | None = None) -> FakeResponse:
        return self._dispatch("GET", url, None, params)

    def post(self, url: str, json: dict | None = None) -> FakeResponse:
        return self._dispatch("POST", url, json, None)

    def put(self, url: str, json: dict | None = None, params: dict | None = None) -> FakeResponse:
        return self._dispatch("PUT", url, json, params)

    def _dispatch(self, method: str, url: str, payload: dict | None, params: dict | None):
        self.calls.append((method, url, payload, params))
        path = urlsplit(url).path
        if (method, path) in self.overrides:
            return self.overrides[(method, path)]
        if path != "/api/v1/security/login" and "Authorization" not in self.headers:
            return FakeResponse({"msg": "Missing Authorization Header"}, 401)
        if method == "GET":
            return self._get(path, params)
        if method == "POST":
            return self._post(path, payload)
        return self._put(path, payload)

    def _get(self, path: str, params: dict | None) -> FakeResponse:
        if path == "/api/v1/security/csrf_token/":
            return FakeResponse({"result": "csrf"})
        m = re.fullmatch(r"/api/v1/(\w+)/", path)
        if m and m.group(1) in self.rows:
            filters = parse_filters((params or {}).get("q", ""))
            if filters is None:
                return FakeResponse({"message": "bad rison"}, 400)
            result = [
                row
                for row in self.rows[m.group(1)].values()
                if all(row.get(col) == value for col, value in filters.items())
            ]
            return FakeResponse({"count": len(result), "result": result})
        return FakeResponse({"message": "Not found"}, 404)

    def _post(self, path: str, payload: dict | None) -> FakeResponse:
        if path == "/api/v1/security/login":
            return FakeResponse({"access_token": "tok"})
        if path == "/api/v1/sqllab/execute/":
            return FakeResponse({"data": [{"run_label": DEFAULT_LABEL}]})
        m = re.fullmatch(r"/api/v1/(dataset|chart|dashboard)/", path)
        if m:
            assert payload is not None
            row_id = self.seed(m.group(1), **payload)
            return FakeResponse({"id": row_id, "result": payload}, 201)
        return FakeResponse({"message": "Not found"}, 404)

    def _put(self, path: str, payload: dict | None) -> FakeResponse:
        m = re.fullmatch(r"/api/v1/(dataset|chart|dashboard)/(\d+)", path)
        if m and int(m.group(2)) in self.rows[m.group(1)]:
            assert payload is not None
            row = self.rows[m.group(1)][int(m.group(2))]
            row.update(payload)
            return FakeResponse({"id": row["id"], "result": payload})
        return FakeResponse({"message": "Not found"}, 404)


@pytest.fixture
def script():
    return import_script("create_forecast_dashboard")


@pytest.fixture
def spot(script):
    return script.DASHBOARDS["spot_price"]


@pytest.fixture
def demand(script):
    return script.DASHBOARDS["demand"]


@pytest.fixture(params=["spot_price", "demand"])
def spec(script, request):
    """Either dashboard spec — for behaviour that must hold for both."""
    return script.DASHBOARDS[request.param]


@pytest.fixture
def fake() -> FakeSupersetSession:
    return FakeSupersetSession()


def make_client(script, fake: FakeSupersetSession, base_url: str = BASE):
    return script.SupersetClient(base_url, "admin", "secret", session=fake)


LOGIN_CALL = (
    "POST",
    f"{BASE}/api/v1/security/login",
    {"username": "admin", "password": "secret", "provider": "db", "refresh": True},
    None,
)
CSRF_CALL = ("GET", f"{BASE}/api/v1/security/csrf_token/", None, None)


# --------------------------------------------------------------------------- client
class TestSupersetClient:
    def test_logs_in_then_fetches_csrf_and_sets_headers(self, script, fake):
        client = make_client(script, fake)

        assert fake.calls == [LOGIN_CALL, CSRF_CALL]
        assert fake.headers == {
            "Authorization": "Bearer tok",
            "Referer": BASE,
            "X-CSRFToken": "csrf",
        }
        assert client.session is fake

    def test_trailing_slash_is_stripped_from_base_url(self, script, fake):
        client = make_client(script, fake, base_url=BASE + "/")
        assert client.base_url == BASE
        assert fake.calls[0][1] == f"{BASE}/api/v1/security/login"
        assert fake.headers["Referer"] == BASE

    def test_default_session_is_a_requests_session(self, script, fake, monkeypatch):
        monkeypatch.setattr(script.requests, "Session", lambda: fake)
        client = script.SupersetClient(BASE, "admin", "secret")
        assert client.session is fake
        assert fake.headers["Authorization"] == "Bearer tok"

    def test_post_json_raises_on_http_error(self, script, fake):
        fake.overrides[("POST", "/api/v1/security/login")] = FakeResponse({"message": "no"}, 401)
        with pytest.raises(requests.HTTPError, match="401"):
            make_client(script, fake)
        assert fake.headers == {}  # never got as far as the bearer header

    def test_get_json_raises_on_http_error(self, script, fake):
        fake.overrides[("GET", "/api/v1/security/csrf_token/")] = FakeResponse({}, 500)
        with pytest.raises(requests.HTTPError, match="500"):
            make_client(script, fake)
        assert "X-CSRFToken" not in fake.headers

    def test_put_json_raises_on_http_error(self, script, fake):
        client = make_client(script, fake)
        with pytest.raises(requests.HTTPError, match="404"):
            client._put_json("/api/v1/chart/999", {"slice_name": "x"})

    def test_find_one_returns_first_matching_id_and_sends_rison_filter(self, script, fake):
        fake.seed("chart", id=41, slice_name="Other")
        fake.seed("chart", id=42, slice_name="Overall MAE")
        fake.seed("chart", id=43, slice_name="Overall MAE")
        client = make_client(script, fake)

        assert client.find_one("chart", slice_name="Overall MAE") == 42
        assert fake.calls_after_login() == [
            (
                "GET",
                f"{BASE}/api/v1/chart/",
                None,
                {"q": "(filters:!((col:slice_name,opr:eq,value:'Overall MAE')),page_size:100)"},
            )
        ]

    def test_find_one_ands_several_filters_and_leaves_ints_unquoted(self, script, fake):
        fake.seed("chart", id=41, slice_name="Overall MAE", datasource_id=3)
        fake.seed("chart", id=42, slice_name="Overall MAE", datasource_id=7)
        client = make_client(script, fake)

        assert client.find_one("chart", slice_name="Overall MAE", datasource_id=7) == 42
        assert client.find_one("chart", slice_name="Overall MAE", datasource_id=8) is None
        assert fake.calls_after_login()[0][3] == {
            "q": (
                "(filters:!((col:slice_name,opr:eq,value:'Overall MAE'),"
                "(col:datasource_id,opr:eq,value:7)),page_size:100)"
            )
        }

    def test_find_one_returns_none_when_nothing_matches(self, script, fake):
        fake.seed("dashboard", dashboard_title="Something else")
        client = make_client(script, fake)
        assert client.find_one("dashboard", dashboard_title="Spot Price Forecast Analysis") is None


# --------------------------------------------------------------------------- specs
SPOT_DATASET_SQL = """\
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

DEMAND_DATASET_SQL = """\
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
  f.forecast_demand_kwh,
  f.actual_demand_kwh,
  cast(round(f.actual_demand_kwh, -6) as bigint) as actual_demand_round_kwh,
  case
    when f.actual_demand_kwh is null then null
    else concat(
      lpad(cast(cast(floor(f.actual_demand_kwh / 2000000) * 2 as int) as string), 2, '0'),
      '-',
      lpad(cast(cast(floor(f.actual_demand_kwh / 2000000) * 2 + 2 as int) as string), 2, '0')
    )
  end as actual_demand_band,
  f.error_kwh,
  f.abs_error_kwh,
  f.pct_error,
  f.abs_pct_error
from pma_curated.fct_demand_forecast_accuracy f
join pma_curated.dim_area a on f.area_key = a.area_key
join pma_curated.dim_delivery_period p on f.time_code = p.time_code
join pma_curated.dim_date d on f.date_key = d.date_key
"""

COMMON_COLUMNS_HEAD = [
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
]
SPOT_COLUMNS = COMMON_COLUMNS_HEAD + [
    ("forecast_price_jpy_kwh", "DOUBLE", False),
    ("actual_price_jpy_kwh", "DOUBLE", False),
    ("actual_price_round_jpy", "INT", False),
    ("actual_price_band", "STRING", False),
    ("error_jpy_kwh", "DOUBLE", False),
    ("abs_error_jpy_kwh", "DOUBLE", False),
    ("pct_error", "DOUBLE", False),
    ("abs_pct_error", "DOUBLE", False),
]
DEMAND_COLUMNS = COMMON_COLUMNS_HEAD + [
    ("forecast_demand_kwh", "DOUBLE", False),
    ("actual_demand_kwh", "BIGINT", False),
    ("actual_demand_round_kwh", "BIGINT", False),
    ("actual_demand_band", "STRING", False),
    ("error_kwh", "DOUBLE", False),
    ("abs_error_kwh", "DOUBLE", False),
    ("pct_error", "DOUBLE", False),
    ("abs_pct_error", "DOUBLE", False),
]


class TestDashboardSpecs:
    def test_registry_lists_spot_price_then_demand_keyed_by_task(self, script):
        assert list(script.DASHBOARDS) == ["spot_price", "demand"]
        assert all(s.task == task for task, s in script.DASHBOARDS.items())

    def test_specs_are_frozen(self, spec):
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.unit = "x"

    def test_spot_price_identity(self, spot):
        assert spot.dataset_name == "spot_price_forecast_analysis"
        assert spot.dashboard_title == "Spot Price Forecast Analysis"
        assert spot.dashboard_slug == "spot-price-forecast-analysis"
        assert spot.accuracy_table == "pma_curated.fct_spot_price_forecast_accuracy"
        assert spot.unit == "JPY/kWh"
        assert (spot.forecast_col, spot.actual_col) == (
            "forecast_price_jpy_kwh",
            "actual_price_jpy_kwh",
        )
        assert (spot.error_col, spot.abs_error_col) == ("error_jpy_kwh", "abs_error_jpy_kwh")
        assert spot.band_col == "actual_price_band"
        assert spot.band_chart_title == "MAE by actual price band"
        assert spot.calibration_x_col == "actual_price_round_jpy"
        assert spot.calibration_chart_title == "Calibration: forecast vs actual price level"
        assert spot.calibration_x_title == "Actual price (JPY/kWh, rounded)"
        assert spot.number_format == ",.3f"
        assert spot.signed_number_format == "+,.3f"
        assert spot.axis_format == ",.2f"
        assert spot.calibration_x_format == "~g"
        assert spot.calibration_y_format == ",.1f"
        assert spot.worst_days_max_format == ",.2f"

    def test_demand_identity(self, demand):
        assert demand.dataset_name == "demand_forecast_analysis"
        assert demand.dashboard_title == "Demand Forecast Analysis"
        assert demand.dashboard_slug == "demand-forecast-analysis"
        assert demand.accuracy_table == "pma_curated.fct_demand_forecast_accuracy"
        assert demand.unit == "kWh"
        assert (demand.forecast_col, demand.actual_col) == (
            "forecast_demand_kwh",
            "actual_demand_kwh",
        )
        assert (demand.error_col, demand.abs_error_col) == ("error_kwh", "abs_error_kwh")
        assert demand.band_col == "actual_demand_band"
        assert demand.band_chart_title == "MAE by actual demand band"
        assert demand.calibration_x_col == "actual_demand_round_kwh"
        assert demand.calibration_chart_title == "Calibration: forecast vs actual demand level"
        assert demand.calibration_x_title == "Actual demand (kWh, rounded to 1 GWh)"
        assert demand.number_format == ".4s"
        assert demand.signed_number_format == "+.4s"
        assert demand.axis_format == "SMART_NUMBER"
        assert demand.calibration_x_format == "SMART_NUMBER"
        assert demand.calibration_y_format == "SMART_NUMBER"
        assert demand.worst_days_max_format == ".4s"

    def test_spot_price_dataset_sql_is_unchanged(self, spot):
        assert spot.dataset_sql == SPOT_DATASET_SQL

    def test_demand_dataset_sql(self, demand):
        assert demand.dataset_sql == DEMAND_DATASET_SQL

    def test_dataset_columns_follow_the_sql(self, spot, demand):
        assert spot.dataset_columns == SPOT_COLUMNS
        assert demand.dataset_columns == DEMAND_COLUMNS

    def test_dataset_columns_match_the_sql_select_list_in_order(self, spec):
        select_list = spec.dataset_sql.split("\nfrom ", 1)[0].splitlines()[1:]
        output_names = []
        for line in select_list:
            if m := re.fullmatch(r"\s+[fpda]\.(\w+),?", line):
                output_names.append(m.group(1))
            elif m := re.search(r"\bas (\w+),?$", line):
                output_names.append(m.group(1))
        assert [name for name, _, _ in spec.dataset_columns] == output_names
        assert [n for n, _, is_dttm in spec.dataset_columns if is_dttm] == [
            "date_key",
            "trade_datetime",
            "published_at",
            "forecast_issued_ts",
        ]

    def test_metrics_are_derived_from_the_spec_columns_and_unit(self, script, spot, demand):
        assert spot.mae_metric == script.avg_metric("abs_error_jpy_kwh", "MAE (JPY/kWh)")
        assert demand.mae_metric == script.avg_metric("abs_error_kwh", "MAE (kWh)")
        assert spot.bias_metric == script.avg_metric("error_jpy_kwh", "Bias")
        assert demand.bias_metric["column"]["column_name"] == "error_kwh"
        assert spot.rmse_metric["sqlExpression"] == "sqrt(avg(power(error_jpy_kwh, 2)))"
        assert demand.rmse_metric["sqlExpression"] == "sqrt(avg(power(error_kwh, 2)))"
        assert spot.rmse_metric["optionName"] == "metric_rmse"
        assert spot.rmse_mae_metric["optionName"] == "metric_rmse_mae"
        assert (
            spot.rmse_mae_metric["sqlExpression"]
            == "sqrt(avg(power(error_jpy_kwh, 2))) / avg(abs_error_jpy_kwh)"
        )
        assert (
            demand.rmse_mae_metric["sqlExpression"]
            == "sqrt(avg(power(error_kwh, 2))) / avg(abs_error_kwh)"
        )
        assert spot.wape_metric["sqlExpression"] == (
            "sum(abs_error_jpy_kwh) / sum(actual_price_jpy_kwh)"
        )
        assert demand.wape_metric["sqlExpression"] == "sum(abs_error_kwh) / sum(actual_demand_kwh)"
        assert spot.p90_metric["sqlExpression"] == "percentile(abs_error_jpy_kwh, 0.90)"
        assert demand.p90_metric["sqlExpression"] == "percentile(abs_error_kwh, 0.90)"
        assert spot.p90_metric["optionName"] == "metric_p90_abs_error"


# --------------------------------------------------------------------------- dataset
class TestUpsertDataset:
    def test_creates_then_overrides_columns(self, script, fake, spec):
        client = make_client(script, fake)

        dataset_id = script.upsert_dataset(client, 3, spec)

        assert dataset_id == 10
        find, create, update = fake.calls_after_login()
        assert find[:2] == ("GET", f"{BASE}/api/v1/dataset/")
        assert find[3] == {
            "q": f"(filters:!((col:table_name,opr:eq,value:'{spec.dataset_name}')),page_size:100)"
        }
        assert create == (
            "POST",
            f"{BASE}/api/v1/dataset/",
            {"database": 3, "table_name": spec.dataset_name, "sql": spec.dataset_sql},
            None,
        )
        method, url, payload, params = update
        assert (method, url, params) == (
            "PUT",
            f"{BASE}/api/v1/dataset/10",
            {"override_columns": "true"},
        )
        assert payload["sql"] == spec.dataset_sql
        assert payload["main_dttm_col"] == "trade_datetime"
        assert payload["columns"][0] == {
            "column_name": "date_key",
            "type": "DATE",
            "is_dttm": True,
            "groupby": True,
            "filterable": True,
        }
        assert [(c["column_name"], c["type"], c["is_dttm"]) for c in payload["columns"]] == list(
            spec.dataset_columns
        )
        assert fake.rows["dataset"][10]["main_dttm_col"] == "trade_datetime"

    def test_updates_existing_dataset_without_creating(self, script, fake, spot):
        fake.seed("dataset", id=5, table_name="spot_price_forecast_analysis", database=3)
        client = make_client(script, fake)

        assert script.upsert_dataset(client, 3, spot) == 5

        methods = [(c[0], c[1]) for c in fake.calls_after_login()]
        assert methods == [("GET", f"{BASE}/api/v1/dataset/"), ("PUT", f"{BASE}/api/v1/dataset/5")]
        assert fake.rows["dataset"][5]["sql"] == SPOT_DATASET_SQL
        assert len(fake.rows["dataset"]) == 1

    def test_the_two_datasets_do_not_collide(self, script, fake, spot, demand):
        client = make_client(script, fake)
        assert script.upsert_dataset(client, 3, spot) == 10
        assert script.upsert_dataset(client, 3, demand) == 11
        assert script.upsert_dataset(client, 3, spot) == 10
        assert {r["table_name"] for r in fake.rows["dataset"].values()} == {
            "spot_price_forecast_analysis",
            "demand_forecast_analysis",
        }


# --------------------------------------------------------------------------- run label
class TestLatestRunLabel:
    def test_returns_newest_label_via_sqllab(self, script, fake, spec):
        client = make_client(script, fake)

        assert script.latest_run_label(client, 3, spec) == DEFAULT_LABEL

        (call,) = fake.calls_after_login()
        method, url, payload, params = call
        assert (method, url, params) == ("POST", f"{BASE}/api/v1/sqllab/execute/", None)
        assert payload["database_id"] == 3
        assert payload["runAsync"] is False
        assert f"from {spec.accuracy_table} f" in payload["sql"]
        assert "order by f.published_at desc" in payload["sql"]
        assert "limit 1" in payload["sql"]
        assert "as run_label" in payload["sql"]

    def test_none_on_http_error(self, script, spot):
        fake = FakeSupersetSession(sqllab=FakeResponse({"message": "boom"}, 500))
        assert script.latest_run_label(make_client(script, fake), 3, spot) is None

    def test_none_when_mart_is_empty(self, script, spot):
        fake = FakeSupersetSession(sqllab=FakeResponse({"data": []}))
        assert script.latest_run_label(make_client(script, fake), 3, spot) is None

    def test_none_when_response_has_no_data_key(self, script, spot):
        fake = FakeSupersetSession(sqllab=FakeResponse({"result": "no data here"}))
        assert script.latest_run_label(make_client(script, fake), 3, spot) is None


# --------------------------------------------------------------------------- metrics
class TestMetrics:
    @pytest.mark.parametrize(
        "label, expected",
        [
            ("P90 abs error", "p90_abs_error"),
            ("RMSE/MAE", "rmse_mae"),
            ("Max |error|", "max_error"),
            ("  MAE (JPY/kWh)  ", "mae_jpy_kwh"),
        ],
    )
    def test_slug(self, script, label, expected):
        assert script._slug(label) == expected

    def test_avg_metric(self, script):
        assert script.avg_metric("abs_error_jpy_kwh", "MAE (JPY/kWh)") == {
            "expressionType": "SIMPLE",
            "column": {"column_name": "abs_error_jpy_kwh", "type": "DOUBLE"},
            "aggregate": "AVG",
            "label": "MAE (JPY/kWh)",
            "optionName": "metric_avg_abs_error_jpy_kwh",
        }

    def test_sql_metric_slugs_the_label_into_option_name(self, script):
        assert script.sql_metric("percentile(abs_error_jpy_kwh, 0.90)", "P90 abs error") == {
            "expressionType": "SQL",
            "sqlExpression": "percentile(abs_error_jpy_kwh, 0.90)",
            "label": "P90 abs error",
            "optionName": "metric_p90_abs_error",
        }


# --------------------------------------------------------------------------- chart params
class TestChartParams:
    def test_big_number(self, script, spot):
        p = script.big_number_params(7, spot.mae_metric, "JPY/kWh", ",.3f")
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "big_number_total"
        assert p["metric"] == spot.mae_metric
        assert p["subheader"] == "JPY/kWh"
        assert p["y_axis_format"] == ",.3f"
        assert p["adhoc_filters"] == []

    def test_bar(self, script, spec):
        p = script.bar_params(spec, 7, "day_part")
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "echarts_timeseries_bar"
        assert p["x_axis"] == "day_part"
        assert p["x_axis_sort"] == "day_part"
        assert p["x_axis_sort_asc"] is True
        assert p["metrics"] == [spec.mae_metric]
        assert p["metrics"][0]["label"] == f"MAE ({spec.unit})"
        assert p["row_limit"] == 1000
        assert p["show_legend"] is False
        assert p["y_axis_format"] == spec.axis_format
        assert p["y_axis_title"] == spec.unit
        assert p["time_grain_sqla"] is None

    def test_bar_formats_per_spec(self, script, spot, demand):
        assert script.bar_params(spot, 7, "year")["y_axis_format"] == ",.2f"
        assert script.bar_params(spot, 7, "year")["y_axis_title"] == "JPY/kWh"
        assert script.bar_params(demand, 7, "year")["y_axis_format"] == "SMART_NUMBER"
        assert script.bar_params(demand, 7, "year")["y_axis_title"] == "kWh"

    def test_heatmap(self, script, spec):
        p = script.heatmap_params(spec, 7, "month")
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "heatmap_v2"
        assert p["x_axis"] == "month"
        assert p["groupby"] == "year"
        assert p["metric"] == spec.mae_metric
        assert p["linear_color_scheme"] == "dark_blue"
        assert p["row_limit"] == 10000
        assert p["value_bounds"] == [None, None]
        assert p["time_range"] == "No filter"

    def test_calibration_spot_price(self, script, spot):
        p = script.calibration_params(spot, 7)
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "echarts_timeseries_scatter"
        assert p["x_axis"] == "actual_price_round_jpy"
        assert p["x_axis_sort"] == "actual_price_round_jpy"
        assert [m["label"] for m in p["metrics"]] == [
            "Mean forecast",
            "Mean actual (y = x reference)",
        ]
        assert [m["column"]["column_name"] for m in p["metrics"]] == [
            "forecast_price_jpy_kwh",
            "actual_price_jpy_kwh",
        ]
        assert p["markerSize"] == 5
        assert p["x_axis_title"] == "Actual price (JPY/kWh, rounded)"
        assert p["x_axis_number_format"] == "~g"  # the control's default: unchanged rendering
        assert p["y_axis_title"] == "Forecast (JPY/kWh)"
        assert p["y_axis_format"] == ",.1f"
        assert p["row_limit"] == 10000

    def test_calibration_demand(self, script, demand):
        p = script.calibration_params(demand, 7)
        assert p["x_axis"] == "actual_demand_round_kwh"
        assert p["x_axis_sort"] == "actual_demand_round_kwh"
        assert [m["column"]["column_name"] for m in p["metrics"]] == [
            "forecast_demand_kwh",
            "actual_demand_kwh",
        ]
        assert p["x_axis_title"] == "Actual demand (kWh, rounded to 1 GWh)"
        assert p["x_axis_number_format"] == "SMART_NUMBER"  # not the default ~g → 1.1e+7
        assert p["y_axis_title"] == "Forecast (kWh)"
        assert p["y_axis_format"] == "SMART_NUMBER"

    def test_histogram(self, script, spec):
        p = script.histogram_params(spec, 7)
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "histogram_v2"
        assert p["column"] == spec.error_col
        assert p["bins"] == 60
        assert p["row_limit"] == 100000
        assert p["x_axis_title"] == f"Signed error ({spec.unit}; + = over-forecast)"

    def test_leaderboard(self, script, spec):
        p = script.leaderboard_params(spec, 7)
        mae_label = f"MAE ({spec.unit})"
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "table"
        assert p["query_mode"] == "aggregate"
        assert p["groupby"] == ["run_label", "strategy"]
        assert [m["label"] for m in p["metrics"]] == ["Periods", mae_label, "Bias", "RMSE", "WAPE"]
        assert p["metrics"][0]["sqlExpression"] == "count(*)"
        assert p["metrics"][0]["optionName"] == "metric_periods"
        assert p["metrics"][1] == spec.mae_metric
        assert p["metrics"][3] == spec.rmse_metric
        assert p["metrics"][4] == spec.wape_metric
        assert p["timeseries_limit_metric"] == spec.mae_metric
        assert p["order_desc"] is False  # best MAE first
        assert p["row_limit"] == 100
        assert p["column_config"] == {
            "Periods": {"d3NumberFormat": ",d"},
            mae_label: {"d3NumberFormat": spec.number_format},
            "Bias": {"d3NumberFormat": spec.signed_number_format},
            "RMSE": {"d3NumberFormat": spec.number_format},
            "WAPE": {"d3NumberFormat": ".1%"},
        }

    def test_leaderboard_formats_per_spec(self, script, spot, demand):
        assert script.leaderboard_params(spot, 7)["column_config"]["Bias"] == {
            "d3NumberFormat": "+,.3f"
        }
        assert script.leaderboard_params(demand, 7)["column_config"]["MAE (kWh)"] == {
            "d3NumberFormat": ".4s"
        }

    def test_worst_days(self, script, spec):
        p = script.worst_days_params(spec, 7)
        mae_label = f"MAE ({spec.unit})"
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "table"
        assert p["groupby"] == ["date_key", "day_of_week", "day_type"]
        assert [m["label"] for m in p["metrics"]] == [
            mae_label,
            "Bias",
            "Max |error|",
            "Max actual",
        ]
        assert p["metrics"][2]["sqlExpression"] == f"max({spec.abs_error_col})"
        assert p["metrics"][2]["optionName"] == "metric_max_error"
        assert p["metrics"][3]["sqlExpression"] == f"max({spec.actual_col})"
        assert p["order_desc"] is True  # highest daily MAE first
        assert p["row_limit"] == 20
        assert p["table_timestamp_format"] == "%Y-%m-%d"
        assert p["column_config"] == {
            mae_label: {"d3NumberFormat": spec.number_format},
            "Bias": {"d3NumberFormat": spec.signed_number_format},
            "Max |error|": {"d3NumberFormat": spec.worst_days_max_format},
            "Max actual": {"d3NumberFormat": spec.worst_days_max_format},
        }

    def test_worst_days_formats_per_spec(self, script, spot, demand):
        assert script.worst_days_params(spot, 7)["column_config"]["Max actual"] == {
            "d3NumberFormat": ",.2f"
        }
        assert script.worst_days_params(demand, 7)["column_config"]["Max actual"] == {
            "d3NumberFormat": ".4s"
        }

    def test_detail(self, script, spec):
        p = script.detail_params(spec, 7)
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "echarts_timeseries_line"
        assert p["x_axis"] == "trade_datetime"
        assert p["zoomable"] is True
        assert [m["label"] for m in p["metrics"]] == ["Forecast", "Actual"]
        assert [m["column"]["column_name"] for m in p["metrics"]] == [
            spec.forecast_col,
            spec.actual_col,
        ]
        assert p["row_limit"] == 100000
        assert p["seriesType"] == "line"
        assert p["time_range"] == "No filter"
        assert p["y_axis_format"] == "SMART_NUMBER"
        assert p["y_axis_title"] == spec.unit

    def test_every_builder_targets_the_dataset_and_starts_unfiltered(self, script, spec):
        builders = [
            lambda: script.big_number_params(12, spec.mae_metric, "x", ",.1f"),
            lambda: script.bar_params(spec, 12, "year"),
            lambda: script.heatmap_params(spec, 12, "time_code"),
            lambda: script.calibration_params(spec, 12),
            lambda: script.histogram_params(spec, 12),
            lambda: script.leaderboard_params(spec, 12),
            lambda: script.worst_days_params(spec, 12),
            lambda: script.detail_params(spec, 12),
        ]
        for build in builders:
            p = build()
            assert p["datasource"] == "12__table"
            assert p["adhoc_filters"] == []
            assert p["extra_form_data"] == {}


# --------------------------------------------------------------------------- charts
class TestUpsertChart:
    def test_creates_a_new_chart_with_json_encoded_params(self, script, fake, spot):
        client = make_client(script, fake)
        params = script.bar_params(spot, 10, "year")

        chart_id = script.upsert_chart(client, "MAE by year", 10, params)

        assert chart_id == 10
        find, create = fake.calls_after_login()
        assert find[:2] == ("GET", f"{BASE}/api/v1/chart/")
        assert find[3] == {
            "q": (
                "(filters:!((col:slice_name,opr:eq,value:'MAE by year'),"
                "(col:datasource_id,opr:eq,value:10)),page_size:100)"
            )
        }
        method, url, payload, _ = create
        assert (method, url) == ("POST", f"{BASE}/api/v1/chart/")
        assert payload["slice_name"] == "MAE by year"
        assert payload["datasource_id"] == 10
        assert payload["datasource_type"] == "table"
        assert payload["viz_type"] == "echarts_timeseries_bar"
        assert isinstance(payload["params"], str)
        assert json.loads(payload["params"]) == params
        assert set(payload) == {
            "slice_name",
            "datasource_id",
            "datasource_type",
            "viz_type",
            "params",
        }

    def test_updates_an_existing_chart_in_place(self, script, fake, spot):
        fake.seed("chart", id=42, slice_name="MAE by year", datasource_id=10, viz_type="table")
        client = make_client(script, fake)

        params = script.bar_params(spot, 10, "year")
        assert script.upsert_chart(client, "MAE by year", 10, params) == 42

        methods = [(c[0], c[1]) for c in fake.calls_after_login()]
        assert methods == [("GET", f"{BASE}/api/v1/chart/"), ("PUT", f"{BASE}/api/v1/chart/42")]
        assert fake.rows["chart"][42]["viz_type"] == "echarts_timeseries_bar"
        assert len(fake.rows["chart"]) == 1

    def test_same_name_on_another_dataset_is_a_different_chart(self, script, fake, spot, demand):
        fake.seed("chart", id=42, slice_name="MAE by year", datasource_id=10)
        client = make_client(script, fake)

        new_id = script.upsert_chart(
            client, "MAE by year", 11, script.bar_params(demand, 11, "year")
        )

        assert new_id == 10  # freshly created (the fake's id counter starts at 10)
        assert fake.rows["chart"][42]["datasource_id"] == 10
        assert json.loads(fake.rows["chart"][10]["params"])["datasource"] == "11__table"
        assert (
            script.upsert_chart(client, "MAE by year", 10, script.bar_params(spot, 10, "year"))
            == 42
        )


# --------------------------------------------------------------------------- layout
class TestBuildPositionJson:
    def test_headed_and_unheaded_sections(self, script, spot):
        sections = [
            {"header": None, "rows": [[(1, "A", 6, 24), (2, "B", 6, 24)]]},
            {"header": "Sec", "rows": [[(3, "C", 12, 40)]]},
        ]
        row_meta = {"background": "BACKGROUND_TRANSPARENT"}
        assert script.build_position_json(spot, sections) == {
            "DASHBOARD_VERSION_KEY": "v2",
            "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
            "GRID_ID": {
                "type": "GRID",
                "id": "GRID_ID",
                "children": ["ROW-0-0", "HEADER-1", "ROW-1-0"],
                "parents": ["ROOT_ID"],
            },
            "HEADER_ID": {
                "type": "HEADER",
                "id": "HEADER_ID",
                "meta": {"text": "Spot Price Forecast Analysis"},
            },
            "ROW-0-0": {
                "type": "ROW",
                "id": "ROW-0-0",
                "children": ["CHART-1", "CHART-2"],
                "parents": ["ROOT_ID", "GRID_ID"],
                "meta": row_meta,
            },
            "CHART-1": {
                "type": "CHART",
                "id": "CHART-1",
                "children": [],
                "parents": ["ROOT_ID", "GRID_ID", "ROW-0-0"],
                "meta": {"chartId": 1, "width": 6, "height": 24, "sliceName": "A"},
            },
            "CHART-2": {
                "type": "CHART",
                "id": "CHART-2",
                "children": [],
                "parents": ["ROOT_ID", "GRID_ID", "ROW-0-0"],
                "meta": {"chartId": 2, "width": 6, "height": 24, "sliceName": "B"},
            },
            "HEADER-1": {
                "type": "HEADER",
                "id": "HEADER-1",
                "children": [],
                "parents": ["ROOT_ID", "GRID_ID"],
                "meta": {
                    "text": "Sec",
                    "headerSize": "MEDIUM_HEADER",
                    "background": "BACKGROUND_TRANSPARENT",
                },
            },
            "ROW-1-0": {
                "type": "ROW",
                "id": "ROW-1-0",
                "children": ["CHART-3"],
                "parents": ["ROOT_ID", "GRID_ID"],
                "meta": row_meta,
            },
            "CHART-3": {
                "type": "CHART",
                "id": "CHART-3",
                "children": [],
                "parents": ["ROOT_ID", "GRID_ID", "ROW-1-0"],
                "meta": {"chartId": 3, "width": 12, "height": 40, "sliceName": "C"},
            },
        }

    def test_multiple_rows_in_one_section_are_numbered_per_section(self, script, spot):
        sections = [{"header": "H", "rows": [[(1, "A", 12, 10)], [(2, "B", 12, 10)]]}]
        position = script.build_position_json(spot, sections)
        assert position["GRID_ID"]["children"] == ["HEADER-0", "ROW-0-0", "ROW-0-1"]
        assert position["ROW-0-1"]["children"] == ["CHART-2"]
        assert position["CHART-2"]["parents"] == ["ROOT_ID", "GRID_ID", "ROW-0-1"]

    def test_dashboard_header_carries_the_spec_title(self, script, demand):
        position = script.build_position_json(demand, [])
        assert position["HEADER_ID"]["meta"] == {"text": "Demand Forecast Analysis"}
        assert position["GRID_ID"]["children"] == []


# --------------------------------------------------------------------------- native filters
class TestBuildNativeFilters:
    def test_explicit_default_applies_on_load(self, script):
        (f,) = script.build_native_filters(10, [27], "2026-08-18 09:00 | tokyo | abcdef12")
        assert f["id"] == "NATIVE_FILTER-run"
        assert f["name"] == "Run"
        assert f["filterType"] == "filter_select"
        assert f["type"] == "NATIVE_FILTER"
        assert f["targets"] == [{"column": {"name": "run_label"}, "datasetId": 10}]
        assert f["defaultDataMask"] == {
            "extraFormData": {
                "filters": [
                    {"col": "run_label", "op": "IN", "val": ["2026-08-18 09:00 | tokyo | abcdef12"]}
                ]
            },
            "filterState": {
                "value": ["2026-08-18 09:00 | tokyo | abcdef12"],
                "label": "2026-08-18 09:00 | tokyo | abcdef12",
            },
        }
        assert f["controlValues"]["defaultToFirstItem"] is False
        assert f["controlValues"]["multiSelect"] is False
        assert f["controlValues"]["enableEmptyFilter"] is True
        assert f["scope"] == {"rootPath": ["ROOT_ID"], "excluded": [27]}
        assert f["cascadeParentIds"] == []

    def test_no_default_falls_back_to_first_item(self, script):
        (f,) = script.build_native_filters(10, [], None)
        assert f["defaultDataMask"] == {"extraFormData": {}, "filterState": {}}
        assert f["controlValues"]["defaultToFirstItem"] is True
        assert f["scope"] == {"rootPath": ["ROOT_ID"], "excluded": []}


# --------------------------------------------------------------------------- dashboard
EXPECTED_JSON_METADATA_KEYS = {
    "native_filter_configuration",
    "cross_filters_enabled",
    "chart_configuration",
    "color_scheme",
    "expanded_slices",
    "label_colors",
    "refresh_frequency",
    "timed_refresh_immune_slices",
}


class TestUpsertDashboard:
    def test_creates_then_writes_layout_and_metadata(self, script, fake, spec):
        client = make_client(script, fake)
        position = {"DASHBOARD_VERSION_KEY": "v2", "ROOT_ID": {"children": ["GRID_ID"]}}
        filters = script.build_native_filters(10, [27], None)

        dashboard_id = script.upsert_dashboard(client, spec, position, filters)

        assert dashboard_id == 10
        find, create, update = fake.calls_after_login()
        assert find[:2] == ("GET", f"{BASE}/api/v1/dashboard/")
        assert find[3] == {
            "q": (
                "(filters:!((col:dashboard_title,opr:eq,"
                f"value:'{spec.dashboard_title}')),page_size:100)"
            )
        }
        assert create == (
            "POST",
            f"{BASE}/api/v1/dashboard/",
            {"dashboard_title": spec.dashboard_title, "slug": spec.dashboard_slug},
            None,
        )
        method, url, payload, params = update
        assert (method, url, params) == ("PUT", f"{BASE}/api/v1/dashboard/10", None)
        assert payload["dashboard_title"] == spec.dashboard_title
        assert payload["slug"] == spec.dashboard_slug
        assert payload["published"] is True
        assert json.loads(payload["position_json"]) == position
        metadata = json.loads(payload["json_metadata"])
        assert set(metadata) == EXPECTED_JSON_METADATA_KEYS
        assert metadata["native_filter_configuration"] == filters
        assert metadata["cross_filters_enabled"] is True
        assert metadata["refresh_frequency"] == 0
        assert metadata["color_scheme"] == ""

    def test_updates_existing_dashboard_without_creating(self, script, fake, spot):
        fake.seed("dashboard", id=8, dashboard_title="Spot Price Forecast Analysis")
        client = make_client(script, fake)

        assert script.upsert_dashboard(client, spot, {"k": 1}, []) == 8

        methods = [(c[0], c[1]) for c in fake.calls_after_login()]
        assert methods == [
            ("GET", f"{BASE}/api/v1/dashboard/"),
            ("PUT", f"{BASE}/api/v1/dashboard/8"),
        ]
        assert fake.rows["dashboard"][8]["slug"] == "spot-price-forecast-analysis"
        assert fake.rows["dashboard"][8]["published"] is True
        assert len(fake.rows["dashboard"]) == 1


class TestAttachCharts:
    def test_puts_the_dashboard_link_on_every_chart(self, script, fake):
        fake.seed("chart", id=11, slice_name="a")
        fake.seed("chart", id=12, slice_name="b")
        client = make_client(script, fake)

        script.attach_charts(client, 30, [11, 12])

        assert fake.calls_after_login() == [
            ("PUT", f"{BASE}/api/v1/chart/11", {"dashboards": [30]}, None),
            ("PUT", f"{BASE}/api/v1/chart/12", {"dashboards": [30]}, None),
        ]
        assert fake.rows["chart"][11]["dashboards"] == [30]
        assert fake.rows["chart"][12]["dashboards"] == [30]

    def test_no_charts_no_calls(self, script, fake):
        client = make_client(script, fake)
        script.attach_charts(client, 30, [])
        assert fake.calls_after_login() == []


# --------------------------------------------------------------------------- build_dashboard / main
COMMON_CHART_NAMES_HEAD = [
    "Overall MAE",
    "Bias (mean error)",
    "RMSE",
    "RMSE / MAE",
    "WAPE",
    "P90 abs error",
    "MAE by year",
    "MAE by time code",
    "MAE by year and time code",
    "MAE by year and month",
    "MAE by day of week",
    "MAE by day part",
    "MAE by day type",
]
COMMON_CHART_NAMES_TAIL = [
    "Error distribution",
    "Run leaderboard",
    "Worst days",
    "Forecast vs actual (30-min detail)",
]
EXPECTED_SPOT_CHART_NAMES = (
    COMMON_CHART_NAMES_HEAD
    + ["MAE by actual price band", "Calibration: forecast vs actual price level"]
    + COMMON_CHART_NAMES_TAIL
)
EXPECTED_DEMAND_CHART_NAMES = (
    COMMON_CHART_NAMES_HEAD
    + ["MAE by actual demand band", "Calibration: forecast vs actual demand level"]
    + COMMON_CHART_NAMES_TAIL
)
EXPECTED_GRID_CHILDREN = [
    "ROW-0-0",
    "HEADER-1",
    "ROW-1-0",
    "ROW-1-1",
    "ROW-1-2",
    "ROW-1-3",
    "HEADER-2",
    "ROW-2-0",
    "ROW-2-1",
    "HEADER-3",
    "ROW-3-0",
    "ROW-3-1",
    "ROW-3-2",
]


def bind_fake_session(script, fake: FakeSupersetSession, monkeypatch) -> None:
    """Make ``main`` build the *real* client on top of ``fake`` instead of a live session."""
    real_client_cls = script.SupersetClient
    monkeypatch.setattr(
        script,
        "SupersetClient",
        lambda url, user, password: real_client_cls(url, user, password, session=fake),
    )


def run_main(script, fake: FakeSupersetSession, monkeypatch, argv: list[str] | None = None):
    bind_fake_session(script, fake, monkeypatch)
    script.main(argv)


def method_counts(calls: list[tuple], resource: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for method, url, _, _ in calls:
        if urlsplit(url).path.startswith(f"/api/v1/{resource}/"):
            counts[method] = counts.get(method, 0) + 1
    return counts


def charts_of(fake: FakeSupersetSession, dataset_id: int) -> list[dict]:
    return [c for c in fake.rows["chart"].values() if c["datasource_id"] == dataset_id]


class TestBuildDashboard:
    @pytest.fixture
    def superset(self) -> FakeSupersetSession:
        fake = FakeSupersetSession()
        fake.seed("database", id=3, database_name="Spark Thriftserver")
        return fake

    def test_builds_dataset_charts_and_dashboard(self, script, superset, demand):
        client = make_client(script, superset)

        dashboard_id = script.build_dashboard(client, 3, demand)

        # dataset
        (dataset,) = superset.rows["dataset"].values()
        assert dataset["id"] == 10
        assert dataset["table_name"] == "demand_forecast_analysis"
        assert dataset["database"] == 3
        assert dataset["sql"] == DEMAND_DATASET_SQL
        assert dataset["main_dttm_col"] == "trade_datetime"
        assert [(c["column_name"], c["type"], c["is_dttm"]) for c in dataset["columns"]] == (
            DEMAND_COLUMNS
        )
        dataset_puts = [
            c for c in superset.calls if c[0] == "PUT" and c[1] == f"{BASE}/api/v1/dataset/10"
        ]
        assert [c[3] for c in dataset_puts] == [{"override_columns": "true"}]

        # charts, in creation order
        charts = list(superset.rows["chart"].values())
        assert [c["slice_name"] for c in charts] == EXPECTED_DEMAND_CHART_NAMES
        assert [c["id"] for c in charts] == list(range(11, 30))
        for c in charts:
            assert c["datasource_id"] == 10
            assert c["datasource_type"] == "table"
            params = json.loads(c["params"])
            assert params["datasource"] == "10__table"
            assert params["viz_type"] == c["viz_type"]
        by_name = {c["slice_name"]: json.loads(c["params"]) for c in charts}
        assert by_name["Overall MAE"]["viz_type"] == "big_number_total"
        assert by_name["Overall MAE"]["metric"] == demand.mae_metric
        assert by_name["Overall MAE"]["subheader"] == "kWh"
        assert by_name["Overall MAE"]["y_axis_format"] == ".4s"
        assert by_name["Bias (mean error)"]["subheader"] == "kWh; + = over-forecast"
        assert by_name["Bias (mean error)"]["y_axis_format"] == "+.4s"
        assert by_name["RMSE"]["y_axis_format"] == ".4s"
        assert by_name["RMSE / MAE"]["subheader"] == ">1.3 = spike-heavy errors"
        assert by_name["RMSE / MAE"]["y_axis_format"] == ",.2f"
        assert by_name["WAPE"]["subheader"] == "Σ|error| / Σ actual"
        assert by_name["WAPE"]["y_axis_format"] == ".1%"
        assert by_name["P90 abs error"]["subheader"] == "kWh"
        assert by_name["P90 abs error"]["metric"] == demand.p90_metric
        assert by_name["MAE by time code"]["x_axis"] == "time_code"
        assert by_name["MAE by year and month"]["x_axis"] == "month"
        assert by_name["MAE by year and time code"]["x_axis"] == "time_code"
        assert by_name["MAE by actual demand band"]["x_axis"] == "actual_demand_band"
        assert (
            by_name["Calibration: forecast vs actual demand level"]["x_axis"]
            == "actual_demand_round_kwh"
        )
        assert by_name["Error distribution"]["column"] == "error_kwh"
        assert by_name["Run leaderboard"]["viz_type"] == "table"
        assert by_name["Forecast vs actual (30-min detail)"]["viz_type"] == (
            "echarts_timeseries_line"
        )

        # dashboard
        (dashboard,) = superset.rows["dashboard"].values()
        assert dashboard_id == dashboard["id"] == 30
        assert dashboard["dashboard_title"] == "Demand Forecast Analysis"
        assert dashboard["slug"] == "demand-forecast-analysis"
        assert dashboard["published"] is True
        metadata = json.loads(dashboard["json_metadata"])
        assert set(metadata) == EXPECTED_JSON_METADATA_KEYS
        (run_filter,) = metadata["native_filter_configuration"]
        assert run_filter["targets"] == [{"column": {"name": "run_label"}, "datasetId": 10}]
        leaderboard_id = superset.id_of("chart", "slice_name", "Run leaderboard")
        assert run_filter["scope"]["excluded"] == [leaderboard_id]
        assert run_filter["defaultDataMask"]["filterState"]["value"] == [DEFAULT_LABEL]
        assert run_filter["controlValues"]["defaultToFirstItem"] is False

        position = json.loads(dashboard["position_json"])
        assert position["HEADER_ID"]["meta"]["text"] == "Demand Forecast Analysis"
        chart_keys = sorted(k for k in position if k.startswith("CHART-"))
        assert chart_keys == sorted(f"CHART-{i}" for i in range(11, 30))
        assert position["GRID_ID"]["children"] == EXPECTED_GRID_CHILDREN
        assert [position[h]["meta"]["text"] for h in ("HEADER-1", "HEADER-2", "HEADER-3")] == [
            "Error structure",
            "Calibration & distribution",
            "Runs & drilldown",
        ]
        assert position["ROW-0-0"]["children"] == [f"CHART-{i}" for i in range(11, 17)]
        assert position["CHART-11"]["meta"] == {
            "chartId": 11,
            "width": 2,
            "height": 24,
            "sliceName": "Overall MAE",
        }
        assert position["ROW-2-0"]["children"] == ["CHART-24", "CHART-25"]
        assert position["CHART-24"]["meta"]["sliceName"] == "MAE by actual demand band"
        assert position["CHART-24"]["meta"]["width"] == 5
        assert position["CHART-25"]["meta"]["width"] == 7
        assert position["ROW-3-0"]["children"] == [f"CHART-{leaderboard_id}"]
        assert position["CHART-29"]["meta"]["height"] == 60  # 30-min detail

        # every chart is linked to the dashboard
        assert all(c["dashboards"] == [30] for c in charts)
        assert method_counts(superset.calls, "dataset") == {"GET": 1, "POST": 1, "PUT": 1}
        assert method_counts(superset.calls, "chart") == {"GET": 19, "POST": 19, "PUT": 19}
        assert method_counts(superset.calls, "dashboard") == {"GET": 1, "POST": 1, "PUT": 1}

    def test_spot_price_dashboard_keeps_its_names_layout_and_formats(self, script, superset, spot):
        client = make_client(script, superset)

        script.build_dashboard(client, 3, spot)

        (dataset,) = superset.rows["dataset"].values()
        assert dataset["table_name"] == "spot_price_forecast_analysis"
        assert dataset["sql"] == SPOT_DATASET_SQL
        charts = list(superset.rows["chart"].values())
        assert [c["slice_name"] for c in charts] == EXPECTED_SPOT_CHART_NAMES
        by_name = {c["slice_name"]: json.loads(c["params"]) for c in charts}
        assert by_name["Overall MAE"]["subheader"] == "JPY/kWh"
        assert by_name["Overall MAE"]["y_axis_format"] == ",.3f"
        assert by_name["Bias (mean error)"]["subheader"] == "JPY/kWh; + = over-forecast"
        assert by_name["Bias (mean error)"]["y_axis_format"] == "+,.3f"
        assert by_name["WAPE"]["y_axis_format"] == ".1%"
        assert by_name["MAE by actual price band"]["x_axis"] == "actual_price_band"
        assert by_name["MAE by year"]["y_axis_format"] == ",.2f"
        assert by_name["Error distribution"]["column"] == "error_jpy_kwh"
        (dashboard,) = superset.rows["dashboard"].values()
        assert dashboard["dashboard_title"] == "Spot Price Forecast Analysis"
        assert dashboard["slug"] == "spot-price-forecast-analysis"
        position = json.loads(dashboard["position_json"])
        assert position["HEADER_ID"]["meta"]["text"] == "Spot Price Forecast Analysis"
        assert position["GRID_ID"]["children"] == EXPECTED_GRID_CHILDREN
        assert position["ROW-3-0"]["children"] == ["CHART-27"]  # Run leaderboard
        (run_filter,) = json.loads(dashboard["json_metadata"])["native_filter_configuration"]
        assert run_filter["scope"]["excluded"] == [27]

    def test_two_dashboards_coexist_with_their_own_datasets_and_charts(
        self, script, superset, spot, demand
    ):
        client = make_client(script, superset)

        spot_id = script.build_dashboard(client, 3, spot)
        demand_id = script.build_dashboard(client, 3, demand)

        assert (spot_id, demand_id) == (30, 51)  # 10 + 19 charts + dashboard, twice
        spot_ds = superset.id_of("dataset", "table_name", "spot_price_forecast_analysis")
        demand_ds = superset.id_of("dataset", "table_name", "demand_forecast_analysis")
        assert (spot_ds, demand_ds) == (10, 31)
        assert len(superset.rows["chart"]) == 38
        assert [c["slice_name"] for c in charts_of(superset, spot_ds)] == EXPECTED_SPOT_CHART_NAMES
        assert [c["slice_name"] for c in charts_of(superset, demand_ds)] == (
            EXPECTED_DEMAND_CHART_NAMES
        )
        assert all(c["dashboards"] == [spot_id] for c in charts_of(superset, spot_ds))
        assert all(c["dashboards"] == [demand_id] for c in charts_of(superset, demand_ds))
        for dashboard_id, dataset_id in ((spot_id, spot_ds), (demand_id, demand_ds)):
            metadata = json.loads(superset.rows["dashboard"][dashboard_id]["json_metadata"])
            (run_filter,) = metadata["native_filter_configuration"]
            assert run_filter["targets"][0]["datasetId"] == dataset_id
            position = json.loads(superset.rows["dashboard"][dashboard_id]["position_json"])
            chart_ids = sorted(int(k[6:]) for k in position if k.startswith("CHART-"))
            assert chart_ids == sorted(c["id"] for c in charts_of(superset, dataset_id))

    def test_second_build_is_idempotent_and_rewrites_metadata(self, script, superset, demand):
        client = make_client(script, superset)
        script.build_dashboard(client, 3, demand)
        first_ids = {r: sorted(rows) for r, rows in superset.rows.items()}
        superset.calls.clear()
        # The mart is now "empty": the filter must fall back to defaultToFirstItem.
        superset.overrides[("POST", "/api/v1/sqllab/execute/")] = FakeResponse({"data": []})

        script.build_dashboard(client, 3, demand)

        assert {r: sorted(rows) for r, rows in superset.rows.items()} == first_ids
        assert method_counts(superset.calls, "dataset") == {"GET": 1, "PUT": 1}
        assert method_counts(superset.calls, "chart") == {"GET": 19, "PUT": 38}
        assert method_counts(superset.calls, "dashboard") == {"GET": 1, "PUT": 1}
        (dashboard,) = superset.rows["dashboard"].values()
        (run_filter,) = json.loads(dashboard["json_metadata"])["native_filter_configuration"]
        assert run_filter["defaultDataMask"] == {"extraFormData": {}, "filterState": {}}
        assert run_filter["controlValues"]["defaultToFirstItem"] is True
        assert run_filter["scope"]["excluded"] == [27]
        assert all(c["dashboards"] == [30] for c in superset.rows["chart"].values())


class TestMain:
    @pytest.fixture
    def superset(self) -> FakeSupersetSession:
        fake = FakeSupersetSession()
        fake.seed("database", id=3, database_name="Spark Thriftserver")
        return fake

    def test_builds_every_dashboard_by_default(self, script, superset, monkeypatch):
        run_main(
            script, superset, monkeypatch, ["--url", BASE, "--user", "admin", "--password", "s"]
        )

        assert [d["dashboard_title"] for d in superset.rows["dashboard"].values()] == [
            "Spot Price Forecast Analysis",
            "Demand Forecast Analysis",
        ]
        assert [d["table_name"] for d in superset.rows["dataset"].values()] == [
            "spot_price_forecast_analysis",
            "demand_forecast_analysis",
        ]
        assert len(superset.rows["chart"]) == 38
        assert method_counts(superset.calls, "database") == {"GET": 1}
        assert method_counts(superset.calls, "chart") == {"GET": 38, "POST": 38, "PUT": 38}

    def test_task_flag_selects_one_dashboard(self, script, superset, monkeypatch):
        run_main(script, superset, monkeypatch, ["--url", BASE, "--task", "demand"])

        assert [d["dashboard_title"] for d in superset.rows["dashboard"].values()] == [
            "Demand Forecast Analysis"
        ]
        assert [d["table_name"] for d in superset.rows["dataset"].values()] == [
            "demand_forecast_analysis"
        ]
        assert [c["slice_name"] for c in superset.rows["chart"].values()] == (
            EXPECTED_DEMAND_CHART_NAMES
        )

    def test_task_flag_is_repeatable_and_ordered(self, script, superset, monkeypatch):
        run_main(
            script,
            superset,
            monkeypatch,
            ["--url", BASE, "--task", "demand", "--task", "spot_price"],
        )
        assert [d["dashboard_title"] for d in superset.rows["dashboard"].values()] == [
            "Demand Forecast Analysis",
            "Spot Price Forecast Analysis",
        ]

    def test_unknown_task_is_rejected_before_any_request(self, script, superset, monkeypatch):
        with pytest.raises(SystemExit) as exc:
            run_main(script, superset, monkeypatch, ["--url", BASE, "--task", "weather"])
        assert exc.value.code == 2
        assert superset.calls == []

    def test_exits_when_database_connection_is_missing(self, script, fake, monkeypatch):
        with pytest.raises(SystemExit, match="Database connection 'Spark Thriftserver' not found"):
            run_main(script, fake, monkeypatch, ["--url", BASE])
        assert fake.rows["dataset"] == {}
        assert fake.rows["chart"] == {}
        assert fake.rows["dashboard"] == {}
        assert [c[:2] for c in fake.calls_after_login()] == [("GET", f"{BASE}/api/v1/database/")]

    def test_env_derived_defaults(self, superset, monkeypatch):
        monkeypatch.setenv("SUPERSET_URL", "http://env-superset:9999/")
        monkeypatch.setenv("SUPERSET_ADMIN_USER", "envuser")
        monkeypatch.setenv("SUPERSET_ADMIN_PASSWORD", "envpass")
        script = import_script("create_forecast_dashboard")
        assert (script.SUPERSET_URL, script.ADMIN_USER, script.ADMIN_PASSWORD) == (
            "http://env-superset:9999/",
            "envuser",
            "envpass",
        )

        run_main(script, superset, monkeypatch, [])

        assert superset.calls[0] == (
            "POST",
            "http://env-superset:9999/api/v1/security/login",
            {"username": "envuser", "password": "envpass", "provider": "db", "refresh": True},
            None,
        )
        assert superset.headers["Referer"] == "http://env-superset:9999"
        assert len(superset.rows["chart"]) == 38

    def test_builtin_defaults_when_env_is_unset(self, monkeypatch):
        for var in ("SUPERSET_URL", "SUPERSET_ADMIN_USER", "SUPERSET_ADMIN_PASSWORD"):
            monkeypatch.delenv(var, raising=False)
        script = import_script("create_forecast_dashboard")
        assert script.SUPERSET_URL == "http://superset:8088"
        assert script.ADMIN_USER == "admin"
        assert script.ADMIN_PASSWORD == "admin"

    def test_cli_arguments_override_env(self, superset, monkeypatch):
        monkeypatch.setenv("SUPERSET_URL", "http://env-superset:9999")
        monkeypatch.setenv("SUPERSET_ADMIN_USER", "envuser")
        monkeypatch.setenv("SUPERSET_ADMIN_PASSWORD", "envpass")
        script = import_script("create_forecast_dashboard")

        run_main(
            script,
            superset,
            monkeypatch,
            ["--url", "http://cli:1", "--user", "cu", "--password", "cp", "--task", "spot_price"],
        )

        assert superset.calls[0] == (
            "POST",
            "http://cli:1/api/v1/security/login",
            {"username": "cu", "password": "cp", "provider": "db", "refresh": True},
            None,
        )
        assert superset.headers["Referer"] == "http://cli:1"
        assert len(superset.rows["chart"]) == 19
