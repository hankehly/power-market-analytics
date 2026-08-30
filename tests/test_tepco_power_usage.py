"""TEPCO でんき予報 過去の電力使用実績 (hourly 電力使用状況) source spec and pipeline."""

from __future__ import annotations

import datetime
import io
import re
import zipfile
from pathlib import Path

import pytest
import requests

from power_market_analytics.area_actuals import (
    AreaActualsDownloader,
    AreaActualsDownloadError,
    AreaActualsSource,
)
from power_market_analytics.csv_loader import CsvTableSchema
from power_market_analytics.tepco.power_usage import (
    DAILY_FILES_FROM,
    DAILY_HOURLY_HEADER,
    TEPCO_POWER_USAGE,
    YEARLY_HEADER,
    YEARLY_URL_TEMPLATE,
    YEARLY_YEARS,
    HourlyRow,
    TepcoPowerUsageCsvLoader,
    TepcoPowerUsageDownloader,
    parse_hourly,
)
from tests.support import REPO_ROOT
from tests.test_area_actuals import FakeResponse, make_zip

# --- fixture builders -------------------------------------------------------

FIVE_MINUTE_HEADER = (
    "DATE,TIME,当日実績(５分間隔値)(万kW),太陽光発電実績(５分間隔値)(万kW),"
    "太陽光発電量(電力使用量に対する割合)(%)"
)


def daily_file_text(
    date: str = "2022/4/1",
    updated: str = "2022/4/1 23:55",
    hours: int = 24,
    *,
    hourly_header: str = DAILY_HOURLY_HEADER,
) -> str:
    """A daily ``YYYYMMDD_power_usage.csv`` in TEPCO's multi-section layout."""
    lines = [
        f"{updated} UPDATE",
        "ピーク時供給力(万kW),時間帯,供給力情報更新日,供給力情報更新時刻,ピーク時予備率(%),ピーク時使用率(%)",
        "4471,9:00～10:00,4/1,23:20,15,86",
        "",
        "予想最大電力(万kW),時間帯,予想最大電力情報更新日,予想最大電力情報更新時刻",
        "3887,9:00～10:00,4/1,23:20",
        "",
        hourly_header,
    ]
    for hour in range(hours):
        lines.append(f"{date},{hour}:00,{2500 + hour},{2490 + hour},{80 + hour % 5},{3100 + hour}")
    lines += [
        "",
        "最大使用率(%),時間帯",
        "86,9:00～10:00",
        "",
        FIVE_MINUTE_HEADER,
        f"{date},0:00,2620,0,0",
        f"{date},0:05,2611,0,0",
        f"{date},23:55,2992,0,0",
        "",
    ]
    return "\r\n".join(lines) + "\r\n"


def yearly_file_text(rows: list[str], updated: str = "2018/1/1 18:10") -> str:
    """A yearly ``juyo-YYYY.csv``: UPDATE stamp, blank line, header, hourly rows."""
    return "\r\n".join([f"{updated} UPDATE", "", YEARLY_HEADER, *rows]) + "\r\n"


def yearly_day(date: str, base: int = 2500) -> list[str]:
    """The 24 yearly-layout rows of one day, demand ``base + hour``."""
    return [f"{date},{hour}:00,{base + hour}" for hour in range(24)]


def write_cp932(path: Path, text: str) -> Path:
    path.write_bytes(text.encode("cp932"))
    return path


# --- source spec ------------------------------------------------------------


