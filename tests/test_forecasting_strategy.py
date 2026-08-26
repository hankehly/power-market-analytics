"""Tests for the strategy interface and its skip exception."""

from __future__ import annotations

import pytest

from power_market_analytics.forecasting.strategy import ForecastStrategy, ForecastUnavailableError


class TestForecastUnavailableError:
    def test_is_a_value_error(self):
        assert issubclass(ForecastUnavailableError, ValueError)
        with pytest.raises(ValueError, match="no lag"):
            raise ForecastUnavailableError("no lag")


class TestForecastStrategy:
    def test_cannot_be_instantiated_without_the_three_methods(self):
        class Incomplete(ForecastStrategy):
            name = "incomplete"

        with pytest.raises(TypeError, match="abstract"):
            Incomplete()

    def test_concrete_subclass_instantiates(self):
        class Done(ForecastStrategy):
            name = "done"

            def predict(self, target_date, history):
                raise NotImplementedError

            def build_eval_set(self, history, start_date, end_date, run=None):
                raise NotImplementedError

            def evaluate(self, eval_set, **kwargs):
                raise NotImplementedError

        assert Done().name == "done"

    def test_contributions_default_to_none(self):
        class Done(ForecastStrategy):
            name = "done"

            def predict(self, target_date, history):
                raise NotImplementedError

            def build_eval_set(self, history, start_date, end_date, run=None):
                raise NotImplementedError

            def evaluate(self, eval_set, **kwargs):
                raise NotImplementedError

        assert Done().contributions() is None
