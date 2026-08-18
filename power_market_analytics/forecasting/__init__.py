"""Task-agnostic day-ahead forecasting framework.

Every modeling task under ``power_market_analytics.tasks`` forecasts one value
per 30-minute delivery period of a delivery day for one area, from an issue
time on the day before. This package holds what does not depend on *which*
value: the frame bases (``frames``), the task spec that names a task's columns,
cutoff and destination (``task``), the strategy interface (``strategy``), the
rolling backtest engine (``backtest``), lag features (``features``), the
sliding-window LightGBM strategy base (``lgbm``), the warehouse write-back
(``publish``) and the error heatmaps (``plots``). A task package supplies a
``TaskSpec``, its frames, its datasets and its concrete strategies.
"""
