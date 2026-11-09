"""WASAPI loopback capture -> HeartbeatAnalyzer -> TerrorRadiusObserved (4 Hz).

STATUS: scaffold (M6, audio agent). Plan:
  * Preferred: PROCESS loopback (Windows 10 2004+/11: ActivateAudioInterfaceAsync with
    AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK targeting the DBD pid) so Discord/music never
    pollute the signal. Needs COM via comtypes or a tiny native helper - same events either way.
  * Fallback: default render device loopback via PyAudioWPatch (captures all system audio).
  * Downmix to mono, anti-alias and resample to 16 kHz with ``MonoResampler16k`` (stateful across
    callbacks), feed the analyzer in the capture callback, run ``analyze`` on a 250 ms timer,
    publish only on tier/chase change or at 1 Hz.
  * Health: "degraded" when only device loopback is available (UI hint: mute other apps).
This is read-only observation of the audio the player already hears.
"""

from __future__ import annotations

import logging
import sys
import threading

import numpy as np

from ..core.bus import EventBus
from ..core.clock import Clock
from ..core.lifecycle import Service
from .heartbeat import HeartbeatAnalyzer

log = logging.getLogger(__name__)


class MonoResampler16k:
    """Streaming downmix + anti-alias + resample to 16 kHz for audio callbacks.

    Filter state and resampling phase carry across blocks, so callback boundaries leave no
    artifacts (a per-block resample would glitch at the callback rate - right next to our band).
    Anti-aliasing: we only analyse 30-140 Hz, so a double boxcar (triangle) with its first null near
    2 kHz is plenty: < 1 % loss at 140 Hz, ~ -50 dB at the fold-down frequencies above 8 kHz.
    """

    OUT_SR = 16000

    def __init__(self, sr: int, channels: int = 1) -> None:
        self.sr = int(sr)
        self.channels = int(channels)
        self.step = self.sr / self.OUT_SR
        n = max(1, round(self.sr / 2000)) if self.sr > self.OUT_SR else 1
        self.kernel = np.convolve(np.ones(n), np.ones(n)) / float(n * n)
        self._hist = np.zeros(len(self.kernel) - 1, dtype=np.float64)
        self._buf = np.zeros(0, dtype=np.float64)
        self._t = 0.0  # position of the next output sample, in units of input samples into _buf
        self._partial = np.zeros(0, dtype=np.float64)  # interleaved samples of an incomplete frame

    def process(self, block: np.ndarray) -> np.ndarray:
        x = np.asarray(block, dtype=np.float64).reshape(-1)
        if self.channels > 1:
            x = np.concatenate([self._partial, x])
            whole = len(x) - len(x) % self.channels
            self._partial = x[whole:]
            x = x[:whole].reshape(-1, self.channels).mean(axis=1)
        if len(self.kernel) > 1:
            xx = np.concatenate([self._hist, x])
            x = np.convolve(xx, self.kernel, mode="valid")
            self._hist = xx[len(xx) - (len(self.kernel) - 1) :]
        buf = np.concatenate([self._buf, x])
        if len(buf) < 2 or self._t > len(buf) - 1:
            self._buf = buf
            return np.zeros(0, dtype=np.float32)
        n_out = int(np.floor((len(buf) - 1 - self._t) / self.step)) + 1
        pos = self._t + self.step * np.arange(n_out)
        out = np.interp(pos, np.arange(len(buf)), buf)
        nxt = self._t + self.step * n_out
        keep_from = min(int(np.floor(nxt)), len(buf) - 1)
        self._buf = buf[keep_from:]
        self._t = nxt - keep_from
        return out.astype(np.float32)


class LoopbackHeartbeatService(Service):
    name = "audio.heartbeat"

    def __init__(self, bus: EventBus, clock: Clock, analyzer: HeartbeatAnalyzer | None = None) -> None:
        super().__init__(bus, clock)
        self.analyzer = analyzer or HeartbeatAnalyzer()
        self._stop = threading.Event()
        self._last_tier: int | None = None

    def start(self) -> None:
        if sys.platform != "win32":
            self.set_health("disabled", "Windows only")
            return
        try:
            import pyaudiowpatch  # type: ignore[import-not-found]  # noqa: F401
        except ImportError:
            self.set_health("down", "PyAudioWPatch not installed (uv sync --extra windows)")
            return
        self.set_health("degraded", "M6: loopback capture not implemented yet")

    def stop(self) -> None:
        self._stop.set()
        self.set_health("down")
