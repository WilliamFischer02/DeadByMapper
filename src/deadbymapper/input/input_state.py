"""Aggregates observed raw input into the state the motion model needs.

Gating: input only counts while it actually drives the in-game camera. When the game window is
not focused, or a menu/cursor is up (pause, TAB, chat), keys are released and mouse deltas are
ignored - otherwise alt-tabbing would spin the minimap.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.events import Event, InputFocusChanged, KeyEvent, MouseButton, MouseDelta
from .keymap import MOUSE_BUTTON_VK, Action, KeyMap


@dataclass(frozen=True, slots=True)
class InputSnapshot:
    forward: bool = False
    back: bool = False
    left: bool = False
    right: bool = False
    sprint: bool = False
    crouch: bool = False
    yaw_counts: int = 0  # cumulative raw X counts while camera_control was True
    pitch_counts: int = 0
    camera_control: bool = True
    ts_ns: int = 0


class InputState:
    def __init__(self, keymap: KeyMap) -> None:
        self.keymap = keymap
        self._down: set[int] = set()
        self._yaw = 0
        self._pitch = 0
        self._camera_control = True
        self._sprint_latched = False
        self._crouch_latched = False
        self._ts = 0

    def apply(self, e: Event) -> None:
        self._ts = e.ts_ns
        if isinstance(e, MouseDelta):
            if self._camera_control:
                self._yaw += e.dx
                self._pitch += e.dy
        elif isinstance(e, KeyEvent):
            self._key(e.vk, e.down)
        elif isinstance(e, MouseButton):
            vk = MOUSE_BUTTON_VK.get(e.button)
            if vk is not None:
                self._key(vk, e.down)
        elif isinstance(e, InputFocusChanged):
            self._camera_control = e.camera_control
            if not e.camera_control:
                self._down.clear()

    def _key(self, vk: int, down: bool) -> None:
        if not self._camera_control:
            return
        was_down = vk in self._down
        if down:
            self._down.add(vk)
            if not was_down:
                if self.keymap.sprint_toggle and self.keymap.is_bound(Action.SPRINT, vk):
                    self._sprint_latched = not self._sprint_latched
                if self.keymap.crouch_toggle and self.keymap.is_bound(Action.CROUCH, vk):
                    self._crouch_latched = not self._crouch_latched
        else:
            self._down.discard(vk)

    def _held(self, action: Action) -> bool:
        return bool(self._down & self.keymap.bindings.get(action, set()))

    def reset_yaw(self) -> None:
        self._yaw = 0
        self._pitch = 0

    def snapshot(self) -> InputSnapshot:
        sprint = self._sprint_latched if self.keymap.sprint_toggle else self._held(Action.SPRINT)
        crouch = self._crouch_latched if self.keymap.crouch_toggle else self._held(Action.CROUCH)
        return InputSnapshot(
            forward=self._held(Action.MOVE_FORWARD),
            back=self._held(Action.MOVE_BACK),
            left=self._held(Action.MOVE_LEFT),
            right=self._held(Action.MOVE_RIGHT),
            sprint=sprint,
            crouch=crouch,
            yaw_counts=self._yaw,
            pitch_counts=self._pitch,
            camera_control=self._camera_control,
            ts_ns=self._ts,
        )
