from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:  # works with or without an editable install
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _isolated_user_dir(tmp_path, monkeypatch):
    """Never touch the real %LOCALAPPDATA%\\DeadByMapper from tests."""
    monkeypatch.setenv("DBM_USER_DIR", str(tmp_path / "user"))
    monkeypatch.setenv("DBM_DBD_SAVED_DIR", str(tmp_path / "dbd_saved"))
    yield


@pytest.fixture(scope="session")
def registry():
    from deadbymapper.world.map_registry import MapRegistry

    return MapRegistry.load()


@pytest.fixture(scope="session")
def arena(registry):
    return registry.get("test_realm.test_arena")


def pytest_collection_modifyitems(config, items):
    win = sys.platform == "win32"
    for item in items:
        if "windows_only" in item.keywords and not win:
            item.add_marker(pytest.mark.skip(reason="Windows only"))
        if "needs_game" in item.keywords and not os.environ.get("DBM_GAME_TESTS"):
            item.add_marker(pytest.mark.skip(reason="needs Dead by Daylight running (set DBM_GAME_TESTS=1)"))
