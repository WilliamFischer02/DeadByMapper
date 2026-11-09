"""Heartbeat detection DSP (numpy only): low-band energy envelope -> onset strength ->
autocorrelation tempo -> terror-radius tier.

Facts (docs/RESEARCH.md): the heartbeat gets louder and faster as the killer approaches; the TR
music layers 1-3 split the radius into three equal bands; a chase swaps layer 3 for chase music.
The bpm->tier thresholds are NOT known publicly - they ship as uncalibrated placeholders and are
fitted from a recorded custom/KYF session (docs/AUDIO_HEARTBEAT.md). Until calibrated, events
carry confidence <= 0.5 and the UI draws the ring as 'approximate'.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from ..core.events import TerrorRadiusObserved, TrSource


@dataclass
class TierModel:
    # bpm upper bounds for tier 1 and 2 (anything faster = tier 3). Placeholder values!
    tier1_max_bpm: float = 85.0
    tier2_max_bpm: float = 110.0
    calibrated: bool = False

    def tier(self, bpm: float) -> int:
        if bpm <= self.tier1_max_bpm:
            return 1
        if bpm <= self.tier2_max_bpm:
            return 2
        return 3


@dataclass(frozen=True, slots=True)
class HeartbeatDiag:
    """What the last ``analyze`` saw, for probes/calibration (not published on the bus)."""

    level_db: float  # in-band (30-140 Hz) energy, dB
    periodicity: float  # autocorrelation peak 0..1 (min_periodicity gates tier > 0)
    bpm_candidate: float | None  # tempo of the strongest period, even when below the gate
    level_fast_db: float = -120.0  # in-band energy over the last ``fast_s`` only (reacts within ~1 tick)


@dataclass
class HeartbeatAnalyzer:
    sr: int = 16000
    frame: int = 1024  # 64 ms -> 15.6 Hz bins
    hop: int = 160  # 10 ms -> envelope at 100 Hz
    band_hz: tuple[float, float] = (30.0, 140.0)
    window_s: float = 4.0
    min_bpm: float = 40.0
    max_bpm: float = 180.0
    min_periodicity: float = 0.25
    min_level_db: float = -60.0
    fast_s: float = 0.3  # short window for ``HeartbeatDiag.level_fast_db`` (M6 level-based tiers)
    tiers: TierModel = field(default_factory=TierModel)

    def __post_init__(self) -> None:
        self._pending = np.zeros(0, dtype=np.float32)
        self._env: deque[float] = deque(maxlen=int(self.window_s * self.sr / self.hop))
        freqs = np.fft.rfftfreq(self.frame, 1.0 / self.sr)
        self._band = (freqs >= self.band_hz[0]) & (freqs <= self.band_hz[1])
        self._win = np.hanning(self.frame).astype(np.float32)
        self.diag: HeartbeatDiag | None = None

    def feed(self, mono: np.ndarray) -> None:
        """Append mono float samples at ``sr`` (resample upstream)."""
        x = np.concatenate([self._pending, np.asarray(mono, dtype=np.float32)])
        n = 0 if len(x) < self.frame else 1 + (len(x) - self.frame) // self.hop
        if n > 0:
            frames = sliding_window_view(x, self.frame)[:: self.hop][:n]
            spec = np.abs(np.fft.rfft(frames * self._win, axis=1)) ** 2
            self._env.extend(spec[:, self._band].sum(axis=1).tolist())
        self._pending = x[n * self.hop :]

    def analyze(self, ts_ns: int, source: TrSource = TrSource.AUDIO) -> TerrorRadiusObserved | None:
        env = np.asarray(self._env, dtype=np.float64)
        if env.size < 2 * self.sr / self.hop:  # need >= 2 s
            return None
        level_db = 10.0 * math.log10(float(np.mean(env)) / self.frame + 1e-12)
        n_fast = max(1, int(self.fast_s * self.sr / self.hop))
        fast_db = 10.0 * math.log10(float(np.mean(env[-n_fast:])) / self.frame + 1e-12)
        if level_db < self.min_level_db:
            self.diag = HeartbeatDiag(level_db, 0.0, None, fast_db)
            return TerrorRadiusObserved(ts_ns=ts_ns, tier=0, confidence=0.6, source=source)
        onset = np.maximum(0.0, np.diff(np.log(env + 1e-9)))
        onset -= onset.mean()
        ac = np.correlate(onset, onset, mode="full")[onset.size - 1 :]
        if ac[0] <= 0:
            return None
        ac /= ac[0]
        env_rate = self.sr / self.hop
        lo = int(env_rate * 60.0 / self.max_bpm)
        hi = min(int(env_rate * 60.0 / self.min_bpm), ac.size - 1)
        if hi <= lo + 2:
            return None
        seg = ac[lo:hi]
        k = int(np.argmax(seg)) + lo
        strength = float(ac[k])
        self.diag = HeartbeatDiag(level_db, strength, round(60.0 * env_rate / k, 1) if k > 0 else None, fast_db)
        # prefer the fundamental: if half the lag is also strong it was a 'dub' sub-period
        if strength < self.min_periodicity:
            return TerrorRadiusObserved(ts_ns=ts_ns, tier=0, confidence=0.5, source=source)
        if 0 < k < ac.size - 1:  # parabolic peak refinement
            y0, y1, y2 = ac[k - 1], ac[k], ac[k + 1]
            d = y0 - 2 * y1 + y2
            kf = k + (0.5 * (y0 - y2) / d if abs(d) > 1e-12 else 0.0)
        else:
            kf = float(k)
        bpm = 60.0 * env_rate / kf
        self.diag = HeartbeatDiag(level_db, strength, round(bpm, 1), fast_db)
        conf = min(1.0, strength) * (1.0 if self.tiers.calibrated else 0.5)
        return TerrorRadiusObserved(
            ts_ns=ts_ns, tier=self.tiers.tier(bpm), bpm=round(bpm, 1), confidence=conf, source=source
        )
