"""Filesystem locations.

* Bundled, read-only data (map JSON, schemas, game constants, QML): ``data_dir()`` / ``qml_dir()``.
  Resolves to the repo in a dev checkout and to ``sys._MEIPASS`` in a PyInstaller build.
* Per-user writable state: ``user_dir()`` -> ``%LOCALAPPDATA%\\DeadByMapper`` on Windows
  (settings.json, calibration, logs, recordings, personal map overlays).
* Dead by Daylight's own files (read-only, shared-read): ``dbd_logs_dir()``, ``dbd_config_dirs()``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from platformdirs import user_data_dir

from .. import APP_NAME

_PKG_DIR = Path(__file__).resolve().parent.parent  # .../src/deadbymapper


def _frozen_root() -> Path | None:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


def repo_root() -> Path:
    """Repo root in a dev checkout (src/deadbymapper/../../)."""
    return _PKG_DIR.parent.parent


def data_dir() -> Path:
    frozen = _frozen_root()
    if frozen is not None:
        return frozen / "data"
    override = os.environ.get("DBM_DATA_DIR")
    return Path(override) if override else repo_root() / "data"


def qml_dir() -> Path:
    frozen = _frozen_root()
    if frozen is not None:
        return frozen / "deadbymapper" / "ui" / "qml"
    return _PKG_DIR / "ui" / "qml"


def user_dir() -> Path:
    override = os.environ.get("DBM_USER_DIR")
    path = Path(override) if override else Path(user_data_dir(APP_NAME, appauthor=False, roaming=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def recordings_dir() -> Path:
    p = user_dir() / "recordings"
    p.mkdir(parents=True, exist_ok=True)
    return p


def overlays_dir() -> Path:
    """Personal reference images (third-party/copyrighted) - never committed, never redistributed."""
    p = user_dir() / "overlays"
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = user_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


# --------------------------------------------------------------------------- Dead by Daylight files
def _localappdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))


def dbd_saved_dir() -> Path:
    override = os.environ.get("DBM_DBD_SAVED_DIR")
    return Path(override) if override else _localappdata() / "DeadByDaylight" / "Saved"


def dbd_logs_dir() -> Path:
    """Contains DeadByDaylight.log (a new file every launch) and DeadByDaylight-backup-*.log."""
    return dbd_saved_dir() / "Logs"


def dbd_log_file() -> Path:
    return dbd_logs_dir() / "DeadByDaylight.log"


# UE5 builds (DBD moved to UE5 in patch 7.7.0, Apr 2024) use "WindowsClient"; UE4 used
# "WindowsNoEditor"; GDK/Game Pass builds may use "WinGDK". Probe all, newest first.
DBD_CONFIG_SUBDIRS = ("WindowsClient", "Windows", "WinGDK", "WindowsNoEditor")


def dbd_config_dirs() -> list[Path]:
    root = dbd_saved_dir() / "Config"
    return [root / sub for sub in DBD_CONFIG_SUBDIRS if (root / sub).is_dir()]
