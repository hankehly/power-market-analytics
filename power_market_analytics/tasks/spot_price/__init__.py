"""Day-ahead JEPX spot price forecasting.

Task definition: at 9:55 JST on day D-1 (just before the 10:00 gate closure
of the day-ahead auction), forecast all 48 half-hour prices for delivery day
D in a given area. At that moment the newest published spot results are for
delivery day D-1 (published ~noon on D-2), so a strategy's usable history is
delivery days <= D-1.
"""

MLFLOW_EXPERIMENT = "spot_price"
