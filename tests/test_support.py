"""Tests for the shared test helpers in :mod:`tests.support`.

The sleep and clock helpers exist because ``monkeypatch.setattr(mod.time,
"sleep", ...)`` reaches every thread in the process: ``mod.time`` *is* the
stdlib module. While a SparkSession is alive, py4j's finalizer thread loops on
``time.sleep(1)``, so a patch that records unconditionally records that
thread's sleeps too -- and, because the replacement returns at once, the thread
spins and floods the list (issue #76).
"""

from __future__ import annotations

import ast
import threading
import time
from pathlib import Path

import pandas as pd
import pytest

from tests.support import patch_monotonic, record_sleeps, write_table

# --------------------------------------------------------------------------- sleeps


class TestRecordSleeps:
    def test_records_the_calling_threads_sleeps_without_waiting(self, monkeypatch):
        real_monotonic = time.monotonic
        sleeps = record_sleeps(monkeypatch)
        started = real_monotonic()

        time.sleep(30.0)

        assert sleeps == [30.0]
        assert real_monotonic() - started < 1.0  # recorded, not slept

    def test_another_threads_sleep_is_not_recorded_and_really_waits(self, monkeypatch):
        real_monotonic = time.monotonic
        sleeps = record_sleeps(monkeypatch)
        started = real_monotonic()

        worker = threading.Thread(target=lambda: time.sleep(0.05))
        worker.start()
        worker.join()

        assert sleeps == []
        assert real_monotonic() - started >= 0.05

    def test_the_real_sleep_is_restored_on_teardown(self, monkeypatch):
        real_sleep = time.sleep
        record_sleeps(monkeypatch)
        assert time.sleep is not real_sleep

        monkeypatch.undo()

        assert time.sleep is real_sleep


# --------------------------------------------------------------------------- clock


class TestPatchMonotonic:
    def test_the_calling_thread_reads_the_fake_clock(self, monkeypatch):
        clock = {"now": 100.0}
        patch_monotonic(monkeypatch, lambda: clock["now"])

        assert time.monotonic() == 100.0
        clock["now"] += 0.2
        assert time.monotonic() == 100.2

    def test_another_thread_reads_the_real_clock(self, monkeypatch):
        real_monotonic = time.monotonic
        patch_monotonic(monkeypatch, lambda: 100.0)
        seen: list[float] = []

        worker = threading.Thread(target=lambda: seen.append(time.monotonic()))
        worker.start()
        worker.join()

        assert seen != [100.0]
        assert seen[0] == pytest.approx(real_monotonic(), abs=1.0)

    def test_the_real_monotonic_is_restored_on_teardown(self, monkeypatch):
        real_monotonic = time.monotonic
        patch_monotonic(monkeypatch, lambda: 100.0)
        assert time.monotonic is not real_monotonic

        monkeypatch.undo()

        assert time.monotonic is real_monotonic


# --------------------------------------------------------------------------- the sweep

TESTS_DIR = Path(__file__).resolve().parent
GLOBAL_CLOCK_ATTRS = {"sleep", "monotonic"}


def _global_clock_patches(tree: ast.Module) -> list[str]:
    """Describe every direct ``time.sleep`` / ``time.monotonic`` patch in ``tree``.

    Parameters
    ----------
    tree : ast.Module
        A parsed test module.

    Returns
    -------
    list of str
        One ``"<line>: <target>"`` entry per ``monkeypatch.setattr`` call whose
        target is ``time.sleep`` / ``time.monotonic``, in either the object form
        (``setattr(mod.time, "sleep", ...)``) or the dotted-string form
        (``setattr("pkg.mod.time.sleep", ...)``).
    """
    found = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setattr"
            and node.args
        ):
            continue
        target = node.args[0]
        if isinstance(target, ast.Constant) and isinstance(target.value, str):
            module, _, attr = target.value.rpartition(".")
            if attr in GLOBAL_CLOCK_ATTRS and module.endswith(".time"):
                found.append(f"{node.lineno}: {target.value}")
        elif len(node.args) >= 2:
            name = node.args[1]
            is_time = (isinstance(target, ast.Attribute) and target.attr == "time") or (
                isinstance(target, ast.Name) and target.id == "time"
            )
            if not (is_time and isinstance(name, ast.Constant)):
                continue
            attr = name.value if isinstance(name.value, str) else ""
            if attr in GLOBAL_CLOCK_ATTRS:
                found.append(f"{node.lineno}: time.{attr}")
    return found


def test_no_test_module_patches_time_sleep_or_monotonic_directly():
    """Such a patch reaches every thread; go through the helpers instead."""
    offenders = {
        path.name: hits
        for path in sorted(TESTS_DIR.glob("*.py"))
        if path.name != "support.py"
        for hits in [_global_clock_patches(ast.parse(path.read_text()))]
        if hits
    }
    assert offenders == {}


# --------------------------------------------------------------------------- tables
# ``spark.createDataFrame(pandas_frame, "a int, b int")`` matches columns by POSITION, so
# a frame whose columns are in another order than the schema string lands its values
# under the wrong names, silently when the types agree. ``write_table`` matches by name.


class TestWriteTable:
    def test_columns_land_under_their_own_names_whatever_the_frames_order(self, spark):
        frame = pd.DataFrame([{"a": 1, "c": 3, "b": 2}])
        write_table(spark, frame, "a int, b int, c int", "default.write_table_by_name")
        row = spark.table("default.write_table_by_name").collect()[0]
        assert row.asDict() == {"a": 1, "b": 2, "c": 3}

    def test_a_frame_column_the_schema_lacks_is_refused_by_name(self, spark):
        frame = pd.DataFrame([{"a": 1, "b": 2, "stray": 9}])
        with pytest.raises(ValueError, match=r"default\.t.*not in the schema: \['stray'\]"):
            write_table(spark, frame, "a int, b int", "default.t")

    def test_dict_rows_land_by_name_and_a_key_they_omit_is_null(self, spark):
        # Spark matches a dict to the schema by name itself; a key a row omits is a null.
        rows = [{"a": 1, "c": 3, "b": 2}, {"c": 6, "a": 4}]
        write_table(spark, rows, "a int, b int, c int", "default.write_table_dict_rows")
        got = [
            r.asDict() for r in spark.table("default.write_table_dict_rows").orderBy("a").collect()
        ]
        assert got == [{"a": 1, "b": 2, "c": 3}, {"a": 4, "b": None, "c": 6}]

    def test_tuple_rows_have_no_names_and_are_written_in_their_order(self, spark):
        write_table(spark, [(1, 2, 3)], "a int, b int, c int", "default.write_table_tuple_rows")
        row = spark.table("default.write_table_tuple_rows").collect()[0]
        assert row.asDict() == {"a": 1, "b": 2, "c": 3}

    def test_a_dict_key_the_schema_lacks_is_refused_by_name(self, spark):
        rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4, "stray": 9}]
        with pytest.raises(ValueError, match=r"default\.t.*not in the schema: \['stray'\]"):
            write_table(spark, rows, "a int, b int", "default.t")

    def test_a_schema_column_the_frame_lacks_is_refused_by_name(self, spark):
        frame = pd.DataFrame([{"a": 1}])
        with pytest.raises(ValueError, match=r"default\.t.*not in the frame: \['b'\]"):
            write_table(spark, frame, "a int, b int", "default.t")
