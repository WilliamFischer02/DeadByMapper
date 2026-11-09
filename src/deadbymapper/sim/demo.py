"""Real-time demo driver: a simulated survivor (and killer) playing the Test Arena, looping.

Publishes exactly what the live sensors would, so `python -m deadbymapper --demo` exercises the
whole pipeline + UI with no game installed. Timestamps are rebased onto the live clock.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import threading
import time

import numpy as np

from ..core.bus import EventBus
from ..core.clock import Clock, s_to_ns
from ..core.events import Event, MatchState, MatchStateChanged, Role, ServiceHealth
from ..world.map_model import MapData
from .simulator import PathPlanner, killer_tr_events, run_gen_scenario

log = logging.getLogger(__name__)


def killer_track(
    m: MapData, planner: PathPlanner, times: list[float], rng: np.random.Generator, speed: float = 4.6
) -> list[tuple[float, float]]:
    """A killer wandering between random waypoints, sampled at the given times."""
    w, h = m.width_m or 96.0, m.height_m or 96.0
    pos = (float(rng.uniform(8, w - 8)), float(rng.uniform(8, h - 8)))
    path: list[tuple[float, float]] = []
    out: list[tuple[float, float]] = []
    t_prev = times[0] if times else 0.0
    for t in times:
        dt = t - t_prev
        t_prev = t
        step = speed * dt
        while step > 0:
            if not path:
                goal = (float(rng.uniform(6, w - 6)), float(rng.uniform(6, h - 6)))
                path = planner.plan(pos, goal)[1:]
            tx, ty = path[0]
            d = math.hypot(tx - pos[0], ty - pos[1])
            if d <= step:
                pos = (tx, ty)
                path.pop(0)
                step -= d
            else:
                pos = (pos[0] + (tx - pos[0]) / d * step, pos[1] + (ty - pos[1]) / d * step)
                step = 0
        out.append(pos)
    return out


class DemoDriver:
    name = "demo"

    def __init__(self, bus: EventBus, clock: Clock, m: MapData, *, visits: int = 5, seed: int = 11) -> None:
        self.bus, self.clock, self.map = bus, clock, m
        self.visits = visits
        self.seed = seed
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def build(self, seed: int) -> list[Event]:
        res = run_gen_scenario(self.map, n_visits=self.visits, seed=seed, deg_per_count_true=0.05)
        from .simulator import SurvivorSim

        planner = SurvivorSim(self.map).planner
        rng = np.random.default_rng(seed + 1000)
        times = [t for t, *_ in res.truth]
        track = killer_track(self.map, planner, times, rng)
        events = res.events + killer_tr_events(res.truth, track)
        events.sort(key=lambda e: e.ts_ns)
        return events

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="demo", daemon=True)
        self._thread.start()
        self.bus.publish(
            ServiceHealth(
                ts_ns=self.clock.now_ns(), service="demo", status="ok", detail="simulated survivor on Test Arena"
            )
        )

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        seed = self.seed
        while not self._stop.is_set():
            events = self.build(seed)
            t0 = self.clock.now_ns()
            for e in events:
                if self._stop.is_set():
                    return
                target = t0 + e.ts_ns
                delay = (target - self.clock.now_ns()) / 1e9
                if delay > 0:
                    time.sleep(delay)
                self.bus.publish(dataclasses.replace(e, ts_ns=target))
            end = self.clock.now_ns()
            self.bus.publish(MatchStateChanged(ts_ns=end, state=MatchState.POST_MATCH, role=Role.SURVIVOR))
            if self._stop.wait(4.0):
                return
            self.bus.publish(MatchStateChanged(ts_ns=self.clock.now_ns(), state=MatchState.MENU))
            if self._stop.wait(2.0):
                return
            seed += 1
            _ = s_to_ns  # keep import for type checkers
