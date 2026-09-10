"""The retrieved features of one area's prediction rows, as a validated frame."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.forecasting.frames import GRAIN_COLS, GRAIN_SCHEMA


class FeatureFrame(DomainFrame):
    """One area's features per delivery period, as of each period's issue time.

    Grain: (trade_date, time_code). Every feature column is float64 and may be
    NaN where no row was available at the issue time; a strategy drops such
    training rows and treats such a target day as unforecastable. Concrete
    classes, one schema per feature list, come from :func:`feature_frame_class`.
    """

    keys = list(GRAIN_COLS)

    @property
    def feature_cols(self) -> tuple[str, ...]:
        """The feature columns, in preset order."""
        return tuple(col for col in self.schema if col not in GRAIN_SCHEMA)


def feature_frame_class(columns: Sequence[str]) -> type[FeatureFrame]:
    """A :class:`FeatureFrame` subclass whose schema holds ``columns`` as float64.

    Parameters
    ----------
    columns : sequence of str
        Feature column names, in order.

    Returns
    -------
    type of FeatureFrame

    Raises
    ------
    ValueError
        If a column repeats or collides with a grain column.
    """
    columns = tuple(columns)
    if len(set(columns)) != len(columns):
        raise ValueError(f"duplicate feature columns in {columns}")
    clash = [col for col in columns if col in GRAIN_SCHEMA]
    if clash:
        raise ValueError(f"feature columns {clash} collide with the grain columns")
    return type(
        "FeatureFrame",
        (FeatureFrame,),
        {
            "schema": {**GRAIN_SCHEMA, **{col: "float64" for col in columns}},
            "keys": list(GRAIN_COLS),
            "non_null_cols": [],
            "__doc__": FeatureFrame.__doc__,
        },
    )


def feature_frame(retrieved: pd.DataFrame, columns: Sequence[str]) -> FeatureFrame:
    """Wrap the output of ``historical_features`` as a :class:`FeatureFrame`.

    Parameters
    ----------
    retrieved : pandas.DataFrame
        Rows with ``trade_date``, ``time_code`` and every column of ``columns``
        (other columns are dropped).
    columns : sequence of str
        The feature column names to keep, in order; cast to float64.

    Returns
    -------
    FeatureFrame
        Sorted by the grain.

    Raises
    ------
    ValueError
        If a column is missing, or the grain is not unique.
    """
    cls = feature_frame_class(columns)
    missing = [col for col in [*GRAIN_COLS, *columns] if col not in retrieved.columns]
    if missing:
        raise ValueError(f"retrieved features lack columns {missing}")
    df = retrieved[[*GRAIN_COLS, *columns]].assign(
        trade_date=lambda d: pd.to_datetime(d["trade_date"]).astype("datetime64[ns]"),
        time_code=lambda d: d["time_code"].astype("int64"),
        **{col: lambda d, c=col: d[c].astype("float64") for col in columns},
    )
    return cls.from_df(df.sort_values(GRAIN_COLS, ignore_index=True))
