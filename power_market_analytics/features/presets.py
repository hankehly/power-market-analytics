"""Presets: the named feature lists a strategy is built from.

A preset names its features as ``<view>:<column>`` references into the
generated feature views, in feature order, and says which of them LightGBM
treats as categorical. The preset's name is the strategy label the runs are
published under. ``feature_service`` turns a preset into the Feast
``FeatureService`` of the same features, so the registry and the UI list it.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable

from feast import FeatureService, FeatureView
from feast.types import Float64, Int64

from power_market_analytics.features import views as _views

#: Feast field types LightGBM can consume, as the pandas dtype of a complete column.
NUMERIC_DTYPES: dict[object, str] = {Int64: "int64", Float64: "float64"}
#: The entity column every preset strategy uses as its first feature, as the
#: calendar prefix of every strategy did before presets.
ENTITY_FEATURE = "time_code"


def feature_column(ref: str) -> str:
    """The column name of a ``<view>:<column>`` reference.

    Parameters
    ----------
    ref : str

    Returns
    -------
    str

    Raises
    ------
    ValueError
        If ``ref`` is not of the form ``<view>:<column>``.
    """
    view, sep, column = ref.partition(":")
    if not sep or not view or not column:
        raise ValueError(f"feature reference {ref!r} is not '<view>:<column>'")
    return column


@dataclasses.dataclass(frozen=True)
class Preset:
    """A named feature list of one task.

    Attributes
    ----------
    task : str
        The task's name (its MLflow experiment), e.g. ``spot_price``.
    name : str
        The strategy label: the registry key and the ``strategy`` column.
    features : tuple of str
        ``<view>:<column>`` references, in feature order.
    categorical : tuple of str
        Column names among ``features`` LightGBM treats as categorical.
    base : str or None
        The registered preset a changed set started from (``with_changes``).
    """

    task: str
    name: str
    features: tuple[str, ...]
    categorical: tuple[str, ...] = ()
    #: The registered preset a changed set started from; None on a registered one.
    base: str | None = None

    def __post_init__(self) -> None:
        columns = [feature_column(ref) for ref in self.features]
        duplicates = sorted({c for c in columns if columns.count(c) > 1})
        if duplicates:
            raise ValueError(f"{self.name}: duplicate feature columns {duplicates}")
        if ENTITY_FEATURE in columns:
            raise ValueError(
                f"{self.name}: {ENTITY_FEATURE!r} is every preset's first feature already"
            )
        unknown = [c for c in self.categorical if c not in columns]
        if unknown:
            raise ValueError(f"{self.name}: categorical columns {unknown} are not features")

    @property
    def columns(self) -> tuple[str, ...]:
        """The feature column names, in feature order."""
        return tuple(feature_column(ref) for ref in self.features)

    @property
    def feature_cols(self) -> tuple[str, ...]:
        """The model's feature columns: ``time_code`` first, then :attr:`columns`."""
        return (ENTITY_FEATURE, *self.columns)

    def with_changes(
        self, *, add: Iterable[str] = (), drop: Iterable[str] = (), name: str
    ) -> Preset:
        """A copy with references dropped and added, under a new name.

        Parameters
        ----------
        add : iterable of str, optional
            References appended, in order, after the kept ones.
        drop : iterable of str, optional
            References removed; a dropped categorical leaves the categorical set.
        name : str
            The new preset's name.

        Returns
        -------
        Preset

        Raises
        ------
        ValueError
            If a dropped reference is absent or an added one present.
        """
        added, dropped = tuple(add), tuple(drop)
        missing = [ref for ref in dropped if ref not in self.features]
        if missing:
            raise ValueError(f"{self.name}: cannot drop {missing}: not in the preset")
        present = [ref for ref in added if ref in self.features]
        if present:
            raise ValueError(f"{self.name}: cannot add {present}: already in the preset")
        dropped_columns = {feature_column(ref) for ref in dropped}
        return dataclasses.replace(
            self,
            name=name,
            base=self.base or self.name,
            features=tuple(ref for ref in self.features if ref not in dropped) + added,
            categorical=tuple(c for c in self.categorical if c not in dropped_columns),
        )


def _views_by_name() -> dict[str, FeatureView]:
    return {view.name: view for view in _views.VIEWS}


def feature_dtypes(preset: Preset) -> dict[str, str]:
    """The pandas dtype of each feature column, read off the generated views.

    Parameters
    ----------
    preset : Preset

    Returns
    -------
    dict of str to str
        Column name → ``int64`` or ``float64``, in feature order.

    Raises
    ------
    ValueError
        If a reference names an unknown view or column, a join key, or a
        column whose Feast type is not numeric.
    """
    by_name = _views_by_name()
    dtypes: dict[str, str] = {}
    for ref in preset.features:
        view_name, _, column = ref.partition(":")
        view = by_name.get(view_name)
        if view is None:
            raise ValueError(f"{preset.name}: unknown feature view {view_name!r} in {ref!r}")
        field = next((f for f in view.schema if f.name == column), None)
        if field is None or column in view.join_keys:
            raise ValueError(f"{preset.name}: {ref!r} is not a feature of {view_name}")
        dtype = NUMERIC_DTYPES.get(field.dtype)
        if dtype is None:
            raise ValueError(f"{preset.name}: {ref!r} has Feast type {field.dtype}, not a number")
        dtypes[column] = dtype
    return dtypes


def feature_service(preset: Preset) -> FeatureService:
    """The Feast feature service of a preset: one projection per view it draws on.

    Parameters
    ----------
    preset : Preset

    Returns
    -------
    feast.FeatureService
        Named ``<task>__<preset>``; tags ``task``, ``preset`` and ``categorical``.
    """
    by_name = _views_by_name()
    grouped: dict[str, list[str]] = {}
    for ref in preset.features:
        view_name, _, column = ref.partition(":")
        grouped.setdefault(view_name, []).append(column)
    return FeatureService(
        name=f"{preset.task}__{preset.name}",
        features=[by_name[view_name][columns] for view_name, columns in grouped.items()],
        description=f"Preset {preset.name!r} of the {preset.task} task: {', '.join(preset.features)}.",
        tags={
            "task": preset.task,
            "preset": preset.name,
            "categorical": ",".join(preset.categorical),
        },
    )
