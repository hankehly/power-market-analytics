"""Presets: the named feature lists a strategy is built from.

A preset names its features as ``<view>:<column>`` references into the
generated feature views, in feature order. Which of them LightGBM treats as
categorical is not the preset's to say: a column is categorical when its
view field carries the ``categorical`` tag the dbt mart declares, whether the
preset is registered or changed with ``--add`` (``categorical_columns``). The
preset's name is the strategy label the runs are published under.
``feature_service`` turns a preset into the Feast ``FeatureService`` of the
same features, so the registry and the UI list it.

A preset is a YAML file under ``conf/presets/<task>/<name>.yaml``: ``description``
and either ``features`` (the full list, in feature order) or ``base`` with ``add``
and/or ``drop`` (a change to another preset of the task). ``load_presets`` reads
a task's directory into presets by name; the file's stem is the name.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

import yaml
from feast import FeatureService, FeatureView, Field
from feast.types import Float64, Int64

from power_market_analytics.features import views as _views

#: Feast field types LightGBM can consume, as the pandas dtype of a complete column.
NUMERIC_DTYPES: dict[object, str] = {Int64: "int64", Float64: "float64"}
#: The entity column every preset strategy uses as its first feature, as the
#: calendar prefix of every strategy did before presets.
ENTITY_FEATURE = "time_code"
#: The field tag the view generator writes from the mart's ``meta.categorical``.
CATEGORICAL_TAG = "categorical"
#: The field tag the view generator writes from the mart's ``meta.expression``:
#: the name people read for the column (``LAG(demand_kwh, 2d)``).
EXPRESSION_TAG = "expression"
#: The directory of preset files: one subdirectory per task, one YAML file per preset.
PRESETS_DIR = Path(__file__).resolve().parents[2] / "conf" / "presets"
#: The keys a preset file may carry.
PRESET_KEYS = frozenset({"description", "features", "base", "add", "drop"})
#: A preset name: the file's stem, the strategy label.
NAME_PATTERN = re.compile(r"^[a-z0-9_]+$")


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
    base : str or None
        The preset this one was changed from (``with_changes``), by name;
        None for one defined from scratch.
    description : str
        What the preset is, from its file; empty for one built in code.
    """

    task: str
    name: str
    features: tuple[str, ...]
    #: The preset this one was changed from, by name; None for one defined from scratch.
    base: str | None = None
    #: What the preset is, from its file; empty for one built in code or changed on the command line.
    description: str = ""

    def __post_init__(self) -> None:
        columns = [feature_column(ref) for ref in self.features]
        duplicates = sorted({c for c in columns if columns.count(c) > 1})
        if duplicates:
            raise ValueError(f"{self.name}: duplicate feature columns {duplicates}")
        if ENTITY_FEATURE in columns:
            raise ValueError(
                f"{self.name}: {ENTITY_FEATURE!r} is every preset's first feature already"
            )

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

        The copy's ``base`` is this preset's name, whether this one is
        registered or itself a changed set. The copy's description is empty:
        a run changed on the command line is not the base's preset.

        Parameters
        ----------
        add : iterable of str, optional
            References appended, in order, after the kept ones.
        drop : iterable of str, optional
            References removed.
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
        return dataclasses.replace(
            self,
            name=name,
            base=self.name,
            features=tuple(ref for ref in self.features if ref not in dropped) + added,
            description="",
        )


def _views_by_name() -> dict[str, FeatureView]:
    return {view.name: view for view in _views.VIEWS}


def _fields(preset: Preset) -> Iterator[tuple[str, str, Field]]:
    """Each reference, its column name and its view field, in feature order.

    Raises
    ------
    ValueError
        If a reference names an unknown view or column, or a join key.
    """
    by_name = _views_by_name()
    for ref in preset.features:
        view_name, _, column = ref.partition(":")
        view = by_name.get(view_name)
        if view is None:
            raise ValueError(f"{preset.name}: unknown feature view {view_name!r} in {ref!r}")
        field = next((f for f in view.schema if f.name == column), None)
        if field is None or column in view.join_keys:
            raise ValueError(f"{preset.name}: {ref!r} is not a feature of {view_name}")
        yield ref, column, field


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
    dtypes: dict[str, str] = {}
    for ref, column, field in _fields(preset):
        dtype = NUMERIC_DTYPES.get(field.dtype)
        if dtype is None:
            raise ValueError(f"{preset.name}: {ref!r} has Feast type {field.dtype}, not a number")
        dtypes[column] = dtype
    return dtypes


def categorical_columns(preset: Preset) -> tuple[str, ...]:
    """The feature columns LightGBM treats as categorical, read off the views.

    A column is categorical when its view field carries the ``categorical``
    tag, i.e. when the mart's YAML declares ``meta: {categorical: true}``.
    That rule holds on a registered preset and on one changed with ``add``.

    Parameters
    ----------
    preset : Preset

    Returns
    -------
    tuple of str
        Column names, in feature order.

    Raises
    ------
    ValueError
        If a reference names an unknown view or column, or a join key.
    """
    return tuple(
        column
        for _, column, field in _fields(preset)
        if (field.tags or {}).get(CATEGORICAL_TAG) == "true"
    )


