"""The fitted similar-day weights as a feature parameter vintage.

``scripts/fit_similar_day.py`` fits the seven weights once (the fit of
``tasks/demand/similar_day.py``) and writes them as one row of
``pma_ml.similar_day_parameters``, partitioned by the fit's MLflow run like
the forecast tables. The ``ftr_period_similar_day`` mart scores every
delivery day with each vintage; a row's ``available_at`` is the midnight after
the last target day of the fit, when every load the fit used was public.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.forecasting.publish import (
    create_run_partitioned_table,
    overwrite_run_partitions,
)
from power_market_analytics.spark import get_spark_session
from power_market_analytics.tasks.demand.similar_day import (
    SIMILAR_DAY_COMPONENTS,
    SimilarDayWeights,
)

#: The MLflow experiment of the fits, named after the feature.
MLFLOW_EXPERIMENT = "similar_day"
#: Where the fits are written; the ``run_id`` partition is the fit's MLflow run.
PARAMETERS_TABLE = "pma_ml.similar_day_parameters"
WEIGHT_COLS: tuple[str, ...] = tuple(f"weight_{part}" for part in SIMILAR_DAY_COMPONENTS)
SCALE_COLS: tuple[str, ...] = tuple(f"scale_{part}" for part in SIMILAR_DAY_COMPONENTS)


class SimilarDayParameterRecords(DomainFrame):
    """One fit of the similar-day weights, shaped for ``pma_ml.similar_day_parameters``.

    The weights and scales are one column per part in
    ``SIMILAR_DAY_COMPONENTS`` order, so the mart scores with a plain
    expression; ``available_at`` is the midnight after ``fit_through``.

    Grain: (run_id).
    """

    schema = {
        "area_code": "object",
        "fit_from": "datetime64[ns]",
        "fit_through": "datetime64[ns]",
        "center_lag_days": "int64",
        "window_half_width_days": "int64",
        "census_year": "int64",
        **{col: "float64" for col in WEIGHT_COLS},
        **{col: "float64" for col in SCALE_COLS},
        "alpha": "float64",
        "beta": "float64",
        "n_pairs": "int64",
        "n_targets": "int64",
        "fit_rmse": "float64",
        "available_at": "datetime64[ns]",
        "published_at": "datetime64[ns]",
        "run_id": "object",
    }
    keys = ["run_id"]
    non_null_cols = [col for col in schema if col != "run_id"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        name = cls.__name__
        weights = df[list(WEIGHT_COLS)].to_numpy(dtype="float64")
        if (weights < 0).any() or not np.allclose(weights.sum(axis=1), 1.0):
            raise ValueError(f"{name}: weights must be >= 0 and sum to one")
        if (df[list(SCALE_COLS)] <= 0).any(axis=None):
            raise ValueError(f"{name}: scales must be > 0")
        if (df["alpha"] < 0).any():
            raise ValueError(f"{name}: alpha must be >= 0")
        if (df["fit_from"] > df["fit_through"]).any():
            raise ValueError(f"{name}: fit_from must not follow fit_through")
        expected = df["fit_through"].dt.normalize() + pd.Timedelta(days=1)
        if (df["available_at"] != expected).any():
            raise ValueError(f"{name}: available_at must be the midnight after fit_through")


def build_parameter_records(
    weights: SimilarDayWeights,
    *,
    run_id: str,
    area_code: str,
    center_lag_days: int,
    window_half_width_days: int,
    census_year: int,
    published_at: pd.Timestamp,
) -> SimilarDayParameterRecords:
    """Shape a fit into the one-row write-back record.

    Parameters
    ----------
    weights : SimilarDayWeights
        The fit.
    run_id : str
        The fit's MLflow run id.
    area_code : str
        dim_area.area_code value the weights were fitted for.
    center_lag_days, window_half_width_days : int
        The candidate window the fit scored, ``center ± half width`` days back.
    census_year : int
        Census vintage of the station weights behind the weather profiles.
    published_at : pandas.Timestamp
        When the row is written (naive JST).

    Returns
    -------
    SimilarDayParameterRecords
    """
    df = pd.DataFrame(
        {
            "area_code": [area_code],
            "fit_from": [pd.Timestamp(weights.fit_from)],
            "fit_through": [pd.Timestamp(weights.fit_through)],
            "center_lag_days": [center_lag_days],
            "window_half_width_days": [window_half_width_days],
            "census_year": [census_year],
            **{col: [float(w)] for col, w in zip(WEIGHT_COLS, weights.weights, strict=True)},
            **{col: [float(s)] for col, s in zip(SCALE_COLS, weights.scales, strict=True)},
            "alpha": [weights.alpha],
            "beta": [weights.beta],
            "n_pairs": [weights.n_pairs],
            "n_targets": [weights.n_targets],
            "fit_rmse": [weights.fit_rmse],
            "available_at": [pd.Timestamp(weights.fit_through).normalize() + pd.Timedelta(days=1)],
            "published_at": [pd.Timestamp(published_at)],
            "run_id": [run_id],
        }
    ).astype({"available_at": "datetime64[ns]", "published_at": "datetime64[ns]"})
    return SimilarDayParameterRecords.from_df(df)


def publish_parameter_records(
    records: SimilarDayParameterRecords, spark: SparkSession | None = None
) -> int:
    """Idempotently write one fit to ``PARAMETERS_TABLE``.

    The table (parquet, partitioned by ``run_id``) is created on first use and
    only the fit's partition is overwritten, as for the forecast tables.

    Parameters
    ----------
    records : SimilarDayParameterRecords
        The fit's row.
    spark : pyspark.sql.SparkSession, optional
        Existing session; defaults to
        :func:`power_market_analytics.spark.get_spark_session`.

    Returns
    -------
    int
        Number of rows written.
    """
    spark = spark if spark is not None else get_spark_session()
    create_run_partitioned_table(
        spark,
        PARAMETERS_TABLE,
        ",\n          ".join(
            [
                "area_code string",
                "fit_from date",
                "fit_through date",
                "center_lag_days int",
                "window_half_width_days int",
                "census_year int",
                *[f"{col} double" for col in (*WEIGHT_COLS, *SCALE_COLS)],
                "alpha double",
                "beta double",
                "n_pairs bigint",
                "n_targets int",
                "fit_rmse double",
                "available_at timestamp",
                "published_at timestamp",
            ]
        ),
    )
    casts = {
        "area_code": "string",
        "fit_from": "date",
        "fit_through": "date",
        "center_lag_days": "int",
        "window_half_width_days": "int",
        "census_year": "int",
        **{col: "double" for col in (*WEIGHT_COLS, *SCALE_COLS)},
        "alpha": "double",
        "beta": "double",
        "n_pairs": "bigint",
        "n_targets": "int",
        "fit_rmse": "double",
        "available_at": "timestamp",
        "published_at": "timestamp",
        "run_id": "string",
    }
    sdf = spark.createDataFrame(records.df).select(
        *[F.col(col).cast(dtype) for col, dtype in casts.items()]
    )
    overwrite_run_partitions(spark, PARAMETERS_TABLE, sdf)
    logger.info(
        "Published {} row(s) to {} (run_id={})",
        len(records),
        PARAMETERS_TABLE,
        records.df["run_id"].iloc[0],
    )
    return len(records)
