"""Minimal ctypes helpers for window discovery (Windows only; importable everywhere).

Read-only window queries: EnumWindows / GetWindowThreadProcessId / GetClientRect / ClientToScreen /
GetForegroundWindow. No OpenProcess, no ReadProcessMemory, no hooks.
"""

from __future__ import annotations

import contextlib
import sys
from dataclasses import dataclass

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:  # pragma: no cover - exercised on Windows CI / the user's PC
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]


@dataclass(frozen=True, slots=True)
class WindowInfo:
    hwnd: int
    class_name: str
    client_rect: tuple[int, int, int, int]  # screen x, y, w, h (physical px; process must be per-monitor DPI aware)
    window_rect: tuple[int, int, int, int]
    monitor_rect: tuple[int, int, int, int]
    focused: bool

    @property
    def display_mode(self) -> str:
        x, y, w, h = self.window_rect
        mx, my, mw, mh = self.monitor_rect
        covers = x <= mx and y <= my and x + w >= mx + mw and y + h >= my + mh
        # Exclusive fullscreen is indistinguishable from borderless here; capture still works via
        # DXGI duplication, but transparent overlays cannot draw over exclusive fullscreen.
        return "windowed_fullscreen" if covers else "windowed"


def set_dpi_awareness() -> None:
    """Per-monitor v2 so rects are physical pixels matching captured frames. Call before Qt starts."""
    if not IS_WINDOWS:
        return
    try:  # pragma: no cover
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except Exception:
        with contextlib.suppress(Exception):
            ctypes.windll.shcore.SetProcessDpiAwareness(2)


def find_main_window(pid: int) -> WindowInfo | None:
    if not IS_WINDOWS:
        return None
    found: list[int] = []  # pragma: no cover - Windows only below

    @EnumWindowsProc
    def _cb(hwnd, _lparam):  # type: ignore[no-untyped-def]
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(_cb, 0)
    best: WindowInfo | None = None
    for hwnd in found:
        info = window_info(hwnd)
        area = info.client_rect[2] * info.client_rect[3] if info else 0
        if info and (best is None or area > best.client_rect[2] * best.client_rect[3]):
            best = info
    return best


def window_info(hwnd: int) -> WindowInfo | None:  # pragma: no cover
    if not IS_WINDOWS:
        return None
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    rc = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
        return None
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    wr = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wr))
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(mi))
    m = mi.rcMonitor
    return WindowInfo(
        hwnd=int(hwnd),
        class_name=buf.value,
        client_rect=(pt.x, pt.y, rc.right - rc.left, rc.bottom - rc.top),
        window_rect=(wr.left, wr.top, wr.right - wr.left, wr.bottom - wr.top),
        monitor_rect=(m.left, m.top, m.right - m.left, m.bottom - m.top),
        focused=int(user32.GetForegroundWindow() or 0) == int(hwnd),
    )
