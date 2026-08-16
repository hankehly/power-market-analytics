"""Loader for TEPCO エリア需要・発電情報 actuals CSVs into a raw warehouse table.

Each ``AREA_JISEKI_YYYYMMDD.csv`` (format in
docs/TEPCO-Area-Demand-Generation-Retrieval.md) opens with two metadata lines
— the header ``ファイル更新日,ファイル更新時間,対象年月日`` and its values — before
the real column header, so the header-name mapping of
:class:`~power_market_analytics.csv_loader.CsvLoader` cannot be used
directly. :class:`TepcoAreaCsvLoader` therefore reads files headerless — the
load contract addresses columns positionally via ``source: _c0`` .. ``_c6`` —
keeps only data rows (a ``yyyymmdd`` date followed by a time code), and
injects the file's update timestamp from line 2 (contract
``source: __file_updated_at``, a ``yyyyMMdd HH:mm:ss`` string the contract
parses).

Before reading, each file's column-header line is compared with the expected
text so a layout change fails the load instead of silently mis-mapping
columns.
"""

from __future__ import annotations

import re

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

from power_market_analytics.csv_loader import CsvLoader

#: Python codec equivalent of the ``windows-31j`` Java charset used by the
#: Spark reader (TEPCO serves Shift_JIS with Windows extensions).
_SNIFF_ENCODING = "cp932"


class TepcoAreaCsvLoader(CsvLoader):
    """Positional full reload of TEPCO area actuals CSVs into a warehouse table.

    Works exactly like :class:`CsvLoader` (same constructor, validation and
    write behavior) except for how each file is read; see the module
    docstring. The contract's ``source`` fields must be ``_c<n>`` positions
    plus ``__file_updated_at`` for the injected update timestamp.
    """

    #: Contract ``source`` name for the file update timestamp parsed from line 2.
    FILE_UPDATED_AT_SOURCE = "__file_updated_at"

    #: Exact column-header line (line 3) of every actuals file since 2022-04.
    #: Note the full-width underscores in 時間帯＿自 / 時間帯＿至.
    EXPECTED_HEADER = (
        "日付,時間コマ,時間帯＿自,時間帯＿至,エリア総需要量,エリア総発電量,エリア風力・太陽光発電量"
    )

    #: Number of physical columns in the data rows.
    COLUMN_COUNT = 7

    _META_LINE_RE = re.compile(r"^(\d{8}),(\d{2}:\d{2}:\d{2}),(\d{8})")

    def _read_file(self, file: str) -> DataFrame:
        file_updated_at = self._sniff_metadata(file)
        spark_schema = StructType(
            [StructField(f"_c{i}", StringType()) for i in range(self.COLUMN_COUNT)]
        )
        raw = (
            self.spark.read.options(**self.schema.read_options)
            .schema(spark_schema)
            .csv(file)
            # Data rows start with a yyyymmdd date AND a numeric time code; the
            # metadata value line (line 2) also starts with a date, so both
            # conditions are needed.
            .filter(F.col("_c0").rlike(r"^\d{8}$") & F.col("_c1").rlike(r"^\d{1,2}$"))
            .withColumn(self.FILE_UPDATED_AT_SOURCE, F.lit(file_updated_at))
        )
        return raw.select([self._cast(raw, c) for c in self.schema.columns])

    @classmethod
    def _sniff_metadata(cls, file: str) -> str:
        """Check the three header lines and return the file update timestamp.

        Parameters
        ----------
        file : str
            Path to an ``AREA_JISEKI_YYYYMMDD.csv`` file.

        Returns
        -------
        str
            ``"yyyyMMdd HH:mm:ss"`` — ファイル更新日 and ファイル更新時間 from
            line 2, joined by a space, for the contract to parse.

        Raises
        ------
        ValueError
            If the column-header line (line 3) differs from
            ``EXPECTED_HEADER`` or line 2 is not
            ``yyyymmdd,HH:MM:SS,yyyymmdd``.
        """
        with open(file, encoding=_SNIFF_ENCODING) as f:
            lines = [f.readline().rstrip("\r\n") for _ in range(3)]
        if lines[2] != cls.EXPECTED_HEADER:
            raise ValueError(
                f"{file}: unexpected column header {lines[2]!r} — expected "
                f"{cls.EXPECTED_HEADER!r} (TEPCO changed the layout?)"
            )
        match = cls._META_LINE_RE.match(lines[1])
        if match is None:
            raise ValueError(f"{file}: cannot parse the update timestamp from line 2 {lines[1]!r}")
        return f"{match.group(1)} {match.group(2)}"
