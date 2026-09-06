"""Download OCCTO public disclosure data via the 情報ダウンロード bulk endpoint.

OCCTO's public portal (``https://occtonet3.occto.or.jp/public/dfw/RP11/OCCTO/SD/``)
serves whole-dataset CSV files from its 情報ダウンロード screen (``CF01S010C``)
through a three-request handshake: an anonymous session, a ``reference/ok``
AJAX call that issues a single-use ``downloadKey`` + ``requestToken`` pair, and
a ``reference/download`` POST that returns the CSV (Shift_JIS). The protocol
and dataset catalog are documented in docs/OCCTO-Demand-Forecast-Retrieval.md.

The screen caps a single download at 150,000 rows. Datasets whose full history
exceeds that (the 30-minute 広域予備率 series) are declared with a
``max_days_per_download`` and are fetched as consecutive target-date windows
that are concatenated into one local CSV, so callers see the same
"one file = whole history" contract for every dataset.
"""

from __future__ import annotations

import dataclasses
import datetime
import html
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import requests
from loguru import logger

BASE_URL = "https://occtonet3.occto.or.jp/public/dfw/RP11/OCCTO/SD"

#: Screen id of the 情報ダウンロード bulk-download screen.
DOWNLOAD_SCREEN = "CF01S010C"

#: The portal's clock; publication dates and "today" are JST.
JST = ZoneInfo("Asia/Tokyo")

#: Maximum rows the screen serves in one download (error CF000010SW beyond it).
MAX_ROWS_PER_DOWNLOAD = 150_000


@dataclasses.dataclass(frozen=True)
class OcctoDataset:
    """One dataset on the エリア・広域ブロック情報 tab of the bulk-download screen.

    Attributes
    ----------
    key : str
        Local dataset key (also the subdirectory / file stem under ``data_dir``).
    area_data_knd : str
        The screen's ``areaDataKnd`` radio value selecting the dataset.
    header : str
        Expected first line of the CSV; used to verify a download response is
        the dataset and not an HTML error page.
    history_start : datetime.date, optional
        First published target date. Required when ``max_days_per_download``
        is set (it anchors the download windows).
    max_days_per_download : int, optional
        If set, the dataset is too large for one download and is fetched in
        consecutive target-date windows of at most this many days, from
        ``history_start`` through the last possible target date. ``None``
        means one すべての期間 (all-term) download.
    """

    key: str
    area_data_knd: str
    header: str
    history_start: datetime.date | None = None
    max_days_per_download: int | None = None

    def __post_init__(self) -> None:
        if self.max_days_per_download is not None:
            if self.max_days_per_download < 1:
                raise ValueError(f"{self.key}: max_days_per_download must be >= 1")
            if self.history_start is None:
                raise ValueError(f"{self.key}: history_start is required when chunking")


#: Datasets known to the downloader, by key. Radio values and history from
#: docs/OCCTO-Demand-Forecast-Retrieval.md §5.
DATASETS: dict[str, OcctoDataset] = {
    dataset.key: dataset
    for dataset in (
        # 需要予想・ピーク時供給力 翌々日 (day-after-next), 12 rows/day → one download.
        OcctoDataset(
            key="demand_forecast_dad",
            area_data_knd="32",
            header=(
                "策定日,対象日付,対象エリア,最小総需要予想時刻,最小総需要予想（MW）,"
                "最大総需要予想時刻,最大総需要予想（MW）,最大供給力予想（MW）,予想使用率,予想予備率"
            ),
        ),
        # 広域予備率 エリア・広域ブロック情報 翌々日: 48 half-hours × 10 areas =
        # 480 rows/day, so the 150,000-row cap allows at most 312 days per
        # download; 300-day windows from the series start (2025-04-01).
        OcctoDataset(
            key="area_reserve_rate_dad",
            area_data_knd="31",
            header=(
                "対象年月日,区分,時刻,エリア,広域予備率(%),広域使用率(%),ブロックNo.,"
                "広域ブロック需要(MW),広域ブロック供給力(MW),広域ブロック予備力(MW),"
                "エリア需要(MW),エリア供給力(MW),エリア予備力(MW)"
            ),
            history_start=datetime.date(2025, 4, 1),
            max_days_per_download=300,
        ),
    )
}

