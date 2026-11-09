"""Localization benchmark over many simulator seeds - THE yardstick for localizer changes.

    uv run python tools/bench_localization.py                 # 30 seeds, 4 generator visits
    uv run python tools/bench_localization.py --seeds 60 --visits 5 --dpc-error 0.03

Reports, per landmark fix k: success rate (error < 3 m at the moment of fix k), plus final-error
median/p90, time-to-first-LOCKED, FALSE-LOCKED rate (status LOCKED while error > 5 m - must stay
~0: a confidently wrong dot is the worst failure for a glance UI) and ms per tick.
Paste the before/after table into your report whenever you touch localization/.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from deadbymapper.core.bus import EventBus  # noqa: E402
from deadbymapper.core.clock import ManualClock  # noqa: E402
from deadbymapper.core.events import InteractionObserved, PoseEstimate, PoseStatus, Tick  # noqa: E402
from deadbymapper.localization.localizer import Localizer  # noqa: E402
from deadbymapper.sim.simulator import run_gen_scenario  # noqa: E402
from deadbymapper.world.map_registry import MapRegistry  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="test_realm.test_arena")
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--first-seed", type=int, default=1000)
    ap.add_argument("--visits", type=int, default=4)
    ap.add_argument("--particles", type=int, default=5000)
    ap.add_argument("--dpc-error", type=float, default=0.015, help="relative mouse-calibration error")
    args = ap.parse_args()

    reg = MapRegistry.load()
    m = reg.get(args.map)
    dpc = 0.05
    ok = np.zeros(args.visits)
    seen = np.zeros(args.visits)
    finals: list[float] = []
    lock_times: list[float] = []
    ticks = false_locked = locked_samples = 0
    t_cost = 0.0
    for seed in range(args.first_seed, args.first_seed + args.seeds):
        res = run_gen_scenario(m, n_visits=args.visits, seed=seed, deg_per_count_true=dpc)
        clock = ManualClock()
        bus = EventBus(synchronous=True)
        loc = Localizer(clock, reg, deg_per_count=dpc * (1 + args.dpc_error), n_particles=args.particles, seed=seed)
        loc.attach(bus)
        truth = {round(t * 60): (x, y) for t, x, y, _ in res.truth}
        poses: list[PoseEstimate] = []
        bus.subscribe(PoseEstimate, poses.append)
        fix = 0
        t0 = time.perf_counter()
        for e in res.events:
            clock.set_ns(max(clock.now_ns(), e.ts_ns))
            bus.publish(e)
            if isinstance(e, Tick):
                ticks += 1
            if isinstance(e, InteractionObserved) and e.active:
                p = loc.current_pose()
                tx, ty = truth[round(clock.now_ns() / 1e9 * 60)]
                if fix < args.visits:
                    seen[fix] += 1
                    ok[fix] += math.hypot(p.x - tx, p.y - ty) < 3.0
                fix += 1
        t_cost += time.perf_counter() - t0
        first_lock = None
        for p in poses:
            key = round(p.ts_ns / 1e9 * 60)
            if key not in truth:
                continue
            err = math.hypot(p.x - truth[key][0], p.y - truth[key][1])
            if p.status == PoseStatus.LOCKED:
                locked_samples += 1
                false_locked += err > 5.0
                if first_lock is None:
                    first_lock = p.ts_ns / 1e9
        if first_lock is not None:
            lock_times.append(first_lock)
        last = poses[-1]
        tx, ty = res.truth[-1][1], res.truth[-1][2]
        finals.append(math.hypot(last.x - tx, last.y - ty))

    print(
        f"map={args.map} seeds={args.seeds} visits={args.visits} particles={args.particles} "
        f"dpc_error={args.dpc_error:+.1%}"
    )
    for k in range(args.visits):
        print(f"  after fix {k + 1}: {ok[k] / max(seen[k], 1):6.1%} within 3 m  (n={int(seen[k])})")
    f = np.array(finals)
    print(f"  final error: median {np.median(f):.2f} m, p90 {np.percentile(f, 90):.2f} m, max {f.max():.2f} m")
    if lock_times:
        print(f"  first LOCKED at: median {np.median(lock_times):.0f} s ({len(lock_times)}/{args.seeds} runs locked)")
    print(f"  FALSE-LOCKED: {false_locked / max(locked_samples, 1):.2%} of LOCKED samples had error > 5 m")
    print(f"  cost: {1000 * t_cost / max(ticks, 1):.2f} ms per 60 Hz tick")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
