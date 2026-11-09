"""DeadByMapper hotkeys -> UserMark events (observe-only: the key still reaches the game).

F9 "I'm at a generator", F10 "I'm at an exit gate", F8 "note this moment" by default
(data/game/keybinds.default.json ``app_hotkeys``). Marks give the localizer landmark fixes before
perception can detect interactions on its own (M1-M3), and label recordings with ground truth.
Live mode only - in replay the recorded UserMark events are replayed as-is.
"""

from __future__ import annotations

from ..core.bus import EventBus
from ..core.events import KeyEvent, UserMark
from .keymap import KeyMap


class HotkeyMapper:
    def __init__(self, keymap: KeyMap) -> None:
        self.hotkeys = dict(keymap.hotkeys)
        self._held: set[int] = set()
        self.bus: EventBus | None = None

    def attach(self, bus: EventBus) -> None:
        self.bus = bus
        bus.subscribe(KeyEvent, self.on_key)

    def on_key(self, e: KeyEvent) -> None:
        kind = self.hotkeys.get(e.vk)
        if kind is None:
            return
        if not e.down:
            self._held.discard(e.vk)
            return
        if e.vk in self._held:  # keyboard auto-repeat while held
            return
        self._held.add(e.vk)
        if self.bus is not None:
            self.bus.publish(UserMark(ts_ns=e.ts_ns, kind=kind))
