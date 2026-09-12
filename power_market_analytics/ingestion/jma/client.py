"""Shared HTTP behavior for the JMA obsdl endpoints.

Both downloaders in this package talk to ``https://www.data.jma.go.jp/risk/obsdl/``
through form POSTs. The site asks users to avoid excessive automated access and
rate-limits bursts (HTTP 429), so every request goes through :class:`JmaDownloader`,
which spaces requests and retries with exponential backoff.
"""

from __future__ import annotations

import time

import requests
from loguru import logger


class JmaDownloader:
    """Shared HTTP behavior for the JMA obsdl endpoints.

    Consecutive requests are separated by ``request_interval`` seconds and
    retried on HTTP 429/5xx with exponential backoff, to respect JMA's
    request to avoid excessive automated access.

    Parameters
    ----------
    timeout : float, default 60.0
        HTTP request timeout in seconds.
    request_interval : float, default 5.0
        Minimum seconds between consecutive HTTP requests.
    max_retries : int, default 4
        Retries on HTTP 429/5xx before giving up. The n-th retry waits
        ``backoff_base * 2**n`` seconds.
    backoff_base : float, default 30.0
        Base wait in seconds for the exponential backoff.
    session : requests.Session, optional
        HTTP session to issue ``post`` calls with; defaults to a fresh
        :class:`requests.Session`. Injected mainly for tests.
    """

    _HEADERS = {
        "Referer": "https://www.data.jma.go.jp/risk/obsdl/index.php",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        ),
    }

    def __init__(
        self,
        timeout: float = 60.0,
        request_interval: float = 5.0,
        max_retries: int = 4,
        backoff_base: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.timeout = timeout
        self.request_interval = request_interval
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.session = session if session is not None else requests.Session()
        self._last_request_at = 0.0

    def _post_with_retry(self, url: str, payload: dict) -> requests.Response:
        """POST to a JMA endpoint, retrying on HTTP 429/5xx with backoff.

        Parameters
        ----------
        url : str
            Endpoint URL.
        payload : dict
            Form fields for the POST.

        Returns
        -------
        requests.Response
            The successful response.

        Raises
        ------
        requests.HTTPError
            If the final attempt still returns an error status.
        """
        attempt = 0
        while True:
            self._throttle()
            response = self.session.post(
                url, data=payload, headers=self._HEADERS, timeout=self.timeout
            )
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or attempt >= self.max_retries:
                response.raise_for_status()
                return response
            wait = self.backoff_base * 2**attempt
            logger.warning(
                "JMA returned HTTP {}; retrying in {:.0f} s (attempt {}/{})",
                response.status_code,
                wait,
                attempt + 1,
                self.max_retries,
            )
            time.sleep(wait)
            attempt += 1

    def _throttle(self) -> None:
        """Sleep so consecutive HTTP requests are ``request_interval`` apart."""
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.request_interval:
            time.sleep(self.request_interval - elapsed)
        self._last_request_at = time.monotonic()
