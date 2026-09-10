"""Open the Feast store and register the catalogue's definitions in it."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import yaml
from feast import FeatureStore
from feast.feast_object import FeastObject
from pyspark.sql import SparkSession

from power_market_analytics.features.entities import ENTITIES

#: The directory holding ``feature_store.yaml``.
FEATURE_STORE_DIR = Path(__file__).resolve().parents[2] / "conf" / "feast"


def open_store(
    repo_path: str | Path = FEATURE_STORE_DIR, *, definitions: Iterable[FeastObject] | None = None
) -> FeatureStore:
    """Open the store described by ``repo_path/feature_store.yaml`` and apply the definitions.

    Applying is idempotent, so every caller gets a registry that matches the
    package: the entities and the generated views by default, or the objects
    given (a test's own view). A relative registry path in the config is
    resolved against ``repo_path``; its directory is created.

    Parameters
    ----------
    repo_path : str or pathlib.Path, optional
        Directory of the ``feature_store.yaml`` to load.
    definitions : iterable of Feast objects, optional
        Entities and feature views to apply instead of the package's.

    Returns
    -------
    feast.FeatureStore
    """
    repo_path = Path(repo_path)
    config = yaml.safe_load((repo_path / "feature_store.yaml").read_text())
    (repo_path / config["registry"]).parent.mkdir(parents=True, exist_ok=True)
    store = FeatureStore(repo_path=str(repo_path))
    if definitions is None:
        from power_market_analytics.features.views import VIEWS

        definitions = (*ENTITIES, *VIEWS)
    store.apply(list(definitions))
    return store


def session_time_zone(spark: SparkSession) -> str:
    """The time zone the session stores naive warehouse timestamps in.

    Warehouse timestamps are naive wall-clock JST values written under the
    session's ``spark.sql.session.timeZone`` (UTC in the devcontainer,
    Asia/Tokyo in the test fixture), so an entity timestamp must be localised
    to that zone, never to a fixed one, for Feast's as-of comparison to line up.

    Parameters
    ----------
    spark : pyspark.sql.SparkSession

    Returns
    -------
    str
    """
    return str(spark.conf.get("spark.sql.session.timeZone"))
