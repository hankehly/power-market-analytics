"""A sliding-window LightGBM strategy whose features come from a preset.

The features are retrieved once per run through Feast as a
:class:`~power_market_analytics.features.frame.FeatureFrame`; the strategy's
feature path is a merge with that frame on the grain, so training rows and
the target day's rows read the same values, each as of its own issue time.
The window, refit cadence, TreeSHAP records, permutation importance and
evaluation are the base class's.
"""

from __future__ import annotations

import pandas as pd

from power_market_analytics.features.frame import FeatureFrame
from power_market_analytics.features.presets import Preset
from power_market_analytics.forecasting.frames import GRAIN_COLS, GRAIN_SCHEMA
from power_market_analytics.forecasting.lgbm import (
    DEFAULT_TRAIN_WINDOW_DAYS,
    LightGbmEvalSetBase,
    SlidingWindowLightGbmStrategy,
)
from power_market_analytics.forecasting.task import TaskSpec


def preset_eval_set_cls(
    task: TaskSpec, preset: Preset, dtypes: dict[str, str]
) -> type[LightGbmEvalSetBase]:
    """The design-matrix frame class of a preset strategy.

    Parameters
    ----------
    task : TaskSpec
        Names the target and forecast columns.
    preset : Preset
        Names the feature columns.
    dtypes : dict of str to str
        The contract dtype of each feature column once rows are complete
        (``feature_dtypes``); ``time_code`` is the grain's int64.

    Returns
    -------
    type of LightGbmEvalSetBase
    """
    schema = {
        **GRAIN_SCHEMA,
        **{col: dtypes[col] for col in preset.columns},
        task.actual_col: "float64",
        task.forecast_col: "float64",
    }
    return type(
        "PresetEvalSet",
        (LightGbmEvalSetBase,),
        {
            "feature_cols": preset.feature_cols,
            "target_col": task.actual_col,
            "forecast_col": task.forecast_col,
            "schema": schema,
            "keys": list(GRAIN_COLS),
            "non_null_cols": [*preset.feature_cols, task.actual_col, task.forecast_col],
            "__doc__": (
                f"Design matrix of the {preset.name!r} preset of the {task.name} task: "
                "one row per forecast point with the preset's features, the actual and "
                "the walk-forward forecast. Grain: (trade_date, time_code)."
            ),
        },
    )


class PresetLightGbmStrategy(SlidingWindowLightGbmStrategy):
    """LightGBM over a preset's features, retrieved as a :class:`FeatureFrame`.

    Parameters
    ----------
    task : TaskSpec
        The task being forecast.
    preset : Preset
        The feature list; its name is the strategy's unless ``name`` is given.
    features : FeatureFrame
        The preset's features for every day the run may train on or forecast.
    dtypes : dict of str to str
        Contract dtype per feature column (``feature_dtypes``).
    name : str, optional
        The strategy label of an ad hoc feature set (``--name``).
    train_window_days, refit_every_days, train_start_date
        As in :class:`SlidingWindowLightGbmStrategy`.

    Raises
    ------
    ValueError
        If ``features`` lacks a column of the preset.
    """

    def __init__(
        self,
        task: TaskSpec,
        preset: Preset,
        features: FeatureFrame,
        *,
        dtypes: dict[str, str],
        name: str | None = None,
        train_window_days: int = DEFAULT_TRAIN_WINDOW_DAYS,
        refit_every_days: int = 7,
        train_start_date: pd.Timestamp | None = None,
    ) -> None:
        super().__init__(
            train_window_days=train_window_days,
            refit_every_days=refit_every_days,
            train_start_date=train_start_date,
        )
        missing = [col for col in preset.columns if col not in features.df.columns]
        if missing:
            raise ValueError(f"{preset.name}: the feature frame lacks columns {missing}")
        self.task = task
        self.name = name or preset.name
        self.preset = preset
        self.feature_cols = preset.feature_cols
        self.categorical_feature_cols = preset.categorical
        # Every feature is retrieved as of its own row: no lag window to reach back for.
        self.lookback_days = 0
        self.eval_set_cls = preset_eval_set_cls(task, preset, dtypes)
        self._features_df = features.df[[*GRAIN_COLS, *preset.columns]]

    def _features(self, points: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
        """Attach the preset's features to the points: the frame's columns, nothing computed.

        Overrides the base, which would first compute ``month`` and
        ``day_of_week`` itself; a preset reads them from the frame like any
        other feature.

        Parameters
        ----------
        points : pandas.DataFrame
            Rows keyed on (trade_date, time_code); other columns pass through.
        history : pandas.DataFrame
            Unused: the frame holds every feature already.

        Returns
        -------
        pandas.DataFrame
            ``points`` plus the preset's columns (NaN where unavailable).
        """
        return self._add_features(points, history)

    def _add_features(self, featured: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
        """Merge the retrieved frame's columns onto the rows, on the grain.

        Parameters
        ----------
        featured : pandas.DataFrame
            Rows keyed on (trade_date, time_code).
        history : pandas.DataFrame
            Unused.

        Returns
        -------
        pandas.DataFrame
            ``featured`` plus the preset's columns (NaN where unavailable).
        """
        return featured.merge(self._features_df, how="left", on=GRAIN_COLS, validate="one_to_one")

    def _extra_params(self) -> dict[str, object]:
        """The preset and its feature references, next to the ``lgbm_*`` params.

        Returns
        -------
        dict of str to object
        """
        return {
            "feature_preset": self.preset.name,
            "feature_preset_base": self.preset.base or "none",
            "feature_refs": ",".join(self.preset.features),
        }
