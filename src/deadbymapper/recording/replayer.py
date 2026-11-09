"""Deterministic replay of a recorded session into a (synchronous) bus."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

from ..core.bus import EventBus
from ..core.clock import ManualClock
from ..core.events import Event, PoseEstimate, from_record


def read_events(session_dir: Path, *, skip_types: tuple[type, ...] = (PoseEstimate,)) -> Iterator[Event]:
    """Yield recorded events. Derived outputs (PoseEstimate by default) are skipped so the
    pipeline under test regenerates them."""
    with (session_dir / "events.jsonl").open(encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            e = from_record(json.loads(line))
            if not isinstance(e, skip_types):
                yield e


def replay(session_dir: Path, bus: EventBus, clock: ManualClock, *, realtime: bool = False, speed: float = 1.0) -> int:
    n = 0
    t0_wall = time.perf_counter()
    t0_evt: int | None = None
    for e in read_events(session_dir):
        if t0_evt is None:
            t0_evt = e.ts_ns
        if realtime:
            target = (e.ts_ns - t0_evt) / 1e9 / speed
            delay = target - (time.perf_counter() - t0_wall)
            if delay > 0:
                time.sleep(delay)
        clock.set_ns(max(clock.now_ns(), e.ts_ns))
        bus.publish(e)
        n += 1
    return n
