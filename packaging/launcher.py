"""PyInstaller entry script (top-level, so the package's relative imports work when frozen).

Release-only concerns live here, never in src/:
* a crash handler that writes a readable report to %LOCALAPPDATA%\\DeadByMapper\\logs\\crash-*.txt
  and tells the user where it is (the windowed exe has no console to print a traceback to);
* faulthandler for native faults (Qt/driver/ctypes access violations) -> logs\\faulthandler.log;
* a startup-time log line: process creation -> first visible "DeadByMapper" window.
"""

from __future__ import annotations

import contextlib
import faulthandler
import logging
import os
import platform
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

_T0 = time.perf_counter()
FROZEN = bool(getattr(sys, "frozen", False))
DASHBOARD_TITLE = "DeadByMapper"


def _logs_dir() -> Path:
    try:
        from deadbymapper.config.paths import logs_dir

        return logs_dir()
    except Exception:  # the crash may be in the package import itself
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        p = base / "DeadByMapper" / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p


def _write_crash_report(exc: BaseException) -> Path | None:
    try:
        from deadbymapper import __version__
    except Exception:
        __version__ = "?"
    try:
        path = _logs_dir() / f"crash-{datetime.now():%Y%m%d-%H%M%S}.txt"
        body = [
            "DeadByMapper crash report",
            f"time:     {datetime.now().isoformat(timespec='seconds')}",
            f"version:  {__version__} ({'frozen' if FROZEN else 'source'})",
            f"args:     {sys.argv[1:]}",
            f"python:   {sys.version.split()[0]}  windows: {platform.platform()}",
            f"exe:      {sys.executable}",
            "",
            "".join(traceback.format_exception(exc)),
        ]
        path.write_text("\n".join(body), encoding="utf-8")
        return path
    except Exception:
        return None


def _tell_user(report: Path | None) -> None:
    msg = "DeadByMapper hit an unexpected error and has to close.\n\n"
    msg += f"A crash report was saved to:\n{report}" if report else "No crash report could be written."
    if sys.stderr is not None:
        print(msg, file=sys.stderr)
    if FROZEN and sys.platform == "win32" and "--headless" not in sys.argv:
        with contextlib.suppress(Exception):
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, msg, "DeadByMapper", 0x10)  # MB_ICONERROR


def _process_age_s() -> float | None:
    """Seconds since the OS created this process (covers bootloader + DLL loading before Python ran)."""
    try:
        import psutil

        return time.time() - psutil.Process().create_time()
    except Exception:
        return None


def _watch_first_window(started_age_s: float | None) -> None:
    """Log when our first visible top-level window titled 'DeadByMapper' appears.

    Pure Win32 polling from a daemon thread: it never touches Qt objects (CLAUDE.md threading rule).
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    pid = os.getpid()
    found = threading.Event()
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd: int, _lparam: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value == DASHBOARD_TITLE:
                found.set()
                return False
        return True

    cb = proto(_cb)
    deadline = time.perf_counter() + 60
    while time.perf_counter() < deadline:
        user32.EnumWindows(cb, 0)
        if found.is_set():
            in_python = time.perf_counter() - _T0
            total = f"{(started_age_s or 0) + in_python:.2f} s" if started_age_s is not None else "?"
            logging.getLogger("deadbymapper.startup").info(
                "dashboard window visible: %s after process start (%.2f s in Python, %.2f s before Python)",
                total,
                in_python,
                started_age_s or 0.0,
            )
            return
        time.sleep(0.05)


def _open_fault_log():  # type: ignore[no-untyped-def]
    """faulthandler output for native faults. NOTE: on Windows it also records access violations that
    the OS later handles (e.g. inside a window-procedure callback), so an entry is a bug signal, not
    necessarily a crash. Each launch writes a header so entries can be attributed."""
    path = _logs_dir() / "faulthandler.log"
    mode = "w" if path.exists() and path.stat().st_size > 1_000_000 else "a"
    f = path.open(mode, encoding="utf-8")
    f.write(f"--- {datetime.now().isoformat(timespec='seconds')} pid={os.getpid()} args={sys.argv[1:]}\n")
    f.flush()
    return f


def run() -> int:
    native_log = None
    with contextlib.suppress(Exception):
        native_log = _open_fault_log()
        faulthandler.enable(native_log)
    try:
        from deadbymapper.__main__ import main

        if FROZEN and sys.platform == "win32" and not {"--headless", "--screenshot"} & set(sys.argv):
            age = _process_age_s()
            # Process age at this point = time spent before the first line of Python ran.
            pre_python = None if age is None else max(0.0, age - (time.perf_counter() - _T0))
            threading.Thread(target=_watch_first_window, args=(pre_python,), name="startup-timer", daemon=True).start()
        return main()
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as exc:
        with contextlib.suppress(Exception):
            logging.getLogger("deadbymapper").critical("unhandled exception", exc_info=exc)
        _tell_user(_write_crash_report(exc))
        return 1
    finally:
        if native_log is not None:
            with contextlib.suppress(Exception):
                faulthandler.disable()
                native_log.close()


if __name__ == "__main__":
    raise SystemExit(run())
