"""Pure helpers between an audio callback and the analysis thread (no OS calls; fully testable).

* ``to_float32`` / ``downmix``: raw interleaved buffers -> mono float32 in [-1, 1].
* ``BlockQueue``: bounded, thread-safe hand-off from the real-time callback to the analysis thread.
  Drop policy = drop OLDEST: the heartbeat ring must follow what the player hears *now*, so when the
  analyser falls behind we skip stale audio instead of building latency. Drops are counted and shown
  by the probe/health detail.
* ``TierPublisher``: publish ``TerrorRadiusObserved`` only on change (tier / chase / lullaby), plus a
  keep-alive at most once per ``keepalive_s`` while tier > 0, so the bus is not spammed at 4 Hz.
* ``select_mode``: which capture backend to use for the configured mode and the known DBD pid.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Literal

import numpy as np

from ..core.clock import s_to_ns
from ..core.events import TerrorRadiusObserved

SampleFormat = Literal["float32", "int16", "int32"]
_SCALE = {"int16": 1.0 / 32768.0, "int32": 1.0 / 2147483648.0}


def to_float32(raw: bytes | bytearray | memoryview | np.ndarray, sample_format: SampleFormat) -> np.ndarray:
    """Raw little-endian samples -> float32 in [-1, 1]. Always returns a NEW array (safe to keep after
    the driver reuses its buffer)."""
    if sample_format == "float32":
        return np.frombuffer(raw, dtype="<f4").astype(np.float32, copy=True)
    if sample_format in _SCALE:
        dt = "<i2" if sample_format == "int16" else "<i4"
        return np.frombuffer(raw, dtype=dt).astype(np.float32) * np.float32(_SCALE[sample_format])
    raise ValueError(f"unsupported sample format {sample_format!r}")


def downmix(interleaved: np.ndarray, channels: int) -> np.ndarray:
    """Interleaved frames -> mono (mean of channels). A trailing partial frame is dropped (drivers
    deliver whole frames; this only guards against malformed buffers)."""
    x = np.asarray(interleaved, dtype=np.float32).reshape(-1)
    if channels <= 1:
        return x
    whole = len(x) - len(x) % channels
    return x[:whole].reshape(-1, channels).mean(axis=1, dtype=np.float32)


def rms_dbfs(x: np.ndarray) -> float:
    if x.size == 0:
        return -120.0
    r = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return max(-120.0, 20.0 * float(np.log10(r + 1e-12)))


@dataclass(frozen=True, slots=True)
class AudioBlock:
    ts_ns: int  # arrival time (callback), from the app's monotonic clock
    mono: np.ndarray  # float32, source sample rate
    flags: int = 0  # backend status flags (xrun / discontinuity), 0 = clean


class BlockQueue:
    """Bounded FIFO, drop-oldest. ``put`` never blocks (it runs on the audio callback thread)."""

    def __init__(self, maxlen: int = 64) -> None:
        self.maxlen = int(maxlen)
        self._q: deque[AudioBlock] = deque()
        self._cv = threading.Condition()
        self.dropped = 0
        self.put_count = 0

    def put(self, block: AudioBlock) -> None:
        with self._cv:
            if len(self._q) >= self.maxlen:
                self._q.popleft()
                self.dropped += 1
            self._q.append(block)
            self.put_count += 1
            self._cv.notify()

    def get(self, timeout: float | None = None) -> AudioBlock | None:
        with self._cv:
            if not self._q and not self._cv.wait_for(lambda: bool(self._q), timeout=timeout):
                return None
            return self._q.popleft()

    def clear(self) -> None:
        with self._cv:
            self._q.clear()

    def __len__(self) -> int:
        return len(self._q)


class TierPublisher:
    """Change-only publishing with a bounded keep-alive while the killer is in range."""

    def __init__(self, keepalive_s: float = 1.0) -> None:
        self.keepalive_ns = s_to_ns(keepalive_s)
        self._last_key: tuple[int, bool, bool] | None = None
        self._last_ts: int | None = None

    def offer(self, obs: TerrorRadiusObserved | None) -> TerrorRadiusObserved | None:
        if obs is None:
            return None
        key = (obs.tier, obs.in_chase, obs.lullaby)
        changed = key != self._last_key
        due = obs.tier > 0 and self._last_ts is not None and obs.ts_ns - self._last_ts >= self.keepalive_ns
        if not (changed or due):
            return None
        self._last_key = key
        self._last_ts = obs.ts_ns
        return obs

    def reset(self) -> None:
        self._last_key = None
        self._last_ts = None


Mode = Literal["process", "device", "none"]


@dataclass(frozen=True, slots=True)
class ModeChoice:
    mode: Mode
    pid: int | None = None
    reason: str = ""  # why process loopback is NOT used (shown in health detail), or why nothing runs


def select_mode(
    setting: str,
    dbd_pid: int | None,
    process_supported: bool,
    unsupported_reason: str = "",
    process_error: str | None = None,
) -> ModeChoice:
    """``setting``: "auto" | "process" | "device" (AudioSettings.mode).

    auto    -> process loopback of the DBD tree when its pid is known and process loopback works,
               otherwise device loopback (with the reason).
    process -> process only (no fallback): waits for DBD / reports the failure.
    device  -> device loopback only.
    ``process_error`` is the last failure opening process loopback for this pid (auto then falls back).
    """
    s = (setting or "auto").lower()
    if s == "device":
        return ModeChoice("device", reason="device mode forced in settings")
    if not process_supported:
        why = unsupported_reason or "not supported on this Windows"
        return ModeChoice("none", reason=why) if s == "process" else ModeChoice("device", reason=why)
    if dbd_pid is None:
        why = "Dead by Daylight not running"
        return ModeChoice("none", reason=why) if s == "process" else ModeChoice("device", reason=why)
    if process_error:
        return (
            ModeChoice("none", pid=dbd_pid, reason=process_error)
            if s == "process"
            else ModeChoice("device", pid=dbd_pid, reason=process_error)
        )
    return ModeChoice("process", pid=dbd_pid)
