"""Tracks the game window rect/focus and publishes GameWindowChanged + InputFocusChanged.

Discovery: the PID from GameProcessChanged (process_watcher) is authoritative; until one arrives, a
title/exe ``WindowMatcher`` scan runs every ``scan_interval_s`` so the tracker also works when the
process watcher does not know the exe name. Rects are physical pixels (win32_util queries run
per-monitor-v2 DPI aware). ``display_mode`` is a heuristic - see ``win32_util.classify_display_mode``.
"""

from __future__ import annotations

import os

from ..core.bus import EventBus
from ..core.clock import Clock
from ..core.events import GameProcessChanged, GameWindowChanged, InputFocusChanged
from ..core.lifecycle import ThreadedService
from . import win32_util


class WindowTracker(ThreadedService):
    name = "gamelink.window"
    interval_s = 0.25
    scan_interval_s = 2.0  # title/exe scan when no PID is known (EnumWindows + exe lookups)

    def __init__(self, bus: EventBus, clock: Clock, *, matcher: win32_util.WindowMatcher | None = None) -> None:
        super().__init__(bus, clock)
        self.matcher = matcher or win32_util.WindowMatcher(exclude_pids=frozenset({os.getpid()}))
        self._pid: int | None = None
        self._last: tuple | None = None
        self._focused: bool | None = None
        self._hwnd: int | None = None
        self._next_scan_ns = 0
        self.last_info: win32_util.WindowInfo | None = None
        bus.subscribe(GameProcessChanged, self._on_proc)

    def _on_proc(self, e: GameProcessChanged) -> None:
        self._pid = e.pid if e.running else None
        self._hwnd = None
        if not e.running:
            self._emit(None)

    def loop_once(self) -> None:
        self._emit(self._find())

    def _find(self) -> win32_util.WindowInfo | None:
        # Cheap path: re-query the window we already have.
        if self._hwnd is not None and win32_util.is_window(self._hwnd):
            info = win32_util.window_info(self._hwnd, exe=self.last_info.exe if self.last_info else None)
            if info is not None and (self._pid is None or info.pid == self._pid):
                return info
        self._hwnd = None
        if self._pid is not None:
            info = win32_util.find_main_window(self._pid)
        else:
            now = self.clock.now_ns()
            if now < self._next_scan_ns:
                return None
            self._next_scan_ns = now + int(self.scan_interval_s * 1e9)
            info = win32_util.find_window(self.matcher)
        if info is not None:
            self._hwnd = info.hwnd
        return info

    def _emit(self, info: win32_util.WindowInfo | None) -> None:
        self.last_info = info
        key = None if info is None else (info.hwnd, info.client_rect, info.focused, info.display_mode)
        if key == self._last:
            return
        self._last = key
        now = self.clock.now_ns()
        self.bus.publish(
            GameWindowChanged(
                ts_ns=now,
                hwnd=info.hwnd if info else None,
                client_rect=info.client_rect if info else None,
                focused=bool(info and info.focused),
                display_mode=info.display_mode if info else "unknown",
            )
        )
        focused = bool(info and info.focused)
        if focused != self._focused:
            self._focused = focused
            # Menu/cursor detection (pause, TAB, chat) also feeds InputFocusChanged - see perception.
            self.bus.publish(InputFocusChanged(ts_ns=now, camera_control=focused, reason="window focus"))
