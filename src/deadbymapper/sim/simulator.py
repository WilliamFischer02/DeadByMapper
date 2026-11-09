"""Scripted survivor that *plays* a map through synthetic raw input.

The truth kinematics reproduce the game's survivor control model (camera-relative WASD at the
wiki speeds, wall sliding), and the controller steers only through mouse counts and keys - so the
localizer receives exactly what Raw Input would deliver in a real trial, plus the observations the
perception layer would emit (interactions, optical-flow motion, gen counter, terror radius).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np

from ..core.clock import s_to_ns
from ..core.events import (
    Event,
    GenCounterObserved,
    InteractionKind,
    InteractionObserved,
    KeyEvent,
    KillerTerrorRadiusSet,
    MapIdentified,
    MapSource,
    MatchState,
    MatchStateChanged,
    MotionObserved,
    MouseDelta,
    Role,
    TerrorRadiusObserved,
    Tick,
    TrSource,
)
from ..core.geometry import angle_diff_deg, heading_of, wrap_deg
from ..input.keymap import VK
from ..localization.occupancy import Occupancy
from ..world.map_model import MapData

Point = tuple[float, float]


# =========================================================================== path planning
class PathPlanner:
    """A* on a coarse, obstacle-inflated copy of the occupancy grid + line-of-sight smoothing."""

    def __init__(self, occ: Occupancy, *, res_m: float = 0.5, clearance_m: float = 0.7) -> None:
        f = max(1, round(res_m / occ.res))
        rows, cols = occ.rows // f, occ.cols // f
        fine = occ.walkable[: rows * f, : cols * f].reshape(rows, f, cols, f)
        free = fine.all(axis=(1, 3))
        blocked = ~free
        r = math.ceil(clearance_m / (occ.res * f))
        for _ in range(r):  # dilate blocked cells (8-neighbourhood)
            b = blocked.copy()
            b[1:, :] |= blocked[:-1, :]
            b[:-1, :] |= blocked[1:, :]
            b[:, 1:] |= blocked[:, :-1]
            b[:, :-1] |= blocked[:, 1:]
            blocked = b
        self.free = ~blocked
        self.res = occ.res * f
        self.rows, self.cols = rows, cols

    def _cell(self, p: Point) -> tuple[int, int]:
        return (min(max(int(p[1] / self.res), 0), self.rows - 1), min(max(int(p[0] / self.res), 0), self.cols - 1))

    def _center(self, c: tuple[int, int]) -> Point:
        return ((c[1] + 0.5) * self.res, (c[0] + 0.5) * self.res)

    def los(self, a: Point, b: Point) -> bool:
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(2, int(d / (self.res * 0.5)))
        for i in range(n + 1):
            t = i / n
            c = self._cell((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            if not self.free[c]:
                return False
        return True

    def nearest_free(self, p: Point) -> Point:
        c = self._cell(p)
        if self.free[c]:
            return p
        best, best_d = None, 1e18
        r0, c0 = c
        for rad in range(1, 40):
            for dr in range(-rad, rad + 1):
                for dc in (-rad, rad) if abs(dr) != rad else range(-rad, rad + 1):
                    rr, cc = r0 + dr, c0 + dc
                    if 0 <= rr < self.rows and 0 <= cc < self.cols and self.free[rr, cc]:
                        d = dr * dr + dc * dc
                        if d < best_d:
                            best, best_d = (rr, cc), d
            if best is not None:
                return self._center(best)
        return p

    def plan(self, start: Point, goal: Point) -> list[Point]:
        start, goal = self.nearest_free(start), self.nearest_free(goal)
        if self.los(start, goal):
            return [start, goal]
        s, g = self._cell(start), self._cell(goal)
        openq: list[tuple[float, tuple[int, int]]] = [(0.0, s)]
        came: dict[tuple[int, int], tuple[int, int]] = {}
        cost = {s: 0.0}
        nbrs = [
            (-1, 0, 1.0),
            (1, 0, 1.0),
            (0, -1, 1.0),
            (0, 1, 1.0),
            (-1, -1, 1.4142),
            (-1, 1, 1.4142),
            (1, -1, 1.4142),
            (1, 1, 1.4142),
        ]
        while openq:
            _, cur = heapq.heappop(openq)
            if cur == g:
                break
            for dr, dc, w in nbrs:
                nr, nc = cur[0] + dr, cur[1] + dc
                if not (0 <= nr < self.rows and 0 <= nc < self.cols) or not self.free[nr, nc]:
                    continue
                nxt = (nr, nc)
                c2 = cost[cur] + w
                if c2 < cost.get(nxt, 1e18):
                    cost[nxt] = c2
                    came[nxt] = cur
                    h = math.hypot(nr - g[0], nc - g[1])
                    heapq.heappush(openq, (c2 + h, nxt))
        if g not in came and g != s:
            return [start, goal]  # unreachable: walk straight and let collisions sort it out
        cells = [g]
        while cells[-1] != s:
            cells.append(came[cells[-1]])
        pts = [start] + [self._center(c) for c in reversed(cells[1:-1])] + [goal]
        smooth = [pts[0]]  # string pulling
        i = 0
        while i < len(pts) - 1:
            j = len(pts) - 1
            while j > i + 1 and not self.los(pts[i], pts[j]):
                j -= 1
            smooth.append(pts[j])
            i = j
        return smooth


# =========================================================================== truth survivor
@dataclass
class TruthState:
    x: float
    y: float
    cam_heading: float  # absolute camera heading (deg); the localizer never sees this
    t: float = 0.0


@dataclass
class SimConfig:
    tick_hz: float = 60.0
    flow_hz: float = 15.0
    deg_per_count_true: float = 0.05
    run_speed: float = 4.0
    max_turn_dps: float = 420.0
    repair_s: tuple[float, float] = (4.0, 7.0)
    look_around_while_repairing: bool = True
    seed: int = 7


@dataclass
class SimResult:
    events: list[Event]
    truth: list[tuple[float, float, float, float]]  # (t, x, y, cam_heading) per tick
    spawned_gens: list[str]
    visited_gens: list[str]
    interaction_times: list[float] = field(default_factory=list)


class SurvivorSim:
    def __init__(self, m: MapData, cfg: SimConfig | None = None) -> None:
        self.map = m
        self.cfg = cfg or SimConfig()
        occ = Occupancy.from_map(m)
        if occ is None:
            raise ValueError("simulator needs a map with geometry")
        self.occ = occ
        self.planner = PathPlanner(occ)
        self.rng = np.random.default_rng(self.cfg.seed)
        self.events: list[Event] = []
        self.truth: list[tuple[float, float, float, float]] = []
        self.t = 0.0
        self._count_residual = 0.0
        self._keys_down: set[int] = set()
        self._next_flow_t = 0.0
        self._moved_frac = 1.0

    # ------------------------------------------------------------------ primitives
    def _ts(self) -> int:
        return s_to_ns(self.t)

    def emit(self, e: Event) -> None:
        self.events.append(e)

    def _set_keys(self, wanted: set[int]) -> None:
        for vk in sorted(self._keys_down - wanted):
            self.emit(KeyEvent(ts_ns=self._ts(), vk=vk, down=False))
        for vk in sorted(wanted - self._keys_down):
            self.emit(KeyEvent(ts_ns=self._ts(), vk=vk, down=True))
        self._keys_down = set(wanted)

    def _turn(self, st: TruthState, delta_deg: float) -> None:
        counts_f = delta_deg / self.cfg.deg_per_count_true + self._count_residual
        counts = int(round(counts_f))
        self._count_residual = counts_f - counts
        if counts:
            self.emit(MouseDelta(ts_ns=self._ts(), dx=counts, dy=0))
            st.cam_heading = wrap_deg(st.cam_heading + counts * self.cfg.deg_per_count_true)

    def _step(self, st: TruthState, moving: bool, speed: float) -> None:
        dt = 1.0 / self.cfg.tick_hz
        if moving:
            h = math.radians(st.cam_heading)  # W held -> intent 0 -> move along camera
            dx, dy = math.sin(h) * speed * dt, -math.cos(h) * speed * dt
            nx, ny = st.x + dx, st.y + dy
            if self.occ.is_walkable_pt(nx, ny):
                st.x, st.y = nx, ny
                self._moved_frac = 1.0
            elif self.occ.is_walkable_pt(st.x + dx, st.y) and abs(dx) >= abs(dy):
                st.x += dx
                self._moved_frac = abs(dx) / (speed * dt)
            elif self.occ.is_walkable_pt(st.x, st.y + dy):
                st.y += dy
                self._moved_frac = abs(dy) / (speed * dt)
            else:
                self._moved_frac = 0.0
        self.t += dt
        st.t = self.t
        self.emit(Tick(ts_ns=self._ts(), hz=self.cfg.tick_hz))
        self.truth.append((self.t, st.x, st.y, st.cam_heading))
        if self.t >= self._next_flow_t:
            self._next_flow_t = self.t + 1.0 / self.cfg.flow_hz
            p = (0.93 if self._moved_frac > 0.5 else 0.1) if moving else 0.05
            self.emit(MotionObserved(ts_ns=self._ts(), translating_prob=p))

    # ------------------------------------------------------------------ behaviours
    def walk_to(self, st: TruthState, goal: Point, *, arrive_m: float = 0.5, timeout_s: float = 90.0) -> None:
        path = self.planner.plan((st.x, st.y), goal)
        t_end = self.t + timeout_s
        for wp in path[1:]:
            while math.hypot(wp[0] - st.x, wp[1] - st.y) > arrive_m and self.t < t_end:
                want = heading_of(wp[0] - st.x, wp[1] - st.y)
                err = angle_diff_deg(want, st.cam_heading)
                max_step = self.cfg.max_turn_dps / self.cfg.tick_hz
                self._turn(st, max(-max_step, min(max_step, err)))
                go = abs(err) < 60.0
                self._set_keys({VK["W"], VK["LSHIFT"]} if go else set())
                self._step(st, go, self.cfg.run_speed)
        self._set_keys(set())

    def idle(self, st: TruthState, seconds: float, *, look_around: bool = False) -> None:
        n = int(seconds * self.cfg.tick_hz)
        target = st.cam_heading
        for i in range(n):
            if look_around and i % 45 == 0:
                target = wrap_deg(st.cam_heading + float(self.rng.uniform(-120, 120)))
            if look_around:
                err = angle_diff_deg(target, st.cam_heading)
                self._turn(st, max(-4.0, min(4.0, err)))
            self._step(st, False, 0.0)

    def repair(self, st: TruthState, seconds: float) -> None:
        self.emit(InteractionObserved(ts_ns=self._ts(), kind=InteractionKind.GENERATOR, active=True))
        self.idle(st, seconds, look_around=self.cfg.look_around_while_repairing)
        self.emit(InteractionObserved(ts_ns=self._ts(), kind=InteractionKind.GENERATOR, active=False))


def gen_standoff_point(sim: SurvivorSim, gen_xy: Point, from_xy: Point, standoff: float = 1.0) -> Point:
    """Where a survivor stands to repair: on the approach side, ~1 m from the gen centre."""
    dx, dy = from_xy[0] - gen_xy[0], from_xy[1] - gen_xy[1]
    d = math.hypot(dx, dy) or 1.0
    base = math.atan2(dy, dx)
    for k in range(8):
        a = base + (k * math.pi / 4) * (1 if k % 2 else -1)
        p = (gen_xy[0] + math.cos(a) * standoff, gen_xy[1] + math.sin(a) * standoff)
        if sim.occ.is_walkable_pt(*p):
            return p
    return (gen_xy[0] + dx / d * standoff, gen_xy[1] + dy / d * standoff)


def run_gen_scenario(
    m: MapData,
    *,
    n_visits: int = 3,
    seed: int = 7,
    deg_per_count_true: float = 0.05,
    spawn_index: int | None = None,
    pre_wander_s: float = 3.0,
    emit_preamble: bool = True,
    gens_needed: int = 5,
) -> SimResult:
    """Spawn -> wander briefly -> visit ``n_visits`` spawned generators (repairing each)."""
    cfg = SimConfig(seed=seed, deg_per_count_true=deg_per_count_true)
    sim = SurvivorSim(m, cfg)
    rng = sim.rng
    n_spawn = 7 if len(m.generators) >= 7 else len(m.generators)
    fixed = [g.id for g in m.generators if g.fixed]
    others = [g.id for g in m.generators if not g.fixed]
    rng.shuffle(others)
    spawned = (fixed + others)[:n_spawn]
    gens = {g.id: g for g in m.generators}

    spawns = m.survivor_spawns
    if spawns:
        sp = spawns[spawn_index if spawn_index is not None else int(rng.integers(len(spawns)))]
        start = (sp.pos_m[0] + float(rng.normal(0, 1.0)), sp.pos_m[1] + float(rng.normal(0, 1.0)))
    else:
        x, y = sim.occ.sample_uniform(1, rng)
        start = (float(x[0]), float(y[0]))
    start = sim.planner.nearest_free(start)
    st = TruthState(start[0], start[1], float(rng.uniform(0, 360)))

    if emit_preamble:
        sim.emit(MatchStateChanged(ts_ns=0, state=MatchState.IN_MATCH, role=Role.SURVIVOR))
        sim.emit(MapIdentified(ts_ns=0, map_id=m.id, confidence=1.0, source=MapSource.DEMO))
        sim.emit(GenCounterObserved(ts_ns=0, remaining=gens_needed))
        sim.emit(KillerTerrorRadiusSet(ts_ns=0, radius_m=32.0))
    sim.emit(Tick(ts_ns=0, hz=cfg.tick_hz))

    if pre_wander_s > 0:  # short wander before heading to the first gen (exercises breadcrumb back-projection)
        ang = float(rng.uniform(0, 360))
        reach = 4.0 * pre_wander_s
        goal = (st.x + math.sin(math.radians(ang)) * reach, st.y - math.cos(math.radians(ang)) * reach)
        goal = (min(max(goal[0], 4), (m.width_m or 96) - 4), min(max(goal[1], 4), (m.height_m or 96) - 4))
        sim.walk_to(st, goal)

    visited: list[str] = []
    times: list[float] = []
    remaining = gens_needed
    order = sorted(spawned, key=lambda gid: math.hypot(gens[gid].pos_m[0] - st.x, gens[gid].pos_m[1] - st.y))
    for gid in order[:n_visits]:
        g = gens[gid]
        target = gen_standoff_point(sim, g.pos_m, (st.x, st.y))
        sim.walk_to(st, target, arrive_m=0.35)
        times.append(sim.t)
        sim.repair(st, float(rng.uniform(*cfg.repair_s)))
        remaining = max(0, remaining - 1)
        sim.emit(GenCounterObserved(ts_ns=sim._ts(), remaining=remaining))
        visited.append(gid)
    return SimResult(sim.events, sim.truth, spawned, visited, times)


def killer_tr_events(
    truth: list[tuple[float, float, float, float]],
    killer_path: list[tuple[float, float]],
    *,
    tr_m: float = 32.0,
    hz: float = 4.0,
) -> list[Event]:
    """Terror-radius tiers for a scripted killer position track (same length as truth)."""
    out: list[Event] = []
    next_t = 0.0
    for (t, x, y, _), (kx, ky) in zip(truth, killer_path, strict=False):
        if t < next_t:
            continue
        next_t = t + 1.0 / hz
        d = math.hypot(kx - x, ky - y)
        tier = 0 if d > tr_m else (1 if d > 2 * tr_m / 3 else (2 if d > tr_m / 3 else 3))
        bpm = {0: None, 1: 70.0, 2: 95.0, 3: 125.0}[tier]
        out.append(TerrorRadiusObserved(ts_ns=s_to_ns(t), tier=tier, bpm=bpm, source=TrSource.SIM))
    return out
