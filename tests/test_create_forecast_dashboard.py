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
DEFAULT_LABEL = "2026-08-18 09:00 | tokyo | lightgbm | abcdef12"
DEFAULT_LAST_DAY = "2026-08-17"
DEFAULT_RUN_ID = "abcdef1234567890abcdef1234567890"
BASELINE_RUN_ID = "0123456789abcdef0123456789abcdef"
SHORT_RUN_ID = "ffffffff00000000ffffffff00000000"
BASELINE_LABEL = "2026-08-17 09:00 | tokyo | lightgbm | 01234567"
SHORT_LABEL = "2026-08-17 18:00 | tokyo | lightgbm | ffffffff"
# What the fake SQL Lab returns for the runs query: newest first; the second
# row is a short test window, the third the newest run with the same window.
RUN_ROWS = [
    {
        "run_id": DEFAULT_RUN_ID,
        "run_label": DEFAULT_LABEL,
        "area_code": "tokyo",
        "first_day": "2024-08-18",
        "last_day": DEFAULT_LAST_DAY,
        "periods": 34954,
    },
    {
        "run_id": SHORT_RUN_ID,
        "run_label": SHORT_LABEL,
        "area_code": "tokyo",
        "first_day": "2026-07-19",
        "last_day": DEFAULT_LAST_DAY,
        "periods": 1440,
    },
    {
        "run_id": BASELINE_RUN_ID,
        "run_label": BASELINE_LABEL,
        "area_code": "tokyo",
        "first_day": "2024-08-18",
        "last_day": DEFAULT_LAST_DAY,
        "periods": 34954,
    },
]

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
    def __init__(self, payload: dict, status: int = 200, headers: dict[str, str] | None = None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}

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
    ``rate_limited`` maps ``(method, path)`` to a queue of canned 429
    ``FakeResponse``s consumed one per request, so the request after the
    queue drains proceeds normally.
    """

    RESOURCES = ("database", "dataset", "chart", "dashboard")

    def __init__(self, *, sqllab: FakeResponse | None = None):
        self.headers: dict[str, str] = {}
        self.calls: list[tuple] = []
        self.rows: dict[str, dict[int, dict]] = {r: {} for r in self.RESOURCES}
        self.overrides: dict[tuple[str, str], FakeResponse] = {}
        self.rate_limited: dict[tuple[str, str], list[FakeResponse]] = {}
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
        queue = self.rate_limited.get((method, path))
        if queue:
            return queue.pop(0)
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
            return FakeResponse({"data": RUN_ROWS})
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

    def test_retries_a_rate_limited_request_after_retry_after(self, script, fake, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr(script.time, "sleep", sleeps.append)
        fake.seed("chart", id=41, slice_name="a")
        fake.rate_limited[("PUT", "/api/v1/chart/41")] = [
            FakeResponse({"message": "429"}, 429, headers={"Retry-After": "2"}),
            FakeResponse({"message": "429"}, 429),
        ]
        client = make_client(script, fake)

        result = client._put_json("/api/v1/chart/41", {"dashboards": [3]})

        assert result["id"] == 41
        assert fake.rows["chart"][41]["dashboards"] == [3]
        assert sleeps == [2.0, 1.0]  # Retry-After honoured, then the 1 s default
        puts = [c for c in fake.calls_after_login() if c[0] == "PUT"]
        assert len(puts) == 3  # two 429s, then the write

    def test_gives_up_after_the_retry_budget(self, script, fake, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr(script.time, "sleep", sleeps.append)
        fake.seed("chart", id=41, slice_name="a")
        fake.rate_limited[("GET", "/api/v1/chart/")] = [
            FakeResponse({"message": "429"}, 429) for _ in range(script.RATE_LIMIT_RETRIES + 1)
        ]
        client = make_client(script, fake)

        with pytest.raises(requests.HTTPError, match="429"):
            client.find_one("chart", slice_name="a")

        assert sleeps == [1.0] * script.RATE_LIMIT_RETRIES
        gets = [c for c in fake.calls_after_login() if c[0] == "GET"]
        assert len(gets) == script.RATE_LIMIT_RETRIES + 1

    def test_post_and_get_also_retry(self, script, fake, monkeypatch):
        monkeypatch.setattr(script.time, "sleep", lambda s: None)
        fake.rate_limited[("POST", "/api/v1/chart/")] = [FakeResponse({}, 429)]
        client = make_client(script, fake)
        created = client._post_json("/api/v1/chart/", {"slice_name": "b"})
        assert fake.rows["chart"][created["id"]]["slice_name"] == "b"
        fake.rate_limited[("GET", "/api/v1/chart/")] = [FakeResponse({}, 429)]
        assert client.find_one("chart", slice_name="b") == created["id"]

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
    ' | ', f.strategy,
    ' | ', substring(f.run_id, 1, 8)
  ) as run_label,
  concat(
    date_format(f.published_at, 'yyyy-MM-dd HH:mm'),
    ' | ', a.area_code,
    ' | ', f.strategy,
    ' | ', substring(f.run_id, 1, 8)
  ) as baseline_run_label,
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
    ' | ', f.strategy,
    ' | ', substring(f.run_id, 1, 8)
  ) as run_label,
  concat(
    date_format(f.published_at, 'yyyy-MM-dd HH:mm'),
    ' | ', a.area_code,
    ' | ', f.strategy,
    ' | ', substring(f.run_id, 1, 8)
  ) as baseline_run_label,
  f.strategy,
  f.published_at,
  f.forecast_issued_ts,
  f.horizon_hours,
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
  f.abs_error_kwh / 1000 as abs_error_mwh,
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
    ("baseline_run_label", "STRING", False),
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
    ("forecast_demand_mwh", "DOUBLE", False),
    ("actual_demand_mwh", "DOUBLE", False),
    ("actual_demand_round_mwh", "INT", False),
    ("actual_demand_band", "STRING", False),
    ("error_mwh", "DOUBLE", False),
    ("abs_error_mwh", "DOUBLE", False),
    ("pct_error", "DOUBLE", False),
    ("abs_pct_error", "DOUBLE", False),
]

EXPLANATION_SQL_HEAD = """\
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
    ' | ', c.strategy,
    ' | ', substring(c.run_id, 1, 8)
  ) as run_label,
  c.strategy,
  c.published_at,
  c.component,
  c.component_order,
  concat(lpad(cast(c.component_order as string), 2, '0'), ' ', c.component) as component_label,
  c.is_base,
  c.feature_value,
"""


def explanation_sql_tail(contribution_table: str, accuracy_table: str) -> str:
    return f"""\
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


SPOT_EXPLANATION_SQL = (
    EXPLANATION_SQL_HEAD
    + """\
  c.contribution_price_jpy_kwh,
  f.forecast_price_jpy_kwh,
  f.actual_price_jpy_kwh
"""
    + explanation_sql_tail(
        "pma_curated.fct_spot_price_forecast_contribution",
        "pma_curated.fct_spot_price_forecast_accuracy",
    )
)
DEMAND_EXPLANATION_SQL = (
    EXPLANATION_SQL_HEAD
    + """\
  c.contribution_demand_kwh / 1000 as contribution_mwh,
  f.forecast_demand_kwh / 1000 as forecast_demand_mwh,
  f.actual_demand_kwh / 1000 as actual_demand_mwh
"""
    + explanation_sql_tail(
        "pma_curated.fct_demand_forecast_contribution",
        "pma_curated.fct_demand_forecast_accuracy",
    )
)
EXPLANATION_COLUMNS_HEAD = [
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
]
SPOT_EXPLANATION_COLUMNS = EXPLANATION_COLUMNS_HEAD + [
    ("contribution_price_jpy_kwh", "DOUBLE", False),
    ("forecast_price_jpy_kwh", "DOUBLE", False),
    ("actual_price_jpy_kwh", "DOUBLE", False),
]
DEMAND_EXPLANATION_COLUMNS = EXPLANATION_COLUMNS_HEAD + [
    ("contribution_mwh", "DOUBLE", False),
    ("forecast_demand_mwh", "DOUBLE", False),
    ("actual_demand_mwh", "DOUBLE", False),
]

IMPORTANCE_SQL_HEAD = """\
select
  a.area_code,
  a.area_name_en,
  i.run_id,
  concat(
    date_format(i.published_at, 'yyyy-MM-dd HH:mm'),
    ' | ', a.area_code,
    ' | ', i.strategy,
    ' | ', substring(i.run_id, 1, 8)
  ) as run_label,
  i.strategy,
  i.published_at,
  i.feature,
  i.feature_order,
  concat(lpad(cast(i.feature_order as string), 2, '0'), ' ', i.feature) as feature_label,
  i.repeat_index,
  i.n_periods,
"""


def importance_sql_tail(importance_table: str) -> str:
    return f"""\
from {importance_table} i
join pma_curated.dim_area a on i.area_key = a.area_key
"""


SPOT_IMPORTANCE_SQL = (
    IMPORTANCE_SQL_HEAD
    + """\
  i.mae_price_jpy_kwh,
  i.permuted_mae_price_jpy_kwh
"""
    + importance_sql_tail("pma_curated.fct_spot_price_forecast_importance")
)
DEMAND_IMPORTANCE_SQL = (
    IMPORTANCE_SQL_HEAD
    + """\
  i.mae_demand_kwh / 1000 as mae_mwh,
  i.permuted_mae_demand_kwh / 1000 as permuted_mae_mwh
"""
    + importance_sql_tail("pma_curated.fct_demand_forecast_importance")
)
IMPORTANCE_COLUMNS_HEAD = [
    ("area_code", "STRING", False),
    ("area_name_en", "STRING", False),
    ("run_id", "STRING", False),
    ("run_label", "STRING", False),
    ("strategy", "STRING", False),
    ("published_at", "TIMESTAMP", True),
    ("feature", "STRING", False),
    ("feature_order", "INT", False),
    ("feature_label", "STRING", False),
    ("repeat_index", "INT", False),
    ("n_periods", "INT", False),
]
SPOT_IMPORTANCE_COLUMNS = IMPORTANCE_COLUMNS_HEAD + [
    ("mae_price_jpy_kwh", "DOUBLE", False),
    ("permuted_mae_price_jpy_kwh", "DOUBLE", False),
]
DEMAND_IMPORTANCE_COLUMNS = IMPORTANCE_COLUMNS_HEAD + [
    ("mae_mwh", "DOUBLE", False),
    ("permuted_mae_mwh", "DOUBLE", False),
]
IMPORTANCE_CHART_NAMES = [
    "Permutation importance",
    "Mean |SHAP| by feature",
    "Feature importance table",
]

COMPARISON_SQL_HEAD = """\
{{% set candidate = filter_values('run_label') %}}
{{% set baseline = filter_values('baseline_run_label') %}}
with runs as (
select
  f.*,
  a.area_code,
  a.area_name_en,
  concat(
    date_format(f.published_at, 'yyyy-MM-dd HH:mm'),
    ' | ', a.area_code,
    ' | ', f.strategy,
    ' | ', substring(f.run_id, 1, 8)
  ) as run_label
from {accuracy_table} f
join pma_curated.dim_area a on f.area_key = a.area_key
),
candidate as (
select *, count(*) over () as candidate_periods
from runs
where {{% if candidate %}}run_label = '{{{{ candidate[0] | replace("'", "''") }}}}'{{% else %}}1 = 0{{% endif %}}
),
baseline as (
select *
from runs
where {{% if baseline %}}run_label = '{{{{ baseline[0] | replace("'", "''") }}}}'{{% else %}}1 = 0{{% endif %}}
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
"""
COMPARISON_SQL_TAIL = """\
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
  d.day_name,
  concat(d.day_of_week_iso, ' ', substring(d.day_name, 1, 3)) as day_of_week,
  case
    when d.is_holiday then 'Holiday'
    when d.is_weekend then 'Weekend'
    else 'Weekday'
  end as day_type,
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
{value_select}
  avg(m.{abs}) over (partition by m.date_key) as daily_{abs},
  avg(m.baseline_{abs}) over (partition by m.date_key) as daily_baseline_{abs},
  avg(m.delta_{abs}) over (partition by m.date_key) as daily_delta_{abs},
  row_number() over (partition by m.date_key order by m.time_code) = 1 as is_first_matched_period
from matched m
join pma_curated.dim_delivery_period p on m.time_code = p.time_code
join pma_curated.dim_date d on m.date_key = d.date_key
"""
SPOT_COMPARISON_VALUES = """\
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
  c.abs_error_jpy_kwh - b.abs_error_jpy_kwh as delta_abs_error_jpy_kwh
"""
DEMAND_COMPARISON_VALUES = """\
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
  (c.abs_error_kwh - b.abs_error_kwh) / 1000 as delta_abs_error_mwh
"""
SPOT_COMPARISON_VALUE_NAMES = [
    "forecast_price_jpy_kwh",
    "baseline_forecast_price_jpy_kwh",
    "actual_price_jpy_kwh",
    "actual_price_band",
    "error_jpy_kwh",
    "baseline_error_jpy_kwh",
    "abs_error_jpy_kwh",
    "baseline_abs_error_jpy_kwh",
    "delta_abs_error_jpy_kwh",
]
DEMAND_COMPARISON_VALUE_NAMES = [
    "forecast_demand_mwh",
    "baseline_forecast_demand_mwh",
    "actual_demand_mwh",
    "actual_demand_band",
    "error_mwh",
    "baseline_error_mwh",
    "abs_error_mwh",
    "baseline_abs_error_mwh",
    "delta_abs_error_mwh",
]


def comparison_sql(accuracy_table: str, values: str, names: list[str], abs_col: str) -> str:
    return (
        COMPARISON_SQL_HEAD.format(accuracy_table=accuracy_table)
        + values
        + COMPARISON_SQL_TAIL.format(
            value_select="\n".join(f"  m.{name}," for name in names), abs=abs_col
        )
    )


