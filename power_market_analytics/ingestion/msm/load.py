"""Full reload of the per-day ``csv.gz`` extracts into a raw warehouse table.

The extract's columns are the load contract's, read by name, so this loader only
has to resolve which files to read.
"""

from __future__ import annotations

import glob

from power_market_analytics.ingestion.loader import CsvLoader


class MsmForecastCsvLoader(CsvLoader):
    """Full reload of extracted MSM forecast ``csv.gz`` files into a warehouse table."""

    def _resolve_files(self) -> list[str]:
        if self.filepath.is_dir():
            files = sorted(str(p) for p in self.filepath.glob("*.csv.gz"))
        else:
            files = sorted(glob.glob(str(self.filepath)))
        if not files:
            raise FileNotFoundError(f"No MSM forecast csv.gz files found at {self.filepath}")
        return files
