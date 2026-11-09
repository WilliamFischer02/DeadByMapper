"""The heart of the product: does the dot end up where the survivor really is?"""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

from deadbymapper.core.bus import EventBus
from deadbymapper.core.clock import ManualClock
from deadbymapper.core.events import (
    InteractionKind,
    InteractionObserved,
    KeyEvent,
    MatchState,
    MatchStateChanged,
    MotionObserved,
    MouseDelta,
    PoseEstimate,
    PoseStatus,
    Tick,
    UserFix,
    UserMark,
)
from deadbymapper.input.keymap import VK
from deadbymapper.localization.localizer import Localizer
from deadbymapper.localization.measurements import near_candidates_loglik
from deadbymapper.localization.occupancy import Occupancy
from deadbymapper.sim.simulator import PathPlanner, run_gen_scenario

DPC = 0.05


def _run(registry, res, *, dpc_err=1.015, n=4000, seed=0):
    clock = ManualClock()
    bus = EventBus(synchronous=True)
    loc = Localizer(clock, registry, deg_per_count=DPC * dpc_err, n_particles=n, seed=seed)
    loc.attach(bus)
    poses: list[PoseEstimate] = []
    bus.subscribe(PoseEstimate, poses.append)
    errors_at_fix: list[float] = []
    truth = {round(t * 60): (x, y) for t, x, y, _h in res.truth}
    for e in res.events:
        clock.set_ns(max(clock.now_ns(), e.ts_ns))
        bus.publish(e)
        if isinstance(e, InteractionObserved) and e.active:
            p = loc.current_pose()
            tx, ty = truth[round(clock.now_ns() / 1e9 * 60)]
            errors_at_fix.append(math.hypot(p.x - tx, p.y - ty))
    return loc, poses, errors_at_fix


def test_occupancy_blocks_walls_and_bounds(arena):
    occ = Occupancy.from_map(arena)
    assert occ is not None
    assert occ.is_walkable_pt(5, 5)
    assert not occ.is_walkable_pt(40, 44)  # inside the main building
    assert not occ.is_walkable_pt(18, 20.3)  # thin wall w1 (0.6 m) must still block
    assert not occ.is_walkable_pt(-1, 10)
    assert 0.8 * 96 * 96 < occ.free_area_m2 < 96 * 96


def test_planner_avoids_solids(arena):
    occ = Occupancy.from_map(arena)
    path = PathPlanner(occ).plan((20.0, 44.0), (70.0, 44.0))  # straight line crosses the hall
    pts = np.array(path)
    assert len(path) > 2
    seg = np.linspace(0, 1, 50)
    for a, b in itertools.pairwise(pts):
        xs = a[0] + (b[0] - a[0]) * seg
        ys = a[1] + (b[1] - a[1]) * seg
        assert occ.is_walkable(xs, ys).all()


def test_candidate_likelihood_peaks_at_candidates():
    centers = np.array([[10.0, 10.0], [50.0, 50.0]])
    xs, ys = np.array([10.0, 30.0, 51.0]), np.array([11.0, 30.0, 50.0])
    ll = near_candidates_loglik(xs, ys, centers, np.array([1.0, 1.0]))
    assert ll[0] > ll[1] and ll[2] > ll[1]


@pytest.mark.parametrize("seed", [101, 104, 106])
def test_converges_after_a_few_generators(registry, arena, seed):
    res = run_gen_scenario(arena, n_visits=4, seed=seed, deg_per_count_true=DPC)
    loc, poses, errs = _run(registry, res, seed=seed)
    assert len(errs) == 4
    final = poses[-1]
    t, tx, ty, _ = res.truth[-1]
    assert math.hypot(final.x - tx, final.y - ty) < 3.0, (errs, final)
    assert final.status in (PoseStatus.LOCKED, PoseStatus.CONVERGING)
    assert final.heading_deg is not None


def test_user_tap_locks_quickly(registry, arena):
    res = run_gen_scenario(arena, n_visits=1, seed=105, deg_per_count_true=DPC)
    clock = ManualClock()
    bus = EventBus(synchronous=True)
    loc = Localizer(clock, registry, deg_per_count=DPC, n_particles=3000, seed=1)
    loc.attach(bus)
    # replay up to the first interaction, then tap the true position instead of relying on the gen
    for e in res.events:
        clock.set_ns(max(clock.now_ns(), e.ts_ns))
        if isinstance(e, InteractionObserved):
            break
        bus.publish(e)
    t, tx, ty, _ = min(res.truth, key=lambda r: abs(r[0] - clock.now_ns() / 1e9))
    bus.publish(UserFix(ts_ns=clock.now_ns(), pos_m=(tx + 0.8, ty - 0.5), radius_m=2.0))
    p = loc.current_pose()
    assert math.hypot(p.x - tx, p.y - ty) < 2.5


