"""Download JEPX (Japan Electric Power Exchange) market data.

JEPX publishes historical day-ahead (spot) market results as one CSV file
per Japanese fiscal year at ``https://www.jepx.jp/market/excel/spot_{fy}.csv``.
Files are encoded in Shift_JIS (cp932) and contain one row per 30-minute
delivery period (time codes 1-48), with bid/ask volumes, contracted volume,
the system price, and the ten area prices.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


def current_fiscal_year(today: datetime.date | None = None) -> int:
    """Return the Japanese fiscal year containing ``today``.

    The Japanese fiscal year N runs from April 1 of year N through
    March 31 of year N + 1.

    Parameters
    ----------
    today : datetime.date, optional
        Date to evaluate. Defaults to the current local date.

    Returns
    -------
    int
        The fiscal year containing ``today``.
    """
    if today is None:
        today = datetime.date.today()
    return today.year if today.month >= 4 else today.year - 1


class JepxSpotDownloader:
    """Download JEPX spot (day-ahead) price CSV files by fiscal year.

    Downloads are cached: if the file for a fiscal year already exists in
    ``data_dir``, it is not re-downloaded unless ``force=True``. Note that
    JEPX appends rows to the current fiscal year's file daily, so pass
    ``force=True`` to refresh it.

    Parameters
    ----------
    data_dir : pathlib.Path or str, default ``"data/jepx/spot"``
        Directory where downloaded CSV files are stored. Created on first
        download if it does not exist.
    timeout : float, default 60.0
        HTTP request timeout in seconds.

    Examples
    --------
    >>> downloader = JepxSpotDownloader()
    >>> path = downloader.download(2024)
    >>> path
    PosixPath('data/jepx/spot/spot_2024.csv')
    """

    URL_TEMPLATE = "https://www.jepx.jp/market/excel/spot_{fiscal_year}.csv"

    #: Earliest fiscal year hosted on jepx.jp (older years are not published).
    EARLIEST_FISCAL_YEAR = 2016

    #: Encoding of the CSV files served by JEPX.
    ENCODING = "cp932"

    def __init__(
        self,
        data_dir: Path | str = Path("data/jepx/spot"),
        timeout: float = 60.0,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.timeout = timeout

    def path_for(self, fiscal_year: int) -> Path:
        """Return the local path where the fiscal year's CSV is stored.

        Parameters
        ----------
        fiscal_year : int
            Japanese fiscal year (April 1 through March 31 of the next
            calendar year).

        Returns
        -------
        pathlib.Path
            Path to the (possibly not yet downloaded) CSV file.
        """
        return self.data_dir / f"spot_{fiscal_year}.csv"

    def download(self, fiscal_year: int, force: bool = False) -> Path:
        """Download the spot price CSV for a fiscal year into ``data_dir``.

        Parameters
        ----------
        fiscal_year : int
            Japanese fiscal year to download, e.g. ``2024`` for
            2024-04-01 through 2025-03-31.
        force : bool, default False
            Re-download even if the file already exists locally.

        Returns
        -------
        pathlib.Path
            Path to the downloaded (or cached) CSV file.

        Raises
        ------
        ValueError
            If ``fiscal_year`` is outside the published range
            (``EARLIEST_FISCAL_YEAR`` through the current fiscal year).
        requests.HTTPError
            If JEPX responds with an unexpected error status.
        """
        self._validate_fiscal_year(fiscal_year)
        dest = self.path_for(fiscal_year)
        if dest.exists() and not force:
            logger.info("Using cached JEPX spot file: %s", dest)
            return dest

        url = self.URL_TEMPLATE.format(fiscal_year=fiscal_year)
        logger.info("Downloading %s -> %s", url, dest)
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()

        self.data_dir.mkdir(parents=True, exist_ok=True)
        # Write to a temp file and rename so an interrupted download never
        # leaves a truncated file at the cached path.
        partial = dest.with_name(dest.name + ".part")
        partial.write_bytes(response.content)
        partial.replace(dest)
        logger.info("Saved %s (%d bytes)", dest, dest.stat().st_size)
        return dest

    def _validate_fiscal_year(self, fiscal_year: int) -> None:
        latest = current_fiscal_year()
        if not self.EARLIEST_FISCAL_YEAR <= fiscal_year <= latest:
            raise ValueError(
                f"fiscal_year must be between {self.EARLIEST_FISCAL_YEAR} and "
                f"{latest} (got {fiscal_year}); JEPX does not publish spot "
                f"CSVs outside this range."
            )
