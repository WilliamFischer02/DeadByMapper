"""Live match state, fed by events, read by the UI as immutable snapshots.

Thread model: mutated only on the bus dispatcher thread; ``snapshot()`` may be called from the
UI thread (guarded by a lock, returns an immutable copy).
"""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass, field, replace

from ..core.bus import EventBus
from ..core.clock import Clock, ns_to_s
from ..core.events import (
    GenCounterObserved,
    InteractionKind,
    InteractionObserved,
    KillerTerrorRadiusSet,
    MapIdentified,
    MatchState,
    MatchStateChanged,
    PlayerStatus,
    PlayerStatusObserved,
    PoseEstimate,
    PoseStatus,
    Role,
    TerrorRadiusObserved,
    UserMapOverride,
    UserMark,
    UserMarkGenerator,
)
from . import gen_strategy
from .map_model import MapData
from .map_registry import MapRegistry

ASSOCIATE_RADIUS_M = 5.0
TRAIL_JUMP_M = 8.0


@dataclass(frozen=True, slots=True)
class GenView:
    id: str
    x: float
    y: float
    sigma_m: float
    fixed: bool
    state: str  # "candidate" | "spawned" | "completed" | "regressing" | "not_spawned"
    recommended_rank: int | None = None


@dataclass(frozen=True, slots=True)
class GateView:
    id: str
    x: float
    y: float
    facing_deg: float | None
    state: str  # "candidate" | "found" | "powered" | "open"


@dataclass(frozen=True, slots=True)
class TerrorView:
    tier: int
    inner_m: float
    outer_m: float
    in_chase: bool
    bpm: float | None
    age_s: float
    lullaby: bool


@dataclass(frozen=True, slots=True)
class MatchSnapshot:
    match_state: MatchState = MatchState.OFFLINE
    role: Role = Role.UNKNOWN
    manual_trial: bool = False  # the player started this trial by hand (no automatic detection yet)
    map: MapData | None = None
    pose: PoseEstimate | None = None
    player_status: PlayerStatus = PlayerStatus.UNKNOWN
    gens: tuple[GenView, ...] = ()
    gates: tuple[GateView, ...] = ()
    gens_remaining: int | None = None
    trail: tuple[tuple[float, float], ...] = ()
    terror: TerrorView | None = None
    terror_radius_m: float = 32.0
    strategy: gen_strategy.StrategyReport | None = None
    active_interaction: InteractionKind | None = None
    match_elapsed_s: float = 0.0
    notes: tuple[str, ...] = field(default_factory=tuple)