def feature_expressions(preset: Preset) -> dict[str, str]:
    """The expression of each feature column, read off the views' ``expression`` tags.

    The expression is the name a chart or table shows for the column; code
    keeps using the column name. A field without the tag is left out, so a
    caller falls back to the column name.

    Parameters
    ----------
    preset : Preset

    Returns
    -------
    dict of str to str
        Column name → expression, in feature order.

    Raises
    ------
    ValueError
        If a reference names an unknown view or column, or a join key.
    """
    return {
        column: field.tags[EXPRESSION_TAG]
        for _, column, field in _fields(preset)
        if (field.tags or {}).get(EXPRESSION_TAG)
    }


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
        description=preset.description
        or f"Preset {preset.name!r} of the {preset.task} task: {', '.join(preset.features)}.",
        tags={
            "task": preset.task,
            "preset": preset.name,
            CATEGORICAL_TAG: ",".join(categorical_columns(preset)),
        },
    )


@dataclasses.dataclass(frozen=True)
class _PresetFile:
    """One preset file, read and checked, before its base is resolved."""

    description: str
    features: tuple[str, ...] | None
    base: str | None
    add: tuple[str, ...]
    drop: tuple[str, ...]


class _PresetFileError(ValueError):
    """A ``ValueError`` whose message already names the offending file."""


def _refs(file: Path, data: dict, key: str) -> tuple[str, ...]:
    value = data.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(ref, str) for ref in value):
        raise ValueError(f"{file}: {key} must be a list of 'view:column' strings")
    for ref in value:
        try:
            feature_column(ref)
        except ValueError as exc:
            raise ValueError(f"{file}: {exc}") from None
    return tuple(value)


def _read_preset_file(file: Path) -> _PresetFile:
    data = yaml.safe_load(file.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{file}: is not a mapping")
    unknown = sorted(set(data) - PRESET_KEYS)
    if unknown:
        raise ValueError(f"{file}: unknown keys {unknown}")
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"{file}: description is required")
    if ("features" in data) == ("base" in data):
        raise ValueError(f"{file}: exactly one of features and base")
    if "base" not in data and ("add" in data or "drop" in data):
        raise ValueError(f"{file}: add and drop need a base")
    base = data.get("base")
    if "base" in data and not isinstance(base, str):
        raise ValueError(f"{file}: base must be a preset name")
    add, drop = _refs(file, data, "add"), _refs(file, data, "drop")
    if base is not None and not add and not drop:
        raise ValueError(f"{file}: a base needs add or drop")
    features = _refs(file, data, "features") if "features" in data else None
    return _PresetFile(description.strip(), features, base, add, drop)


def preset_tasks(presets_dir: Path = PRESETS_DIR) -> tuple[str, ...]:
    """The tasks with a preset directory, sorted.

    Parameters
    ----------
    presets_dir : pathlib.Path, optional
        The directory of preset files; ``PRESETS_DIR`` when omitted.

    Returns
    -------
    tuple of str
    """
    return tuple(sorted(p.name for p in presets_dir.iterdir() if p.is_dir()))


def load_presets(task: str, presets_dir: Path = PRESETS_DIR) -> dict[str, Preset]:
    """The presets of a task, read from its files, by name and sorted by name.

    A ``features`` file becomes a preset from scratch; a ``base`` file becomes
    the base preset changed with ``add`` and ``drop``, under the file's name
    and description, so ``Preset.base`` names the file it started from.

    Parameters
    ----------
    task : str
        The task's name: the subdirectory of ``presets_dir``.
    presets_dir : pathlib.Path, optional
        The directory of preset files; ``PRESETS_DIR`` when omitted.

    Returns
    -------
    dict of str to Preset

    Raises
    ------
    ValueError
        If the task has no file, a file's name is not lowercase ``a-z0-9_``,
        a file breaks a rule of the format, a base is not a preset of the
        task, a chain of bases loops, or the resolved list breaks a
        :class:`Preset` rule. The message starts with the file's path.
    """
    files = {path.stem: path for path in sorted((presets_dir / task).glob("*.yaml"))}
    if not files:
        raise ValueError(f"{presets_dir / task}: no preset files")
    for stem, path in files.items():
        if not NAME_PATTERN.match(stem):
            raise ValueError(f"{path}: a preset name is lowercase a-z0-9_")
    specs = {stem: _read_preset_file(path) for stem, path in files.items()}
    presets: dict[str, Preset] = {}

    def resolve(name: str, chain: tuple[str, ...]) -> Preset:
        if name in presets:
            return presets[name]
        if name in chain:
            raise _PresetFileError(
                f"{files[name]}: base chain loops: {' -> '.join((*chain, name))}"
            )
        spec = specs[name]
        try:
            if spec.base is None:
                assert spec.features is not None  # exactly one of the two, checked on read
                preset = Preset(task, name, spec.features, description=spec.description)
            else:
                if spec.base not in specs:
                    raise ValueError(f"base {spec.base!r} is not a preset of {task}")
                base = resolve(spec.base, (*chain, name))
                changed = base.with_changes(add=spec.add, drop=spec.drop, name=name)
                preset = dataclasses.replace(changed, description=spec.description)
        except _PresetFileError:
            raise
        except ValueError as exc:
            raise _PresetFileError(f"{files[name]}: {exc}") from None
        presets[name] = preset
        return preset

    for name in files:
        resolve(name, ())
    return {name: presets[name] for name in sorted(presets)}
