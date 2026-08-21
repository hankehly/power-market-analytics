"""Helpers shared by the test modules (not fixtures — plain importable code)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"


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
