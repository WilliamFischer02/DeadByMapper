"""Minimal ctypes helpers for window discovery (Windows only; importable everywhere).

Read-only window queries: EnumWindows / GetWindowThreadProcessId / GetWindowText / GetClientRect /
ClientToScreen / DwmGetWindowAttribute / GetForegroundWindow / MonitorFromWindow. No OpenProcess with
VM rights, no ReadProcessMemory, no hooks (docs/FAIR_PLAY.md). The owning executable's NAME is looked up
with psutil (snapshot / limited-query information only), exactly like gamelink/process_watcher.py.

All rects are PHYSICAL screen pixels: every query runs under a per-monitor-v2 thread DPI context
(``_physical_px``), so the numbers match WGC/DXGI frames even if the process itself is not DPI aware.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from .process_watcher import DBD_EXE_NAMES

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

Rect = tuple[int, int, int, int]  # x, y, w, h

# Window styles (winuser.h)
WS_CAPTION = 0x00C00000  # WS_BORDER | WS_DLGFRAME
WS_THICKFRAME = 0x00040000
WS_BORDER = 0x00800000
WS_POPUP = 0x80000000
WS_EX_TOOLWINDOW = 0x00000080

# Dead by Daylight's window title, matched as a case-insensitive prefix. verified: false (awaiting the
# user's confirmation via tools/capture_probe.py). Observed once on the target PC, 2026-11-08, Steam build:
# title 'DeadByDaylight  ' (two trailing spaces), class 'UnrealWindow'. Exe names are process_watcher.py's.
DBD_WINDOW_TITLE = "DeadByDaylight"

if IS_WINDOWS:  # pragma: no cover - exercised on Windows CI / the user's PC
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]

    _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
    _DWMWA_EXTENDED_FRAME_BOUNDS = 9
    _DWMWA_CLOAKED = 14
    _GWL_STYLE = -16
    _GWL_EXSTYLE = -20
    _GW_OWNER = 4
    _MONITOR_DEFAULTTONEAREST = 2


# --------------------------------------------------------------------------- pure logic (tested anywhere)
def classify_display_mode(client_rect: Rect, monitor_rect: Rect, style: int, *, minimized: bool = False) -> str:
    """Display-mode heuristic from OUTSIDE the game (pure; see tests/test_capture.py).

    * ``windowed_fullscreen``: the client area exactly covers its monitor and the window has no caption.
    * ``windowed``: the window has a caption or sizing border, or does not cover its monitor.
    * ``unknown``: minimized (no meaningful rect).

    ``fullscreen`` (exclusive) is never returned: an exclusive-fullscreen swapchain changes the display
    mode so the monitor rect equals the game resolution and the window is a captionless popup covering
    it - from window styles and rects it is indistinguishable from windowed fullscreen. Only the effects
    are observable (WGC frames go black/stop; overlays are hidden), which capture health reports.
    """
    if minimized:
        return "unknown"
    _, _, cw, ch = client_rect
    if cw <= 0 or ch <= 0:
        return "unknown"
    if style & (WS_CAPTION | WS_THICKFRAME):
        return "windowed"
    if tuple(client_rect) == tuple(monitor_rect):
        return "windowed_fullscreen"
    return "windowed"


@dataclass(frozen=True, slots=True)
class WindowMatcher:
    """Which top-level window is the game? Case-insensitive; exe match beats title match.

    ``title_mode="prefix"`` (default, used for the game) matches titles that START with one of
    ``titles``; ``"contains"`` (probe/testing, e.g. "Untitled - Notepad") matches anywhere.
    """

    titles: tuple[str, ...] = (DBD_WINDOW_TITLE,)
    exe_names: tuple[str, ...] = DBD_EXE_NAMES
    title_mode: str = "prefix"
    pids: frozenset[int] = field(default_factory=frozenset)  # explicit PID override (probe --pid)
    exclude_pids: frozenset[int] = field(default_factory=frozenset)  # never capture our own windows

    def score(self, title: str, exe: str | None, pid: int) -> int:
        """0 = no match; higher is better (pid 4 > exe 3 > title prefix 2 > title contains 1)."""
        if pid in self.exclude_pids:
            return 0
        if self.pids:
            return 4 if pid in self.pids else 0
        if exe and exe.lower() in {e.lower() for e in self.exe_names}:
            return 3
        t = title.strip().lower()
        if not t:
            return 0
        for want in self.titles:
            w = want.lower()
            if t.startswith(w):
                return 2
            if self.title_mode == "contains" and w in t:
                return 1
        return 0

    def describe(self) -> str:
        if self.pids:
            return f"pid {', '.join(str(p) for p in sorted(self.pids))}"
        parts = [f"title {self.title_mode} {'|'.join(self.titles)!r}"] if self.titles else []
        if self.exe_names:
            parts.append(f"exe {'|'.join(self.exe_names)}")
        return " or ".join(parts) or "nothing"


@dataclass(frozen=True, slots=True)
class WindowInfo:
    hwnd: int
    class_name: str
    client_rect: Rect  # screen x, y, w, h in PHYSICAL pixels
    window_rect: Rect  # GetWindowRect (includes invisible resize borders on Win10/11)
    monitor_rect: Rect
    focused: bool
    title: str = ""
    pid: int = 0
    exe: str | None = None
    style: int = 0
    minimized: bool = False
    frame_rect: Rect | None = None  # DWM extended frame bounds = what WGC window capture delivers
    dpi: int = 96

    @property
    def display_mode(self) -> str:
        return classify_display_mode(self.client_rect, self.monitor_rect, self.style, minimized=self.minimized)

    @property
    def area(self) -> int:
        return max(self.client_rect[2], 0) * max(self.client_rect[3], 0)


# --------------------------------------------------------------------------- Win32
def set_dpi_awareness() -> None:
    """Per-monitor v2 so rects are physical pixels matching captured frames. Call before Qt starts."""
    if not IS_WINDOWS:
        return
    try:  # pragma: no cover
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except Exception:
        with contextlib.suppress(Exception):
            ctypes.windll.shcore.SetProcessDpiAwareness(2)


@contextlib.contextmanager
def _physical_px() -> Iterator[None]:  # pragma: no cover - Windows only
    """Run Win32 rect queries per-monitor-v2 DPI aware on THIS thread only, then restore."""
    prev = None
    try:
        prev = user32.SetThreadDpiAwarenessContext(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    except Exception:
        prev = None
    try:
        yield
    finally:
        if prev:
            with contextlib.suppress(Exception):
                user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(prev))


def is_window(hwnd: int | None) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    return bool(user32.IsWindow(hwnd))  # pragma: no cover


def is_minimized(hwnd: int) -> bool:
    if not IS_WINDOWS:
        return False
    return bool(user32.IsIconic(hwnd))  # pragma: no cover


def window_title(hwnd: int) -> str:  # pragma: no cover - Windows only
    if not IS_WINDOWS:
        return ""
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_pid(hwnd: int) -> int:  # pragma: no cover - Windows only
    if not IS_WINDOWS:
        return 0
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    return int(owner.value)


def process_name(pid: int) -> str | None:
    """Executable file name of ``pid`` via psutil (no VM access). None if it cannot be read."""
    if pid <= 0:
        return None
    try:
        import psutil

        return psutil.Process(pid).name() or None
    except Exception:
        return None


def _is_cloaked(hwnd: int) -> bool:  # pragma: no cover - Windows only
    val = wintypes.DWORD(0)
    try:
        hr = dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        return False
    return hr == 0 and val.value != 0


def top_level_windows() -> list[int]:
    """Visible, uncloaked, unowned top-level windows (what a user would call 'a window')."""
    if not IS_WINDOWS:
        return []
    found: list[int] = []  # pragma: no cover - Windows only below

    @EnumWindowsProc
    def _cb(hwnd, _lparam):  # type: ignore[no-untyped-def]
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, _GW_OWNER) and not _is_cloaked(hwnd):
            found.append(int(hwnd))
        return True

    user32.EnumWindows(_cb, 0)
    return found


def _pick_largest(infos: Iterable[WindowInfo | None]) -> WindowInfo | None:
    best: WindowInfo | None = None
    for info in infos:
        if info is not None and (best is None or info.area > best.area):
            best = info
    return best


def find_main_window(pid: int) -> WindowInfo | None:
    """Largest visible top-level window owned by ``pid``."""
    if not IS_WINDOWS:
        return None
    return _pick_largest(window_info(h) for h in top_level_windows() if window_pid(h) == pid)  # pragma: no cover


def find_window(matcher: WindowMatcher) -> WindowInfo | None:
    """Best-matching visible top-level window (highest score, then largest client area)."""
    if not IS_WINDOWS:
        return None
    best: tuple[int, int, WindowInfo] | None = None  # pragma: no cover - Windows only below
    names: dict[int, str | None] = {}
    for hwnd in top_level_windows():
        pid = window_pid(hwnd)
        title = window_title(hwnd)
        if pid not in names:
            names[pid] = process_name(pid) if matcher.exe_names else None
        s = matcher.score(title, names[pid], pid)
        if s <= 0:
            continue
        info = window_info(hwnd, title=title, pid=pid, exe=names[pid])
        if info is None:
            continue
        key = (s, info.area)
        if best is None or key > best[:2]:
            best = (s, info.area, info)
    return best[2] if best else None


def window_info(
    hwnd: int, *, title: str | None = None, pid: int | None = None, exe: str | None = None
) -> WindowInfo | None:
    if not IS_WINDOWS:
        return None
    with _physical_px():  # pragma: no cover - Windows only below
        if not user32.IsWindow(hwnd):
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
        fr = wintypes.RECT()
        frame_rect: Rect | None = None
        if dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(fr), ctypes.sizeof(fr)) == 0:
            frame_rect = (fr.left, fr.top, fr.right - fr.left, fr.bottom - fr.top)
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST), ctypes.byref(mi))
        m = mi.rcMonitor
        style = int(user32.GetWindowLongPtrW(hwnd, _GWL_STYLE)) & 0xFFFFFFFF
        try:
            dpi = int(user32.GetDpiForWindow(hwnd)) or 96
        except Exception:
            dpi = 96
        p = window_pid(hwnd) if pid is None else pid
        return WindowInfo(
            hwnd=int(hwnd),
            class_name=buf.value,
            client_rect=(pt.x, pt.y, rc.right - rc.left, rc.bottom - rc.top),
            window_rect=(wr.left, wr.top, wr.right - wr.left, wr.bottom - wr.top),
            monitor_rect=(m.left, m.top, m.right - m.left, m.bottom - m.top),
            focused=int(user32.GetForegroundWindow() or 0) == int(hwnd),
            title=window_title(hwnd) if title is None else title,
            pid=p,
            exe=exe if exe is not None else process_name(p),
            style=style,
            minimized=bool(user32.IsIconic(hwnd)),
            frame_rect=frame_rect,
            dpi=dpi,
        )
