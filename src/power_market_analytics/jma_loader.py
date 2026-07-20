"""Loader for JMA hourly observation CSVs into raw warehouse tables.

JMA hourly CSVs (format details in ``docs/JMA-Weather-Data-Retrieval.md``
§7) cannot go through the generic header-name mapping of
:class:`~power_market_analytics.csv_loader.CsvLoader`: they open with a
download-timestamp line, a blank line and multiple header rows whose labels
repeat per element (e.g. ``気温(℃)`` three times), and the station id
appears only in the file name. :class:`JmaHourlyCsvLoader` therefore reads
files headerless — the load contract addresses columns positionally via
``source: _c0``, ``_c1``, … — keeps only data rows (first field is a
timestamp), and injects a ``station_id`` column parsed from the file name
(contract ``source: __station_id``).

Because the column count is the only thing distinguishing the AMeDAS layout
from the staffed layout, each file's count is checked against the contract
before reading; a mismatch fails the load rather than silently truncating.
"""

from __future__ import annotations

import re

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

from power_market_analytics.csv_loader import CsvLoader

#: Python codec equivalent of the ``windows-31j`` Java charset used by the
#: Spark reader (JMA serves Shift_JIS with Windows extensions).
_SNIFF_ENCODING = "cp932"


class JmaHourlyCsvLoader(CsvLoader):
    """Positional full reload of JMA hourly CSVs into a warehouse table.

    Works exactly like :class:`CsvLoader` (same constructor, validation and
    write behavior) except for how each file is read; see the module
    docstring. The contract's ``source`` fields must be ``_c<n>`` positions
    plus ``__station_id`` for the injected station id.
    """

    #: Contract ``source`` name for the station id parsed from the file name.
    STATION_ID_SOURCE = "__station_id"

    _FILENAME_RE = re.compile(r"([sa]\d+)_[\d-]+_\d{4}\.csv$")
    _DATA_ROW_PATTERN = r"^\d{4}/"

    def _read_file(self, file: str) -> DataFrame:
        expected = self._expected_column_count()
        actual = self._sniff_column_count(file)
        if actual != expected:
            raise ValueError(
                f"{file}: first data row has {actual} columns, contract "
                f"expects {expected} — file does not match this format "
                "(wrong station class or JMA changed the layout)"
            )
        match = self._FILENAME_RE.search(file)
        if match is None:
            raise ValueError(
                f"{file}: cannot parse a station id from the file name"
            )

        spark_schema = StructType(
            [StructField(f"_c{i}", StringType()) for i in range(expected)]
        )
        raw = (
            self.spark.read.options(**self.schema.read_options)
            .schema(spark_schema)
            .csv(file)
            .filter(F.col("_c0").rlike(self._DATA_ROW_PATTERN))
            .withColumn(self.STATION_ID_SOURCE, F.lit(match.group(1)))
        )
        return raw.select([self._cast(raw, c) for c in self.schema.columns])

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