def test_breadcrumb_mode_without_map(registry):
    clock = ManualClock()
    bus = EventBus(synchronous=True)
    loc = Localizer(clock, registry, deg_per_count=DPC, n_particles=500, seed=1)
    loc.attach(bus)
    bus.publish(MatchStateChanged(ts_ns=0, state=MatchState.IN_MATCH))
    bus.publish(Tick(ts_ns=0))
    bus.publish(KeyEvent(ts_ns=1, vk=VK["W"], down=True))
    bus.publish(KeyEvent(ts_ns=1, vk=VK["LSHIFT"], down=True))
    bus.publish(MouseDelta(ts_ns=2, dx=int(90 / DPC), dy=0))  # face +90 deg relative
    for i in range(1, 301):  # 5 s at 60 Hz, running at 4 m/s
        bus.publish(Tick(ts_ns=int(i * 1e9 / 60)))
    p = loc.current_pose()
    assert p.relative and p.status == PoseStatus.NO_MAP
    assert p.x == pytest.approx(20.0, abs=0.3) and p.y == pytest.approx(0.0, abs=0.3)


def test_stuck_gate_stops_dead_reckoning(registry):
    clock = ManualClock()
    bus = EventBus(synchronous=True)
    loc = Localizer(clock, registry, deg_per_count=DPC, n_particles=500, seed=1)
    loc.attach(bus)
    bus.publish(MatchStateChanged(ts_ns=0, state=MatchState.IN_MATCH))
    bus.publish(Tick(ts_ns=0))
    bus.publish(KeyEvent(ts_ns=1, vk=VK["W"], down=True))
    for i in range(1, 121):
        ts = int(i * 1e9 / 60)
        bus.publish(MotionObserved(ts_ns=ts, translating_prob=0.05))  # optical flow: not moving
        bus.publish(Tick(ts_ns=ts))
    p = loc.current_pose()
    assert math.hypot(p.x, p.y) < 0.05


def test_interaction_locks_motion(registry):
    clock = ManualClock()
    bus = EventBus(synchronous=True)
    loc = Localizer(clock, registry, deg_per_count=DPC, n_particles=500, seed=1)
    loc.attach(bus)
    bus.publish(MatchStateChanged(ts_ns=0, state=MatchState.IN_MATCH))
    bus.publish(Tick(ts_ns=0))
    bus.publish(InteractionObserved(ts_ns=1, kind=InteractionKind.CHEST, active=True))
    bus.publish(KeyEvent(ts_ns=1, vk=VK["W"], down=True))
    for i in range(1, 61):
        bus.publish(Tick(ts_ns=int(i * 1e9 / 60)))
    assert math.hypot(loc.rel_x, loc.rel_y) == 0.0


def test_hotkey_mark_is_a_landmark_and_dedupes_with_perceived_interaction(registry, arena):
    clock = ManualClock()
    bus = EventBus(synchronous=True)
    loc = Localizer(clock, registry, deg_per_count=DPC, n_particles=2000, seed=3, require_match_state=False)
    loc.attach(bus)
    loc.set_map(arena.id)
    assert loc.status == PoseStatus.NO_FIX
    calls: list[float] = []
    real_fix = loc._landmark_fix
    loc._landmark_fix = lambda lm, conf: (calls.append(conf), real_fix(lm, conf))  # type: ignore[method-assign]
    bus.publish(Tick(ts_ns=clock.now_ns()))
    bus.publish(UserMark(ts_ns=clock.now_ns(), kind="generator"))
    clock.advance_s(1.0)
    # the vision lane's prompt reader sees the same repair a second later: must not count twice
    bus.publish(InteractionObserved(ts_ns=clock.now_ns(), kind=InteractionKind.GENERATOR, active=True))
    bus.publish(UserMark(ts_ns=clock.now_ns(), kind="note"))  # notes are labels only
    assert calls == [0.9]
    assert loc.current_pose().status != PoseStatus.NO_FIX
