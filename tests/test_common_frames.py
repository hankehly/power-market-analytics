"""Tests for the ``DomainFrame`` validated-wrapper base class."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.common.frames import DomainFrame


class Sample(DomainFrame):
    """Two-column grain, one extra non-null column, one nullable column."""

    schema = {
        "day": "datetime64[ns]",
        "code": "int64",
        "value": "float64",
        "note": "object",
    }
    keys = ["day", "code"]
    non_null_cols = ["value"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        if (df["code"] < 0).any():
            raise ValueError(f"{cls.__name__}: negative code")


class Unkeyed(DomainFrame):
    """No grain: duplicates are allowed."""

    schema = {"code": "int64", "value": "float64"}
    keys = []


def sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "day": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-01-02"]),
            "code": np.array([1, 2, 1], dtype="int64"),
            "value": [1.5, 2.5, 3.5],
            "note": ["a", None, "c"],
        }
    )


class TestFromDf:
    def test_valid_frame_is_wrapped_as_the_subclass(self):
        frame = Sample.from_df(sample_df())
        assert isinstance(frame, Sample)
        assert len(frame) == 3

    def test_missing_columns_listed_in_schema_order(self):
        df = sample_df().drop(columns=["value", "note"])
        with pytest.raises(
            ValueError, match=re.escape("Sample: missing required columns ['value', 'note']")
        ):
            Sample.from_df(df)

    def test_extra_columns_dropped_and_schema_order_enforced(self):
        df = sample_df()
        scrambled = df[["note", "value", "code", "day"]].assign(extra="x")
        frame = Sample.from_df(scrambled)
        assert list(frame.df.columns) == ["day", "code", "value", "note"]
        # The wrapper holds a copy: the caller's frame keeps its extra column.
        assert "extra" in scrambled.columns

    def test_dtype_mismatch_reports_actual_and_expected(self):
        df = sample_df().astype({"code": "float64"})
        with pytest.raises(ValueError, match=re.escape("'code': ('float64', 'int64')")):
            Sample.from_df(df)

    def test_null_in_key_column_rejected_with_count(self):
        df = sample_df()
        df.loc[0, "day"] = pd.NaT
        with pytest.raises(ValueError, match="Sample: column 'day' has 1 null values"):
            Sample.from_df(df)

    def test_null_in_non_null_column_rejected_with_count(self):
        df = sample_df()
        df.loc[[0, 2], "value"] = np.nan
        with pytest.raises(ValueError, match="Sample: column 'value' has 2 null values"):
            Sample.from_df(df)

    def test_null_in_nullable_column_accepted(self):
        frame = Sample.from_df(sample_df())
        assert frame.df["note"].isna().sum() == 1

    def test_duplicate_grain_rejected_with_duplicate_count(self):
        df = pd.concat([sample_df(), sample_df().iloc[:2]], ignore_index=True)
        # Rows 0 and 1 each appear twice -> 2 rows flagged as duplicates.
        with pytest.raises(
            ValueError,
            match=re.escape("Sample: grain ['day', 'code'] not unique (2 duplicate rows)"),
        ):
            Sample.from_df(df)

    def test_empty_keys_skip_uniqueness_check(self):
        df = pd.DataFrame({"code": np.array([1, 1], dtype="int64"), "value": [0.5, 0.5]})
        assert len(Unkeyed.from_df(df)) == 2

    def test_validate_extra_failure_propagates(self):
        df = sample_df()
        df.loc[1, "code"] = -1
        with pytest.raises(ValueError, match="Sample: negative code"):
            Sample.from_df(df)

    def test_base_validate_extra_is_a_no_op(self):
        # Unkeyed does not override the hook, so from_df must not raise.
        df = pd.DataFrame({"code": np.array([-1], dtype="int64"), "value": [0.0]})
        assert len(Unkeyed.from_df(df)) == 1

    def test_missing_columns_checked_before_dtypes(self):
        # A frame both missing 'note' and with a wrong 'code' dtype fails on the
        # missing column first (the dtype check needs every column present).
        df = sample_df().drop(columns=["note"]).astype({"code": "float64"})
        with pytest.raises(ValueError, match="missing required columns"):
            Sample.from_df(df)


class TestProperties:
    def test_df_returns_conformed_frame(self):
        frame = Sample.from_df(sample_df())
        pd.testing.assert_frame_equal(frame.df, sample_df())

    def test_grain_is_tuple_of_keys(self):
        assert Sample.from_df(sample_df()).grain == ("day", "code")
        assert Unkeyed.from_df(pd.DataFrame({"code": np.array([1]), "value": [1.0]})).grain == ()

    def test_schema_name_is_class_name(self):
        assert Sample.from_df(sample_df()).schema_name == "Sample"

    def test_len_is_row_count(self):
        assert len(Sample.from_df(sample_df().iloc[:2])) == 2
