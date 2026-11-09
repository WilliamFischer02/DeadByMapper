"""Match-state machine with hysteresis.

Inputs: GameProcessChanged, ScreenObserved (classifier), LogEvent (verified kinds only).
Output: MatchStateChanged (+ InputFocusChanged when a menu overlay opens/closes mid-match).
A single noisy frame must never flip the state: N agreeing observations are required, except
for OFFLINE (process gone) which is immediate.
"""

from __future__ import annotations

from collections import deque

from ..core.bus import EventBus
from ..core.clock import Clock
from ..core.events import (
    GameProcessChanged,
    InputFocusChanged,
    LogEvent,
    MatchState,
    MatchStateChanged,
    Role,
    ScreenObserved,
)

_SCREEN_TO_STATE = {
    "menu": (MatchState.MENU, Role.UNKNOWN),
    "lobby": (MatchState.LOBBY, Role.UNKNOWN),
    "loading": (MatchState.LOADING, Role.UNKNOWN),
    "in_match_survivor": (MatchState.IN_MATCH, Role.SURVIVOR),
    "in_match_killer": (MatchState.IN_MATCH, Role.KILLER),
    "tally": (MatchState.POST_MATCH, Role.UNKNOWN),
}
_LOG_TO_STATE = {
    "lobby": MatchState.LOBBY,
    "loading": MatchState.LOADING,
    "match_start": MatchState.IN_MATCH,
    "match_end": MatchState.POST_MATCH,
}


class MatchStateMachine:
    def __init__(self, clock: Clock, *, agree: int = 3, min_conf: float = 0.6) -> None:
        self.clock = clock
        self.agree = agree
        self.min_conf = min_conf
        self.state = MatchState.OFFLINE
        self.role = Role.UNKNOWN
        self._recent: deque[tuple[MatchState, Role]] = deque(maxlen=agree)
        self._menu_overlay = False
        self.bus: EventBus | None = None

    def attach(self, bus: EventBus) -> None:
        self.bus = bus
        bus.subscribe(GameProcessChanged, self._on_proc)
        bus.subscribe(ScreenObserved, self._on_screen)
        bus.subscribe(LogEvent, self._on_log)

    def _set(self, state: MatchState, role: Role, ts_ns: int) -> None:
        if state == self.state and role in (self.role, Role.UNKNOWN):
            return
        if state == MatchState.IN_MATCH and role == Role.UNKNOWN:
            role = self.role
        self.state, self.role = state, (role if state == MatchState.IN_MATCH else Role.UNKNOWN)
        if self.bus:
            self.bus.publish(MatchStateChanged(ts_ns=ts_ns, state=self.state, role=self.role))

    def _on_proc(self, e: GameProcessChanged) -> None:
        self._recent.clear()
        self._set(MatchState.MENU if e.running else MatchState.OFFLINE, Role.UNKNOWN, e.ts_ns)

    def _on_screen(self, e: ScreenObserved) -> None:
        if self.state == MatchState.OFFLINE or e.confidence < self.min_conf:
            return
        if self.state == MatchState.IN_MATCH and e.menu_overlay != self._menu_overlay:
            self._menu_overlay = e.menu_overlay
            if self.bus:
                self.bus.publish(
                    InputFocusChanged(ts_ns=e.ts_ns, camera_control=not e.menu_overlay, reason="menu overlay")
                )
        target = _SCREEN_TO_STATE.get(e.screen)
        if target is None:
            return
        self._recent.append(target)
        if len(self._recent) == self.agree and all(t == target for t in self._recent):
            self._set(target[0], target[1], e.ts_ns)

    def _on_log(self, e: LogEvent) -> None:
        if not e.verified or e.kind not in _LOG_TO_STATE or self.state == MatchState.OFFLINE:
            return
        self._recent.clear()
        self._set(_LOG_TO_STATE[e.kind], Role.UNKNOWN, e.ts_ns)