SPOT_COMPARISON_SQL = comparison_sql(
    "pma_curated.fct_spot_price_forecast_accuracy",
    SPOT_COMPARISON_VALUES,
    SPOT_COMPARISON_VALUE_NAMES,
    "abs_error_jpy_kwh",
)
DEMAND_COMPARISON_SQL = comparison_sql(
    "pma_curated.fct_demand_forecast_accuracy",
    DEMAND_COMPARISON_VALUES,
    DEMAND_COMPARISON_VALUE_NAMES,
    "abs_error_mwh",
)
COMPARISON_COLUMNS_HEAD = [
    ("date_key", "DATE", True),
    ("trade_datetime", "TIMESTAMP", True),
    ("year", "BIGINT", False),
    ("month", "BIGINT", False),
    ("time_code", "INT", False),
    ("hour_of_day", "INT", False),
    ("day_part", "STRING", False),
    ("day_name", "STRING", False),
    ("day_of_week", "STRING", False),
    ("day_type", "STRING", False),
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
]
SPOT_COMPARISON_COLUMNS = (
    COMPARISON_COLUMNS_HEAD
    + [
        (n, "STRING" if n == "actual_price_band" else "DOUBLE", False)
        for n in SPOT_COMPARISON_VALUE_NAMES
    ]
    + [
        ("daily_abs_error_jpy_kwh", "DOUBLE", False),
        ("daily_baseline_abs_error_jpy_kwh", "DOUBLE", False),
        ("daily_delta_abs_error_jpy_kwh", "DOUBLE", False),
        ("is_first_matched_period", "BOOLEAN", False),
    ]
)
DEMAND_COMPARISON_COLUMNS = (
    COMPARISON_COLUMNS_HEAD
    + [
        (n, "STRING" if n == "actual_demand_band" else "DOUBLE", False)
        for n in DEMAND_COMPARISON_VALUE_NAMES
    ]
    + [
        ("daily_abs_error_mwh", "DOUBLE", False),
        ("daily_baseline_abs_error_mwh", "DOUBLE", False),
        ("daily_delta_abs_error_mwh", "DOUBLE", False),
        ("is_first_matched_period", "BOOLEAN", False),
    ]
)
# The explanation-comparison dataset: the contribution fact self-joined on the
# periods both runs explained, one row per period x component of either run.
# A str.format template like COMPARISON_SQL_HEAD (doubled braces = Jinja).
EXPLANATION_COMPARISON_SQL_HEAD = """\
{{% set candidate = filter_values('run_label') %}}
{{% set baseline = filter_values('baseline_run_label') %}}
with runs as (
select
  c.*,
  concat(
    date_format(c.published_at, 'yyyy-MM-dd HH:mm'),
    ' | ', a.area_code,
    ' | ', c.strategy,
    ' | ', substring(c.run_id, 1, 8)
  ) as run_label
from {contribution_table} c
join pma_curated.dim_area a on c.area_key = a.area_key
),
candidate as (
select *
from runs
where {{% if candidate %}}run_label = '{{{{ candidate[0] | replace("'", "''") }}}}'{{% else %}}1 = 0{{% endif %}}
),
baseline as (
select *
from runs
where {{% if baseline %}}run_label = '{{{{ baseline[0] | replace("'", "''") }}}}'{{% else %}}1 = 0{{% endif %}}
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
"""
EXPLANATION_COMPARISON_SQL_TAIL = """\
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
left join {accuracy_table} fc
  on fc.run_id = p.run_id
  and fc.date_key = p.date_key
  and fc.time_code = p.time_code
  and fc.area_key = p.area_key
left join {accuracy_table} fb
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
  {{% if candidate %}}'{{{{ candidate[0] | replace("'", "''") }}}}'{{% else %}}cast(null as string){{% endif %}} as run_label,
  {{% if baseline %}}'{{{{ baseline[0] | replace("'", "''") }}}}'{{% else %}}cast(null as string){{% endif %}} as baseline_run_label,
  m.component,
  m.component_order,
  concat(lpad(cast(m.component_order as string), 3, '0'), ' ', m.component) as component_label,
  m.is_base,
  m.feature_value,
  m.baseline_feature_value,
{value_select}
from matched m
join pma_curated.dim_area a on m.area_key = a.area_key
join pma_curated.dim_delivery_period p on m.time_code = p.time_code
join pma_curated.dim_date d on m.date_key = d.date_key
"""
SPOT_EXPLANATION_COMPARISON_VALUES = """\
  coalesce(c.contribution_price_jpy_kwh, 0) as contribution_price_jpy_kwh,
  coalesce(b.contribution_price_jpy_kwh, 0) as baseline_contribution_price_jpy_kwh,
  coalesce(c.contribution_price_jpy_kwh, 0) - coalesce(b.contribution_price_jpy_kwh, 0) as delta_contribution_price_jpy_kwh,
  fc.forecast_price_jpy_kwh,
  fb.forecast_price_jpy_kwh as baseline_forecast_price_jpy_kwh,
  fc.actual_price_jpy_kwh
"""
DEMAND_EXPLANATION_COMPARISON_VALUES = """\
  coalesce(c.contribution_demand_kwh, 0) / 1000 as contribution_mwh,
  coalesce(b.contribution_demand_kwh, 0) / 1000 as baseline_contribution_mwh,
  (coalesce(c.contribution_demand_kwh, 0) - coalesce(b.contribution_demand_kwh, 0)) / 1000 as delta_contribution_mwh,
  fc.forecast_demand_kwh / 1000 as forecast_demand_mwh,
  fb.forecast_demand_kwh / 1000 as baseline_forecast_demand_mwh,
  fc.actual_demand_kwh / 1000 as actual_demand_mwh
"""
SPOT_EXPLANATION_COMPARISON_VALUE_NAMES = [
    "contribution_price_jpy_kwh",
    "baseline_contribution_price_jpy_kwh",
    "delta_contribution_price_jpy_kwh",
    "forecast_price_jpy_kwh",
    "baseline_forecast_price_jpy_kwh",
    "actual_price_jpy_kwh",
]
DEMAND_EXPLANATION_COMPARISON_VALUE_NAMES = [
    "contribution_mwh",
    "baseline_contribution_mwh",
    "delta_contribution_mwh",
    "forecast_demand_mwh",
    "baseline_forecast_demand_mwh",
    "actual_demand_mwh",
]


def explanation_comparison_sql(
    contribution_table: str, accuracy_table: str, values: str, names: list[str]
) -> str:
    return (
        EXPLANATION_COMPARISON_SQL_HEAD.format(contribution_table=contribution_table)
        + values
        + EXPLANATION_COMPARISON_SQL_TAIL.format(
            accuracy_table=accuracy_table,
            value_select=",\n".join(f"  m.{name}" for name in names),
        )
    )


