"""Open the Feast store and register the catalogue's definitions in it."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import cast

import yaml
from feast import Entity, FeatureService, FeatureStore, FeatureView
from feast.data_source import DataSource
from feast.feast_object import FeastObject
from feast.feature_view import DUMMY_ENTITY_NAME
from pyspark.sql import SparkSession

from power_market_analytics.features.entities import ENTITIES

#: The directory holding ``feature_store.yaml``.
FEATURE_STORE_DIR = Path(__file__).resolve().parents[2] / "conf" / "feast"


def open_store(
    repo_path: str | Path | None = None, *, definitions: Iterable[FeastObject] | None = None
) -> FeatureStore:
    """Open the store described by ``repo_path/feature_store.yaml`` and apply the definitions.

    The registry is reconciled to the definitions: they are applied, and every
    feature view, entity, data source and feature service the registry holds
    that they no longer name is deleted, so a renamed or removed mart or
    preset does not linger in ``data/feast/registry.db``. Every caller
    therefore gets a registry that matches the package: the entities, the
    generated views and every task's preset services by default, or the
    objects given (a test's own view). A relative registry path in the
    config is resolved against ``repo_path``; its directory is created.

    Parameters
    ----------
    repo_path : str or pathlib.Path, optional
        Directory of the ``feature_store.yaml`` to load; ``FEATURE_STORE_DIR``
        (read at call time) when omitted.
    definitions : iterable of Feast objects, optional
        Entities and feature views to apply instead of the package's.

    Returns
    -------
    feast.FeatureStore
    """
    repo_path = Path(FEATURE_STORE_DIR if repo_path is None else repo_path)
    config = yaml.safe_load((repo_path / "feature_store.yaml").read_text())
    (repo_path / config["registry"]).parent.mkdir(parents=True, exist_ok=True)
    store = FeatureStore(repo_path=str(repo_path))
    if definitions is None:
        from power_market_analytics.features.catalogue import feature_services
        from power_market_analytics.features.views import VIEWS

        definitions = (*ENTITIES, *VIEWS, *feature_services())
    objects = list(definitions)
    views = [obj for obj in objects if isinstance(obj, FeatureView)]
    kept_views = {view.name for view in views}
    kept_entities = {obj.name for obj in objects if isinstance(obj, Entity)}
    kept_services = {obj.name for obj in objects if isinstance(obj, FeatureService)}
    # Every view over a mart has a batch source; the attribute is typed Optional.
    kept_sources = {cast(DataSource, view.batch_source).name for view in views}
    stale: list[FeastObject] = [
        *(v for v in store.list_feature_views() if v.name not in kept_views),
        *(
            e
            for e in store.list_entities()
            if e.name not in kept_entities and e.name != DUMMY_ENTITY_NAME
        ),
        *(d for d in store.list_data_sources() if d.name not in kept_sources),
        *(f for f in store.list_feature_services() if f.name not in kept_services),
    ]
    # partial=False deletes exactly objects_to_delete; the rest is applied as usual.
    store.apply(objects, objects_to_delete=stale, partial=False)
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