class MatchModel:
    def __init__(self, registry: MapRegistry, clock: Clock, *, trail_points: int = 900) -> None:
        self.registry = registry
        self.clock = clock
        self._lock = threading.Lock()
        self._snap = MatchSnapshot()
        self._gens: dict[str, GenView] = {}
        self._gates: dict[str, GateView] = {}
        self._trail: deque[tuple[float, float]] = deque(maxlen=trail_points)
        self._full_path: list[tuple[float, float, float]] = []  # (t, x, y) for post-match review
        self._last_trail_t = 0
        self._active_gen: str | None = None
        self._match_start_ns: int | None = None
        self._terror_ns: int | None = None

    # ------------------------------------------------------------------ wiring
    def attach(self, bus: EventBus) -> None:
        bus.subscribe(MapIdentified, self._on_map)
        bus.subscribe(UserMapOverride, self._on_map)
        bus.subscribe(MatchStateChanged, self._on_state)
        bus.subscribe(PoseEstimate, self._on_pose)
        bus.subscribe(PlayerStatusObserved, self._on_status)
        bus.subscribe(InteractionObserved, self._on_interaction)
        bus.subscribe(GenCounterObserved, self._on_gen_counter)
        bus.subscribe(TerrorRadiusObserved, self._on_terror)
        bus.subscribe(KillerTerrorRadiusSet, self._on_tr_set)
        bus.subscribe(UserMarkGenerator, self._on_user_mark)
        bus.subscribe(UserMark, self._on_hotkey_mark)

    def snapshot(self) -> MatchSnapshot:
        with self._lock:
            snap = self._snap
        if snap.terror is not None and self._terror_ns is not None:
            snap = replace(snap, terror=replace(snap.terror, age_s=ns_to_s(self.clock.now_ns() - self._terror_ns)))
        if self._match_start_ns is not None and snap.match_state == MatchState.IN_MATCH:
            snap = replace(snap, match_elapsed_s=ns_to_s(self.clock.now_ns() - self._match_start_ns))
        return snap

    def full_path(self) -> list[tuple[float, float, float]]:
        with self._lock:
            return list(self._full_path)

    def _publish(self, **changes: object) -> None:
        with self._lock:
            self._snap = replace(self._snap, **changes)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ handlers
    def _on_map(self, e: MapIdentified | UserMapOverride) -> None:
        if self._snap.map is not None and self._snap.map.id == e.map_id:
            return  # same map confirmed again mid-trial: keep gen states and trail (new trials reset in _on_state)
        self._load_map(e.map_id)

    def _load_map(self, map_id: str) -> None:
        if map_id not in self.registry:
            return
        m = self.registry.get(map_id)
        self._gens = {
            g.id: GenView(g.id, g.pos_m[0], g.pos_m[1], g.sigma_m, g.fixed, "spawned" if g.fixed else "candidate")
            for g in m.generators
        }
        self._gates = {g.id: GateView(g.id, g.pos_m[0], g.pos_m[1], g.facing_deg, "candidate") for g in m.exit_gates}
        self._trail.clear()
        self._full_path.clear()
        self._publish(map=m, gens=self._gen_tuple(), gates=tuple(self._gates.values()), trail=(), strategy=None)
        self._recompute_strategy()

    def _on_state(self, e: MatchStateChanged) -> None:
        if e.state == MatchState.IN_MATCH and self._snap.match_state != MatchState.IN_MATCH:
            self._match_start_ns = e.ts_ns
            self._trail.clear()
            self._full_path.clear()
            self._active_gen = None
            if self._snap.map is not None:
                self._load_map(self._snap.map.id)
        self._publish(match_state=e.state, role=e.role, manual_trial=e.manual and e.state == MatchState.IN_MATCH)

    def _on_pose(self, e: PoseEstimate) -> None:
        changes: dict[str, object] = {"pose": e}
        prev = self._snap.pose
        if prev is not None and prev.relative and not e.relative:
            self._trail.clear()  # first absolute fix: relative breadcrumb coords are a different frame
            changes["trail"] = ()
        stable = e.status in (PoseStatus.LOCKED, PoseStatus.CONVERGING)
        fresh = e.ts_ns - self._last_trail_t > 250_000_000
        if not e.relative and stable and fresh and math.isfinite(e.x) and math.isfinite(e.y):
            last = self._trail[-1] if self._trail else None
            if last is not None and math.hypot(e.x - last[0], e.y - last[1]) > TRAIL_JUMP_M:
                self._trail.clear()  # hypothesis switched: do not draw a line across the map
                last = None
            if last is None or math.hypot(e.x - last[0], e.y - last[1]) > 0.4:
                self._trail.append((e.x, e.y))
                self._full_path.append((ns_to_s(e.ts_ns), e.x, e.y))
                self._last_trail_t = e.ts_ns
                changes["trail"] = tuple(self._trail)
        self._publish(**changes)

    def _on_status(self, e: PlayerStatusObserved) -> None:
        self._publish(player_status=e.status)

    def _on_interaction(self, e: InteractionObserved) -> None:
        self._publish(active_interaction=e.kind if e.active else None)
        if e.kind != InteractionKind.GENERATOR:
            if e.kind == InteractionKind.EXIT_GATE and e.active:
                self._associate_gate()
            return
        if not e.active:
            self._active_gen = None
            return
        self._active_gen = self._touch_nearest_gen()

    def _on_hotkey_mark(self, e: UserMark) -> None:
        # F9/F10: the player says "I'm at one" - confirm it spawned, but do not start an interaction
        # (we don't know when it ends, so gen-counter completions are not attributed to it).
        if e.kind == "generator":
            self._touch_nearest_gen()
        elif e.kind == "exit_gate":
            self._associate_gate()

    def _touch_nearest_gen(self) -> str | None:
        gid = self._nearest_gen()
        if gid is not None:
            g = self._gens[gid]
            if g.state in ("candidate", "not_spawned"):
                self._gens[gid] = replace(g, state="spawned")
                self._publish(gens=self._gen_tuple())
                self._recompute_strategy()
        return gid

    def _nearest_gen(self) -> str | None:
        pose = self._snap.pose
        if pose is None or pose.status not in (PoseStatus.LOCKED, PoseStatus.CONVERGING):
            return None
        best, best_d = None, ASSOCIATE_RADIUS_M
        for g in self._gens.values():
            d = math.hypot(g.x - pose.x, g.y - pose.y)
            if d < best_d:
                best, best_d = g.id, d
        return best

    def _associate_gate(self) -> None:
        pose = self._snap.pose
        if pose is None or pose.status not in (PoseStatus.LOCKED, PoseStatus.CONVERGING):
            return
        for gid, g in self._gates.items():
            if math.hypot(g.x - pose.x, g.y - pose.y) < ASSOCIATE_RADIUS_M + 3:
                self._gates[gid] = replace(g, state="found")
                self._publish(gates=tuple(self._gates.values()))
                return

    def _on_gen_counter(self, e: GenCounterObserved) -> None:
        prev = self._snap.gens_remaining
        if prev is not None and e.remaining < prev and self._active_gen is not None:
            g = self._gens[self._active_gen]
            self._gens[self._active_gen] = replace(g, state="completed")
            self._active_gen = None
            self._publish(gens=self._gen_tuple())
            self._recompute_strategy()
        if e.remaining == 0:
            self._gates = {
                k: replace(v, state="powered" if v.state == "candidate" else v.state) for k, v in self._gates.items()
            }
            self._publish(gates=tuple(self._gates.values()))
        self._publish(gens_remaining=e.remaining)

    def _on_terror(self, e: TerrorRadiusObserved) -> None:
        tr = self._snap.terror_radius_m
        if e.tier <= 0 and not e.in_chase:
            self._terror_ns = None
            self._publish(terror=None)
            return
        tier = 3 if e.in_chase and e.tier == 0 else max(1, min(3, e.tier))
        inner = tr * (3 - tier) / 3.0
        outer = tr * (4 - tier) / 3.0
        self._terror_ns = e.ts_ns
        self._publish(terror=TerrorView(tier, inner, outer, e.in_chase, e.bpm, 0.0, e.lullaby))

    def _on_tr_set(self, e: KillerTerrorRadiusSet) -> None:
        self._publish(terror_radius_m=e.radius_m)

    def _on_user_mark(self, e: UserMarkGenerator) -> None:
        if e.gen_id in self._gens:
            self._gens[e.gen_id] = replace(self._gens[e.gen_id], state=e.state)
            self._publish(gens=self._gen_tuple())
            self._recompute_strategy()

    # ------------------------------------------------------------------ strategy
    def _gen_tuple(self) -> tuple[GenView, ...]:
        return tuple(self._gens.values())

    def _recompute_strategy(self) -> None:
        m = self._snap.map
        if m is None or not self._gens:
            return
        known = [g for g in self._gens.values() if g.state in ("spawned", "regressing")]
        live = [g for g in self._gens.values() if g.state in ("candidate", "spawned", "regressing")]
        pool = known if len(known) >= 3 else live
        report = gen_strategy.analyse([(g.id, g.x, g.y) for g in pool], m.width_m, m.height_m)
        ranks = {s.gen_id: i + 1 for i, s in enumerate(report.ranking)}
        self._gens = {k: replace(v, recommended_rank=ranks.get(k)) for k, v in self._gens.items()}
        self._publish(strategy=report, gens=self._gen_tuple())