class TestTepcoPowerUsageSource:
    def test_is_an_area_actuals_source(self):
        assert isinstance(TEPCO_POWER_USAGE, AreaActualsSource)
        assert TEPCO_POWER_USAGE.code == "tepco_power_usage"

    def test_zip_url_and_name(self):
        assert (
            TEPCO_POWER_USAGE.zip_url(2022, 4)
            == "https://www.tepco.co.jp/forecast/html/images/202204_power_usage.zip"
        )
        assert TEPCO_POWER_USAGE.zip_name(2026, 8) == "202608_power_usage.zip"

    def test_monthly_archives_start_when_the_daily_files_do(self):
        assert TEPCO_POWER_USAGE.earliest_month == (2022, 4)
        assert DAILY_FILES_FROM == datetime.date(2022, 4, 1)

    def test_only_daily_members_are_extracted(self):
        assert TEPCO_POWER_USAGE.is_actuals_member("20220401_power_usage.csv")
        assert TEPCO_POWER_USAGE.is_actuals_member("202204/20220430_power_usage.csv")
        assert not TEPCO_POWER_USAGE.is_actuals_member("AREA_JISEKI_20220401.csv")
        assert not TEPCO_POWER_USAGE.is_actuals_member("juyo-2016.csv")

    def test_accepted_headers_are_both_hourly_layouts(self):
        assert TEPCO_POWER_USAGE.accepted_headers == frozenset(
            {
                "DATE,TIME,当日実績(万kW),予測値(万kW),使用率(%),供給力(万kW)",
                "DATE,TIME,実績(万kW)",
            }
        )
        assert FIVE_MINUTE_HEADER not in TEPCO_POWER_USAGE.accepted_headers

    def test_archives_hold_finished_days_only(self):
        assert TEPCO_POWER_USAGE.archive_includes_current_day is False

    def test_default_data_dir(self):
        assert TEPCO_POWER_USAGE.default_data_dir == "data/tepco/power_usage"

    def test_yearly_files_cover_2016_through_2022(self):
        assert YEARLY_URL_TEMPLATE.format(year=2016) == (
            "https://www.tepco.co.jp/forecast/html/images/juyo-2016.csv"
        )
        assert tuple(YEARLY_YEARS) == (2016, 2017, 2018, 2019, 2020, 2021, 2022)


# --- hourly-block parser ----------------------------------------------------


class TestParseHourly:
    def test_daily_layout_yields_the_hourly_rows_only(self, tmp_path):
        file = write_cp932(tmp_path / "20220401_power_usage.csv", daily_file_text())

        parsed = parse_hourly(file)

        assert parsed.header == DAILY_HOURLY_HEADER
        assert parsed.file_updated_at == "20220401 23:55:00"
        assert len(parsed.rows) == 24
        assert parsed.rows[0] == HourlyRow("20220401", 0, "2500", "2490", "80", "3100")
        assert parsed.rows[23] == HourlyRow("20220401", 23, "2523", "2513", "83", "3123")
        # The 5-minute block that follows the hourly one is not read.
        assert {row.hour_start for row in parsed.rows} == set(range(24))

    def test_yearly_layout_yields_rows_with_only_the_actual(self, tmp_path):
        text = yearly_file_text(yearly_day("2016/4/1", 2555))
        file = write_cp932(tmp_path / "juyo-2016.csv", text)

        parsed = parse_hourly(file)

        assert parsed.header == YEARLY_HEADER
        assert parsed.file_updated_at == "20180101 18:10:00"
        assert len(parsed.rows) == 24
        assert parsed.rows[0] == HourlyRow("20160401", 0, "2555", None, None, None)
        assert parsed.rows[23] == HourlyRow("20160401", 23, "2578", None, None, None)

    def test_unpadded_dates_and_hours_are_normalised(self, tmp_path):
        text = yearly_file_text([*yearly_day("2016/12/31"), *yearly_day("2017/1/2")])
        file = write_cp932(tmp_path / "juyo-2016.csv", text)

        rows = parse_hourly(file).rows

        assert (rows[9].target_date, rows[9].hour_start) == ("20161231", 9)
        assert (rows[47].target_date, rows[47].hour_start) == ("20170102", 23)

    def test_missing_update_stamp_raises(self, tmp_path):
        text = "\r\n".join(["", YEARLY_HEADER, "2016/4/1,0:00,2555"]) + "\r\n"
        file = write_cp932(tmp_path / "juyo-2016.csv", text)

        with pytest.raises(ValueError, match="UPDATE"):
            parse_hourly(file)

    def test_unknown_header_raises(self, tmp_path):
        text = daily_file_text(hourly_header="DATE,TIME,当日実績(万kW),予測値(万kW)")
        file = write_cp932(tmp_path / "20220401_power_usage.csv", text)

        with pytest.raises(ValueError, match="accepted"):
            parse_hourly(file)

    def test_row_that_is_not_on_the_hour_raises(self, tmp_path):
        text = yearly_file_text(["2016/4/1,0:00,2555", "2016/4/1,0:30,2500"])
        file = write_cp932(tmp_path / "juyo-2016.csv", text)

        with pytest.raises(ValueError, match=re.escape("0:30")):
            parse_hourly(file)

    def test_row_with_the_wrong_field_count_raises(self, tmp_path):
        text = yearly_file_text(["2016/4/1,0:00,2555,99"])
        file = write_cp932(tmp_path / "juyo-2016.csv", text)

        with pytest.raises(ValueError, match="fields"):
            parse_hourly(file)

    def test_empty_hourly_block_raises(self, tmp_path):
        file = write_cp932(tmp_path / "juyo-2016.csv", yearly_file_text([]))

        with pytest.raises(ValueError, match="no hourly rows"):
            parse_hourly(file)

    def test_daily_block_with_missing_hours_raises(self, tmp_path):
        # A truncated member with one well-formed row and a blank line would
        # otherwise load as a day with absent hours, which no grain test sees.
        file = write_cp932(tmp_path / "20220401_power_usage.csv", daily_file_text(hours=23))

        with pytest.raises(ValueError, match="hours 0-23"):
            parse_hourly(file)

    def test_daily_block_spanning_two_dates_raises(self, tmp_path):
        text = daily_file_text().replace("2022/4/1,23:00", "2022/4/2,23:00")
        file = write_cp932(tmp_path / "20220401_power_usage.csv", text)

        with pytest.raises(ValueError, match="one target date"):
            parse_hourly(file)

    def test_yearly_block_with_an_incomplete_day_raises(self, tmp_path):
        rows = [f"2016/4/1,{h}:00,{2500 + h}" for h in range(24)]
        rows += [f"2016/4/2,{h}:00,{2500 + h}" for h in range(23)]  # last day cut short
        file = write_cp932(tmp_path / "juyo-2016.csv", yearly_file_text(rows))

        with pytest.raises(ValueError, match="2016/4/2|20160402"):
            parse_hourly(file)

    def test_duplicate_hour_raises(self, tmp_path):
        rows = [f"2016/4/1,{h}:00,{2500 + h}" for h in range(24)]
        rows[5] = "2016/4/1,4:00,2504"  # hour 4 twice, hour 5 missing
        file = write_cp932(tmp_path / "juyo-2016.csv", yearly_file_text(rows))

        with pytest.raises(ValueError, match="hours 0-23"):
            parse_hourly(file)

    def test_row_with_a_malformed_date_raises(self, tmp_path):
        text = yearly_file_text(["20160401,0:00,2555"])
        file = write_cp932(tmp_path / "juyo-2016.csv", text)

        with pytest.raises(ValueError, match="yyyy/M/d"):
            parse_hourly(file)

    def test_empty_file_raises(self, tmp_path):
        file = write_cp932(tmp_path / "juyo-2016.csv", "")

        with pytest.raises(ValueError, match="empty file"):
            parse_hourly(file)


