"""DeadByMapper hotkeys -> UserMark events (observe-only: the key still reaches the game).

F9 "I'm at a generator", F10 "I'm at an exit gate", F8 "note this moment" by default
(data/game/keybinds.default.json ``app_hotkeys``). Marks give the localizer landmark fixes before
perception can detect interactions on its own (M1-M3), and label recordings with ground truth.
Live mode only - in replay the recorded UserMark events are replayed as-is.

Hotkeys only ever see gated KeyEvents (input/privacy.py): they fire while DBD is foreground. Because
the key also reaches the game, a hotkey that is ALSO a game bind would trigger both; ``hotkey_collisions``
finds those and the Raw Input listener surfaces them in its health detail (dashboard Input chip).
"""

from __future__ import annotations

import logging

from ..core.bus import EventBus
from ..core.events import KeyEvent, UserMark
from .keymap import _GENERIC_MODIFIERS, VK_NAME, KeyMap

log = logging.getLogger(__name__)


def _equivalent_codes(vk: int) -> set[int]:
    """vk plus its generic/sided modifier twins (a bind to SHIFT also covers LSHIFT and RSHIFT)."""
    out = {vk}
    for generic, sided in _GENERIC_MODIFIERS.items():
        if vk == generic:
            out.update(sided)
        elif vk in sided:
            out.add(generic)
    return out


def hotkey_collisions(keymap: KeyMap) -> list[str]:
    """Human-readable warnings, one per app hotkey that is also bound to a game action."""
    out: list[str] = []
    for vk, kind in sorted(keymap.hotkeys.items()):
        codes = _equivalent_codes(vk)
        actions = sorted({a.value for a, bound in keymap.bindings.items() if bound & codes})
        if actions:
            name = VK_NAME.get(vk, f"0x{vk:02X}")
            out.append(f"WARNING hotkey {name} ({kind}) is also the game bind for {', '.join(actions)}")
    return out


class HotkeyMapper:
    def __init__(self, keymap: KeyMap) -> None:
        self.hotkeys = dict(keymap.hotkeys)
        self.collisions = hotkey_collisions(keymap)
        self._held: set[int] = set()
        self.bus: EventBus | None = None

    def attach(self, bus: EventBus) -> None:
        self.bus = bus
        bus.subscribe(KeyEvent, self.on_key)
        for c in self.collisions:
            log.warning("%s", c)

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
