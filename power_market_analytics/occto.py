"""Download OCCTO public disclosure data via the 情報ダウンロード bulk endpoint.

OCCTO's public portal (``https://occtonet3.occto.or.jp/public/dfw/RP11/OCCTO/SD/``)
serves whole-dataset CSV files from its 情報ダウンロード screen (``CF01S010C``)
through a three-request handshake: an anonymous session, a ``reference/ok``
AJAX call that issues a single-use ``downloadKey`` + ``requestToken`` pair, and
a ``reference/download`` POST that returns the CSV (Shift_JIS). The protocol
and dataset catalog are documented in docs/OCCTO-Demand-Forecast-Retrieval.md.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import requests
from loguru import logger

BASE_URL = "https://occtonet3.occto.or.jp/public/dfw/RP11/OCCTO/SD"

#: Screen id of the 情報ダウンロード bulk-download screen.
DOWNLOAD_SCREEN = "CF01S010C"

#: ``areaDataKnd`` radio values for the エリア・広域ブロック情報 tab (``tabSntk=1``).
AREA_DATASETS = {
    "demand_forecast_dad": "32",  # 需要予想・ピーク時供給力 翌々日 (day-after-next)
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


class OcctoBulkDownloader:
    """Download a whole dataset from OCCTO's 情報ダウンロード screen as one CSV.

    Each call performs the full three-request handshake with a fresh anonymous
    session. The full-history file for the day-after-next demand forecast is
    ~700 KB, so callers are expected to simply re-download it on every refresh
    rather than manage incremental pulls.

    Parameters
    ----------
    data_dir : pathlib.Path or str, default ``"data/occto"``
        Directory where downloaded CSV files are stored; one subdirectory per
        dataset (``data/occto/demand_forecast_dad/``). Created on first
        download if it does not exist.
    timeout : float, default 120.0
        HTTP request timeout in seconds. The download step assembles the CSV
        server-side and can take a while for the full history.

    Examples
    --------
    >>> downloader = OcctoBulkDownloader()
    >>> path = downloader.download("demand_forecast_dad")
    >>> path
    PosixPath('data/occto/demand_forecast_dad/demand_forecast_dad.csv')
    """

    #: Encoding of the CSV files served by OCCTO.
    ENCODING = "cp932"

    #: Expected header row of the day-after-next demand forecast CSV; used to
    #: verify that the download response is the CSV and not an error page.
    DEMAND_FORECAST_DAD_HEADER = (
        "策定日,対象日付,対象エリア,最小総需要予想時刻,最小総需要予想（MW）,"
        "最大総需要予想時刻,最大総需要予想（MW）,最大供給力予想（MW）,予想使用率,予想予備率"
    )

    def __init__(
        self,
        data_dir: Path | str = Path("data/occto"),
        timeout: float = 120.0,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.timeout = timeout

    def path_for(self, dataset: str) -> Path:
        """Return the local path where the dataset's CSV is stored.

        Parameters
        ----------
        dataset : str
            Dataset key, one of ``AREA_DATASETS``.

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
            Dataset key, one of ``AREA_DATASETS``.
        target_date_from, target_date_to : datetime.date, optional
            Restrict the download to target dates (対象日付) in this closed
            range. Both must be given together; when omitted the entire
            available history is downloaded (すべての期間).

        Returns
        -------
        pathlib.Path
            Path to the downloaded CSV file.

        Raises
        ------
        ValueError
            If ``dataset`` is unknown or only one bound of the date range is
            given.
        OcctoDownloadError
            If the portal returns an error page or a validation error
            instead of the CSV.
        requests.HTTPError
            If the portal responds with an unexpected HTTP error status.
        """
        if dataset not in AREA_DATASETS:
            raise ValueError(
                f"Unknown dataset {dataset!r}; expected one of {sorted(AREA_DATASETS)}"
            )
        if (target_date_from is None) != (target_date_to is None):
            raise ValueError("target_date_from and target_date_to must be given together")

        selection = self._selection(dataset, target_date_from, target_date_to)
        dest = self.path_for(dataset)
        logger.info("Downloading OCCTO {} -> {}", dataset, dest)

        with requests.Session() as session:
            self._open_session(session)
            download_key, request_token = self._issue_download_key(session, selection)
            content = self._fetch_csv(session, selection, download_key, request_token)

        self._verify_csv(dataset, content)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file and rename so an interrupted download never
        # leaves a truncated file at the cached path.
        partial = dest.with_name(dest.name + ".part")
        partial.write_bytes(content)
        partial.replace(dest)
        logger.info("Saved {} ({} bytes)", dest, dest.stat().st_size)
        return dest

    # -- protocol steps -----------------------------------------------------

    def _open_session(self, session: requests.Session) -> None:
        response = session.get(f"{BASE_URL}/LOGIN_login", timeout=self.timeout)
        response.raise_for_status()
        if "JSESSIONID" not in session.cookies:
            raise OcctoDownloadError("OCCTO did not issue a session cookie on LOGIN_login")

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
            root = response.json()["root"]
        except (ValueError, KeyError) as exc:
            raise OcctoDownloadError(
                f"Unexpected reference/ok response: {response.text[:200]!r}"
            ) from exc
        if root.get("interceptorErr"):
            raise OcctoDownloadError(f"OCCTO session error: {root['interceptorErr']}")
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
            raise OcctoDownloadError(
                "reference/download did not return an attachment "
                f"(Content-Type={response.headers.get('Content-Type')!r}); "
                f"body starts {response.content[:120]!r}"
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
        dataset: str,
        target_date_from: datetime.date | None,
        target_date_to: datetime.date | None,
    ) -> dict[str, str]:
        selection = {
            "tabSntk": "1",
            "areaDataKnd": AREA_DATASETS[dataset],
            "allAreaSectDwld": "11",
            **AREA_CHECKBOXES,
        }
        if target_date_from is None:
            selection["areaAllTermDwld"] = "Y"
        else:
            selection["areaNngpFrom"] = target_date_from.strftime("%Y/%m/%d")
            selection["areaNngpTo"] = target_date_to.strftime("%Y/%m/%d")
        return selection

    def _verify_csv(self, dataset: str, content: bytes) -> None:
        try:
            first_line = content.decode(self.ENCODING).splitlines()[0]
        except UnicodeDecodeError as exc:
            raise OcctoDownloadError(f"Downloaded {dataset} is not {self.ENCODING} text") from exc
        except IndexError as exc:
            raise OcctoDownloadError(f"Downloaded {dataset} is empty") from exc
        expected = self.DEMAND_FORECAST_DAD_HEADER
        if first_line != expected:
            raise OcctoDownloadError(
                f"Downloaded {dataset} has an unexpected header row: {first_line[:200]!r}"
            )