SPOT_EXPLANATION_COMPARISON_SQL = explanation_comparison_sql(
    "pma_curated.fct_spot_price_forecast_contribution",
    "pma_curated.fct_spot_price_forecast_accuracy",
    SPOT_EXPLANATION_COMPARISON_VALUES,
    SPOT_EXPLANATION_COMPARISON_VALUE_NAMES,
)
DEMAND_EXPLANATION_COMPARISON_SQL = explanation_comparison_sql(
    "pma_curated.fct_demand_forecast_contribution",
    "pma_curated.fct_demand_forecast_accuracy",
    DEMAND_EXPLANATION_COMPARISON_VALUES,
    DEMAND_EXPLANATION_COMPARISON_VALUE_NAMES,
)
EXPLANATION_COMPARISON_COLUMNS_HEAD = [
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
    ("component_order", "INT", False),
    ("component_label", "STRING", False),
    ("is_base", "BOOLEAN", False),
    ("feature_value", "DOUBLE", False),
    ("baseline_feature_value", "DOUBLE", False),
]
SPOT_EXPLANATION_COMPARISON_COLUMNS = EXPLANATION_COMPARISON_COLUMNS_HEAD + [
    (n, "DOUBLE", False) for n in SPOT_EXPLANATION_COMPARISON_VALUE_NAMES
]
DEMAND_EXPLANATION_COMPARISON_COLUMNS = EXPLANATION_COMPARISON_COLUMNS_HEAD + [
    (n, "DOUBLE", False) for n in DEMAND_EXPLANATION_COMPARISON_VALUE_NAMES
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
        assert demand.unit == "MWh"
        assert (demand.forecast_col, demand.actual_col) == (
            "forecast_demand_mwh",
            "actual_demand_mwh",
        )
        assert (demand.error_col, demand.abs_error_col) == ("error_mwh", "abs_error_mwh")
        assert demand.band_col == "actual_demand_band"
        assert demand.band_chart_title == "MAE by actual demand band"
        assert demand.calibration_x_col == "actual_demand_round_mwh"
        assert demand.calibration_chart_title == "Calibration: forecast vs actual demand level"
        assert demand.calibration_x_title == "Actual demand (MWh, rounded to 1,000 MWh)"
        assert demand.number_format == ",.1f"
        assert demand.signed_number_format == "+,.1f"
        assert demand.axis_format == ",.0f"
        assert demand.calibration_x_format == ",.0f"
        assert demand.calibration_y_format == ",.0f"
        assert demand.worst_days_max_format == ",.0f"

    def test_spot_price_dataset_sql_is_unchanged(self, spot):
        assert spot.dataset_sql == SPOT_DATASET_SQL

    def test_demand_dataset_sql(self, demand):
        assert demand.dataset_sql == DEMAND_DATASET_SQL

    def test_run_label_has_one_definition(self, script, spec):
        label = script.RUN_LABEL_SQL.format(f="f", a="a")
        assert label == (
            "concat(\n"
            "    date_format(f.published_at, 'yyyy-MM-dd HH:mm'),\n"
            "    ' | ', a.area_code,\n"
            "    ' | ', f.strategy,\n"
            "    ' | ', substring(f.run_id, 1, 8)\n"
            "  )"
        )
        assert spec.dataset_sql.count(f"  {label} as run_label,\n") == 1
        assert spec.dataset_sql.count(f"  {label} as baseline_run_label,\n") == 1
        assert f"  {script.RUN_LABEL_SQL.format(f='c', a='a')} as run_label,\n" in (
            spec.explanation_dataset_sql
        )

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
        assert demand.mae_metric == script.avg_metric("abs_error_mwh", "MAE (MWh)")
        assert spot.bias_metric == script.avg_metric("error_jpy_kwh", "Bias")
        assert demand.bias_metric["column"]["column_name"] == "error_mwh"
        assert spot.rmse_metric["sqlExpression"] == "sqrt(avg(power(error_jpy_kwh, 2)))"
        assert demand.rmse_metric["sqlExpression"] == "sqrt(avg(power(error_mwh, 2)))"
        assert spot.rmse_metric["optionName"] == "metric_rmse"
        assert spot.rmse_mae_metric["optionName"] == "metric_rmse_mae"
        assert (
            spot.rmse_mae_metric["sqlExpression"]
            == "sqrt(avg(power(error_jpy_kwh, 2))) / avg(abs_error_jpy_kwh)"
        )
        assert (
            demand.rmse_mae_metric["sqlExpression"]
            == "sqrt(avg(power(error_mwh, 2))) / avg(abs_error_mwh)"
        )
        assert spot.wape_metric["sqlExpression"] == (
            "sum(abs_error_jpy_kwh) / sum(actual_price_jpy_kwh)"
        )
        assert demand.wape_metric["sqlExpression"] == "sum(abs_error_mwh) / sum(actual_demand_mwh)"
        assert spot.p90_metric["sqlExpression"] == "percentile(abs_error_jpy_kwh, 0.90)"
        assert demand.p90_metric["sqlExpression"] == "percentile(abs_error_mwh, 0.90)"
        assert spot.p90_metric["optionName"] == "metric_p90_abs_error"

    def test_explanation_identity(self, spot, demand):
        assert spot.explanation_dataset_name == "spot_price_forecast_explanation"
        assert spot.contribution_table == "pma_curated.fct_spot_price_forecast_contribution"
        assert spot.contribution_col == "contribution_price_jpy_kwh"
        assert spot.contribution_format == "+,.3f"
        assert demand.explanation_dataset_name == "demand_forecast_explanation"
        assert demand.contribution_table == "pma_curated.fct_demand_forecast_contribution"
        assert demand.contribution_col == "contribution_mwh"
        assert demand.contribution_format == "+,.0f"

    def test_explanation_dataset_sql(self, spot, demand):
        assert spot.explanation_dataset_sql == SPOT_EXPLANATION_SQL
        assert demand.explanation_dataset_sql == DEMAND_EXPLANATION_SQL

    def test_explanation_columns_follow_the_sql(self, spot, demand):
        assert spot.explanation_dataset_columns == SPOT_EXPLANATION_COLUMNS
        assert demand.explanation_dataset_columns == DEMAND_EXPLANATION_COLUMNS

    def test_explanation_columns_match_the_sql_select_list_in_order(self, spec):
        select_list = spec.explanation_dataset_sql.split("\nfrom ", 1)[0].splitlines()[1:]
        output_names = []
        for line in select_list:
            if m := re.fullmatch(r"\s+[cfpda]\.(\w+),?", line):
                output_names.append(m.group(1))
            elif m := re.search(r"\bas (\w+),?$", line):
                output_names.append(m.group(1))
        assert [name for name, _, _ in spec.explanation_dataset_columns] == output_names
        assert [n for n, _, is_dttm in spec.explanation_dataset_columns if is_dttm] == [
            "date_key",
            "trade_datetime",
            "published_at",
        ]

    def test_explanation_metrics(self, script, spot, demand):
        assert spot.contribution_metric == script.avg_metric(
            "contribution_price_jpy_kwh", "Contribution (JPY/kWh)"
        )
        assert demand.contribution_metric == script.avg_metric(
            "contribution_mwh", "Contribution (MWh)"
        )
        assert spot.base_value_metric == script.sql_metric(
            "avg(case when is_base then contribution_price_jpy_kwh end)", "Base value"
        )
        assert demand.base_value_metric["sqlExpression"] == (
            "avg(case when is_base then contribution_mwh end)"
        )
        assert spot.net_effect_metric["sqlExpression"] == (
            "avg(forecast_price_jpy_kwh) - avg(case when is_base then contribution_price_jpy_kwh end)"
        )
        assert demand.net_effect_metric["sqlExpression"] == (
            "avg(forecast_demand_mwh) - avg(case when is_base then contribution_mwh end)"
        )
        assert demand.net_effect_metric["label"] == "Net feature effect"
        assert demand.net_effect_metric["optionName"] == "metric_net_feature_effect"

    def test_minus_base_metrics_read_the_base_row_of_the_same_period(self, script, spot, demand):
        # Same quantity as the net effect, labelled for the by-period chart's legend
        assert (
            spot.forecast_minus_base_metric["sqlExpression"]
            == (spot.net_effect_metric["sqlExpression"])
        )
        assert demand.forecast_minus_base_metric["label"] == "Forecast − base"
        assert spot.actual_minus_base_metric["sqlExpression"] == (
            "avg(actual_price_jpy_kwh) - avg(case when is_base then contribution_price_jpy_kwh end)"
        )
        assert demand.actual_minus_base_metric["sqlExpression"] == (
            "avg(actual_demand_mwh) - avg(case when is_base then contribution_mwh end)"
        )
        assert demand.actual_minus_base_metric["label"] == "Actual − base"
        assert demand.actual_minus_base_metric["expressionType"] == "SQL"
        assert (
            demand.forecast_minus_base_metric["optionName"]
            != demand.actual_minus_base_metric["optionName"]
        )

    def test_importance_identity(self, spot, demand):
        assert spot.importance_dataset_name == "spot_price_forecast_importance"
        assert spot.importance_table == "pma_curated.fct_spot_price_forecast_importance"
        assert (spot.importance_mae_col, spot.importance_permuted_mae_col) == (
            "mae_price_jpy_kwh",
            "permuted_mae_price_jpy_kwh",
        )
        assert demand.importance_dataset_name == "demand_forecast_importance"
        assert demand.importance_table == "pma_curated.fct_demand_forecast_importance"
        assert (demand.importance_mae_col, demand.importance_permuted_mae_col) == (
            "mae_mwh",
            "permuted_mae_mwh",
        )

    def test_importance_dataset_sql(self, spot, demand):
        assert spot.importance_dataset_sql == SPOT_IMPORTANCE_SQL
        assert demand.importance_dataset_sql == DEMAND_IMPORTANCE_SQL

    def test_importance_columns_follow_the_sql(self, spot, demand):
        assert spot.importance_dataset_columns == SPOT_IMPORTANCE_COLUMNS
        assert demand.importance_dataset_columns == DEMAND_IMPORTANCE_COLUMNS

    def test_importance_columns_match_the_sql_select_list_in_order(self, spec):
        select_list = spec.importance_dataset_sql.split("\nfrom ", 1)[0].splitlines()[1:]
        output_names = []
        for line in select_list:
            if m := re.fullmatch(r"\s+[ia]\.(\w+),?", line):
                output_names.append(m.group(1))
            elif m := re.search(r"\bas (\w+),?$", line):
                output_names.append(m.group(1))
        assert [name for name, _, _ in spec.importance_dataset_columns] == output_names
        assert [n for n, _, is_dttm in spec.importance_dataset_columns if is_dttm] == [
            "published_at"
        ]

    def test_importance_metrics(self, script, spot, demand):
        assert demand.importance_mae_metric == script.avg_metric("mae_mwh", "MAE (MWh)")
        assert demand.permuted_mae_metric == script.avg_metric(
            "permuted_mae_mwh", "Permuted MAE (MWh)"
        )
        assert demand.importance_delta_sql == "avg(permuted_mae_mwh) - avg(mae_mwh)"
        assert demand.importance_metric == script.sql_metric(
            "avg(permuted_mae_mwh) - avg(mae_mwh)", "ΔMAE (MWh)", option_name="importance_delta_mae"
        )
        # try_divide: a zero MAE yields null rather than an ANSI DIVIDE_BY_ZERO error
        assert demand.importance_pct_metric == script.sql_metric(
            "100 * try_divide(avg(permuted_mae_mwh) - avg(mae_mwh), avg(mae_mwh))",
            "Importance %",
            option_name="importance_pct",
        )
        assert demand.importance_std_metric == script.sql_metric(
            "stddev_pop(permuted_mae_mwh)", "Std over repeats (MWh)", option_name="importance_std"
        )
        assert spot.importance_metric["sqlExpression"] == (
            "avg(permuted_mae_price_jpy_kwh) - avg(mae_price_jpy_kwh)"
        )
        assert spot.importance_metric["label"] == "ΔMAE (JPY/kWh)"
        # on the explanation dataset: the attribution counterpart of the importance bars
        assert spot.mean_abs_shap_metric == script.sql_metric(
            "avg(abs(contribution_price_jpy_kwh))",
            "Mean |SHAP| (JPY/kWh)",
            option_name="mean_abs_shap",
        )
        assert demand.mean_abs_shap_metric["sqlExpression"] == "avg(abs(contribution_mwh))"

    def test_comparison_identity(self, spot, demand):
        assert spot.comparison_dataset_name == "spot_price_forecast_comparison"
        assert demand.comparison_dataset_name == "demand_forecast_comparison"
        assert spot.baseline_forecast_col == "baseline_forecast_price_jpy_kwh"
        assert spot.baseline_error_col == "baseline_error_jpy_kwh"
        assert spot.baseline_abs_error_col == "baseline_abs_error_jpy_kwh"
        assert spot.delta_abs_error_col == "delta_abs_error_jpy_kwh"
        assert spot.daily_abs_error_col == "daily_abs_error_jpy_kwh"
        assert spot.daily_baseline_abs_error_col == "daily_baseline_abs_error_jpy_kwh"
        assert spot.daily_delta_abs_error_col == "daily_delta_abs_error_jpy_kwh"
        assert demand.baseline_abs_error_col == "baseline_abs_error_mwh"
        assert demand.delta_abs_error_col == "delta_abs_error_mwh"
        assert demand.daily_delta_abs_error_col == "daily_delta_abs_error_mwh"

    def test_comparison_dataset_sql(self, spot, demand):
        assert spot.comparison_dataset_sql == SPOT_COMPARISON_SQL
        assert demand.comparison_dataset_sql == DEMAND_COMPARISON_SQL

    def test_comparison_columns_follow_the_sql(self, spot, demand):
        assert spot.comparison_dataset_columns == SPOT_COMPARISON_COLUMNS
        assert demand.comparison_dataset_columns == DEMAND_COMPARISON_COLUMNS

    def test_comparison_columns_match_the_final_select_list_in_order(self, spec):
        final_select = spec.comparison_dataset_sql.rsplit("\nselect\n", 1)[1]
        select_list = final_select.split("\nfrom matched m", 1)[0].splitlines()
        output_names = []
        for line in select_list:
            if m := re.fullmatch(r"\s+[mpd]\.(\w+),?", line):
                output_names.append(m.group(1))
            elif m := re.search(r"\bas (\w+),?$", line):
                output_names.append(m.group(1))
        assert [name for name, _, _ in spec.comparison_dataset_columns] == output_names
        assert [n for n, _, is_dttm in spec.comparison_dataset_columns if is_dttm] == [
            "date_key",
            "trade_datetime",
            "published_at",
        ]

    def test_comparison_value_columns_carry_the_derived_names(self, spec):
        names = [name for name, _, _ in spec.comparison_value_columns]
        assert names == [
            spec.forecast_col,
            spec.baseline_forecast_col,
            spec.actual_col,
            spec.band_col,
            spec.error_col,
            spec.baseline_error_col,
            spec.abs_error_col,
            spec.baseline_abs_error_col,
            spec.delta_abs_error_col,
        ]
        for name in names:
            # every column is either `<expr> as <name>` or the candidate's own `c.<name>`
            assert re.search(
                rf"(\bas {name}|^  c\.{name}),?$", spec.comparison_value_columns_sql, re.M
            ), name

    def test_comparison_sql_pins_both_runs_inside_the_dataset(self, spec):
        sql = spec.comparison_dataset_sql
        assert sql.startswith("{% set candidate = filter_values('run_label') %}\n")
        assert "{% set baseline = filter_values('baseline_run_label') %}" in sql
        assert sql.count("{% else %}1 = 0{% endif %}") == 2
        assert "count(*) over () as candidate_periods" in sql
        assert "join baseline b\n  on b.date_key = c.date_key" in sql

    def test_comparison_band_expression_matches_the_analysis_dataset(self, spec):
        # The Compare tab's bands must be the Accuracy tab's bands: the same
        # case expression, on the candidate alias instead of the fact alias.
        def band_block(sql: str, alias: str) -> str:
            start = sql.index("  case\n")
            end = sql.index(f" as {spec.band_col}", start) + len(f" as {spec.band_col}")
            return sql[start:end].replace(f"{alias}.", "@.")

        assert band_block(spec.comparison_value_columns_sql, "c") == band_block(
            spec.value_columns_sql, "f"
        )

    def test_comparison_metrics(self, script, spot, demand):
        a, b = "abs_error_mwh", "baseline_abs_error_mwh"
        assert demand.delta_mae_sql == f"avg({a}) - avg({b})"
        assert demand.delta_mae_pct_sql == f"100 * (avg({a}) - avg({b})) / avg({b})"
        assert demand.baseline_mae_metric == script.avg_metric(b, "Baseline MAE (MWh)")
        assert demand.candidate_mae_metric == script.avg_metric(a, "Candidate MAE (MWh)")
        assert demand.delta_mae_metric == script.sql_metric(
            demand.delta_mae_sql, "ΔMAE", option_name="delta_mae"
        )
        assert demand.delta_mae_pct_metric == script.sql_metric(
            demand.delta_mae_pct_sql, "ΔMAE %", option_name="delta_mae_pct"
        )
        assert demand.delta_abs_bias_metric == script.sql_metric(
            "abs(avg(error_mwh)) - abs(avg(baseline_error_mwh))",
            "Δ|bias|",
            option_name="delta_abs_bias",
        )
        assert demand.delta_wape_metric == script.sql_metric(
            f"sum({a}) / sum(actual_demand_mwh) - sum({b}) / sum(actual_demand_mwh)",
            "ΔWAPE",
            option_name="delta_wape",
        )
        assert demand.matched_coverage_metric == script.sql_metric(
            "count(*) / max(candidate_periods)", "Matched coverage"
        )
        assert demand.matched_days_metric == script.sql_metric(
            "count(distinct date_key)", "Matched days"
        )
        assert demand.days_candidate_lower_metric == script.sql_metric(
            "count(distinct case when daily_delta_abs_error_mwh < 0 then date_key end)"
            " / count(distinct date_key)",
            "Days candidate lower",
        )
        assert demand.median_daily_delta_metric == script.sql_metric(
            "percentile(case when is_first_matched_period then daily_delta_abs_error_mwh end, 0.5)",
            "Median daily ΔMAE",
            option_name="median_daily_delta_mae",
        )
        assert demand.error_reduction_metric == script.sql_metric(
            f"sum({b}) - sum({a})", "Error reduction"
        )
        assert demand.better_worse_metrics("x") == [
            script.sql_metric("least(x, 0)", "Better"),
            script.sql_metric("greatest(x, 0)", "Worse"),
        ]
        assert spot.baseline_mae_metric["column"]["column_name"] == "baseline_abs_error_jpy_kwh"
        assert spot.baseline_mae_metric["label"] == "Baseline MAE (JPY/kWh)"
        assert spot.delta_wape_metric["sqlExpression"] == (
            "sum(abs_error_jpy_kwh) / sum(actual_price_jpy_kwh)"
            " - sum(baseline_abs_error_jpy_kwh) / sum(actual_price_jpy_kwh)"
        )
        assert spot.days_candidate_lower_metric["sqlExpression"].startswith(
            "count(distinct case when daily_delta_abs_error_jpy_kwh < 0"
        )

    def test_delta_band_chart_title(self, spot, demand):
        assert spot.delta_band_chart_title == "ΔMAE % by actual price band"
        assert demand.delta_band_chart_title == "ΔMAE % by actual demand band"

    def test_explanation_comparison_identity(self, spot, demand):
        assert spot.explanation_comparison_dataset_name == (
            "spot_price_forecast_explanation_comparison"
        )
        assert demand.explanation_comparison_dataset_name == (
            "demand_forecast_explanation_comparison"
        )
        assert spot.baseline_contribution_col == "baseline_contribution_price_jpy_kwh"
        assert spot.delta_contribution_col == "delta_contribution_price_jpy_kwh"
        assert demand.baseline_contribution_col == "baseline_contribution_mwh"
        assert demand.delta_contribution_col == "delta_contribution_mwh"

    def test_explanation_comparison_dataset_sql(self, spot, demand):
        assert spot.explanation_comparison_dataset_sql == SPOT_EXPLANATION_COMPARISON_SQL
        assert demand.explanation_comparison_dataset_sql == DEMAND_EXPLANATION_COMPARISON_SQL

    def test_explanation_comparison_columns_follow_the_sql(self, spot, demand):
        assert spot.explanation_comparison_dataset_columns == SPOT_EXPLANATION_COMPARISON_COLUMNS
        assert demand.explanation_comparison_dataset_columns == (
            DEMAND_EXPLANATION_COMPARISON_COLUMNS
        )

    def test_explanation_comparison_columns_match_the_final_select_list_in_order(self, spec):
        final_select = spec.explanation_comparison_dataset_sql.rsplit("\nselect\n", 1)[1]
        select_list = final_select.split("\nfrom matched m", 1)[0].splitlines()
        output_names = []
        for line in select_list:
            if m := re.fullmatch(r"\s+[mpda]\.(\w+),?", line):
                output_names.append(m.group(1))
            elif m := re.search(r"\bas (\w+),?$", line):
                output_names.append(m.group(1))
        assert [name for name, _, _ in spec.explanation_comparison_dataset_columns] == (
            output_names
        )
        assert [n for n, _, is_dttm in spec.explanation_comparison_dataset_columns if is_dttm] == [
            "date_key",
            "trade_datetime",
        ]

    def test_explanation_comparison_value_columns_carry_the_derived_names(self, spec):
        assert [name for name, _, _ in spec.explanation_comparison_value_columns] == [
            spec.contribution_col,
            spec.baseline_contribution_col,
            spec.delta_contribution_col,
            spec.forecast_col,
            spec.baseline_forecast_col,
            spec.actual_col,
        ]

    def test_explanation_comparison_sql_pins_both_runs_and_matches_periods(self, spec):
        sql = spec.explanation_comparison_dataset_sql
        assert sql.startswith("{% set candidate = filter_values('run_label') %}\n")
        assert sql.count("{% else %}1 = 0{% endif %}") == 2
        assert "where c.is_base and b.is_base" in sql  # one base row per explained period
        assert "cross join components k" in sql  # the union of both runs' components
        assert "coalesce(c.component_order, 100 + b.component_order)" in sql
        # the filters' values are echoed as constant columns so Superset's outer
        # WHERE on run_label / baseline_run_label keeps every row
        assert sql.count("{% else %}cast(null as string){% endif %} as") == 2

    def test_explanation_comparison_metrics(self, script, spot, demand):
        d = "delta_contribution_mwh"
        assert demand.delta_contribution_metric == script.avg_metric(d, "Δ contribution (MWh)")
        assert demand.baseline_contribution_metric == script.avg_metric(
            "baseline_contribution_mwh", "Baseline contribution (MWh)"
        )
        assert demand.candidate_contribution_metric == script.avg_metric(
            "contribution_mwh", "Candidate contribution (MWh)"
        )
        assert demand.delta_base_value_metric == script.sql_metric(
            f"avg(case when is_base then {d} end)", "Δ base value", option_name="delta_base_value"
        )
        assert demand.delta_net_effect_metric == script.sql_metric(
            f"sum(case when not is_base then {d} end) / count(distinct date_key, time_code)",
            "Δ net feature effect",
            option_name="delta_net_feature_effect",
        )
        assert demand.delta_forecast_metric == script.sql_metric(
            "avg(forecast_demand_mwh) - avg(baseline_forecast_demand_mwh)",
            "Δ forecast",
            option_name="delta_forecast",
        )
        assert spot.delta_contribution_metric["column"]["column_name"] == (
            "delta_contribution_price_jpy_kwh"
        )
        assert spot.delta_contribution_metric["label"] == "Δ contribution (JPY/kWh)"
        assert spot.delta_forecast_metric["sqlExpression"] == (
            "avg(forecast_price_jpy_kwh) - avg(baseline_forecast_price_jpy_kwh)"
        )


# --------------------------------------------------------------------------- dataset
class TestUpsertDataset:
    def test_creates_then_overrides_columns(self, script, fake, spec):
        client = make_client(script, fake)

        dataset_id = script.upsert_dataset(
            client, 3, spec.dataset_name, spec.dataset_sql, spec.dataset_columns
        )

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

    def test_main_dttm_col_can_be_overridden(self, script, fake, demand):
        client = make_client(script, fake)
        dataset_id = script.upsert_dataset(
            client,
            3,
            demand.importance_dataset_name,
            demand.importance_dataset_sql,
            demand.importance_dataset_columns,
            main_dttm_col="published_at",
        )
        assert fake.rows["dataset"][dataset_id]["main_dttm_col"] == "published_at"

    def test_updates_existing_dataset_without_creating(self, script, fake, spot):
        fake.seed("dataset", id=5, table_name="spot_price_forecast_analysis", database=3)
        client = make_client(script, fake)

        assert (
            script.upsert_dataset(
                client, 3, spot.dataset_name, spot.dataset_sql, spot.dataset_columns
            )
            == 5
        )

        methods = [(c[0], c[1]) for c in fake.calls_after_login()]
        assert methods == [("GET", f"{BASE}/api/v1/dataset/"), ("PUT", f"{BASE}/api/v1/dataset/5")]
        assert fake.rows["dataset"][5]["sql"] == SPOT_DATASET_SQL
        assert len(fake.rows["dataset"]) == 1

    def test_the_two_datasets_do_not_collide(self, script, fake, spot, demand):
        client = make_client(script, fake)
        assert (
            script.upsert_dataset(
                client, 3, spot.dataset_name, spot.dataset_sql, spot.dataset_columns
            )
            == 10
        )
        assert (
            script.upsert_dataset(
                client, 3, demand.dataset_name, demand.dataset_sql, demand.dataset_columns
            )
            == 11
        )
        assert (
            script.upsert_dataset(
                client, 3, spot.dataset_name, spot.dataset_sql, spot.dataset_columns
            )
            == 10
        )
        assert {r["table_name"] for r in fake.rows["dataset"].values()} == {
            "spot_price_forecast_analysis",
            "demand_forecast_analysis",
        }

    def test_explanation_dataset_is_a_second_dataset_on_the_same_database(
        self, script, fake, demand
    ):
        client = make_client(script, fake)
        analysis_id = script.upsert_dataset(
            client, 3, demand.dataset_name, demand.dataset_sql, demand.dataset_columns
        )
        explanation_id = script.upsert_dataset(
            client,
            3,
            demand.explanation_dataset_name,
            demand.explanation_dataset_sql,
            demand.explanation_dataset_columns,
        )
        assert (analysis_id, explanation_id) == (10, 11)
        row = fake.rows["dataset"][11]
        assert row["table_name"] == "demand_forecast_explanation"
        assert row["sql"] == DEMAND_EXPLANATION_SQL
        assert row["main_dttm_col"] == "trade_datetime"
        assert [(c["column_name"], c["type"], c["is_dttm"]) for c in row["columns"]] == (
            DEMAND_EXPLANATION_COLUMNS
        )


# --------------------------------------------------------------------------- run label
class TestRunDefaults:
    def test_newest_run_its_last_day_and_the_matched_window_baseline(self, script, fake, spec):
        client = make_client(script, fake)

        defaults = script.run_defaults(client, 3, spec)

        assert defaults == script.RunDefaults(DEFAULT_LABEL, DEFAULT_LAST_DAY, BASELINE_LABEL)
        (call,) = fake.calls_after_login()
        method, url, payload, params = call
        assert (method, url, params) == ("POST", f"{BASE}/api/v1/sqllab/execute/", None)
        assert payload["database_id"] == 3
        assert payload["runAsync"] is False
        assert f"from {spec.accuracy_table} f" in payload["sql"]
        assert f"  {script.RUN_LABEL_SQL.format(f='f', a='a')} as run_label," in payload["sql"]
        assert "date_format(min(f.date_key), 'yyyy-MM-dd') as first_day" in payload["sql"]
        assert "date_format(max(f.date_key), 'yyyy-MM-dd') as last_day" in payload["sql"]
        assert "count(*) as periods" in payload["sql"]
        assert "group by f.run_id, f.strategy, f.published_at, a.area_code" in payload["sql"]
        assert "order by f.published_at desc" in payload["sql"]
        assert "limit" not in payload["sql"]

    def test_baseline_run_override_matches_a_run_id_prefix(self, script, fake, spot):
        client = make_client(script, fake)
        assert script.run_defaults(client, 3, spot, baseline_run="ffff").baseline_run_label == (
            SHORT_LABEL
        )
        assert script.run_defaults(client, 3, spot, baseline_run=BASELINE_RUN_ID) == (
            script.RunDefaults(DEFAULT_LABEL, DEFAULT_LAST_DAY, BASELINE_LABEL)
        )

    def test_unknown_baseline_run_warns_and_keeps_the_rule(self, script, fake, spot, monkeypatch):
        warnings: list[tuple] = []
        monkeypatch.setattr(script.logger, "warning", lambda *args, **kwargs: warnings.append(args))
        client = make_client(script, fake)

        defaults = script.run_defaults(client, 3, spot, baseline_run="nope")

        assert defaults.baseline_run_label == BASELINE_LABEL
        assert len(warnings) == 1
        assert warnings[0][1] == "nope"

    def test_no_run_shares_the_newest_window(self, script, spot):
        fake = FakeSupersetSession(sqllab=FakeResponse({"data": RUN_ROWS[:2]}))
        defaults = script.run_defaults(make_client(script, fake), 3, spot)
        assert defaults == script.RunDefaults(DEFAULT_LABEL, DEFAULT_LAST_DAY, None)

    def test_none_on_http_error(self, script, spot):
        fake = FakeSupersetSession(sqllab=FakeResponse({"message": "boom"}, 500))
        assert script.run_defaults(make_client(script, fake), 3, spot) is None

    def test_none_when_mart_is_empty(self, script, spot):
        fake = FakeSupersetSession(sqllab=FakeResponse({"data": []}))
        assert script.run_defaults(make_client(script, fake), 3, spot) is None

    def test_none_when_response_has_no_data_key(self, script, spot):
        fake = FakeSupersetSession(sqllab=FakeResponse({"result": "no data here"}))
        assert script.run_defaults(make_client(script, fake), 3, spot) is None

    def test_none_when_a_row_lacks_a_column(self, script, spot):
        fake = FakeSupersetSession(sqllab=FakeResponse({"data": [{"run_label": DEFAULT_LABEL}]}))
        assert script.run_defaults(make_client(script, fake), 3, spot) is None


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

    def test_sql_metric_takes_an_explicit_option_name(self, script):
        m = script.sql_metric("avg(a) - avg(b)", "ΔMAE", option_name="delta_mae")
        assert m["optionName"] == "metric_delta_mae"
        assert m["label"] == "ΔMAE"


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
        assert script.bar_params(demand, 7, "year")["y_axis_format"] == ",.0f"
        assert script.bar_params(demand, 7, "year")["y_axis_title"] == "MWh"

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
        assert p["x_axis"] == "actual_demand_round_mwh"
        assert p["x_axis_sort"] == "actual_demand_round_mwh"
        assert [m["column"]["column_name"] for m in p["metrics"]] == [
            "forecast_demand_mwh",
            "actual_demand_mwh",
        ]
        assert p["x_axis_title"] == "Actual demand (MWh, rounded to 1,000 MWh)"
        assert p["x_axis_number_format"] == ",.0f"  # not the default ~g
        assert p["y_axis_title"] == "Forecast (MWh)"
        assert p["y_axis_format"] == ",.0f"

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
        assert [m["label"] for m in p["metrics"]] == [
            "Periods",
            "First day",
            "Last day",
            "Days",
            mae_label,
            "Bias",
            "RMSE",
            "WAPE",
        ]
        assert p["metrics"][0]["sqlExpression"] == "count(*)"
        assert p["metrics"][0]["optionName"] == "metric_periods"
        assert p["metrics"][1]["sqlExpression"] == "date_format(min(date_key), 'yyyy-MM-dd')"
        assert p["metrics"][2]["sqlExpression"] == "date_format(max(date_key), 'yyyy-MM-dd')"
        assert p["metrics"][3]["sqlExpression"] == "count(distinct date_key)"
        assert p["metrics"][4] == spec.mae_metric
        assert p["metrics"][6] == spec.rmse_metric
        assert p["metrics"][7] == spec.wape_metric
        assert p["timeseries_limit_metric"] == spec.mae_metric
        assert p["order_desc"] is False  # best MAE first
        assert p["row_limit"] == 100
        assert p["column_config"] == {
            "Periods": {"d3NumberFormat": ",d"},
            "Days": {"d3NumberFormat": ",d"},
            mae_label: {"d3NumberFormat": spec.number_format},
            "Bias": {"d3NumberFormat": spec.signed_number_format},
            "RMSE": {"d3NumberFormat": spec.number_format},
            "WAPE": {"d3NumberFormat": ".1%"},
        }

    def test_leaderboard_formats_per_spec(self, script, spot, demand):
        assert script.leaderboard_params(spot, 7)["column_config"]["Bias"] == {
            "d3NumberFormat": "+,.3f"
        }
        assert script.leaderboard_params(demand, 7)["column_config"]["MAE (MWh)"] == {
            "d3NumberFormat": ",.1f"
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
            "d3NumberFormat": ",.0f"
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

    def test_waterfall(self, script, spec):
        p = script.waterfall_params(spec, 7)
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "waterfall"
        assert p["x_axis"] == "component_label"
        assert p["groupby"] == []
        assert p["metric"] == spec.contribution_metric
        assert p["adhoc_filters"] == [script.NOT_BASE_FILTER]
        assert p["show_total"] is True
        assert p["total_label"] == "Net effect"
        assert p["increase_label"] == "Pushes forecast up"
        assert p["decrease_label"] == "Pushes forecast down"
        assert p["show_value"] is True
        assert p["show_legend"] is True
        assert p["x_ticks_layout"] == "auto"
        assert p["y_axis_format"] == spec.axis_format
        assert p["y_axis_label"] == spec.unit
        assert p["row_limit"] == 100
        assert p["extra_form_data"] == {}

    def test_not_base_filter_is_a_sql_where_clause(self, script):
        assert script.NOT_BASE_FILTER == {
            "expressionType": "SQL",
            "sqlExpression": "not is_base",
            "clause": "WHERE",
            "filterOptionName": "filter_not_is_base",
        }

    def test_feature_table(self, script, spec):
        p = script.feature_table_params(spec, 7)
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "table"
        assert p["query_mode"] == "aggregate"
        assert p["groupby"] == ["component_label"]
        order, value, contribution = p["metrics"]
        assert order == script.sql_metric("min(component_order)", "Order")
        assert value == script.sql_metric("avg(feature_value)", "Feature value")
        assert contribution == spec.contribution_metric
        assert p["timeseries_limit_metric"] == order
        assert p["order_desc"] is False
        assert p["adhoc_filters"] == []  # the base row stays: the column sums to the forecast
        assert p["column_config"] == {
            "Order": {"d3NumberFormat": ",d"},
            "Feature value": {"d3NumberFormat": ",.2~f"},
            f"Contribution ({spec.unit})": {"d3NumberFormat": spec.contribution_format},
        }

    def test_importance_bar_is_horizontal_sorted_by_the_delta(self, script, spec):
        p = script.importance_bar_params(spec, 7)
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "echarts_timeseries_bar"
        assert p["orientation"] == "horizontal"
        assert p["x_axis"] == "feature"
        assert p["metrics"] == [spec.importance_metric]
        # ascending on a horizontal bar puts the largest importance on top
        assert p["x_axis_sort"] == spec.importance_metric["label"]
        assert p["x_axis_sort_asc"] is True
        assert p["adhoc_filters"] == []
        assert p["y_axis_format"] == spec.axis_format
        assert p["y_axis_title"] == f"ΔMAE ({spec.unit}) when the feature is shuffled"
        assert p["show_legend"] is False
        assert p["extra_form_data"] == {}

    def test_mean_abs_shap_bar_reads_the_explanation_dataset_without_the_base(self, script, spec):
        p = script.mean_abs_shap_params(spec, 7)
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "echarts_timeseries_bar"
        assert p["orientation"] == "horizontal"
        assert p["x_axis"] == "component"
        assert p["metrics"] == [spec.mean_abs_shap_metric]
        assert p["x_axis_sort"] == spec.mean_abs_shap_metric["label"]
        assert p["adhoc_filters"] == [script.NOT_BASE_FILTER]
        assert p["y_axis_title"] == f"Mean |SHAP| ({spec.unit})"

    def test_importance_table(self, script, spec):
        p = script.importance_table_params(spec, 7)
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "table"
        assert p["query_mode"] == "aggregate"
        assert p["groupby"] == ["feature_label"]
        order, mae, permuted, delta, std, pct = p["metrics"]
        assert order == script.sql_metric("min(feature_order)", "Order")
        assert (mae, permuted, delta, std, pct) == (
            spec.importance_mae_metric,
            spec.permuted_mae_metric,
            spec.importance_metric,
            spec.importance_std_metric,
            spec.importance_pct_metric,
        )
        assert p["timeseries_limit_metric"] == order
        assert p["order_desc"] is False
        assert p["adhoc_filters"] == []
        assert p["column_config"] == {
            "Order": {"d3NumberFormat": ",d"},
            f"MAE ({spec.unit})": {"d3NumberFormat": spec.number_format},
            f"Permuted MAE ({spec.unit})": {"d3NumberFormat": spec.number_format},
            f"ΔMAE ({spec.unit})": {"d3NumberFormat": spec.signed_number_format},
            f"Std over repeats ({spec.unit})": {"d3NumberFormat": spec.number_format},
            "Importance %": {"d3NumberFormat": "+.1f"},
        }
        assert p["extra_form_data"] == {}

    def test_contribution_by_period(self, script, spec):
        p = script.contribution_by_period_params(spec, 7)
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "mixed_timeseries"
        assert p["x_axis"] == "time_code"
        assert p["time_grain_sqla"] is None
        # Query A: one stacked bar per feature (the base row filtered out)
        assert p["metrics"] == [spec.contribution_metric]
        assert p["groupby"] == ["component_label"]
        assert p["adhoc_filters"] == [script.NOT_BASE_FILTER]
        assert p["seriesType"] == "bar"
        assert p["stack"] is True
        assert p["yAxisIndex"] == 0
        assert p["order_desc"] is False
        assert p["row_limit"] == 10000
        # Query B: forecast and actual, both relative to the base, as lines on the same axis
        assert p["metrics_b"] == [spec.forecast_minus_base_metric, spec.actual_minus_base_metric]
        assert p["groupby_b"] == []
        assert p["adhoc_filters_b"] == []  # the base row stays: both metrics read it
        assert p["seriesTypeB"] == "line"
        assert p["stackB"] is False
        assert p["markerEnabledB"] is True
        assert p["yAxisIndexB"] == 0
        assert p["order_desc_b"] is False
        assert p["row_limit_b"] == 10000
        assert p["show_legend"] is True
        assert p["legendOrientation"] == "top"
        assert p["rich_tooltip"] is True
        assert p["y_axis_format"] == spec.axis_format
        assert p["y_axis_title"] == spec.unit
        assert p["truncateYAxis"] is False
        assert p["extra_form_data"] == {}

    def test_label_colors_name_the_roles(self, script):
        assert script.LABEL_COLORS == {
            "Candidate": "#1FA8C9",
            "Baseline": "#B2B2B2",
            "Actual": "#222222",
            "Better": "#1FA8C9",
            "Worse": "#FF7F44",
        }

    def test_delta_big_number_colours_the_value_by_sign(self, script, demand):
        p = script.delta_big_number_params(7, demand.delta_mae_metric, "MWh", "+,.1f")
        plain = script.big_number_params(7, demand.delta_mae_metric, "MWh", "+,.1f")
        assert {k: v for k, v in p.items() if k != "conditional_formatting"} == plain
        assert p["conditional_formatting"] == [
            {"colorScheme": "#1FA8C9", "column": "ΔMAE", "operator": "<", "targetValue": 0},
            {"colorScheme": "#FF7F44", "column": "ΔMAE", "operator": ">", "targetValue": 0},
        ]

    def test_delta_bar_is_a_stacked_better_worse_bar_of_mae_pct(self, script, spec):
        p = script.delta_bar_params(spec, 7, "day_part")
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "echarts_timeseries_bar"
        assert p["x_axis"] == "day_part"
        assert p["x_axis_sort"] == "day_part"
        assert p["x_axis_sort_asc"] is True
        assert p["time_grain_sqla"] is None
        assert p["metrics"] == spec.better_worse_metrics(spec.delta_mae_pct_sql)
        assert p["stack"] == "Stack"
        assert p["show_legend"] is True
        assert p["zoomable"] is False
        assert p["row_limit"] == 10000
        assert p["y_axis_format"] == "+,.1f"
        assert p["y_axis_title"] == "ΔMAE % vs baseline"
        assert p["truncateYAxis"] is False
        assert p["rich_tooltip"] is True

    def test_daily_delta_bar_is_the_unit_delta_by_day_and_zoomable(self, script, spec):
        p = script.daily_delta_bar_params(spec, 7)
        segment = script.delta_bar_params(spec, 7, "date_key")
        assert p["x_axis"] == "date_key"
        assert p["metrics"] == spec.better_worse_metrics(spec.delta_mae_sql)
        assert p["zoomable"] is True
        assert p["y_axis_format"] == "+" + spec.axis_format
        assert p["y_axis_title"] == f"Daily ΔMAE ({spec.unit}) vs baseline"
        for key in ("viz_type", "stack", "show_legend", "row_limit", "x_axis_sort_asc"):
            assert p[key] == segment[key]

    def test_delta_heatmap_is_diverging_with_symmetric_bounds(self, script, spec):
        p = script.delta_heatmap_params(spec, 7, "month")
        plain = script.heatmap_params(spec, 7, "month")
        assert p["metric"] == spec.delta_mae_pct_metric
        assert p["linear_color_scheme"] == "blue_white_yellow"
        assert p["value_bounds"] == [-30, 30]
        assert p["y_axis_format"] == "+,.1f"
        assert {
            k: v
            for k, v in p.items()
            if k not in ("metric", "linear_color_scheme", "value_bounds", "y_axis_format")
        } == {
            k: v
            for k, v in plain.items()
            if k not in ("metric", "linear_color_scheme", "value_bounds", "y_axis_format")
        }

    def test_cumulative_reduction_is_a_cumsum_line_by_day(self, script, spec):
        p = script.cumulative_reduction_params(spec, 7)
        plain = script.detail_params(spec, 7)
        assert p["viz_type"] == "echarts_timeseries_line"
        assert p["x_axis"] == "date_key"
        assert p["time_grain_sqla"] is None
        assert p["metrics"] == [spec.error_reduction_metric]
        assert p["rolling_type"] == "cumsum"
        assert p["zoomable"] is True
        assert p["show_legend"] is False
        assert p["row_limit"] == 10000
        assert p["y_axis_format"] == spec.axis_format
        assert p["y_axis_title"] == f"{spec.unit}; Σ (baseline |error| − candidate |error|)"
        for key in ("seriesType", "opacity", "markerEnabled", "time_range", "comparison_type"):
            assert p[key] == plain[key]

    @pytest.mark.parametrize("improved, order_desc", [(True, False), (False, True)])
    def test_ranked_days_tables(self, script, spec, improved, order_desc):
        p = script.ranked_days_params(spec, 7, improved=improved)
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "table"
        assert p["query_mode"] == "aggregate"
        assert p["groupby"] == ["date_key", "day_of_week", "day_type", "holiday_name_ja"]
        assert p["metrics"] == [
            spec.baseline_mae_metric,
            spec.candidate_mae_metric,
            spec.delta_mae_metric,
            spec.delta_mae_pct_metric,
        ]
        assert p["timeseries_limit_metric"] == spec.delta_mae_metric
        assert p["order_desc"] is order_desc
        assert p["row_limit"] == 10
        assert p["server_page_length"] == 10
        assert p["table_timestamp_format"] == "%Y-%m-%d"
        assert p["column_config"] == {
            f"Baseline MAE ({spec.unit})": {"d3NumberFormat": spec.number_format},
            f"Candidate MAE ({spec.unit})": {"d3NumberFormat": spec.number_format},
            "ΔMAE": {"d3NumberFormat": spec.signed_number_format},
            "ΔMAE %": {"d3NumberFormat": "+,.1f"},
        }

    def test_comparison_detail_has_three_lines(self, script, spec):
        p = script.comparison_detail_params(spec, 7)
        plain = script.detail_params(spec, 7)
        assert [m["label"] for m in p["metrics"]] == ["Actual", "Candidate", "Baseline"]
        assert [m["column"]["column_name"] for m in p["metrics"]] == [
            spec.actual_col,
            spec.forecast_col,
            spec.baseline_forecast_col,
        ]
        assert {k: v for k, v in p.items() if k != "metrics"} == {
            k: v for k, v in plain.items() if k != "metrics"
        }

    def test_delta_waterfall_overrides_the_explanation_waterfall(self, script, spec):
        p = script.delta_waterfall_params(spec, 7)
        plain = script.waterfall_params(spec, 7)
        assert p["metric"] == spec.delta_contribution_metric
        assert p["adhoc_filters"] == [script.NOT_BASE_FILTER]
        assert p["increase_label"] == "Higher than baseline"
        assert p["decrease_label"] == "Lower than baseline"
        assert p["total_label"] == "Δ net effect"
        assert p["x_axis_label"] == (
            "Component (candidate feature order; baseline-only features last)"
        )
        overridden = {"metric", "increase_label", "decrease_label", "total_label", "x_axis_label"}
        assert {k: v for k, v in p.items() if k not in overridden} == {
            k: v for k, v in plain.items() if k not in overridden
        }

    def test_delta_feature_table(self, script, spec):
        p = script.delta_feature_table_params(spec, 7)
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "table"
        assert p["query_mode"] == "aggregate"
        assert p["groupby"] == ["component_label"]
        order, baseline, candidate, delta = p["metrics"]
        assert order == script.sql_metric("min(component_order)", "Order")
        assert baseline == spec.baseline_contribution_metric
        assert candidate == spec.candidate_contribution_metric
        assert delta == spec.delta_contribution_metric
        assert p["timeseries_limit_metric"] == order
        assert p["order_desc"] is False
        assert p["adhoc_filters"] == []  # the base row stays: Δ base is a line of the table
        assert p["row_limit"] == 100
        assert p["column_config"] == {
            "Order": {"d3NumberFormat": ",d"},
            baseline["label"]: {"d3NumberFormat": spec.contribution_format},
            candidate["label"]: {"d3NumberFormat": spec.contribution_format},
            delta["label"]: {"d3NumberFormat": spec.contribution_format},
        }

    def test_every_builder_targets_the_dataset_and_starts_unfiltered(self, script, spec):
        builders = [
            lambda: script.delta_feature_table_params(spec, 12),
            lambda: script.big_number_params(12, spec.mae_metric, "x", ",.1f"),
            lambda: script.bar_params(spec, 12, "year"),
            lambda: script.heatmap_params(spec, 12, "time_code"),
            lambda: script.calibration_params(spec, 12),
            lambda: script.histogram_params(spec, 12),
            lambda: script.leaderboard_params(spec, 12),
            lambda: script.worst_days_params(spec, 12),
            lambda: script.detail_params(spec, 12),
            lambda: script.feature_table_params(spec, 12),
            lambda: script.importance_bar_params(spec, 12),
            lambda: script.importance_table_params(spec, 12),
            lambda: script.delta_big_number_params(12, spec.delta_mae_metric, "x", "+,.1f"),
            lambda: script.delta_bar_params(spec, 12, "day_part"),
            lambda: script.daily_delta_bar_params(spec, 12),
            lambda: script.delta_heatmap_params(spec, 12, "month"),
            lambda: script.cumulative_reduction_params(spec, 12),
            lambda: script.ranked_days_params(spec, 12, improved=True),
            lambda: script.comparison_detail_params(spec, 12),
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

    def test_update_resets_an_explore_saved_query_context(self, script, fake, spot):
        # A chart saved from Explore carries a query_context snapshot of the
        # dataset columns of its day; the chart-data API (thumbnails, alerts,
        # MCP) replays it, so a rebuild after a dataset SQL change must reset
        # it rather than leave it pointing at dropped columns.
        fake.seed(
            "chart",
            id=42,
            slice_name="MAE by year",
            datasource_id=10,
            query_context='{"queries": [{"columns": ["abs_error_kwh"]}]}',
        )
        client = make_client(script, fake)

        script.upsert_chart(client, "MAE by year", 10, script.bar_params(spot, 10, "year"))

        (update,) = [c for c in fake.calls_after_login() if c[0] == "PUT"]
        assert update[2]["query_context"] is None
        assert fake.rows["chart"][42]["query_context"] is None

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
    def test_tabs_of_headed_and_unheaded_sections(self, script, spot):
        tabs = [
            {
                "title": "Accuracy",
                "sections": [
                    {"header": None, "rows": [[(1, "A", 6, 24), (2, "B", 6, 24)]]},
                    {"header": "Sec", "rows": [[(3, "C", 12, 40)]]},
                ],
            },
            {
                "title": "Explanation (SHAP)",
                "sections": [{"header": None, "rows": [[(4, "D", 12, 30)]]}],
            },
        ]
        row_meta = {"background": "BACKGROUND_TRANSPARENT"}
        tab_meta = {"defaultText": "Tab title", "placeholder": "Tab title"}
        assert script.build_position_json(spot, tabs) == {
            "DASHBOARD_VERSION_KEY": "v2",
            "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["TABS-0"]},
            "TABS-0": {
                "type": "TABS",
                "id": "TABS-0",
                "children": ["TAB-0", "TAB-1"],
                "parents": ["ROOT_ID"],
                "meta": {},
            },
            "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
            "HEADER_ID": {
                "type": "HEADER",
                "id": "HEADER_ID",
                "meta": {"text": "Spot Price Forecast Analysis"},
            },
            "TAB-0": {
                "type": "TAB",
                "id": "TAB-0",
                "children": ["ROW-0-0-0", "HEADER-0-1", "ROW-0-1-0"],
                "parents": ["ROOT_ID", "TABS-0"],
                "meta": {"text": "Accuracy", **tab_meta},
            },
            "ROW-0-0-0": {
                "type": "ROW",
                "id": "ROW-0-0-0",
                "children": ["CHART-1", "CHART-2"],
                "parents": ["ROOT_ID", "TABS-0", "TAB-0"],
                "meta": row_meta,
            },
            "CHART-1": {
                "type": "CHART",
                "id": "CHART-1",
                "children": [],
                "parents": ["ROOT_ID", "TABS-0", "TAB-0", "ROW-0-0-0"],
                "meta": {"chartId": 1, "width": 6, "height": 24, "sliceName": "A"},
            },
            "CHART-2": {
                "type": "CHART",
                "id": "CHART-2",
                "children": [],
                "parents": ["ROOT_ID", "TABS-0", "TAB-0", "ROW-0-0-0"],
                "meta": {"chartId": 2, "width": 6, "height": 24, "sliceName": "B"},
            },
            "HEADER-0-1": {
                "type": "HEADER",
                "id": "HEADER-0-1",
                "children": [],
                "parents": ["ROOT_ID", "TABS-0", "TAB-0"],
                "meta": {
                    "text": "Sec",
                    "headerSize": "MEDIUM_HEADER",
                    "background": "BACKGROUND_TRANSPARENT",
                },
            },
            "ROW-0-1-0": {
                "type": "ROW",
                "id": "ROW-0-1-0",
                "children": ["CHART-3"],
                "parents": ["ROOT_ID", "TABS-0", "TAB-0"],
                "meta": row_meta,
            },
            "CHART-3": {
                "type": "CHART",
                "id": "CHART-3",
                "children": [],
                "parents": ["ROOT_ID", "TABS-0", "TAB-0", "ROW-0-1-0"],
                "meta": {"chartId": 3, "width": 12, "height": 40, "sliceName": "C"},
            },
            "TAB-1": {
                "type": "TAB",
                "id": "TAB-1",
                "children": ["ROW-1-0-0"],
                "parents": ["ROOT_ID", "TABS-0"],
                "meta": {"text": "Explanation (SHAP)", **tab_meta},
            },
            "ROW-1-0-0": {
                "type": "ROW",
                "id": "ROW-1-0-0",
                "children": ["CHART-4"],
                "parents": ["ROOT_ID", "TABS-0", "TAB-1"],
                "meta": row_meta,
            },
            "CHART-4": {
                "type": "CHART",
                "id": "CHART-4",
                "children": [],
                "parents": ["ROOT_ID", "TABS-0", "TAB-1", "ROW-1-0-0"],
                "meta": {"chartId": 4, "width": 12, "height": 30, "sliceName": "D"},
            },
        }

    def test_sections_and_rows_are_numbered_per_tab(self, script, spot):
        tabs = [
            {
                "title": "A",
                "sections": [{"header": "H", "rows": [[(1, "A", 12, 10)], [(2, "B", 12, 10)]]}],
            },
            {"title": "B", "sections": [{"header": "H2", "rows": [[(3, "C", 12, 10)]]}]},
        ]
        position = script.build_position_json(spot, tabs)
        assert position["TAB-0"]["children"] == ["HEADER-0-0", "ROW-0-0-0", "ROW-0-0-1"]
        assert position["ROW-0-0-1"]["children"] == ["CHART-2"]
        assert position["CHART-2"]["parents"] == ["ROOT_ID", "TABS-0", "TAB-0", "ROW-0-0-1"]
        assert position["TAB-1"]["children"] == ["HEADER-1-0", "ROW-1-0-0"]
        assert position["HEADER-1-0"]["meta"]["text"] == "H2"
        assert position["CHART-3"]["parents"] == ["ROOT_ID", "TABS-0", "TAB-1", "ROW-1-0-0"]

    def test_dashboard_header_carries_the_spec_title(self, script, demand):
        position = script.build_position_json(demand, [])
        assert position["HEADER_ID"]["meta"] == {"text": "Demand Forecast Analysis"}
        assert position["ROOT_ID"]["children"] == ["TABS-0"]
        assert position["TABS-0"]["children"] == []
        assert position["GRID_ID"]["children"] == []


# --------------------------------------------------------------------------- native filters
class TestBuildNativeFilters:
    def test_explicit_defaults_apply_on_load(self, script):
        run, day, baseline = script.build_native_filters(
            dataset_id=10,
            run_excluded=[27],
            default_run_label=DEFAULT_LABEL,
            explanation_dataset_id=11,
            day_excluded=[12, 13],
            default_day_label=DEFAULT_LAST_DAY,
            baseline_excluded=[12, 13, 14],
            default_baseline_label=BASELINE_LABEL,
        )
        assert [f["type"] for f in (run, day, baseline)] == ["NATIVE_FILTER"] * 3
        assert [f["filterType"] for f in (run, day, baseline)] == ["filter_select"] * 3

        assert run["id"] == "NATIVE_FILTER-run"
        assert run["name"] == "Run"
        assert run["targets"] == [{"column": {"name": "run_label"}, "datasetId": 10}]
        assert run["defaultDataMask"] == {
            "extraFormData": {
                "filters": [{"col": "run_label", "op": "IN", "val": [DEFAULT_LABEL]}]
            },
            "filterState": {"value": [DEFAULT_LABEL], "label": DEFAULT_LABEL},
        }
        assert run["controlValues"] == {
            "multiSelect": False,
            "enableEmptyFilter": True,
            "defaultToFirstItem": False,
            "inverseSelection": False,
            "searchAllOptions": False,
            "sortAscending": False,
        }
        assert run["scope"] == {"rootPath": ["ROOT_ID"], "excluded": [27]}
        assert run["cascadeParentIds"] == []

        assert day["id"] == "NATIVE_FILTER-day"
        assert day["name"] == "Day"
        assert day["targets"] == [{"column": {"name": "trade_date_label"}, "datasetId": 11}]
        assert day["defaultDataMask"] == {
            "extraFormData": {
                "filters": [{"col": "trade_date_label", "op": "IN", "val": [DEFAULT_LAST_DAY]}]
            },
            "filterState": {"value": [DEFAULT_LAST_DAY], "label": DEFAULT_LAST_DAY},
        }
        assert day["controlValues"] == {
            "multiSelect": False,
            "enableEmptyFilter": False,
            "defaultToFirstItem": False,
            "inverseSelection": False,
            "searchAllOptions": False,
            "sortAscending": False,
        }
        assert day["cascadeParentIds"] == ["NATIVE_FILTER-run"]
        assert day["scope"] == {"rootPath": ["ROOT_ID"], "excluded": [12, 13]}
        assert "Worst days" in day["description"]
        assert "Most improved days" in day["description"]

        assert baseline["id"] == "NATIVE_FILTER-baseline"
        assert baseline["name"] == "Baseline"
        assert baseline["targets"] == [{"column": {"name": "baseline_run_label"}, "datasetId": 10}]
        assert baseline["defaultDataMask"] == {
            "extraFormData": {
                "filters": [{"col": "baseline_run_label", "op": "IN", "val": [BASELINE_LABEL]}]
            },
            "filterState": {"value": [BASELINE_LABEL], "label": BASELINE_LABEL},
        }
        assert baseline["controlValues"] == {
            "multiSelect": False,
            "enableEmptyFilter": True,
            "defaultToFirstItem": False,
            "inverseSelection": False,
            "searchAllOptions": False,
            "sortAscending": False,
        }
        assert baseline["cascadeParentIds"] == []
        assert baseline["scope"] == {"rootPath": ["ROOT_ID"], "excluded": [12, 13, 14]}
        assert "Compare tab" in baseline["description"]

    def test_no_defaults_fall_back_to_first_item_except_baseline(self, script):
        run, day, baseline = script.build_native_filters(
            dataset_id=10,
            run_excluded=[],
            default_run_label=None,
            explanation_dataset_id=11,
            day_excluded=[],
            default_day_label=None,
            baseline_excluded=[],
            default_baseline_label=None,
        )
        assert run["defaultDataMask"] == {"extraFormData": {}, "filterState": {}}
        assert run["controlValues"]["defaultToFirstItem"] is True
        assert run["scope"] == {"rootPath": ["ROOT_ID"], "excluded": []}
        assert day["defaultDataMask"] == {"extraFormData": {}, "filterState": {}}
        assert day["controlValues"]["defaultToFirstItem"] is True
        # No baseline: the Compare tab stays on "No data" until one is picked
        assert baseline["defaultDataMask"] == {"extraFormData": {}, "filterState": {}}
        assert baseline["controlValues"]["defaultToFirstItem"] is False


# --------------------------------------------------------------------------- tab builders
def recording_factory():
    """A chart factory that hands out ids 101, 102, ... and records (name, dataset id)."""
    created: list[tuple[str, int]] = []

    def chart(name: str, params: dict, on: int) -> int:
        assert params["datasource"] == f"{on}__table"
        created.append((name, on))
        return 100 + len(created)

    return chart, created


def assert_sections_are_consistent(tab) -> None:
    """Every row spans the 12-column grid and names the tab's own charts."""
    for section in tab.sections:
        for row in section["rows"]:
            assert sum(width for _, _, width, _ in row) == 12
            for chart_id, name, _, _ in row:
                assert tab.charts[name] == chart_id


class TestTabBuilders:
    def test_dashboard_tab_exposes_ids_in_creation_order_and_its_layout(self, script):
        tab = script.DashboardTab("T", {"b": 2, "a": 1}, [{"header": None, "rows": []}])
        assert tab.chart_ids == [2, 1]
        assert tab.layout == {"title": "T", "sections": [{"header": None, "rows": []}]}

    def test_chart_adder_records_into_the_shared_dict(self, script):
        chart, created = recording_factory()
        charts: dict[str, int] = {}
        add = script._chart_adder(chart, charts, 7)
        assert add("x", {"datasource": "7__table"}) == 101
        assert add("y", {"datasource": "7__table"}) == 102
        assert charts == {"x": 101, "y": 102}
        assert created == [("x", 7), ("y", 7)]

    def test_accuracy_tab(self, script, demand):
        chart, created = recording_factory()
        tab = script.build_accuracy_tab(chart, demand, 10)
        assert tab.title == "Accuracy"
        assert list(tab.charts) == EXPECTED_DEMAND_CHART_NAMES
        assert tab.chart_ids == list(range(101, 120))
        assert {on for _, on in created} == {10}
        assert [s["header"] for s in tab.sections] == [
            None,
            "Error structure",
            "Calibration & distribution",
            "Runs & drilldown",
        ]
        assert_sections_are_consistent(tab)

    def test_explanation_tab(self, script, spot):
        chart, created = recording_factory()
        tab = script.build_explanation_tab(chart, spot, 11, 14)
        assert tab.title == "Explanation"
        assert list(tab.charts) == EXPLANATION_CHART_NAMES + IMPORTANCE_CHART_NAMES
        assert tab.chart_ids == list(range(101, 111))
        # the importance bars and table read the importance dataset; mean |SHAP| the explanation one
        assert [on for _, on in created] == [11] * 7 + [14, 11, 14]
        assert list(script.RUN_LEVEL_CHART_NAMES) == IMPORTANCE_CHART_NAMES
        assert [s["header"] for s in tab.sections] == [None, script.IMPORTANCE_SECTION_HEADER]
        assert_sections_are_consistent(tab)
        assert [[name for _, name, _, _ in row] for row in tab.sections[1]["rows"]] == [
            ["Permutation importance", "Mean |SHAP| by feature"],
            ["Feature importance table"],
        ]

    def test_compare_tab_spans_both_comparison_datasets(self, script, demand):
        chart, created = recording_factory()
        tab = script.build_compare_tab(chart, demand, 12, 13)
        assert tab.title == "Compare"
        assert list(tab.charts) == (
            DEMAND_COMPARISON_CHART_NAMES + EXPLANATION_VS_BASELINE_CHART_NAMES
        )
        assert tab.chart_ids == list(range(101, 129))
        assert [on for _, on in created] == [12] * 23 + [13] * 5
        assert list(script.EXPLANATION_VS_BASELINE_CHART_NAMES) == (
            EXPLANATION_VS_BASELINE_CHART_NAMES
        )
        assert [s["header"] for s in tab.sections] == [
            None,
            "Where the candidate wins (ΔMAE % vs baseline)",
            "Day by day",
            "Explanation vs baseline (SHAP; mean per period of the Day, or of the run when Day is"
            " empty)",
            "Detail",
        ]
        assert_sections_are_consistent(tab)
        day_by_day = tab.sections[2]["rows"]
        assert [[name for _, name, _, _ in row] for row in day_by_day] == [
            ["Daily ΔMAE"],
            ["Cumulative error reduction"],
            ["Most improved days"],
            ["Most worsened days"],
        ]
        assert all(row[0][2] == 12 for row in day_by_day)  # the day tables are full width


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


class TestBuildChartConfiguration:
    def test_each_emitter_scopes_its_targets_and_excludes_the_rest(self, script):
        configuration = script.build_chart_configuration(
            {30: [31, 32, 61], 59: [61, 32]}, [29, 30, 31, 32, 59, 60, 61]
        )
        assert list(configuration) == ["30", "59"]
        assert configuration["30"] == {
            "id": 30,
            "crossFilters": {
                "scope": {"rootPath": ["ROOT_ID"], "excluded": [29, 30, 59, 60]},
                "chartsInScope": [31, 32, 61],
            },
        }
        assert configuration["59"]["crossFilters"]["chartsInScope"] == [61, 32]
        assert configuration["59"]["crossFilters"]["scope"]["excluded"] == [29, 30, 31, 59, 60]

    def test_no_emitters_no_configuration(self, script):
        assert script.build_chart_configuration({}, [1, 2]) == {}


class TestUpsertDashboard:
    def test_creates_then_writes_layout_and_metadata(self, script, fake, spec):
        client = make_client(script, fake)
        position = {"DASHBOARD_VERSION_KEY": "v2", "ROOT_ID": {"children": ["GRID_ID"]}}
        filters = script.build_native_filters(
            dataset_id=10,
            run_excluded=[27],
            default_run_label=None,
            explanation_dataset_id=11,
            day_excluded=[],
            default_day_label=None,
            baseline_excluded=[],
            default_baseline_label=None,
        )

        dashboard_id = script.upsert_dashboard(client, spec, position, filters, {})

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
        assert metadata["label_colors"] == script.LABEL_COLORS
        assert metadata["chart_configuration"] == {}

    def test_updates_existing_dashboard_without_creating(self, script, fake, spot):
        fake.seed("dashboard", id=8, dashboard_title="Spot Price Forecast Analysis")
        client = make_client(script, fake)

        assert script.upsert_dashboard(client, spot, {"k": 1}, [], {}) == 8

        methods = [(c[0], c[1]) for c in fake.calls_after_login()]
        assert methods == [
            ("GET", f"{BASE}/api/v1/dashboard/"),
            ("PUT", f"{BASE}/api/v1/dashboard/8"),
        ]
        assert fake.rows["dashboard"][8]["slug"] == "spot-price-forecast-analysis"
        assert fake.rows["dashboard"][8]["published"] is True
        assert len(fake.rows["dashboard"]) == 1
        metadata = json.loads(fake.rows["dashboard"][8]["json_metadata"])
        assert metadata["chart_configuration"] == {}


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
EXPLANATION_CHART_NAMES = [
    "Base value",
    "Forecast (selection)",
    "Actual (selection)",
    "Net feature effect",
    "SHAP waterfall",
    "Feature values & contributions",
    "Contributions by period",
]
COMPARISON_CHART_NAMES_HEAD = [
    "Baseline MAE",
    "Candidate MAE",
    "ΔMAE vs baseline",
    "ΔMAE % vs baseline",
    "Δ|bias| vs baseline",
    "ΔWAPE vs baseline",
    "Matched coverage",
    "Matched days",
    "Days candidate lower",
    "Median daily ΔMAE",
    "ΔMAE % by time code",
    "ΔMAE % by day part",
    "ΔMAE % by day type",
    "ΔMAE % by day of week",
]
COMPARISON_CHART_NAMES_TAIL = [
    "ΔMAE % by year",
    "ΔMAE % by year and month",
    "ΔMAE % by year and time code",
    "Daily ΔMAE",
    "Cumulative error reduction",
    "Most improved days",
    "Most worsened days",
    "Candidate vs baseline vs actual (30-min detail)",
]
SPOT_COMPARISON_CHART_NAMES = (
    COMPARISON_CHART_NAMES_HEAD + ["ΔMAE % by actual price band"] + COMPARISON_CHART_NAMES_TAIL
)
DEMAND_COMPARISON_CHART_NAMES = (
    COMPARISON_CHART_NAMES_HEAD + ["ΔMAE % by actual demand band"] + COMPARISON_CHART_NAMES_TAIL
)
EXPLANATION_VS_BASELINE_CHART_NAMES = [
    "Δ base value vs baseline",
    "Δ net feature effect vs baseline",
    "Δ forecast vs baseline",
    "SHAP waterfall vs baseline",
    "Contributions vs baseline",
]
EXPECTED_COMPARE_TAB_CHILDREN = [
    "ROW-2-0-0",
    "ROW-2-0-1",
    "HEADER-2-1",
    "ROW-2-1-0",
    "ROW-2-1-1",
    "ROW-2-1-2",
    "ROW-2-1-3",
    "ROW-2-1-4",
    "HEADER-2-2",
    "ROW-2-2-0",
    "ROW-2-2-1",
    "ROW-2-2-2",
    "ROW-2-2-3",
    "HEADER-2-3",
    "ROW-2-3-0",
    "ROW-2-3-1",
    "ROW-2-3-2",
    "HEADER-2-4",
    "ROW-2-4-0",
]
EXPECTED_ACCURACY_TAB_CHILDREN = [
    "ROW-0-0-0",
    "HEADER-0-1",
    "ROW-0-1-0",
    "ROW-0-1-1",
    "ROW-0-1-2",
    "ROW-0-1-3",
    "HEADER-0-2",
    "ROW-0-2-0",
    "ROW-0-2-1",
    "HEADER-0-3",
    "ROW-0-3-0",
    "ROW-0-3-1",
    "ROW-0-3-2",
]
EXPECTED_EXPLANATION_TAB_CHILDREN = [
    "ROW-1-0-0",
    "ROW-1-0-1",
    "ROW-1-0-2",
    "HEADER-1-1",
    "ROW-1-1-0",
    "ROW-1-1-1",
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

        # datasets: analysis, explanation, comparison, explanation comparison, importance
        analysis, explanation, comparison, explanation_comparison, importance = superset.rows[
            "dataset"
        ].values()
        assert (
            analysis["id"],
            explanation["id"],
            comparison["id"],
            explanation_comparison["id"],
            importance["id"],
        ) == (10, 11, 12, 13, 14)
        assert analysis["table_name"] == "demand_forecast_analysis"
        assert analysis["sql"] == DEMAND_DATASET_SQL
        assert analysis["main_dttm_col"] == "trade_datetime"
        assert [(c["column_name"], c["type"], c["is_dttm"]) for c in analysis["columns"]] == (
            DEMAND_COLUMNS
        )
        assert explanation["table_name"] == "demand_forecast_explanation"
        assert explanation["sql"] == DEMAND_EXPLANATION_SQL
        assert [(c["column_name"], c["type"], c["is_dttm"]) for c in explanation["columns"]] == (
            DEMAND_EXPLANATION_COLUMNS
        )
        assert comparison["table_name"] == "demand_forecast_comparison"
        assert comparison["sql"] == DEMAND_COMPARISON_SQL
        assert comparison["main_dttm_col"] == "trade_datetime"
        assert [(c["column_name"], c["type"], c["is_dttm"]) for c in comparison["columns"]] == (
            DEMAND_COMPARISON_COLUMNS
        )
        assert explanation_comparison["table_name"] == "demand_forecast_explanation_comparison"
        assert explanation_comparison["sql"] == DEMAND_EXPLANATION_COMPARISON_SQL
        assert explanation_comparison["main_dttm_col"] == "trade_datetime"
        assert [
            (c["column_name"], c["type"], c["is_dttm"]) for c in explanation_comparison["columns"]
        ] == DEMAND_EXPLANATION_COMPARISON_COLUMNS
        assert importance["table_name"] == "demand_forecast_importance"
        assert importance["sql"] == DEMAND_IMPORTANCE_SQL
        assert importance["main_dttm_col"] == "published_at"
        assert [(c["column_name"], c["type"], c["is_dttm"]) for c in importance["columns"]] == (
            DEMAND_IMPORTANCE_COLUMNS
        )
        dataset_puts = [
            c
            for c in superset.calls
            if c[0] == "PUT"
            and c[1] in {f"{BASE}/api/v1/dataset/{i}" for i in (10, 11, 12, 13, 14)}
        ]
        assert [c[3] for c in dataset_puts] == [{"override_columns": "true"}] * 5

        # charts, in creation order: 19 analysis, 7 explanation, 3 importance (the mean |SHAP|
        # one on the explanation dataset), 23 comparison, 5 explanation vs baseline
        charts = list(superset.rows["chart"].values())
        assert [c["slice_name"] for c in charts] == (
            EXPECTED_DEMAND_CHART_NAMES
            + EXPLANATION_CHART_NAMES
            + IMPORTANCE_CHART_NAMES
            + DEMAND_COMPARISON_CHART_NAMES
            + EXPLANATION_VS_BASELINE_CHART_NAMES
        )
        assert [c["id"] for c in charts] == list(range(15, 72))
        for c, dataset_id in zip(
            charts, [10] * 19 + [11] * 7 + [14, 11, 14] + [12] * 23 + [13] * 5, strict=True
        ):
            assert c["datasource_id"] == dataset_id
            assert json.loads(c["params"])["datasource"] == f"{dataset_id}__table"
            assert c["datasource_type"] == "table"
            assert json.loads(c["params"])["viz_type"] == c["viz_type"]
        by_name = {c["slice_name"]: json.loads(c["params"]) for c in charts}
        assert by_name["Overall MAE"]["viz_type"] == "big_number_total"
        assert by_name["Overall MAE"]["metric"] == demand.mae_metric
        assert by_name["Overall MAE"]["subheader"] == "MWh"
        assert by_name["Bias (mean error)"]["y_axis_format"] == "+,.1f"
        assert by_name["WAPE"]["y_axis_format"] == ".1%"
        assert by_name["P90 abs error"]["metric"] == demand.p90_metric
        assert by_name["MAE by year and month"]["x_axis"] == "month"
        assert by_name["MAE by actual demand band"]["x_axis"] == "actual_demand_band"
        assert by_name["Error distribution"]["column"] == "error_mwh"
        assert by_name["Run leaderboard"]["viz_type"] == "table"
        assert by_name["Base value"]["metric"] == demand.base_value_metric
        assert by_name["Contributions by period"]["viz_type"] == "mixed_timeseries"
        assert by_name["Permutation importance"]["metrics"] == [demand.importance_metric]
        assert by_name["Mean |SHAP| by feature"]["adhoc_filters"] == [script.NOT_BASE_FILTER]
        assert by_name["Feature importance table"]["groupby"] == ["feature_label"]
        # the Compare tab
        assert by_name["Baseline MAE"]["metric"] == demand.baseline_mae_metric
        assert by_name["Baseline MAE"]["subheader"] == "MWh; the Baseline run, matched periods"
        assert by_name["Candidate MAE"]["metric"] == demand.candidate_mae_metric
        assert by_name["Candidate MAE"]["subheader"] == "MWh; the Run, matched periods"
        assert by_name["ΔMAE vs baseline"]["metric"] == demand.delta_mae_metric
        assert by_name["ΔMAE vs baseline"]["subheader"] == "MWh; − = candidate better"
        assert by_name["ΔMAE vs baseline"]["y_axis_format"] == "+,.1f"
        assert by_name["ΔMAE vs baseline"]["conditional_formatting"][0]["column"] == "ΔMAE"
        assert by_name["ΔMAE % vs baseline"]["metric"] == demand.delta_mae_pct_metric
        assert by_name["ΔMAE % vs baseline"]["subheader"] == "%; − = candidate better"
        assert by_name["ΔMAE % vs baseline"]["y_axis_format"] == "+,.1f"
        assert by_name["Δ|bias| vs baseline"]["metric"] == demand.delta_abs_bias_metric
        assert by_name["Δ|bias| vs baseline"]["subheader"] == "MWh; − = candidate less biased"
        assert by_name["ΔWAPE vs baseline"]["metric"] == demand.delta_wape_metric
        assert by_name["ΔWAPE vs baseline"]["y_axis_format"] == "+.2%"
        assert by_name["Matched coverage"]["metric"] == demand.matched_coverage_metric
        assert by_name["Matched coverage"]["y_axis_format"] == ".1%"
        assert by_name["Matched coverage"]["subheader"] == (
            "share of the candidate's periods the baseline also scored"
        )
        assert "conditional_formatting" not in by_name["Matched coverage"]
        assert by_name["Matched days"]["metric"] == demand.matched_days_metric
        assert by_name["Matched days"]["y_axis_format"] == ",d"
        assert by_name["Days candidate lower"]["metric"] == demand.days_candidate_lower_metric
        assert by_name["Days candidate lower"]["y_axis_format"] == ".1%"
        assert by_name["Median daily ΔMAE"]["metric"] == demand.median_daily_delta_metric
        assert by_name["Median daily ΔMAE"]["conditional_formatting"][1]["operator"] == ">"
        assert by_name["ΔMAE % by time code"]["x_axis"] == "time_code"
        assert by_name["ΔMAE % by day part"]["x_axis"] == "day_part"
        assert by_name["ΔMAE % by day type"]["x_axis"] == "day_type"
        assert by_name["ΔMAE % by day of week"]["x_axis"] == "day_of_week"
        assert by_name["ΔMAE % by actual demand band"]["x_axis"] == "actual_demand_band"
        assert by_name["ΔMAE % by year"]["x_axis"] == "year"
        assert by_name["ΔMAE % by year and month"]["viz_type"] == "heatmap_v2"
        assert by_name["ΔMAE % by year and month"]["x_axis"] == "month"
        assert by_name["ΔMAE % by year and time code"]["x_axis"] == "time_code"
        assert by_name["Daily ΔMAE"]["x_axis"] == "date_key"
        assert by_name["Daily ΔMAE"]["zoomable"] is True
        assert by_name["Cumulative error reduction"]["rolling_type"] == "cumsum"
        assert by_name["Most improved days"]["order_desc"] is False
        assert by_name["Most worsened days"]["order_desc"] is True
        assert [
            m["label"]
            for m in by_name["Candidate vs baseline vs actual (30-min detail)"]["metrics"]
        ] == ["Actual", "Candidate", "Baseline"]

        # dashboard
        (dashboard,) = superset.rows["dashboard"].values()
        assert dashboard_id == dashboard["id"] == 72
        assert dashboard["dashboard_title"] == "Demand Forecast Analysis"
        assert dashboard["slug"] == "demand-forecast-analysis"
        assert dashboard["published"] is True
        metadata = json.loads(dashboard["json_metadata"])
        assert set(metadata) == EXPECTED_JSON_METADATA_KEYS
        assert metadata["label_colors"] == script.LABEL_COLORS
        run_filter, day_filter, baseline_filter = metadata["native_filter_configuration"]
        analysis_ids = list(range(15, 34))
        explanation_ids = list(range(34, 41))
        importance_ids = [41, 42, 43]
        comparison_ids = list(range(44, 67))
        explained_vs_baseline_ids = list(range(67, 72))
        leaderboard_id = superset.id_of("chart", "slice_name", "Run leaderboard")
        assert leaderboard_id == 31
        assert run_filter["targets"] == [{"column": {"name": "run_label"}, "datasetId": 10}]
        assert run_filter["scope"]["excluded"] == [leaderboard_id]
        assert run_filter["defaultDataMask"]["filterState"]["value"] == [DEFAULT_LABEL]
        assert run_filter["controlValues"]["defaultToFirstItem"] is False
        # Day applies to the Explanation tab's per-day charts only ...
        assert day_filter["targets"] == [{"column": {"name": "trade_date_label"}, "datasetId": 11}]
        # ... and the Compare tab's explanation-vs-baseline section; the run-level
        # feature-importance section is out
        assert day_filter["scope"]["excluded"] == analysis_ids + importance_ids + comparison_ids
        assert set(explained_vs_baseline_ids).isdisjoint(day_filter["scope"]["excluded"])
        assert day_filter["cascadeParentIds"] == ["NATIVE_FILTER-run"]
        assert day_filter["defaultDataMask"]["filterState"]["value"] == [DEFAULT_LAST_DAY]
        # Baseline applies to the Compare tab only, reading its options off the analysis dataset
        assert baseline_filter["targets"] == [
            {"column": {"name": "baseline_run_label"}, "datasetId": 10}
        ]
        assert baseline_filter["scope"]["excluded"] == (
            analysis_ids + explanation_ids + importance_ids
        )
        assert baseline_filter["defaultDataMask"]["filterState"]["value"] == [BASELINE_LABEL]
        assert baseline_filter["controlValues"]["enableEmptyFilter"] is True

        position = json.loads(dashboard["position_json"])
        assert position["HEADER_ID"]["meta"]["text"] == "Demand Forecast Analysis"
        chart_keys = sorted(k for k in position if k.startswith("CHART-"))
        assert chart_keys == sorted(f"CHART-{i}" for i in range(15, 72))
        assert position["ROOT_ID"]["children"] == ["TABS-0"]
        assert position["TABS-0"]["children"] == ["TAB-0", "TAB-1", "TAB-2"]
        assert [position[t]["meta"]["text"] for t in ("TAB-0", "TAB-1", "TAB-2")] == [
            "Accuracy",
            "Explanation",
            "Compare",
        ]
        assert position["HEADER-1-1"]["meta"]["text"] == script.IMPORTANCE_SECTION_HEADER
        assert position["TAB-0"]["children"] == EXPECTED_ACCURACY_TAB_CHILDREN
        assert position["TAB-1"]["children"] == EXPECTED_EXPLANATION_TAB_CHILDREN
        assert position["TAB-2"]["children"] == EXPECTED_COMPARE_TAB_CHILDREN
        assert [
            position[h]["meta"]["text"] for h in ("HEADER-0-1", "HEADER-0-2", "HEADER-0-3")
        ] == ["Error structure", "Calibration & distribution", "Runs & drilldown"]
        assert [
            position[h]["meta"]["text"]
            for h in ("HEADER-2-1", "HEADER-2-2", "HEADER-2-3", "HEADER-2-4")
        ] == [
            "Where the candidate wins (ΔMAE % vs baseline)",
            "Day by day",
            "Explanation vs baseline (SHAP; mean per period of the Day, or of the run when Day is"
            " empty)",
            "Detail",
        ]
        assert position["ROW-0-0-0"]["children"] == [f"CHART-{i}" for i in range(15, 21)]
        assert position["CHART-15"]["meta"] == {
            "chartId": 15,
            "width": 2,
            "height": 24,
            "sliceName": "Overall MAE",
        }
        assert position["ROW-0-2-0"]["children"] == ["CHART-28", "CHART-29"]
        assert (position["CHART-28"]["meta"]["width"], position["CHART-29"]["meta"]["width"]) == (
            5,
            7,
        )
        assert position["ROW-0-3-0"]["children"] == [f"CHART-{leaderboard_id}"]
        assert position["CHART-33"]["meta"]["height"] == 60  # 30-min detail
        assert position["ROW-1-0-0"]["children"] == [f"CHART-{i}" for i in range(34, 38)]
        assert position["ROW-1-0-1"]["children"] == ["CHART-38", "CHART-39"]
        assert position["ROW-1-0-2"]["children"] == ["CHART-40"]
        assert position["CHART-40"]["parents"] == ["ROOT_ID", "TABS-0", "TAB-1", "ROW-1-0-2"]
        # the feature-importance section: bars side by side, then the full-width table
        assert position["ROW-1-1-0"]["children"] == ["CHART-41", "CHART-42"]
        assert position["CHART-41"]["meta"] == {
            "chartId": 41,
            "width": 6,
            "height": 40,
            "sliceName": "Permutation importance",
        }
        assert position["ROW-1-1-1"]["children"] == ["CHART-43"]
        assert position["CHART-43"]["meta"]["width"] == 12
        # Compare tab rows
        assert position["ROW-2-0-0"]["children"] == [f"CHART-{i}" for i in range(44, 50)]
        assert position["CHART-44"]["meta"] == {
            "chartId": 44,
            "width": 2,
            "height": 24,
            "sliceName": "Baseline MAE",
        }
        assert position["ROW-2-0-1"]["children"] == [f"CHART-{i}" for i in range(50, 54)]
        assert position["CHART-50"]["meta"]["width"] == 3
        assert position["ROW-2-1-0"]["children"] == ["CHART-54"]
        assert (position["CHART-54"]["meta"]["width"], position["CHART-54"]["meta"]["height"]) == (
            12,
            36,
        )
        assert position["ROW-2-1-1"]["children"] == ["CHART-55", "CHART-56", "CHART-57"]
        assert position["CHART-55"]["meta"]["width"] == 4
        assert position["ROW-2-1-2"]["children"] == ["CHART-58", "CHART-59"]
        assert position["CHART-58"]["meta"] == {
            "chartId": 58,
            "width": 6,
            "height": 36,
            "sliceName": "ΔMAE % by actual demand band",
        }
        assert position["ROW-2-1-3"]["children"] == ["CHART-60"]
        assert position["CHART-60"]["meta"]["height"] == 46
        assert position["ROW-2-1-4"]["children"] == ["CHART-61"]
        assert position["CHART-61"]["meta"]["height"] == 50
        assert position["ROW-2-2-0"]["children"] == ["CHART-62"]
        assert position["CHART-62"]["meta"]["height"] == 44
        assert position["ROW-2-2-1"]["children"] == ["CHART-63"]
        assert position["CHART-63"]["meta"]["height"] == 40
        # the day tables are stacked full width so every column is visible
        assert position["ROW-2-2-2"]["children"] == ["CHART-64"]
        assert (position["CHART-64"]["meta"]["width"], position["CHART-64"]["meta"]["height"]) == (
            12,
            40,
        )
        assert position["ROW-2-2-3"]["children"] == ["CHART-65"]
        assert position["CHART-65"]["meta"]["sliceName"] == "Most worsened days"
        assert position["CHART-65"]["meta"]["width"] == 12
        # explanation vs baseline: three tiles, then the waterfall and the table
        assert position["ROW-2-3-0"]["children"] == ["CHART-67", "CHART-68", "CHART-69"]
        assert position["CHART-67"]["meta"] == {
            "chartId": 67,
            "width": 4,
            "height": 24,
            "sliceName": "Δ base value vs baseline",
        }
        # the waterfall and its table each take a full-width row (a five-column
        # table clips at half width)
        assert position["ROW-2-3-1"]["children"] == ["CHART-70"]
        assert (position["CHART-70"]["meta"]["width"], position["CHART-70"]["meta"]["height"]) == (
            12,
            46,
        )
        assert position["ROW-2-3-2"]["children"] == ["CHART-71"]
        assert (position["CHART-71"]["meta"]["width"], position["CHART-71"]["meta"]["height"]) == (
            12,
            40,
        )
        assert position["ROW-2-4-0"]["children"] == ["CHART-66"]
        assert position["CHART-66"]["meta"]["height"] == 60
        assert position["CHART-66"]["parents"] == ["ROOT_ID", "TABS-0", "TAB-2", "ROW-2-4-0"]

        # every chart is linked to the dashboard
        assert all(c["dashboards"] == [72] for c in charts)
        assert method_counts(superset.calls, "dataset") == {"GET": 5, "POST": 5, "PUT": 5}
        assert method_counts(superset.calls, "chart") == {"GET": 57, "POST": 57, "PUT": 57}
        assert method_counts(superset.calls, "dashboard") == {"GET": 1, "POST": 1, "PUT": 1}

    def test_spot_price_dashboard_keeps_its_names_layout_and_formats(self, script, superset, spot):
        client = make_client(script, superset)

        script.build_dashboard(client, 3, spot)

        analysis, explanation, comparison, explanation_comparison, importance = superset.rows[
            "dataset"
        ].values()
        assert analysis["table_name"] == "spot_price_forecast_analysis"
        assert analysis["sql"] == SPOT_DATASET_SQL
        assert explanation["table_name"] == "spot_price_forecast_explanation"
        assert explanation["sql"] == SPOT_EXPLANATION_SQL
        assert comparison["table_name"] == "spot_price_forecast_comparison"
        assert explanation_comparison["table_name"] == "spot_price_forecast_explanation_comparison"
        assert explanation_comparison["sql"] == SPOT_EXPLANATION_COMPARISON_SQL
        assert comparison["sql"] == SPOT_COMPARISON_SQL
        assert importance["table_name"] == "spot_price_forecast_importance"
        assert importance["sql"] == SPOT_IMPORTANCE_SQL
        charts = list(superset.rows["chart"].values())
        assert [c["slice_name"] for c in charts] == (
            EXPECTED_SPOT_CHART_NAMES
            + EXPLANATION_CHART_NAMES
            + IMPORTANCE_CHART_NAMES
            + SPOT_COMPARISON_CHART_NAMES
            + EXPLANATION_VS_BASELINE_CHART_NAMES
        )
        by_name = {c["slice_name"]: json.loads(c["params"]) for c in charts}
        assert by_name["Overall MAE"]["subheader"] == "JPY/kWh"
        assert by_name["Overall MAE"]["y_axis_format"] == ",.3f"
        assert by_name["Bias (mean error)"]["subheader"] == "JPY/kWh; + = over-forecast"
        assert by_name["Bias (mean error)"]["y_axis_format"] == "+,.3f"
        assert by_name["WAPE"]["y_axis_format"] == ".1%"
        assert by_name["MAE by actual price band"]["x_axis"] == "actual_price_band"
        assert by_name["MAE by year"]["y_axis_format"] == ",.2f"
        assert by_name["Error distribution"]["column"] == "error_jpy_kwh"
        assert by_name["Net feature effect"]["y_axis_format"] == "+,.3f"
        assert by_name["SHAP waterfall"]["y_axis_format"] == ",.2f"
        assert by_name["Permutation importance"]["y_axis_format"] == ",.2f"
        assert by_name["ΔMAE vs baseline"]["y_axis_format"] == "+,.3f"
        assert by_name["Daily ΔMAE"]["y_axis_format"] == "+,.2f"
        (dashboard,) = superset.rows["dashboard"].values()
        assert dashboard["dashboard_title"] == "Spot Price Forecast Analysis"
        assert dashboard["slug"] == "spot-price-forecast-analysis"
        position = json.loads(dashboard["position_json"])
        assert position["HEADER_ID"]["meta"]["text"] == "Spot Price Forecast Analysis"
        assert position["TABS-0"]["children"] == ["TAB-0", "TAB-1", "TAB-2"]
        assert position["TAB-0"]["children"] == EXPECTED_ACCURACY_TAB_CHILDREN
        assert position["TAB-1"]["children"] == EXPECTED_EXPLANATION_TAB_CHILDREN
        assert position["TAB-2"]["children"] == EXPECTED_COMPARE_TAB_CHILDREN
        assert position["ROW-0-3-0"]["children"] == ["CHART-31"]  # Run leaderboard
        run_filter, _, baseline_filter = json.loads(dashboard["json_metadata"])[
            "native_filter_configuration"
        ]
        assert run_filter["scope"]["excluded"] == [31]
        assert baseline_filter["scope"]["excluded"] == list(range(15, 44))

    def test_two_dashboards_coexist_with_their_own_datasets_and_charts(
        self, script, superset, spot, demand
    ):
        client = make_client(script, superset)

        spot_id = script.build_dashboard(client, 3, spot)
        demand_id = script.build_dashboard(client, 3, demand)

        assert (spot_id, demand_id) == (72, 135)  # 5 datasets + 57 charts + dashboard, twice
        spot_ds = superset.id_of("dataset", "table_name", "spot_price_forecast_analysis")
        spot_ex = superset.id_of("dataset", "table_name", "spot_price_forecast_explanation")
        spot_cmp = superset.id_of("dataset", "table_name", "spot_price_forecast_comparison")
        spot_xc = superset.id_of(
            "dataset", "table_name", "spot_price_forecast_explanation_comparison"
        )
        spot_imp = superset.id_of("dataset", "table_name", "spot_price_forecast_importance")
        demand_ds = superset.id_of("dataset", "table_name", "demand_forecast_analysis")
        demand_ex = superset.id_of("dataset", "table_name", "demand_forecast_explanation")
        demand_cmp = superset.id_of("dataset", "table_name", "demand_forecast_comparison")
        demand_xc = superset.id_of(
            "dataset", "table_name", "demand_forecast_explanation_comparison"
        )
        demand_imp = superset.id_of("dataset", "table_name", "demand_forecast_importance")
        assert (
            spot_ds,
            spot_ex,
            spot_cmp,
            spot_xc,
            spot_imp,
            demand_ds,
            demand_ex,
            demand_cmp,
            demand_xc,
            demand_imp,
        ) == (10, 11, 12, 13, 14, 73, 74, 75, 76, 77)
        assert len(superset.rows["chart"]) == 114
        explanation_names = EXPLANATION_CHART_NAMES + ["Mean |SHAP| by feature"]
        importance_names = ["Permutation importance", "Feature importance table"]
        assert [c["slice_name"] for c in charts_of(superset, spot_ds)] == EXPECTED_SPOT_CHART_NAMES
        assert [c["slice_name"] for c in charts_of(superset, spot_ex)] == explanation_names
        assert [c["slice_name"] for c in charts_of(superset, spot_imp)] == importance_names
        assert [
            c["slice_name"] for c in charts_of(superset, spot_cmp)
        ] == SPOT_COMPARISON_CHART_NAMES
        assert [c["slice_name"] for c in charts_of(superset, demand_ds)] == (
            EXPECTED_DEMAND_CHART_NAMES
        )
        assert [c["slice_name"] for c in charts_of(superset, demand_ex)] == explanation_names
        assert [c["slice_name"] for c in charts_of(superset, demand_imp)] == importance_names
        assert [
            c["slice_name"] for c in charts_of(superset, demand_cmp)
        ] == DEMAND_COMPARISON_CHART_NAMES
        for dataset_id in (spot_xc, demand_xc):
            assert [c["slice_name"] for c in charts_of(superset, dataset_id)] == (
                EXPLANATION_VS_BASELINE_CHART_NAMES
            )
        for dashboard_id, dataset_ids in (
            (spot_id, (spot_ds, spot_ex, spot_cmp, spot_xc, spot_imp)),
            (demand_id, (demand_ds, demand_ex, demand_cmp, demand_xc, demand_imp)),
        ):
            for dataset_id in dataset_ids:
                assert all(
                    c["dashboards"] == [dashboard_id] for c in charts_of(superset, dataset_id)
                )
            metadata = json.loads(superset.rows["dashboard"][dashboard_id]["json_metadata"])
            run_filter, day_filter, baseline_filter = metadata["native_filter_configuration"]
            assert run_filter["targets"][0]["datasetId"] == dataset_ids[0]
            assert day_filter["targets"][0]["datasetId"] == dataset_ids[1]
            assert baseline_filter["targets"][0]["datasetId"] == dataset_ids[0]
            position = json.loads(superset.rows["dashboard"][dashboard_id]["position_json"])
            chart_ids = sorted(int(k[6:]) for k in position if k.startswith("CHART-"))
            assert chart_ids == sorted(c["id"] for d in dataset_ids for c in charts_of(superset, d))

    def test_second_build_is_idempotent_and_rewrites_metadata(self, script, superset, demand):
        client = make_client(script, superset)
        script.build_dashboard(client, 3, demand)
        first_ids = {r: sorted(rows) for r, rows in superset.rows.items()}
        superset.calls.clear()
        # The mart is now "empty": the filter must fall back to defaultToFirstItem.
        superset.overrides[("POST", "/api/v1/sqllab/execute/")] = FakeResponse({"data": []})

        script.build_dashboard(client, 3, demand)

        assert {r: sorted(rows) for r, rows in superset.rows.items()} == first_ids
        assert method_counts(superset.calls, "dataset") == {"GET": 5, "PUT": 5}
        assert method_counts(superset.calls, "chart") == {"GET": 57, "PUT": 114}
        assert method_counts(superset.calls, "dashboard") == {"GET": 1, "PUT": 1}
        (dashboard,) = superset.rows["dashboard"].values()
        run_filter, day_filter, baseline_filter = json.loads(dashboard["json_metadata"])[
            "native_filter_configuration"
        ]
        assert run_filter["defaultDataMask"] == {"extraFormData": {}, "filterState": {}}
        assert run_filter["controlValues"]["defaultToFirstItem"] is True
        assert run_filter["scope"]["excluded"] == [31]
        assert day_filter["defaultDataMask"] == {"extraFormData": {}, "filterState": {}}
        assert day_filter["controlValues"]["defaultToFirstItem"] is True
        assert baseline_filter["defaultDataMask"] == {"extraFormData": {}, "filterState": {}}
        assert baseline_filter["controlValues"]["defaultToFirstItem"] is False
        assert all(c["dashboards"] == [72] for c in superset.rows["chart"].values())

    def test_day_tables_cross_filter_the_detail_charts_and_the_explanation_tab(
        self, script, superset, demand
    ):
        client = make_client(script, superset)
        script.build_dashboard(client, 3, demand)
        (dashboard,) = superset.rows["dashboard"].values()
        configuration = json.loads(dashboard["json_metadata"])["chart_configuration"]
        worst_days = superset.id_of("chart", "slice_name", "Worst days")
        improved = superset.id_of("chart", "slice_name", "Most improved days")
        worsened = superset.id_of("chart", "slice_name", "Most worsened days")
        detail = superset.id_of("chart", "slice_name", "Forecast vs actual (30-min detail)")
        compare_detail = superset.id_of(
            "chart", "slice_name", "Candidate vs baseline vs actual (30-min detail)"
        )
        # the Explanation tab's per-day charts (not its feature-importance section)
        # plus the Compare tab's explanation-vs-baseline section
        explained = [*range(34, 41), *range(67, 72)]
        assert (worst_days, improved, worsened, detail, compare_detail) == (32, 64, 65, 33, 66)
        assert list(configuration) == ["32", "64", "65"]
        assert configuration["32"]["crossFilters"]["chartsInScope"] == [
            detail,
            *explained,
            compare_detail,
        ]
        assert configuration["32"]["crossFilters"]["scope"]["excluded"] == [
            i for i in range(15, 72) if i not in (detail, *explained, compare_detail)
        ]
        for emitter in ("64", "65"):
            assert configuration[emitter]["crossFilters"]["chartsInScope"] == [
                compare_detail,
                *explained,
            ]
            assert configuration[emitter]["crossFilters"]["scope"]["excluded"] == [
                i for i in range(15, 72) if i not in (compare_detail, *explained)
            ]

    def test_baseline_run_override_reaches_the_baseline_filter(self, script, superset, demand):
        client = make_client(script, superset)
        script.build_dashboard(client, 3, demand, baseline_run="ffff")
        (dashboard,) = superset.rows["dashboard"].values()
        _, _, baseline_filter = json.loads(dashboard["json_metadata"])[
            "native_filter_configuration"
        ]
        assert baseline_filter["defaultDataMask"]["filterState"]["value"] == [SHORT_LABEL]


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
            "spot_price_forecast_explanation",
            "spot_price_forecast_comparison",
            "spot_price_forecast_explanation_comparison",
            "spot_price_forecast_importance",
            "demand_forecast_analysis",
            "demand_forecast_explanation",
            "demand_forecast_comparison",
            "demand_forecast_explanation_comparison",
            "demand_forecast_importance",
        ]
        assert len(superset.rows["chart"]) == 114
        assert method_counts(superset.calls, "database") == {"GET": 1}
        assert method_counts(superset.calls, "chart") == {"GET": 114, "POST": 114, "PUT": 114}

    def test_task_flag_selects_one_dashboard(self, script, superset, monkeypatch):
        run_main(script, superset, monkeypatch, ["--url", BASE, "--task", "demand"])

        assert [d["dashboard_title"] for d in superset.rows["dashboard"].values()] == [
            "Demand Forecast Analysis"
        ]
        assert [d["table_name"] for d in superset.rows["dataset"].values()] == [
            "demand_forecast_analysis",
            "demand_forecast_explanation",
            "demand_forecast_comparison",
            "demand_forecast_explanation_comparison",
            "demand_forecast_importance",
        ]
        assert [c["slice_name"] for c in superset.rows["chart"].values()] == (
            EXPECTED_DEMAND_CHART_NAMES
            + EXPLANATION_CHART_NAMES
            + IMPORTANCE_CHART_NAMES
            + DEMAND_COMPARISON_CHART_NAMES
            + EXPLANATION_VS_BASELINE_CHART_NAMES
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
        assert len(superset.rows["chart"]) == 114

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
        assert len(superset.rows["chart"]) == 57

    def test_baseline_run_flag_is_passed_to_every_dashboard(self, script, superset, monkeypatch):
        run_main(
            script,
            superset,
            monkeypatch,
            ["--url", BASE, "--task", "demand", "--baseline-run", "ffffffff"],
        )
        (dashboard,) = superset.rows["dashboard"].values()
        _, _, baseline_filter = json.loads(dashboard["json_metadata"])[
            "native_filter_configuration"
        ]
        assert baseline_filter["defaultDataMask"]["filterState"]["value"] == [SHORT_LABEL]
