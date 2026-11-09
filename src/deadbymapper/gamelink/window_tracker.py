"""Tracks the game window rect/focus and publishes GameWindowChanged + InputFocusChanged."""

from __future__ import annotations

from ..core.bus import EventBus
from ..core.clock import Clock
from ..core.events import GameProcessChanged, GameWindowChanged, InputFocusChanged
from ..core.lifecycle import ThreadedService
from . import win32_util


class WindowTracker(ThreadedService):
    name = "gamelink.window"
    interval_s = 0.25

    def __init__(self, bus: EventBus, clock: Clock) -> None:
        super().__init__(bus, clock)
        self._pid: int | None = None
        self._last: tuple | None = None
        self._focused: bool | None = None
        bus.subscribe(GameProcessChanged, self._on_proc)

    def _on_proc(self, e: GameProcessChanged) -> None:
        self._pid = e.pid if e.running else None
        if not e.running:
            self._emit(None)

    def loop_once(self) -> None:
        if self._pid is None:
            return
        self._emit(win32_util.find_main_window(self._pid))

    def _emit(self, info: win32_util.WindowInfo | None) -> None:
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
