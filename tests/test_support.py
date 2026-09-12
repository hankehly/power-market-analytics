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

import pytest

from tests.support import patch_monotonic, record_sleeps

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
