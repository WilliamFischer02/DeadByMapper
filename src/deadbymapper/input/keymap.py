"""Action <-> virtual-key mapping.

Defaults live in data/game/keybinds.default.json and MUST be verified against the player's
in-game bindings (Settings > Controls). game_config.py tries to read Input.ini; the calibration
wizard can also capture bindings by asking the player to press each key.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..config.paths import data_dir

# Windows virtual-key codes we care about.
VK = {
    "LBUTTON": 0x01,
    "RBUTTON": 0x02,
    "MBUTTON": 0x04,
    "XBUTTON1": 0x05,
    "XBUTTON2": 0x06,
    "TAB": 0x09,
    "RETURN": 0x0D,
    "SHIFT": 0x10,
    "CONTROL": 0x11,
    "MENU": 0x12,
    "ESCAPE": 0x1B,
    "SPACE": 0x20,
    "A": 0x41,
    "C": 0x43,
    "D": 0x44,
    "E": 0x45,
    "F": 0x46,
    "Q": 0x51,
    "R": 0x52,
    "S": 0x53,
    "V": 0x56,
    "W": 0x57,
    "X": 0x58,
    "Z": 0x5A,
    "F8": 0x77,
    "F9": 0x78,
    "F10": 0x79,
    "LSHIFT": 0xA0,
    "RSHIFT": 0xA1,
    "LCONTROL": 0xA2,
    "RCONTROL": 0xA3,
    "LMENU": 0xA4,
    "RMENU": 0xA5,
}
VK_NAME = {v: k for k, v in VK.items()}
MOUSE_BUTTON_VK = {
    "left": VK["LBUTTON"],
    "right": VK["RBUTTON"],
    "middle": VK["MBUTTON"],
    "x1": VK["XBUTTON1"],
    "x2": VK["XBUTTON2"],
}


class Action(enum.StrEnum):
    MOVE_FORWARD = "move_forward"
    MOVE_BACK = "move_back"
    MOVE_LEFT = "move_left"
    MOVE_RIGHT = "move_right"
    SPRINT = "sprint"
    CROUCH = "crouch"
    INTERACT = "interact"
    RUSH = "rush"  # vault / pallet / skill check on default binds
    USE_ITEM = "use_item"
    ACTIVE_ABILITY = "active_ability"
    MENU = "menu"
    MATCH_DETAILS = "match_details"


HOTKEY_KINDS = ("generator", "exit_gate", "note")
# Raw Input resolves generic modifiers to left/right codes; a binding to the generic code covers both.
_GENERIC_MODIFIERS = {0x10: (0xA0, 0xA1), 0x11: (0xA2, 0xA3), 0x12: (0xA4, 0xA5)}


def _codes(keys: list[str | int]) -> set[int]:
    return {VK[k] if isinstance(k, str) else int(k) for k in keys}


@dataclass
class KeyMap:
    bindings: dict[Action, set[int]] = field(default_factory=dict)
    sprint_toggle: bool = False
    crouch_toggle: bool = False
    hotkeys: dict[int, str] = field(default_factory=dict)  # vk -> UserMark kind (DeadByMapper's own keys)

    @classmethod
    def load(cls, path: Path | None = None, overrides: dict[str, list[int]] | None = None) -> KeyMap:
        path = path or data_dir() / "game" / "keybinds.default.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        bindings: dict[Action, set[int]] = {}
        for action_name, keys in raw["survivor"].items():
            bindings[Action(action_name)] = _codes(keys)
        for action_name, codes in (overrides or {}).items():
            bindings[Action(action_name)] = set(codes)
        hotkeys: dict[int, str] = {}
        for kind, keys in raw.get("app_hotkeys", {}).items():
            if kind not in HOTKEY_KINDS:
                raise ValueError(f"unknown hotkey kind {kind!r} in {path}")
            for vk in _codes(keys):
                hotkeys[vk] = kind
        return cls(bindings, bool(raw.get("sprint_toggle", False)), bool(raw.get("crouch_toggle", False)), hotkeys)

    def actions_for(self, vk: int) -> list[Action]:
        return [a for a, codes in self.bindings.items() if vk in codes]

    def is_bound(self, action: Action, vk: int) -> bool:
        return vk in self.bindings.get(action, set())

    def allowed_vks(self) -> frozenset[int]:
        """Every key DeadByMapper may observe: bound game actions + its own hotkeys. Nothing else is
        ever published or recorded (input/privacy.py)."""
        out: set[int] = set(self.hotkeys)
        for codes in self.bindings.values():
            out |= codes
        for generic, sided in _GENERIC_MODIFIERS.items():
            if generic in out:
                out.update(sided)
        mouse_vks = set(MOUSE_BUTTON_VK.values())  # buttons arrive as MouseButton events, not KeyEvent
        return frozenset(out - mouse_vks)