#: Area checkbox names and their values on the bulk-download screen.
AREA_CHECKBOXES = {
    "hkd": "01",
    "thk": "02",
    "tko": "03",
    "chb": "04",
    "hkr": "05",
    "kns": "06",
    "cgk": "07",
    "skk": "08",
    "kys": "09",
    "oki": "10",
    "areaSum": "11",
}


class OcctoDownloadError(RuntimeError):
    """Raised when the OCCTO portal returns something other than the CSV."""


class OcctoTransientError(OcctoDownloadError):
    """The portal failed to serve the request rather than rejecting it.

    Raised for the portal's HTTP-200 error screen (不正なリクエストです, the
    session-timeout page) and the session-timeout JSON — answers that a fresh
    session and key/token pair are expected to get past, unlike a validation
    ``errMessage`` or a header mismatch.
    """


def _strip_tags(markup: str) -> str:
    """Return the visible text of an HTML fragment, whitespace collapsed."""
    markup = re.sub(r"<!--.*?-->", " ", markup, flags=re.S)
    markup = re.sub(r"<(script|style)\b.*?</\1\s*>", " ", markup, flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    return " ".join(text.split())


def _error_page_message(content: bytes) -> str:
    """Return the message a portal error page shows the user.

    The framework's error screen puts its message in the first ``<p>`` of the
    body (repeated in a hidden table); a page without one is reduced to its
    visible text, and a body without any to its first bytes. Error pages are
    UTF-8 (the CSV is CP932). At most 200 characters, for exception messages
    and logs.
    """
    text = content.decode("utf-8", errors="replace")
    paragraphs = re.findall(r"<p\b[^>]*>(.*?)</p\s*>", text, flags=re.S | re.I)
    message = next((p for p in map(_strip_tags, paragraphs) if p), None) or _strip_tags(text)
    return message[:200] or repr(content[:120])


class OcctoBulkDownloader:
    """Download a whole dataset from OCCTO's 情報ダウンロード screen as one CSV.

    Each call opens a fresh anonymous session and performs the
    ``reference/ok`` → ``reference/download`` handshake once per download
    window (once for all-term datasets, once per ``max_days_per_download``
    window for chunked ones); the windows are concatenated into a single
    local CSV. Files are small (~700 KB for the demand forecast, ~20 MB/year
    for the half-hourly reserve-rate series), so callers are expected to
    simply re-download on every refresh rather than manage incremental pulls.

    A window the portal fails to serve — its HTTP-200 error screen
    (不正なリクエストです, the session-timeout page), the session-timeout JSON,
    a login without a session cookie, or an HTTP 5xx at any step — is retried
    up to ``max_attempts`` times. An attempt is the login (first window, and
    every retry), the ``ok`` and the ``download``; a failure at any of the
    three consumes it. Every retry waits ``retry_wait`` seconds and starts
    from a fresh anonymous session with a fresh key/token pair (a used pair
    is one-shot). Answers that reject the request itself — a validation
    ``errMessage``, an HTTP 4xx, a CSV with the wrong header — are raised at
    once. The 2026-09-06 refresh failed on one error screen that no rerun
    could reproduce.

    Parameters
    ----------
    data_dir : pathlib.Path or str, default ``"data/occto"``
        Directory where downloaded CSV files are stored; one subdirectory per
        dataset (``data/occto/demand_forecast_dad/``). Created on first
        download if it does not exist.
    timeout : float, default 120.0
        HTTP request timeout in seconds. The download step assembles the CSV
        server-side and can take a while for large windows.
    session_factory : callable, default :class:`requests.Session`
        Zero-argument callable returning a fresh session (used as a context
        manager) for each :meth:`download` call. Injected mainly for tests.
    max_attempts : int, default 3
        Attempts per window before a transient failure is raised.
    retry_wait : float, default 5.0
        Seconds to wait before each retry.

    Examples
    --------
    >>> downloader = OcctoBulkDownloader()
    >>> path = downloader.download("demand_forecast_dad")
    >>> path
    PosixPath('data/occto/demand_forecast_dad/demand_forecast_dad.csv')
    """

    #: Encoding of the CSV files served by OCCTO.
    ENCODING = "cp932"

    def __init__(
        self,
        data_dir: Path | str = Path("data/occto"),
        timeout: float = 120.0,
        session_factory: Callable[[], requests.Session] = requests.Session,
        max_attempts: int = 3,
        retry_wait: float = 5.0,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.data_dir = Path(data_dir)
        self.timeout = timeout
        self.session_factory = session_factory
        self.max_attempts = max_attempts
        self.retry_wait = retry_wait

    def path_for(self, dataset: str) -> Path:
        """Return the local path where the dataset's CSV is stored.

        Parameters
        ----------
        dataset : str
            Dataset key, one of ``DATASETS``.

        Returns
        -------
        pathlib.Path
            Path to the (possibly not yet downloaded) CSV file.
        """
        return self.data_dir / dataset / f"{dataset}.csv"

    def download(
        self,
        dataset: str,
        target_date_from: datetime.date | None = None,
        target_date_to: datetime.date | None = None,
    ) -> Path:
        """Download a dataset's CSV into ``data_dir`` (always re-downloads).

        Parameters
        ----------
        dataset : str
            Dataset key, one of ``DATASETS``.
        target_date_from, target_date_to : datetime.date, optional
            Restrict the download to target dates (対象日付) in this closed
            range. Both must be given together. When omitted, all-term
            datasets are downloaded with すべての期間 and chunked datasets from
            their ``history_start`` through today + 2 (JST) — the furthest
            target date a 翌々日 series can hold. Either way a chunked
            dataset's range is split into ``max_days_per_download`` windows.

        Returns
        -------
        pathlib.Path
            Path to the downloaded CSV file.

        Raises
        ------
        ValueError
            If ``dataset`` is unknown, only one bound of the date range is
            given, or the range is inverted.
        OcctoDownloadError
            If the portal rejects the selection or a window's header does not
            match; :class:`OcctoTransientError` if it still fails to serve a
            window after ``max_attempts`` attempts.
        requests.HTTPError
            If the portal responds with an HTTP 4xx, or with a 5xx on every
            attempt.
        """
        try:
            spec = DATASETS[dataset]
        except KeyError:
            raise ValueError(
                f"Unknown dataset {dataset!r}; expected one of {sorted(DATASETS)}"
            ) from None
        if (target_date_from is None) != (target_date_to is None):
            raise ValueError("target_date_from and target_date_to must be given together")
        if (
            target_date_from is not None
            and target_date_to is not None
            and target_date_from > target_date_to
        ):
            raise ValueError("target_date_from must not be after target_date_to")

        windows = self._windows(spec, target_date_from, target_date_to)
        dest = self.path_for(dataset)
        logger.info("Downloading OCCTO {} in {} window(s) -> {}", dataset, len(windows), dest)

        chunks: list[bytes] = []
        with self.session_factory() as session:
            for index, (window_from, window_to) in enumerate(windows):
                content = self._fetch_window(
                    session, spec, window_from, window_to, login=index == 0
                )
                logger.info(
                    "Fetched {} window {}..{} ({} bytes)",
                    dataset,
                    window_from or "all",
                    window_to or "all",
                    len(content),
                )
                chunks.append(content)

        dest.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file and rename so an interrupted download never
        # leaves a truncated file at the cached path.
        partial = dest.with_name(dest.name + ".part")
        partial.write_bytes(self._concatenate(chunks))
        partial.replace(dest)
        logger.info("Saved {} ({} bytes)", dest, dest.stat().st_size)
        return dest

    # -- windows ------------------------------------------------------------

    @staticmethod
    def _windows(
        spec: OcctoDataset,
        target_date_from: datetime.date | None,
        target_date_to: datetime.date | None,
    ) -> list[tuple[datetime.date | None, datetime.date | None]]:
        """Split the requested range into per-download windows.

        Returns ``[(None, None)]`` for a single all-term download.
        """
        if spec.max_days_per_download is None:
            return [(target_date_from, target_date_to)]
        if target_date_from is None or target_date_to is None:
            # OcctoDataset.__post_init__ guarantees history_start for
            # chunked datasets.
            target_date_from = cast(datetime.date, spec.history_start)
            # The 翌々日 series is published for D+2 on day D (~17:45 JST), so
            # today + 2 is the furthest target date that can exist. Asking past
            # the last published day is harmless: the portal returns the rows
            # that exist (a fully-future window comes back header-only).
            target_date_to = datetime.datetime.now(JST).date() + datetime.timedelta(days=2)
        step = datetime.timedelta(days=spec.max_days_per_download)
        windows: list[tuple[datetime.date | None, datetime.date | None]] = []
        window_from = target_date_from
        while window_from <= target_date_to:
            window_to = min(window_from + step - datetime.timedelta(days=1), target_date_to)
            windows.append((window_from, window_to))
            window_from = window_to + datetime.timedelta(days=1)
        return windows

    @staticmethod
    def _concatenate(chunks: list[bytes]) -> bytes:
        """Join window CSVs into one file: keep the first header, drop the rest."""
        parts = []
        for i, chunk in enumerate(chunks):
            if i > 0:
                # Every chunk has passed _verify_csv, so the header is the
                # first line (a header-only window may lack the trailing LF).
                newline = chunk.find(b"\n")
                chunk = chunk[newline + 1 :] if newline >= 0 else b""
            if chunk and not chunk.endswith(b"\n"):
                chunk += b"\n"
            parts.append(chunk)
        return b"".join(parts)

    # -- protocol steps -----------------------------------------------------

    def _fetch_window(
        self,
        session: requests.Session,
        spec: OcctoDataset,
        window_from: datetime.date | None,
        window_to: datetime.date | None,
        login: bool,
    ) -> bytes:
        """Fetch one window, retrying attempts the portal fails to serve.

        An attempt is the ``LOGIN_login`` GET (when ``login`` — the first
        window, and every retry), then the ``ok`` → ``download`` handshake;
        a transient failure at any of the three steps consumes the attempt.
        Every retry waits ``retry_wait`` seconds and starts from a cleared
        cookie jar, so the portal issues a fresh anonymous session and a fresh
        key/token pair.
        """
        selection = self._selection(spec, window_from, window_to)
        attempt = 1
        while True:
            try:
                if login:
                    self._open_session(session)
                download_key, request_token = self._issue_download_key(session, selection)
                content = self._fetch_csv(session, selection, download_key, request_token)
            except (OcctoTransientError, requests.HTTPError) as exc:
                if attempt >= self.max_attempts or not self._is_transient(exc):
                    raise
                logger.warning(
                    "OCCTO {} window {}..{} attempt {}/{} failed: {}; "
                    "retrying in {} s with a fresh session",
                    spec.key,
                    window_from or "all",
                    window_to or "all",
                    attempt,
                    self.max_attempts,
                    exc,
                    self.retry_wait,
                )
                time.sleep(self.retry_wait)
                session.cookies.clear()
                login = True
                attempt += 1
                continue
            self._verify_csv(spec, content)
            return content

    @staticmethod
    def _is_transient(exc: OcctoTransientError | requests.HTTPError) -> bool:
        """Whether a fresh session and key pair are expected to get past ``exc``."""
        if isinstance(exc, OcctoTransientError):
            return True
        return exc.response is not None and exc.response.status_code >= 500

    def _open_session(self, session: requests.Session) -> None:
        response = session.get(f"{BASE_URL}/LOGIN_login", timeout=self.timeout)
        response.raise_for_status()
        if "JSESSIONID" not in session.cookies:
            raise OcctoTransientError("OCCTO did not issue a session cookie on LOGIN_login")

    def _issue_download_key(
        self, session: requests.Session, selection: dict[str, str]
    ) -> tuple[str, str]:
        data = {
            **self._framework_fields("ok"),
            "requestToken": "",
            "downloadKey": "",
            **selection,
        }
        response = session.post(
            f"{BASE_URL}/{DOWNLOAD_SCREEN}",
            data=data,
            headers={"sdReqType": "AJAX"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError:
            raise OcctoTransientError(
                "reference/ok answered with the portal's error screen "
                f"(Content-Type={response.headers.get('Content-Type')!r}): "
                f"{_error_page_message(response.content)}"
            ) from None
        try:
            root = payload["root"]
        except KeyError as exc:
            raise OcctoDownloadError(
                f"Unexpected reference/ok response: {response.text[:200]!r}"
            ) from exc
        if root.get("interceptorErr"):
            raise OcctoTransientError(f"OCCTO session error: {root['interceptorErr']}")
        if root.get("errMessage"):
            raise OcctoDownloadError(f"OCCTO rejected the selection: {root['errMessage']}")
        header = (root.get("bizRoot") or {}).get("header") or {}
        try:
            return header["downloadKey"]["value"], header["requestToken"]["value"]
        except KeyError as exc:
            raise OcctoDownloadError(
                f"reference/ok response lacks downloadKey/requestToken: {response.text[:200]!r}"
            ) from exc

    def _fetch_csv(
        self,
        session: requests.Session,
        selection: dict[str, str],
        download_key: str,
        request_token: str,
    ) -> bytes:
        data = {
            **self._framework_fields("download"),
            "requestToken": request_token,
            "downloadKey": download_key,
            **selection,
        }
        response = session.post(f"{BASE_URL}/{DOWNLOAD_SCREEN}", data=data, timeout=self.timeout)
        response.raise_for_status()
        if "attachment" not in response.headers.get("Content-Disposition", ""):
            raise OcctoTransientError(
                "reference/download answered with the portal's error screen "
                f"(Content-Type={response.headers.get('Content-Type')!r}): "
                f"{_error_page_message(response.content)}"
            )
        return response.content

    # -- request payloads ---------------------------------------------------

    @staticmethod
    def _framework_fields(sub_type: str) -> dict[str, str]:
        return {
            "fwExtention.actionType": "reference",
            "fwExtention.actionSubType": sub_type,
            "fwExtention.pagingTargetTable": "",
            "fwExtention.pathInfo": DOWNLOAD_SCREEN,
            "fwExtention.prgbrh": "0",
            "fwExtention.formId": DOWNLOAD_SCREEN[:-1] + "P",
            "fwExtention.jsonString": "",
            "ajaxToken": "",
            "requestTokenBk": "",
            "transitionContextKey": "DEFAULT",
        }

    @staticmethod
    def _selection(
        spec: OcctoDataset,
        target_date_from: datetime.date | None,
        target_date_to: datetime.date | None,
    ) -> dict[str, str]:
        selection = {
            "tabSntk": "1",
            "areaDataKnd": spec.area_data_knd,
            "allAreaSectDwld": "11",
            **AREA_CHECKBOXES,
        }
        if target_date_from is None or target_date_to is None:
            selection["areaAllTermDwld"] = "Y"
        else:
            selection["areaNngpFrom"] = target_date_from.strftime("%Y/%m/%d")
            selection["areaNngpTo"] = target_date_to.strftime("%Y/%m/%d")
        return selection

    def _verify_csv(self, spec: OcctoDataset, content: bytes) -> None:
        try:
            first_line = content.decode(self.ENCODING).splitlines()[0]
        except UnicodeDecodeError as exc:
            raise OcctoDownloadError(f"Downloaded {spec.key} is not {self.ENCODING} text") from exc
        except IndexError as exc:
            raise OcctoDownloadError(f"Downloaded {spec.key} is empty") from exc
        if first_line != spec.header:
            raise OcctoDownloadError(
                f"Downloaded {spec.key} has an unexpected header row: {first_line[:200]!r}"
            )