# --- downloader -------------------------------------------------------------


class RoutingSession:
    """Stand-in for requests.Session answering each URL with a canned response."""

    def __init__(self, responses: dict[str, FakeResponse]):
        self.responses = responses
        self.calls: list[tuple[str, float]] = []

    def get(self, url: str, timeout: float) -> FakeResponse:
        self.calls.append((url, timeout))
        try:
            return self.responses[url]
        except KeyError:
            return FakeResponse(b"not found", status=404, content_type="text/html")


def yearly_url(year: int) -> str:
    return YEARLY_URL_TEMPLATE.format(year=year)


def full_year_text(year: int) -> str:
    """A complete yearly file: every day TEPCO publishes for ``year``, 24 rows each."""
    first = max(datetime.date(year, 1, 1), datetime.date(2016, 4, 1))
    rows: list[str] = []
    day = first
    while day.year == year:
        rows += yearly_day(f"{day.year}/{day.month}/{day.day}")
        day += datetime.timedelta(days=1)
    return yearly_file_text(rows)


def yearly_response(year: int) -> FakeResponse:
    return FakeResponse(full_year_text(year).encode("cp932"), content_type="text/csv")


def month_zip(year: int, month: int, through: int | None = None) -> bytes:
    """A monthly archive with one daily member per day (all days unless ``through``)."""
    last = (
        through
        or (datetime.date(year + month // 12, month % 12 + 1, 1) - datetime.timedelta(days=1)).day
    )
    return make_zip(
        {
            f"{year}{month:02d}{day:02d}_power_usage.csv": daily_file_text(
                date=f"{year}/{month}/{day}", updated=f"{year}/{month}/{day} 23:55"
            ).encode("cp932")
            for day in range(1, last + 1)
        }
    )


YEARLY_2016 = full_year_text(2016).encode("cp932")


class TestTepcoPowerUsageDownloader:
    def test_is_bound_to_the_source_and_its_data_dir(self):
        dl = TepcoPowerUsageDownloader()
        assert isinstance(dl, AreaActualsDownloader)
        assert dl.source is TEPCO_POWER_USAGE
        assert dl.data_dir == Path("data/tepco/power_usage")

    def test_yearly_files_live_next_to_the_daily_ones(self, tmp_path):
        dl = TepcoPowerUsageDownloader(data_dir=tmp_path)
        assert dl.yearly_path_for(2016) == tmp_path / "csv" / "juyo-2016.csv"

    def test_download_yearly_fetches_and_saves_the_file(self, tmp_path):
        session = RoutingSession(
            {yearly_url(2016): FakeResponse(YEARLY_2016, content_type="text/csv")}
        )
        dl = TepcoPowerUsageDownloader(data_dir=tmp_path, timeout=7.5, session=session)

        path = dl.download_yearly(2016)

        assert path == tmp_path / "csv" / "juyo-2016.csv"
        assert path.read_bytes() == YEARLY_2016
        assert session.calls == [(yearly_url(2016), 7.5)]
        assert list((tmp_path / "csv").glob("*.part")) == []

    def test_download_yearly_uses_the_cached_file_unless_forced(self, tmp_path):
        session = RoutingSession(
            {yearly_url(2016): FakeResponse(YEARLY_2016, content_type="text/csv")}
        )
        dl = TepcoPowerUsageDownloader(data_dir=tmp_path, session=session)
        dl.download_yearly(2016)

        dl.download_yearly(2016)
        assert len(session.calls) == 1

        dl.download_yearly(2016, force=True)
        assert len(session.calls) == 2

    def test_download_yearly_rejects_a_response_without_the_hourly_header(self, tmp_path):
        html = b"<html><body>maintenance</body></html>"
        session = RoutingSession({yearly_url(2016): FakeResponse(html, content_type="text/html")})
        dl = TepcoPowerUsageDownloader(data_dir=tmp_path, session=session)

        with pytest.raises(AreaActualsDownloadError, match="juyo-2016.csv"):
            dl.download_yearly(2016)
        assert not (tmp_path / "csv" / "juyo-2016.csv").exists()

    def test_download_yearly_propagates_http_errors(self, tmp_path):
        dl = TepcoPowerUsageDownloader(data_dir=tmp_path, session=RoutingSession({}))

        with pytest.raises(requests.HTTPError):
            dl.download_yearly(2016)

    def test_download_yearly_rejects_an_incomplete_year(self, tmp_path):
        # A well-formed file missing days would otherwise be cached for good
        # (only --force-yearly refetches) and leave silent gaps in the history.
        partial = yearly_file_text(yearly_day("2016/4/1")).encode("cp932")
        session = RoutingSession({yearly_url(2016): FakeResponse(partial, content_type="text/csv")})
        dl = TepcoPowerUsageDownloader(data_dir=tmp_path, session=session)

        with pytest.raises(AreaActualsDownloadError, match="2016-04-01.*2016-12-31"):
            dl.download_yearly(2016)
        assert not (tmp_path / "csv" / "juyo-2016.csv").exists()
        assert list((tmp_path / "csv").glob("*.part")) == []

    def test_download_yearly_rejects_a_file_with_a_broken_day(self, tmp_path):
        rows = full_year_text(2016).replace("2016/7/1,12:00,2512\r\n", "")
        session = RoutingSession(
            {yearly_url(2016): FakeResponse(rows.encode("cp932"), content_type="text/csv")}
        )
        dl = TepcoPowerUsageDownloader(data_dir=tmp_path, session=session)

        with pytest.raises(AreaActualsDownloadError, match="20160701"):
            dl.download_yearly(2016)
        assert not (tmp_path / "csv" / "juyo-2016.csv").exists()

    def test_download_all_rejects_a_settled_month_with_a_missing_day(self, tmp_path):
        responses = {yearly_url(y): yearly_response(y) for y in YEARLY_YEARS}
        april = zipfile.ZipFile(io.BytesIO(month_zip(2022, 4)))
        members = {n: april.read(n) for n in april.namelist() if n != "20220415_power_usage.csv"}
        responses[TEPCO_POWER_USAGE.zip_url(2022, 4)] = FakeResponse(make_zip(members))
        dl = TepcoPowerUsageDownloader(data_dir=tmp_path, session=RoutingSession(responses))

        with pytest.raises(AreaActualsDownloadError, match="20220415"):
            dl.download_all(today=datetime.date(2022, 5, 15))

    def test_download_all_fetches_every_yearly_file_then_every_month(self, tmp_path):
        responses = {yearly_url(y): yearly_response(y) for y in YEARLY_YEARS}
        responses[TEPCO_POWER_USAGE.zip_url(2022, 4)] = FakeResponse(month_zip(2022, 4))
        responses[TEPCO_POWER_USAGE.zip_url(2022, 5)] = FakeResponse(month_zip(2022, 5, through=14))
        session = RoutingSession(responses)
        dl = TepcoPowerUsageDownloader(data_dir=tmp_path, session=session)

        paths = dl.download_all(today=datetime.date(2022, 5, 15))

        assert [url for url, _ in session.calls] == [
            *(yearly_url(y) for y in YEARLY_YEARS),
            TEPCO_POWER_USAGE.zip_url(2022, 4),
            TEPCO_POWER_USAGE.zip_url(2022, 5),
        ]
        assert paths[: len(YEARLY_YEARS)] == [
            tmp_path / "csv" / f"juyo-{y}.csv" for y in YEARLY_YEARS
        ]
        assert paths[len(YEARLY_YEARS)] == tmp_path / "csv" / "20220401_power_usage.csv"
        assert paths[-1] == tmp_path / "csv" / "20220514_power_usage.csv"
        assert len(paths) == len(YEARLY_YEARS) + 30 + 14

    def test_download_all_can_force_the_yearly_files(self, tmp_path):
        responses = {yearly_url(y): yearly_response(y) for y in YEARLY_YEARS}
        responses[TEPCO_POWER_USAGE.zip_url(2022, 4)] = FakeResponse(month_zip(2022, 4, through=9))
        session = RoutingSession(responses)
        dl = TepcoPowerUsageDownloader(data_dir=tmp_path, session=session)
        dl.download_all(today=datetime.date(2022, 4, 10))
        first = len(session.calls)

        dl.download_all(today=datetime.date(2022, 4, 10))
        assert len(session.calls) == first + 1  # only the month is re-fetched

        dl.download_all(today=datetime.date(2022, 4, 10), force_yearly=True)
        assert len(session.calls) == first + 1 + len(YEARLY_YEARS) + 1

    def test_download_all_skips_the_current_month_on_day_one(self, tmp_path):
        responses = {yearly_url(y): yearly_response(y) for y in YEARLY_YEARS}
        # The previous month may still lack its last day on the 1st (posted ~06:00).
        responses[TEPCO_POWER_USAGE.zip_url(2022, 4)] = FakeResponse(month_zip(2022, 4, through=29))
        session = RoutingSession(responses)
        dl = TepcoPowerUsageDownloader(data_dir=tmp_path, session=session)

        paths = dl.download_all(today=datetime.date(2022, 5, 1))

        assert [url for url, _ in session.calls][-1] == TEPCO_POWER_USAGE.zip_url(2022, 4)
        assert TEPCO_POWER_USAGE.zip_url(2022, 5) not in [url for url, _ in session.calls]
        assert paths[-1] == tmp_path / "csv" / "20220429_power_usage.csv"


# --- load contract + loader -------------------------------------------------

CONTRACT_PATH = REPO_ROOT / "conf/schemas/tepco_power_usage_hourly.yaml"


class TestContract:
    def test_grain_columns_and_nullability(self):
        schema = CsvTableSchema.from_yaml(CONTRACT_PATH)

        assert schema.grain == ["target_date", "hour_start"]
        assert [c.name for c in schema.columns] == [
            "target_date",
            "hour_start",
            "demand_mankw",
            "forecast_mankw",
            "usage_rate_pct",
            "supply_capacity_mankw",
            "file_updated_at",
            "source_file",
        ]
        assert {c.name for c in schema.columns if not c.nullable} == {
            "target_date",
            "hour_start",
            "demand_mankw",
            "file_updated_at",
            "source_file",
        }


class TestTepcoPowerUsageCsvLoader:
    def loader(self, spark, path: Path, table: str) -> TepcoPowerUsageCsvLoader:
        return TepcoPowerUsageCsvLoader(
            CsvTableSchema.from_yaml(CONTRACT_PATH), path, table, spark=spark
        )

    def test_loads_both_layouts_into_one_table(self, spark, tmp_path):
        yearly = yearly_file_text(
            [*yearly_day("2022/3/31", 3000), *yearly_day("2022/4/1", 2800)],
            updated="2024/1/1 18:10",
        )
        write_cp932(tmp_path / "juyo-2022.csv", yearly)
        write_cp932(tmp_path / "20220401_power_usage.csv", daily_file_text())

        n_rows = self.loader(spark, tmp_path, "test_tepco.power_usage_both").load()

        assert n_rows == 48
        table = spark.table("test_tepco.power_usage_both")
        assert dict(table.dtypes) == {
            "target_date": "date",
            "hour_start": "int",
            "demand_mankw": "double",
            "forecast_mankw": "double",
            "usage_rate_pct": "double",
            "supply_capacity_mankw": "double",
            "file_updated_at": "timestamp",
            "source_file": "string",
        }
        rows = {(r.target_date.isoformat(), r.hour_start): r for r in table.collect()}
        assert set(rows) == {
            (day, hour) for day in ("2022-03-31", "2022-04-01") for hour in range(24)
        }
        old = rows[("2022-03-31", 23)]
        assert old.demand_mankw == 3023.0
        assert (old.forecast_mankw, old.usage_rate_pct, old.supply_capacity_mankw) == (
            None,
            None,
            None,
        )
        assert old.file_updated_at.isoformat() == "2024-01-01T18:10:00"
        assert old.source_file == "juyo-2022.csv"
        # 2022-04-01 exists in both files; the daily file wins (0:00 = 2500, not 2800).
        new = rows[("2022-04-01", 0)]
        assert (
            new.demand_mankw,
            new.forecast_mankw,
            new.usage_rate_pct,
            new.supply_capacity_mankw,
        ) == (2500.0, 2490.0, 80.0, 3100.0)
        assert new.file_updated_at.isoformat() == "2022-04-01T23:55:00"
        assert new.source_file == "20220401_power_usage.csv"

    def test_all_files_are_read_into_one_relation_not_a_union(self, spark, tmp_path):
        # 1,600+ per-file frames unioned together made the real load run for
        # hours (16k tasks per action) and crash the driver; every file's rows
        # must land in a single DataFrame.
        write_cp932(tmp_path / "juyo-2016.csv", yearly_file_text(yearly_day("2016/4/1")))
        write_cp932(tmp_path / "20220401_power_usage.csv", daily_file_text())
        write_cp932(tmp_path / "20220402_power_usage.csv", daily_file_text(date="2022/4/2"))
        loader = self.loader(spark, tmp_path, "test_tepco.power_usage_one_relation")

        df = loader._read_all(loader._resolve_files())

        assert df.count() == 72
        assert "Union" not in df._jdf.queryExecution().analyzed().toString()

    def test_loader_has_no_per_file_read(self):
        assert "_read_file" not in TepcoPowerUsageCsvLoader.__dict__

    def test_a_yearly_file_before_the_daily_era_is_loaded_whole(self, spark, tmp_path):
        write_cp932(tmp_path / "juyo-2016.csv", yearly_file_text(yearly_day("2016/4/1")))

        assert self.loader(spark, tmp_path, "test_tepco.power_usage_yearly").load() == 24

    def test_a_yearly_file_entirely_in_the_daily_era_adds_no_rows(self, spark, tmp_path):
        write_cp932(tmp_path / "juyo-2022.csv", yearly_file_text(yearly_day("2022/5/1", 2800)))
        write_cp932(tmp_path / "20220401_power_usage.csv", daily_file_text())

        assert self.loader(spark, tmp_path, "test_tepco.power_usage_cutoff").load() == 24

    def test_duplicate_hours_across_files_fail_the_grain_check(self, spark, tmp_path):
        write_cp932(tmp_path / "20220401_power_usage.csv", daily_file_text())
        write_cp932(tmp_path / "20220401_power_usage_reissued.csv", daily_file_text())

        with pytest.raises(ValueError, match="Grain"):
            self.loader(spark, tmp_path, "test_tepco.power_usage_dup").load()

    def test_unknown_layout_fails_the_load(self, spark, tmp_path):
        text = daily_file_text(hourly_header="DATE,TIME,当日実績(万kW),予測値(万kW)")
        write_cp932(tmp_path / "20220401_power_usage.csv", text)

        with pytest.raises(ValueError, match="accepted"):
            self.loader(spark, tmp_path, "test_tepco.power_usage_bad").load()
