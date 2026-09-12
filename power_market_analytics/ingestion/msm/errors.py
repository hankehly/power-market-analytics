"""The MSM error hierarchy, so a caller can catch the whole pipeline at once."""

from __future__ import annotations


class MsmError(RuntimeError):
    """Base error for MSM download, extraction and lookup failures."""


class MsmExtractError(MsmError):
    """A GRIB2 file could not be decoded into a complete set of station records."""


class MsmDownloadError(MsmError):
    """A GRIB2 archive member could not be downloaded: HTTP 404 (the file is
    absent) or a completed download's content failed validation (empty body,
    or missing the GRIB2 magic bytes)."""
