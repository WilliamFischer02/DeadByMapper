"""Capture pacing, statistics and health assessment (pure; tested in tests/test_capture.py).

* ``FrameThrottle`` - decides in the capture callback whether a frame is delivered (~30 fps out of a
  60/144 Hz compositor stream) without drifting: the due time advances by a fixed period.
* ``CaptureStats`` - written by the capture threads, read by the supervisor/probe via immutable
  ``StatsSnapshot``s; ``StatsWindow.between(a, b)`` turns two snapshots into rates and percentiles, so
  several readers (health loop, probe) never reset each other's counters.
* ``assess_health`` - one place that maps measurements to ServiceHealth (status, detail).
"""

from __future__ import annotations

import math
import os
import threading
from collections import deque
from dataclasses import dataclass

NS = 1_000_000_000


class FrameThrottle:
    """Accept at most ~``target_fps`` frames per second, evenly spaced.

    A frame is accepted when it arrives no earlier than ``slack`` periods before the next due time; the
    due time then advances by exactly one period (or restarts from now after a gap), so a 60 Hz source
    gives exactly every other frame and a 144 Hz source averages ``target_fps`` with bounded jitter.
    """

    def __init__(self, target_fps: float, slack: float = 0.25) -> None:
        self.period_ns = int(NS / target_fps) if target_fps > 0 else 0
        self.slack_ns = int(self.period_ns * slack)
        self._due: int | None = None

    def accept(self, t_ns: int) -> bool:
        if self.period_ns <= 0:
            return True
        if self._due is not None and t_ns < self._due - self.slack_ns:
            return False
        if self._due is None or t_ns - self._due > self.period_ns:
            self._due = t_ns + self.period_ns  # first frame or after a gap: restart the schedule
        else:
            self._due += self.period_ns
        return True

    def reset(self) -> None:
        self._due = None


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile (q in 0..100); nan for an empty list."""
    if not values:
        return math.nan
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * min(max(q, 0.0), 100.0) / 100.0
    lo = math.floor(pos)
    hi = min(lo + 1, len(xs) - 1)
    return float(xs[lo] + (xs[hi] - xs[lo]) * (pos - lo))


@dataclass(frozen=True, slots=True)
class StatsSnapshot:
    t_ns: int
    arrived: int  # callbacks from WGC (compositor rate after native pacing)
    handed_off: int  # frames accepted by the throttle and copied to the converter
    delivered: int  # analysis frames put into the mailbox
    errors: int
    cpu_ns: int  # CPU time of our capture threads (native callback thread incl. the GPU->CPU readback, + converter)
    src_size: tuple[int, int]  # last captured client area (w, h), before downscale
    out_size: tuple[int, int]  # last analysis frame (w, h)
    recent_cb: tuple[tuple[int, int], ...]  # (t_ns, duration_ns) of recent callbacks that copied a frame
    recent_conv: tuple[tuple[int, int], ...]  # (t_ns, duration_ns) of recent conversions (downscale + BGR + put)
    last_error: str = ""


def _window_ms(recent: tuple[tuple[int, int], ...], a_ns: int, b_ns: int) -> list[float]:
    return [d / 1e6 for t, d in recent if a_ns < t <= b_ns]


@dataclass(frozen=True, slots=True)
class StatsWindow:
    duration_s: float
    fps_in: float  # WGC callbacks per second
    fps: float  # delivered into the mailbox
    cb_p50_ms: float  # capture callback (throttle + crop copy + hand-off)
    cb_p95_ms: float
    conv_p50_ms: float  # converter (INTER_AREA downscale + BGRA->BGR + mailbox put)
    conv_p95_ms: float
    cpu_pct: float  # % of the whole machine (Task Manager style)
    cpu_core_pct: float  # % of one logical core
    src_size: tuple[int, int]
    out_size: tuple[int, int]
    errors: int
    last_error: str = ""

    @classmethod
    def between(cls, a: StatsSnapshot, b: StatsSnapshot, n_cpus: int | None = None) -> StatsWindow:
        dt_ns = max(b.t_ns - a.t_ns, 1)
        dt = dt_ns / NS
        cbs = _window_ms(b.recent_cb, a.t_ns, b.t_ns)
        conv = _window_ms(b.recent_conv, a.t_ns, b.t_ns)
        core = 100.0 * max(b.cpu_ns - a.cpu_ns, 0) / dt_ns
        return cls(
            duration_s=dt,
            fps_in=(b.arrived - a.arrived) / dt,
            fps=(b.delivered - a.delivered) / dt,
            cb_p50_ms=percentile(cbs, 50),
            cb_p95_ms=percentile(cbs, 95),
            conv_p50_ms=percentile(conv, 50),
            conv_p95_ms=percentile(conv, 95),
            cpu_pct=core / max(n_cpus or os.cpu_count() or 1, 1),
            cpu_core_pct=core,
            src_size=b.src_size,
            out_size=b.out_size,
            errors=b.errors - a.errors,
            last_error=b.last_error,
        )


class CaptureStats:
    """Counters written from the capture callback and converter threads; cheap (one short lock and a
    deque append per frame). Readers take immutable snapshots, so several readers never interfere."""

    def __init__(self, keep: int = 512) -> None:
        self._lock = threading.Lock()
        self._arrived = 0
        self._handed_off = 0
        self._delivered = 0
        self._errors = 0
        self._last_error = ""
        self._cpu_ns = 0
        self._threads: dict[str, tuple[int, int]] = {}  # role -> (native thread id, last thread CPU ns)
        self._src = (0, 0)
        self._out = (0, 0)
        self._recent_cb: deque[tuple[int, int]] = deque(maxlen=keep)
        self._recent_conv: deque[tuple[int, int]] = deque(maxlen=keep)

    def cpu(self, role: str, tid: int, thread_cpu_ns: int) -> None:
        """Charge a thread's CPU time since its previous sample. A new thread id for ``role`` (WGC
        re-attach creates a new native thread) starts a fresh baseline."""
        with self._lock:
            prev = self._threads.get(role)
            if prev is not None and prev[0] == tid:
                self._cpu_ns += max(thread_cpu_ns - prev[1], 0)
            self._threads[role] = (tid, thread_cpu_ns)

    def arrival(self) -> None:
        with self._lock:
            self._arrived += 1

    def callback(self, t_ns: int, duration_ns: int) -> None:
        with self._lock:
            self._handed_off += 1
            self._recent_cb.append((t_ns, duration_ns))

    def delivery(self, t_ns: int, duration_ns: int, src: tuple[int, int], out: tuple[int, int]) -> None:
        with self._lock:
            self._delivered += 1
            self._src = src
            self._out = out
            self._recent_conv.append((t_ns, duration_ns))

    def error(self, msg: str) -> None:
        with self._lock:
            self._errors += 1
            self._last_error = msg

    def snapshot(self, t_ns: int) -> StatsSnapshot:
        with self._lock:
            return StatsSnapshot(
                t_ns=t_ns,
                arrived=self._arrived,
                handed_off=self._handed_off,
                delivered=self._delivered,
                errors=self._errors,
                cpu_ns=self._cpu_ns,
                src_size=self._src,
                out_size=self._out,
                recent_cb=tuple(self._recent_cb),
                recent_conv=tuple(self._recent_conv),
                last_error=self._last_error,
            )


# --------------------------------------------------------------------------- health
MIN_OK_FPS = 5.0
BLACK_AFTER_S = 3.0
FULLSCREEN_HINT = "set DBD Display Mode to Windowed Fullscreen"


def format_ok_detail(w: StatsWindow, backend: str = "WGC") -> str:
    """e.g. 'WGC 1280x720 -> 960x540, 30.1 fps, 1.8 % CPU'."""
    sw, sh = w.src_size
    ow, oh = w.out_size
    return f"{backend} {sw}x{sh} -> {ow}x{oh}, {w.fps:.1f} fps, {w.cpu_pct:.1f} % CPU"


def assess_health(
    w: StatsWindow | None,
    *,
    black_for_s: float = 0.0,
    minimized: bool = False,
    backend: str = "WGC",
) -> tuple[str, str]:
    """Map one measurement window to (status, detail). ``w`` None = attached, no window measured yet."""
    if minimized:
        return "degraded", "game window minimized - no frames until it is restored"
    if w is None:
        return "ok", f"{backend} attached, waiting for frames"
    if w.errors and w.fps < MIN_OK_FPS:
        return "degraded", f"frame callback errors ({w.errors}): {w.last_error}"[:200]
    if black_for_s >= BLACK_AFTER_S and w.fps >= MIN_OK_FPS:
        return "degraded", f"{backend} frames are black (exclusive fullscreen?) - {FULLSCREEN_HINT}"
    if w.fps < MIN_OK_FPS:
        return (
            "degraded",
            f"{backend} only {w.fps:.1f} fps (window idle, occluded by exclusive fullscreen, or minimized) - "
            f"{FULLSCREEN_HINT}",
        )
    return "ok", format_ok_detail(w, backend)


class BlackTracker:
    """How long have delivered frames been continuously black? (fed ~1/s from the callback)."""

    def __init__(self) -> None:
        self._since: int | None = None

    def update(self, black: bool, t_ns: int) -> None:
        if not black:
            self._since = None
        elif self._since is None:
            self._since = t_ns

    def black_for_s(self, t_ns: int) -> float:
        return 0.0 if self._since is None else max(t_ns - self._since, 0) / NS

    def reset(self) -> None:
        self._since = None
