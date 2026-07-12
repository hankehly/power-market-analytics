"""Validated pandas DataFrame domain wrappers.

Subclasses declare a schema (column -> pandas dtype), grain key columns, and
non-nullable columns; :meth:`DomainFrame.from_df` is the only supported
constructor and fails fast when the contract is violated.
"""

from __future__ import annotations

from typing import ClassVar

import pandas as pd


class DomainFrame:
    """Base class for validated, read-only pandas DataFrame wrappers.

    Class Attributes
    ----------------
    schema : dict of str to str
        Required columns and their pandas dtypes, in canonical order.
    keys : list of str
        Grain columns; their combination must be unique and non-null.
    non_null_cols : list of str
        Additional columns that must not contain nulls.
    """

    schema: ClassVar[dict[str, str]] = {}
    keys: ClassVar[list[str]] = []
    non_null_cols: ClassVar[list[str]] = []

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    @property
    def df(self) -> pd.DataFrame:
        """Underlying DataFrame; treat as read-only in shared code."""
        return self._df

    @property
    def grain(self) -> tuple[str, ...]:
        """Grain key columns."""
        return tuple(self.keys)

    @property
    def schema_name(self) -> str:
        """Name of this frame type."""
        return type(self).__name__

    def __len__(self) -> int:
        return len(self._df)

    @classmethod
    def from_df(cls, df: pd.DataFrame) -> "DomainFrame":
        """Construct a validated wrapper from a raw DataFrame.

        Parameters
        ----------
        df : pandas.DataFrame
            Input data. Only schema columns are kept, in schema order.

        Returns
        -------
        DomainFrame
            The validated wrapper (an instance of ``cls``).

        Raises
        ------
        ValueError
            If required columns are missing, dtypes do not match, key or
            non-nullable columns contain nulls, the grain is not unique, or
            a subclass ``_validate_extra`` check fails.
        """
        name = cls.__name__
        missing = [c for c in cls.schema if c not in df.columns]
        if missing:
            raise ValueError(f"{name}: missing required columns {missing}")

        out = df[list(cls.schema)].copy()

        bad_dtypes = {
            col: (str(out[col].dtype), expected)
            for col, expected in cls.schema.items()
            if str(out[col].dtype) != expected
        }
        if bad_dtypes:
            raise ValueError(f"{name}: dtype mismatch (actual, expected): {bad_dtypes}")

        for col in [*cls.keys, *cls.non_null_cols]:
            n_null = int(out[col].isna().sum())
            if n_null:
                raise ValueError(f"{name}: column {col!r} has {n_null} null values")

        if cls.keys and out.duplicated(subset=cls.keys).any():
            n_dup = int(out.duplicated(subset=cls.keys).sum())
            raise ValueError(f"{name}: grain {cls.keys} not unique ({n_dup} duplicate rows)")

        cls._validate_extra(out)
        return cls(out)

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        """Hook for subclass-specific validation; raise ValueError on failure.

        Parameters
        ----------
        df : pandas.DataFrame
            The schema-conformed DataFrame about to be wrapped.
        """
