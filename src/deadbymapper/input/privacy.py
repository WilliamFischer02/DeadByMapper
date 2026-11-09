"""Source-side privacy gate for Raw Input. DeadByMapper is not a keylogger.

Raw Input with RIDEV_INPUTSINK delivers keyboard/mouse input system-wide, even while other apps are
focused. Everything that reaches the bus can end up in a recording, so the listener asks this gate
BEFORE publishing anything:

  * keyboard: only virtual-keys the app needs (bound game actions + DeadByMapper hotkeys,
    ``KeyMap.allowed_vks()``), and only while the Dead by Daylight window is the OS foreground window;
  * mouse: only while the game window is the foreground window.

Everything else is dropped at the source: never published, never recorded, never logged.
Pure logic (no Win32 here) so it is unit-tested on every OS.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

from ..core.events import GameWindowChanged

MOUSE_RECHECK_S = 0.02  # mouse can arrive at 8 kHz - re-query the foreground window at most every 20 ms


class InputGate:
    def __init__(
        self,
        allowed_vks: Iterable[int],
        foreground_hwnd: Callable[[], int | None],
        *,
        now_s: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.allowed_vks = frozenset(allowed_vks)
        self._foreground_hwnd = foreground_hwnd
        self._now_s = now_s
        self.game_hwnd: int | None = None
        self._mouse_ok = False
        self._mouse_checked_s = float("-inf")
        self.dropped = 0  # count only - the dropped content itself is never kept

    def on_game_window(self, e: GameWindowChanged) -> None:
        self.game_hwnd = e.hwnd
        self._mouse_checked_s = float("-inf")

    def game_is_foreground(self) -> bool:
        return self.game_hwnd is not None and self._foreground_hwnd() == self.game_hwnd

    def allow_key(self, vk: int) -> bool:
        ok = vk in self.allowed_vks and self.game_is_foreground()
        if not ok:
            self.dropped += 1
        return ok

    def allow_mouse(self) -> bool:
        now = self._now_s()
        if now - self._mouse_checked_s >= MOUSE_RECHECK_S:
            self._mouse_ok = self.game_is_foreground()
            self._mouse_checked_s = now
        if not self._mouse_ok:
            self.dropped += 1
        return self._mouse_ok
