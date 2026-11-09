"""Analysis side of the heartbeat pipeline (runs on its own thread, never on the audio callback).

    BlockQueue -> MonoResampler16k (per source rate) -> HeartbeatAnalyzer.feed
               -> every ``tick_s``: analyze -> TierPublisher (change-only + 1 Hz keep-alive) -> publish

Gaps (no blocks for longer than ``gap_fill_s``: device stalled, unplugged, reopening) are filled
with digital silence at 16 kHz, so the envelope decays and the tier falls to 0 instead of freezing on
the last value. The pipeline is clock-driven and fully deterministic under ``ManualClock``.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..core.clock import Clock, s_to_ns
from ..core.events import TerrorRadiusObserved
from .blocks import AudioBlock, BlockQueue, TierPublisher, rms_dbfs
from .heartbeat import HeartbeatAnalyzer, HeartbeatDiag
from .resample import MonoResampler16k

OUT_SR = MonoResampler16k.OUT_SR


@dataclass(frozen=True, slots=True)
class PipelineStats:
    blocks: int
    dropped: int
    xruns: int
    queue_depth: int
    gap_fills: int
    rms_dbfs: float  # last ~1 s of the source signal (mono, before resampling)
    in_rms_dbfs: float  # last ~1 s of the analyser input (16 kHz)
    last: TerrorRadiusObserved | None  # latest analyser output (published or not)
    diag: HeartbeatDiag | None  # level / periodicity / bpm candidate behind ``last``
    published: int


class HeartbeatPipeline:
    def __init__(
        self,
        clock: Clock,
        analyzer: HeartbeatAnalyzer,
        publish: Callable[[TerrorRadiusObserved], None],
        queue: BlockQueue | None = None,
        tick_s: float = 0.25,
        keepalive_s: float = 1.0,
        gap_fill_s: float = 0.2,
    ) -> None:
        self.clock = clock
        self.analyzer = analyzer
        self.publish = publish
        self.queue = queue if queue is not None else BlockQueue()  # (an empty queue is falsy)
        self.tick_ns = s_to_ns(tick_s)
        self.gap_fill_ns = s_to_ns(gap_fill_s)
        self.publisher = TierPublisher(keepalive_s)
        self.taps: list[Callable[[np.ndarray, int], None]] = []  # (16 kHz block, ts_ns) observers
        self._resampler: MonoResampler16k | None = None
        self._sr: int | None = None
        self._lock = threading.Lock()
        self._fed_until_ns: int | None = None
        self._next_tick_ns: int | None = None
        self._src_sq: deque[tuple[float, int]] = deque(maxlen=200)  # (sum of squares, n) per block
        self._in_sq: deque[tuple[float, int]] = deque(maxlen=200)
        self._blocks = 0
        self._xruns = 0
        self._gap_fills = 0
        self._published = 0
        self._last: TerrorRadiusObserved | None = None

    # ------------------------------------------------------------------ configuration
    def set_source_rate(self, sr: int) -> None:
        """New source (or reopened one). Keeps analyser history; resampler state starts fresh."""
        with self._lock:
            if sr != self._sr:
                self._resampler = MonoResampler16k(sr)
                self._sr = int(sr)
            self._src_sq.clear()

    # ------------------------------------------------------------------ processing
    def process_block(self, block: AudioBlock) -> None:
        with self._lock:
            if self._resampler is None:
                return
            x = block.mono
            self._blocks += 1
            if block.flags:
                self._xruns += 1
            self._src_sq.append((float(np.dot(x, x)), int(x.size)))
            y = self._resampler.process(x)
            self._feed16k(y, block.ts_ns)
            self._fed_until_ns = block.ts_ns

    def _feed16k(self, y: np.ndarray, ts_ns: int) -> None:
        if y.size == 0:
            return
        self.analyzer.feed(y)
        self._in_sq.append((float(np.dot(y, y)), int(y.size)))
        for tap in self.taps:
            tap(y, ts_ns)

    def fill_gap(self, now_ns: int) -> bool:
        """Feed silence for the time no audio arrived (beyond ``gap_fill_s``). Returns True if it did."""
        with self._lock:
            if self._fed_until_ns is None:
                self._fed_until_ns = now_ns
                return False
            gap = now_ns - self._fed_until_ns
            if gap < self.gap_fill_ns:
                return False
            n = min(int(gap * OUT_SR // 1_000_000_000), int(self.analyzer.window_s * OUT_SR))
            if n > 0:
                self._feed16k(np.zeros(n, dtype=np.float32), now_ns)
                self._gap_fills += 1
            self._fed_until_ns = now_ns
            return n > 0

    def maybe_analyze(self, now_ns: int) -> TerrorRadiusObserved | None:
        """Run the analyser if a tick is due; publish through the change-only gate. Returns what was
        published (or None)."""
        if self._next_tick_ns is None:
            self._next_tick_ns = now_ns + self.tick_ns
            return None
        if now_ns < self._next_tick_ns:
            return None
        self._next_tick_ns = max(self._next_tick_ns + self.tick_ns, now_ns - self.tick_ns // 2)
        with self._lock:
            obs = self.analyzer.analyze(now_ns)
        if obs is None:
            return None
        self._last = obs
        out = self.publisher.offer(obs)
        if out is not None:
            self._published += 1
            self.publish(out)
        return out

    def step(self, timeout_s: float = 0.05) -> None:
        """One iteration of the analysis thread: drain the queue, fill gaps, maybe analyse."""
        blk = self.queue.get(timeout=timeout_s)
        while blk is not None:
            self.process_block(blk)
            blk = self.queue.get(timeout=0) if len(self.queue) else None
        now = self.clock.now_ns()
        self.fill_gap(now)
        self.maybe_analyze(now)

    # ------------------------------------------------------------------ introspection
    def stats(self) -> PipelineStats:
        def db(sq: deque[tuple[float, int]], n_keep: int) -> float:
            items = list(sq)
            tot_n, tot_sq = 0, 0.0
            for s, n in reversed(items):
                tot_sq += s
                tot_n += n
                if tot_n >= n_keep:
                    break
            if tot_n == 0:
                return -120.0
            return rms_dbfs(np.array([np.sqrt(tot_sq / tot_n)], dtype=np.float64))

        with self._lock:
            return PipelineStats(
                blocks=self._blocks,
                dropped=self.queue.dropped,
                xruns=self._xruns,
                queue_depth=len(self.queue),
                gap_fills=self._gap_fills,
                rms_dbfs=db(self._src_sq, self._sr or OUT_SR),
                in_rms_dbfs=db(self._in_sq, OUT_SR),
                last=self._last,
                diag=self.analyzer.diag,
                published=self._published,
            )

    def reset_publisher(self) -> None:
        self.publisher.reset()
