"""Layered map identification - fuses every source into one decision.

Sources, strongest first:
    MANUAL      the player picked/confirmed a map in the UI (always wins)
    LOG         a VERIFIED rule in data/game/log_rules.json matched a token that a map lists in log_ids
    OCR_INTRO   the map title the game shows as the trial starts (needs 2 agreeing reads)
    OCR_MENU    the map name in the pause/TAB menu (player can open it if detection is unsure)
    VISUAL      future image classifier
Ambiguity (two maps within 5 points) -> MapAmbiguous for a one-tap confirm chip in the UI.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from ..core.bus import EventBus
from ..core.clock import Clock
from ..core.events import (
    LogEvent,
    MapAmbiguous,
    MapCandidate,
    MapIdentified,
    MapSource,
    MatchState,
    MatchStateChanged,
    UserMapOverride,
)
from ..world.map_registry import MapRegistry

log = logging.getLogger(__name__)

OCR_ACCEPT = 0.85
OCR_READS = 2
AMBIGUITY_MARGIN = 0.05


def candidates_from_text(registry: MapRegistry, text: str, source: MapSource, ts_ns: int) -> list[MapCandidate]:
    return [
        MapCandidate(ts_ns=ts_ns, map_id=m.map_id, confidence=m.score / 100.0, source=source, raw_text=text)
        for m in registry.match_text(text)
    ]


class MapIdentifier:
    def __init__(self, registry: MapRegistry, clock: Clock) -> None:
        self.registry = registry
        self.clock = clock
        self.bus: EventBus | None = None
        self.current: str | None = None
        self.current_source: MapSource | None = None
        self._ocr_votes: dict[str, list[float]] = defaultdict(list)

    def attach(self, bus: EventBus) -> None:
        self.bus = bus
        bus.subscribe(MapCandidate, self.on_candidate)
        bus.subscribe(UserMapOverride, lambda e: self._identify(e.map_id, 1.0, MapSource.MANUAL, e.ts_ns))
        bus.subscribe(LogEvent, self._on_log)
        bus.subscribe(MatchStateChanged, self._on_state)

    def _on_state(self, e: MatchStateChanged) -> None:
        if e.state in (MatchState.LOBBY, MatchState.MENU, MatchState.OFFLINE):
            self.current = None
            self.current_source = None
            self._ocr_votes.clear()

    def _on_log(self, e: LogEvent) -> None:
        if e.kind != "map_hint":
            return
        token = str(e.data.get("token") or e.data.get("map") or e.data.get("path") or "")
        map_id = self.registry.by_log_token(token.rsplit("/", 1)[-1]) or self.registry.by_log_token(token)
        if map_id:
            self.on_candidate(
                MapCandidate(
                    ts_ns=e.ts_ns,
                    map_id=map_id,
                    confidence=0.97 if e.verified else 0.6,
                    source=MapSource.LOG,
                    raw_text=token,
                )
            )

    def on_candidate(self, e: MapCandidate) -> None:
        if e.map_id not in self.registry:
            return
        if self.current_source == MapSource.MANUAL and e.source != MapSource.MANUAL:
            return  # never override the player's choice
        if e.source in (MapSource.MANUAL, MapSource.DEMO, MapSource.REPLAY):
            self._identify(e.map_id, e.confidence, e.source, e.ts_ns)
        elif e.source == MapSource.LOG:
            if e.confidence >= 0.9:
                self._identify(e.map_id, e.confidence, e.source, e.ts_ns)
        elif e.source in (MapSource.OCR_INTRO, MapSource.OCR_MENU, MapSource.VISUAL):
            self._ocr_votes[e.map_id].append(e.confidence)
            ranked = sorted(((max(v), len(v), mid) for mid, v in self._ocr_votes.items()), reverse=True)
            best_score, best_n, best_id = ranked[0]
            if len(ranked) > 1 and best_score - ranked[1][0] < AMBIGUITY_MARGIN and best_score >= OCR_ACCEPT:
                if self.bus:
                    self.bus.publish(MapAmbiguous(ts_ns=e.ts_ns, candidates=(best_id, ranked[1][2])))
                return
            if best_score >= OCR_ACCEPT and (best_n >= OCR_READS or best_score >= 0.97):
                self._identify(best_id, best_score, e.source, e.ts_ns)

    def _identify(self, map_id: str, confidence: float, source: MapSource, ts_ns: int) -> None:
        if map_id not in self.registry:
            return
        if map_id == self.current and source != MapSource.MANUAL:
            return
        self.current = map_id
        self.current_source = source
        log.info("map identified: %s (%s, %.2f)", map_id, source, confidence)
        if self.bus:
            self.bus.publish(MapIdentified(ts_ns=ts_ns, map_id=map_id, confidence=confidence, source=source))
