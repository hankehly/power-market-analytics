"""MLflow tracking conventions.

One MLflow experiment per modeling task (e.g. ``spot_price``), one run per
strategy/config execution. The tracking URI comes from the
``MLFLOW_TRACKING_URI`` environment variable (set to http://mlflow:5005 in
the devcontainer).
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import mlflow
import pandas as pd


@contextmanager
def task_run(
    experiment: str,
    run_name: str | None = None,
    tags: dict[str, str] | None = None,
) -> Iterator[mlflow.ActiveRun]:
    """Start an MLflow run under the given task experiment.

    Parameters
    ----------
    experiment : str
        MLflow experiment name; created if it does not exist.
    run_name : str, optional
        Display name for the run.
    tags : dict of str to str, optional
        Tags to set on the run (e.g. strategy, universe).

    Yields
    ------
    mlflow.ActiveRun
    """
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=run_name, tags=tags) as run:
        yield run


def log_dataframe(df: pd.DataFrame, filename: str) -> None:
    """Log a DataFrame to the active MLflow run as a CSV artifact.

    Parameters
    ----------
    df : pandas.DataFrame
        Data to log.
    filename : str
        Artifact file name, e.g. ``daily_errors.csv``.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / filename
        df.to_csv(path, index=False)
        mlflow.log_artifact(str(path))
