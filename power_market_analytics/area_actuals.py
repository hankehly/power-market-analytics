"""Generic download + load of TSO エリア需要・発電情報 (area demand/generation) actuals.

Under the インバランス料金 information-disclosure rules every 一般送配電事業者
publishes the same three 30-minute series — エリア総需要量, エリア総発電量 and
エリア風力・太陽光発電量 in 30分kWh — as one CSV per day, archived in one zip
per month on its own website. The archive URL, member names, earliest month
and the exact column-header line differ per TSO, so those live in an
:class:`AreaActualsSource` spec (see ``power_market_analytics.tepco`` and
``power_market_analytics.kansai``) while the download/extract and the
positional CSV load are shared here.
"""

from __future__ import annotations

import datetime
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests
from loguru import logger
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

from power_market_analytics.csv_loader import CsvLoader, CsvTableSchema


class AreaActualsDownloadError(RuntimeError):
    """Raised when a TSO returns something other than the expected zip archive."""


def month_range(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    """Return every ``(year, month)`` from ``start`` through ``end`` inclusive.

    Parameters
    ----------
    start, end : tuple of (int, int)
        ``(year, month)`` bounds, both inclusive.

    Returns
    -------
    list of tuple of (int, int)
        Consecutive months in ascending order.

    Raises
    ------
    ValueError
        If ``start`` is after ``end``.
    """
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    months = []
    year, month = start
    while (year, month) <= end:
        months.append((year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


@dataclass(frozen=True)
class AreaActualsSource:
    """Per-TSO description of where and how the actuals archives are published.

    Attributes
    ----------
    code : str
        Short TSO code used in paths and logs, e.g. ``"tepco"``.
    url_template : str
        ``str.format`` template of a month's zip URL with ``{year}`` and
        ``{month}`` fields, e.g. ``".../AREA_{year:04d}{month:02d}.zip"``.
    earliest_month : tuple of (int, int)
        First ``(year, month)`` archive to download.
    member_re : re.Pattern
        Matches the zip members holding daily *actuals* (the archives also
        carry forecast / BG-plan files, which are skipped).
    accepted_headers : frozenset of str
        Every column-header line the TSO has ever used for the actuals files;
        a file whose header is not in this set fails the load.
    default_data_dir : str
        Root directory for the ``zip/`` and ``csv/`` folders.
    """

    code: str
    url_template: str
    earliest_month: tuple[int, int]
    member_re: re.Pattern[str]
    accepted_headers: frozenset[str]
    default_data_dir: str

    def zip_url(self, year: int, month: int) -> str:
        """Return the download URL of one month's archive."""
        return self.url_template.format(year=year, month=month)

    def zip_name(self, year: int, month: int) -> str:
        """Return the local file name of one month's archive (last URL segment)."""
        return self.zip_url(year, month).rsplit("/", 1)[-1]

    def is_actuals_member(self, member_name: str) -> bool:
        """Return whether a zip member is a daily actuals CSV."""
        return self.member_re.search(member_name) is not None


class AreaActualsDownloader:
    """Download a TSO's monthly archives and extract the daily actuals CSVs.

    Every call re-downloads the requested month: TSOs revise past days
    without notice, the current month's zip grows daily, and a whole history
    is only a few MB, so no caching is attempted.

    Parameters
    ----------
    source : AreaActualsSource
        Where and how the TSO publishes its archives.
    data_dir : pathlib.Path or str, optional
        Root directory; zips are kept under ``zip/`` and the extracted daily
        CSVs under ``csv/``. Defaults to ``source.default_data_dir``. Created
        on first download if it does not exist.
    timeout : float, default 60.0
        HTTP request timeout in seconds.
    session : requests.Session, optional
        HTTP session to issue ``get`` calls with; defaults to a fresh
        :class:`requests.Session`. Injected mainly for tests.
    """

    def __init__(
        self,
        source: AreaActualsSource,
        data_dir: Path | str | None = None,
        timeout: float = 60.0,
        session: requests.Session | None = None,
    ) -> None:
        self.source = source
        self.data_dir = Path(data_dir) if data_dir is not None else Path(source.default_data_dir)
        self.timeout = timeout
        self.session = session if session is not None else requests.Session()

    @property
    def zip_dir(self) -> Path:
        """Directory holding the downloaded monthly zip archives."""
        return self.data_dir / "zip"

    @property
    def csv_dir(self) -> Path:
        """Directory holding the extracted daily actuals CSV files."""
        return self.data_dir / "csv"

    def zip_path_for(self, year: int, month: int) -> Path:
        """Return the local path of a month's zip archive.

        Parameters
        ----------
        year, month : int
            Calendar year and month of the archive.

        Returns
        -------
        pathlib.Path
            Path to the (possibly not yet downloaded) zip file.
        """
        return self.zip_dir / self.source.zip_name(year, month)

    def download(self, year: int, month: int) -> list[Path]:
        """Download one month's archive and extract its actuals files.

        Parameters
        ----------
        year, month : int
            Calendar year and month of the archive.

        Returns
        -------
        list of pathlib.Path
            Extracted daily actuals CSV paths, sorted by name.

        Raises
        ------
        AreaActualsDownloadError
            If the response is not a zip archive or contains no actuals files.
        requests.HTTPError
            If the TSO responds with an error status (e.g. 404 for a month
            that is not published).
        """
        url = self.source.zip_url(year, month)
        dest = self.zip_path_for(year, month)
        logger.info("Downloading {} -> {}", url, dest)
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        content = response.content
        if not zipfile.is_zipfile(io.BytesIO(content)):
            raise AreaActualsDownloadError(
                f"{url} did not return a zip archive "
                f"(Content-Type={response.headers.get('Content-Type')!r}); "
                f"body starts {content[:120]!r}"
            )

        self.zip_dir.mkdir(parents=True, exist_ok=True)
        # Write to a temp file and rename so an interrupted download never
        # leaves a truncated archive at the cached path.
        partial = dest.with_name(dest.name + ".part")
        partial.write_bytes(content)
        partial.replace(dest)
        extracted = self._extract_actuals(dest)
        logger.info(
            "Saved {} ({} bytes); extracted {} actuals file(s) to {}",
            dest,
            dest.stat().st_size,
            len(extracted),
            self.csv_dir,
        )
        return extracted

    def download_all(self, today: datetime.date | None = None) -> list[Path]:
        """Download every month from the source's earliest month through the current month.

        Parameters
        ----------
        today : datetime.date, optional
            Date whose month is the last one downloaded. Defaults to the
            current local date.

        Returns
        -------
        list of pathlib.Path
            All extracted actuals CSV paths, in month then day order.
        """
        if today is None:
            today = datetime.date.today()
        first = self.source.earliest_month
        extracted: list[Path] = []
        for year, month in month_range(first, (today.year, today.month)):
            extracted.extend(self.download(year, month))
        logger.info(
            "Downloaded {} {}-{:02d}..{}-{:02d}: {} actuals file(s)",
            self.source.code,
            first[0],
            first[1],
            today.year,
            today.month,
            len(extracted),
        )
        return extracted

    def _extract_actuals(self, zip_path: Path) -> list[Path]:
        self.csv_dir.mkdir(parents=True, exist_ok=True)
        extracted = []
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                if member.is_dir() or not self.source.is_actuals_member(member.filename):
                    continue
                # Some archives nest members under a folder — keep the base
                # name only so csv/ stays flat.
                target = self.csv_dir / Path(member.filename).name
                target.write_bytes(archive.read(member))
                extracted.append(target)
        if not extracted:
            raise AreaActualsDownloadError(
                f"{zip_path} contains no members matching {self.source.member_re.pattern!r}"
            )
        return sorted(extracted)


#: Python codec equivalent of the ``windows-31j`` Java charset used by the
#: Spark reader (the TSOs serve Shift_JIS with Windows extensions).
_SNIFF_ENCODING = "cp932"

#: Number of physical columns in every actuals data row.
COLUMN_COUNT = 7

#: Contract ``source`` name for the file update timestamp injected by the loader.
FILE_UPDATED_AT_SOURCE = "__file_updated_at"

#: A data row starts with a date — ``yyyymmdd`` (TEPCO, Kansai until
#: 2025-12-24) or ``yyyy/mm/dd`` (Kansai from 2025-12-25) — and a time code.
_DATE_RE = r"(\d{8}|\d{4}/\d{2}/\d{2})"
_META_LINE_RE = re.compile(rf"^{_DATE_RE},(\d{{1,2}}):(\d{{2}}):(\d{{2}}),{_DATE_RE},*$")

#: The column-header line is within the first few lines: TEPCO and Kansai's
#: current layout put it on line 3, Kansai's older layout (extra title line)
#: on line 4.
_MAX_HEADER_LINES = 4


def sniff_metadata(file: Path | str, accepted_headers: frozenset[str]) -> str:
    """Check a daily actuals file's header lines and return its update timestamp.

    Parameters
    ----------
    file : pathlib.Path or str
        Path to a daily actuals CSV (CP932).
    accepted_headers : frozenset of str
        Column-header lines the file may use.

    Returns
    -------
    str
        ``"yyyyMMdd HH:mm:ss"`` — ファイル更新日 and ファイル更新時間 from the
        metadata line preceding the column header, date slashes removed and
        the hour zero-padded, for the load contract to parse.

    Raises
    ------
    ValueError
        If none of the first lines is an accepted column header, or the line
        before it is not ``<date>,<HH:MM:SS>,<date>``.
    """
    with open(file, encoding=_SNIFF_ENCODING) as f:
        lines = [f.readline().rstrip("\r\n") for _ in range(_MAX_HEADER_LINES)]
    header_index = next((i for i, line in enumerate(lines) if line in accepted_headers), None)
    if header_index is None or header_index == 0:
        raise ValueError(
            f"{file}: no accepted column header in the first {_MAX_HEADER_LINES} lines "
            f"{lines!r} — expected one of {sorted(accepted_headers)!r} preceded by a "
            "metadata line (did the TSO change the layout?)"
        )
    meta = lines[header_index - 1]
    match = _META_LINE_RE.match(meta)
    if match is None:
        raise ValueError(f"{file}: cannot parse the update timestamp from {meta!r}")
    date, hour, minute, second, _target = match.groups()
    return f"{date.replace('/', '')} {int(hour):02d}:{minute}:{second}"


class AreaActualsCsvLoader(CsvLoader):
    """Positional full reload of daily area-actuals CSVs into a warehouse table.

    Works exactly like :class:`~power_market_analytics.csv_loader.CsvLoader`
    (same validation and write behaviour) except for how each file is read:
    the files open with metadata lines before the real column header, so they
    are read headerless and the load contract addresses columns positionally
    (``source: _c0`` .. ``_c6``) plus ``__file_updated_at`` for the update
    timestamp taken from the metadata line. Only data rows (a date followed
    by a numeric time code) are kept, and ``yyyy/mm/dd`` dates are normalised
    to ``yyyymmdd`` so one contract format serves every layout. Each file's
    column-header line must be one of the source's ``accepted_headers``.

    Parameters
    ----------
    schema, filepath, table, spark
        As for :class:`CsvLoader`.
    source : AreaActualsSource, optional
        TSO spec supplying ``accepted_headers``. Subclasses may fix it via the
        ``source`` class attribute instead.
    """

    #: Default source for subclasses (e.g. ``TepcoAreaCsvLoader.source = TEPCO``).
    source: AreaActualsSource | None = None

    def __init__(
        self,
        schema: CsvTableSchema,
        filepath: Path | str,
        table: str,
        spark: SparkSession | None = None,
        source: AreaActualsSource | None = None,
    ) -> None:
        resolved = source if source is not None else type(self).source
        if resolved is None:
            raise ValueError("AreaActualsCsvLoader needs a source (argument or class attribute)")
        self.source = resolved
        super().__init__(schema=schema, filepath=filepath, table=table, spark=spark)

    def _read_file(self, file: str) -> DataFrame:
        file_updated_at = sniff_metadata(file, self.source.accepted_headers)
        spark_schema = StructType(
            [StructField(f"_c{i}", StringType()) for i in range(COLUMN_COUNT)]
        )
        raw = (
            self.spark.read.options(**self.schema.read_options)
            .schema(spark_schema)
            .csv(file)
            # Data rows start with a date AND a numeric time code; the metadata
            # value line also starts with a date, so both conditions are needed.
            .filter(F.col("_c0").rlike(f"^{_DATE_RE}$") & F.col("_c1").rlike(r"^\d{1,2}$"))
            .withColumn("_c0", F.regexp_replace(F.col("_c0"), "/", ""))
            .withColumn(FILE_UPDATED_AT_SOURCE, F.lit(file_updated_at))
        )
        return raw.select([self._cast(raw, c) for c in self.schema.columns])
