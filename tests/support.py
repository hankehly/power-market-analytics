"""Helpers shared by the test modules (not fixtures — plain importable code)."""

from __future__ import annotations

import importlib.util
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def record_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record the calling thread's ``time.sleep`` calls instead of sleeping.

    A module's ``time`` attribute *is* the stdlib module, so patching
    ``sleep`` through it reaches every thread. py4j's finalizer thread loops
    on ``time.sleep(1)`` for as long as a SparkSession lives, and a patch that
    returns at once makes it spin, so an unguarded recorder collects hundreds
    of thousands of stray ``1``\\ s (issue #76). Other threads therefore get
    the real sleep and are not recorded.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        The test's monkeypatch fixture; it restores ``time.sleep`` on teardown.

    Returns
    -------
    list of float
        The seconds passed to ``time.sleep`` on this thread, in call order.
        Live: it fills as the code under test runs.
    """
    recorded: list[float] = []
    caller = threading.current_thread()
    real_sleep = time.sleep

    def sleep(seconds: float) -> None:
        if threading.current_thread() is caller:
            recorded.append(seconds)
        else:
            real_sleep(seconds)

    monkeypatch.setattr(time, "sleep", sleep)
    return recorded


def patch_monotonic(monkeypatch: pytest.MonkeyPatch, clock: Callable[[], float]) -> None:
    """Make ``time.monotonic`` read ``clock`` on the calling thread only.

    The patch is process-wide for the same reason :func:`record_sleeps` is, and
    a thread polling a deadline against a frozen clock would never see it move,
    so other threads keep the real ``time.monotonic``.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        The test's monkeypatch fixture; it restores ``time.monotonic`` on teardown.
    clock : callable
        Returns the fake reading, in seconds.
    """
    caller = threading.current_thread()
    real_monotonic = time.monotonic

    def monotonic() -> float:
        return clock() if threading.current_thread() is caller else real_monotonic()

    monkeypatch.setattr(time, "monotonic", monotonic)


def import_script(name: str) -> ModuleType:
    """Import ``scripts/<name>.py`` as a module (scripts are not a package).

    Parameters
    ----------
    name : str
        Script file stem, e.g. ``"download_jepx_spot"``.

    Returns
    -------
    types.ModuleType
        A freshly executed module object, also registered in ``sys.modules``
        under ``name`` so ``monkeypatch.setattr(module, ...)`` works.
    """
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FEATURE_STORE_YAML = """project: pma_test
provider: local
registry: {registry}
offline_store:
  type: spark
online_store:
  type: sqlite
  path: online.db
entity_key_serialization_version: 3
"""


def write_feature_store_yaml(directory: Path, registry: str = "registry.db") -> Path:
    """Write a test ``feature_store.yaml`` (Spark offline store, file registry) into ``directory``.

    Parameters
    ----------
    directory : pathlib.Path
        The store's repo directory, e.g. a ``tmp_path``.
    registry : str, optional
        Registry path relative to ``directory``.

    Returns
    -------
    pathlib.Path
        ``directory``, for :func:`power_market_analytics.features.store.open_store`.
    """
    (directory / "feature_store.yaml").write_text(FEATURE_STORE_YAML.format(registry=registry))
    return directory
