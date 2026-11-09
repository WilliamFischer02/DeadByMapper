"""Timekeeping.

Every event carries ``ts_ns`` from a single monotonic clock so that inputs, frames, audio
and log lines can be fused and *replayed deterministically*. Never use wall-clock time for
ordering. ``time.perf_counter_ns`` is backed by QueryPerformanceCounter on Windows.
"""

from __future__ import annotations

import time
from typing import Protocol

NS_PER_S = 1_000_000_000


class Clock(Protocol):
    def now_ns(self) -> int: ...


class MonotonicClock:
    """High-resolution monotonic clock used in live mode."""

    __slots__ = ()

    def now_ns(self) -> int:
        return time.perf_counter_ns()


class ManualClock:
    """Deterministic clock for tests, the simulator and replay."""

    def __init__(self, start_ns: int = 0) -> None:
        self._t = int(start_ns)

    def now_ns(self) -> int:
        return self._t

    def set_ns(self, t_ns: int) -> None:
        if t_ns < self._t:
            raise ValueError(f"ManualClock cannot go backwards ({t_ns} < {self._t})")
        self._t = int(t_ns)

    def advance_s(self, dt_s: float) -> int:
        self._t += s_to_ns(dt_s)
        return self._t


def s_to_ns(seconds: float) -> int:
    return int(round(seconds * NS_PER_S))


def ns_to_s(ns: int) -> float:
    return ns / NS_PER_S
