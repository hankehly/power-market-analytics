"""Positional full reload of the hourly CSVs into a raw warehouse table.

The files cannot go through the generic header-name mapping of
:class:`~power_market_analytics.ingestion.loader.CsvLoader`: they open with a
download-timestamp line, a blank line and multiple header rows whose labels
repeat per element (e.g. ``気温(℃)`` three times), and the station id appears
only in the file name. The loader therefore reads all files headerless in a
single Spark scan — the load contract addresses columns positionally via
``source: _c0``, ``_c1``, … — keeps only data rows (first field is a timestamp),
and injects a ``station_id`` column parsed from each row's file name (contract
``source: __station_id``).

Every file's column count and name are checked in Python before the scan; a
mismatch fails the load rather than silently truncating, guarding against JMA
layout drift (or a stale pre-re-scope file) rather than a station-class mixup.
"""

from __future__ import annotations

import re

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from power_market_analytics.ingestion.loader import SOURCE_FILE_COL, CsvLoader

#: Python codec equivalent of the ``windows-31j`` Java charset used by the
#: Spark reader (JMA serves Shift_JIS with Windows extensions).
_SNIFF_ENCODING = "cp932"


class JmaHourlyCsvLoader(CsvLoader):
    """Positional full reload of JMA hourly CSVs into a warehouse table.

    Works exactly like :class:`CsvLoader` (same constructor, validation and
    write behavior) except for how the files are read: every file is checked
    in Python first (column count against the contract, station id in the
    name), then all of them are read in one positional scan
    (:meth:`CsvLoader._scan_positional`) and the station id is taken from
    each row's file name — one frame per file unioned together made Spark
    re-analyse the plan 1,600 times and ship a 45 MiB task binary. The
    contract's ``source`` fields must be ``_c<n>`` positions plus
    ``__station_id`` for the injected station id.
    """

    #: Contract ``source`` name for the station id parsed from the file name.
    STATION_ID_SOURCE = "__station_id"

    _FILENAME_RE = re.compile(r"([sa]\d+)_[\d-]+_\d{4}\.csv$")
    _DATA_ROW_PATTERN = r"^\d{4}/"

    def _read_all(self, files: list[str]) -> DataFrame:
        expected = self._expected_column_count()
        for file in files:
            self._check_file(file, expected)
        raw = (
            self._scan_positional(files, expected)
            .filter(F.col("_c0").rlike(self._DATA_ROW_PATTERN))
            .withColumn(
                self.STATION_ID_SOURCE,
                F.regexp_extract(F.col(SOURCE_FILE_COL), self._FILENAME_RE.pattern, 1),
            )
        )
        return self._project(raw)

    def _check_file(self, file: str, expected: int) -> None:
        """Fail on a file the contract cannot read, before any Spark scan.

        Parameters
        ----------
        file : str
            Path to a JMA hourly CSV file.
        expected : int
            Physical column count implied by the contract.

        Raises
        ------
        ValueError
            If the first data row's column count differs from ``expected``
            (wrong station class or JMA changed the layout) or the file name
            carries no station id.
        """
        actual = self._sniff_column_count(file)
        if actual != expected:
            raise ValueError(
                f"{file}: first data row has {actual} columns, contract "
                f"expects {expected} — file does not match this format "
                "(wrong station class or JMA changed the layout)"
            )
        if self._FILENAME_RE.search(file) is None:
            raise ValueError(f"{file}: cannot parse a station id from the file name")

    def _expected_column_count(self) -> int:
        """Number of physical CSV columns implied by the contract.

        Returns
        -------
        int
            Highest ``_c<n>`` position referenced by the contract, plus one.
        """
        positions = [
            int(c.source_name[2:])
            for c in self.schema.columns
            if re.fullmatch(r"_c\d+", c.source_name)
        ]
        return max(positions) + 1

    @staticmethod
    def _sniff_column_count(file: str) -> int:
        """Count the columns of a file's first data row.

        JMA values never contain commas (wind directions are compass words,
        numbers are unquoted), so a plain comma count is exact.

        Parameters
        ----------
        file : str
            Path to a JMA hourly CSV file.

        Returns
        -------
        int
            Column count of the first row whose first field is a timestamp.

        Raises
        ------
        ValueError
            If the file contains no data rows.
        """
        with open(file, encoding=_SNIFF_ENCODING) as f:
            for line in f:
                if re.match(r"^\d{4}/", line):
                    return line.rstrip("\r\n").count(",") + 1
        raise ValueError(f"{file}: no data rows found")
