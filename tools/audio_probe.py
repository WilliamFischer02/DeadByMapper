"""Audio probe: run the real loopback -> heartbeat pipeline and print what it hears, once per second.

Usage (from the repo root):
    uv run python tools/audio_probe.py --mode device --seconds 20      # no game needed
    uv run python tools/audio_probe.py --mode auto --seconds 120       # during a DBD custom game

Each line: mode in use, device / process, sample rate, RMS level (dBFS, source and 16 kHz analyser
input), analyser output (tempo candidate in bpm, tier, confidence, lullaby flag), published-event
count, queue depth, dropped blocks and xruns. The analyser input (mono, 16 kHz) is written to a local
WAV under %LOCALAPPDATA%\\DeadByMapper\\probes\\audio\\ - never inside the repo, never uploaded.

Tier numbers come from UNCALIBRATED placeholder bpm thresholds (calibrated: false, confidence <= 0.5)
until session S3 is recorded (docs/AUDIO_HEARTBEAT.md). Read them as "tempo went up/down", not as
distance.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from deadbymapper.audio.heartbeat import HeartbeatDiag  # noqa: E402
from deadbymapper.audio.loopback import LoopbackHeartbeatService  # noqa: E402
from deadbymapper.config.paths import user_dir  # noqa: E402
from deadbymapper.core.bus import EventBus  # noqa: E402
from deadbymapper.core.clock import MonotonicClock  # noqa: E402
from deadbymapper.core.events import ServiceHealth, TerrorRadiusObserved  # noqa: E402


def probe_dir() -> Path:
    p = user_dir() / "probes" / "audio"
    p.mkdir(parents=True, exist_ok=True)
    return p


class WavTap:
    """Writes the analyser input (float32, 16 kHz) as 16-bit PCM."""

    def __init__(self, path: Path, sr: int = 16000) -> None:
        self.path = path
        self._w = wave.open(str(path), "wb")  # noqa: SIM115 - closed in close()
        self._w.setnchannels(1)
        self._w.setsampwidth(2)
        self._w.setframerate(sr)
        self._lock = threading.Lock()
        self.frames = 0

    def __call__(self, y: np.ndarray, _ts_ns: int) -> None:
        pcm = (np.clip(y, -1.0, 1.0) * 32767.0).astype("<i2")
        with self._lock:
            self._w.writeframes(pcm.tobytes())
            self.frames += pcm.size

    def close(self) -> None:
        with self._lock:
            self._w.close()


def find_dbd_pid() -> int | None:
    from deadbymapper.gamelink.process_watcher import ProcessWatcher

    found = ProcessWatcher(EventBus(synchronous=True), MonotonicClock()).find()
    return found.pid if found else None


def fmt_obs(o: TerrorRadiusObserved | None, d: HeartbeatDiag | None) -> str:
    cand = f"{d.bpm_candidate:6.1f}" if d is not None and d.bpm_candidate is not None else "   -  "
    per = f"{d.periodicity:4.2f}" if d is not None else "  - "
    lvl = f"{d.level_db:6.1f}/{d.level_fast_db:6.1f}" if d is not None else "     -/     - "
    head = f"band4s/0.3s={lvl} dB bpm~{cand} period={per}"
    if o is None:
        return f"{head} tier=- conf=-    lullaby=-  "
    return f"{head} tier={o.tier} conf={o.confidence:.2f} lullaby={'yes' if o.lullaby else 'no '}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--mode", choices=["auto", "process", "device"], default="auto")
    ap.add_argument("--device", default=None, help="loopback device name substring (default: default output)")
    ap.add_argument("--wav", action="store_true", help="also write the analyser input as a local WAV (opt-in)")
    args = ap.parse_args(argv)

    if sys.platform != "win32":
        print("audio_probe needs Windows (WASAPI loopback).")
        return 2

    clock = MonotonicClock()
    bus = EventBus(synchronous=True)
    timeline: list[str] = []
    t_start = time.perf_counter()

    def on_health(e: ServiceHealth) -> None:
        if e.service == LoopbackHeartbeatService.name:
            print(f"[health {time.perf_counter() - t_start:5.1f}s] {e.status}: {e.detail}", flush=True)

    def on_tr(e: TerrorRadiusObserved) -> None:
        bpm = f"{e.bpm:.1f}" if e.bpm is not None else "-"
        timeline.append(f"{time.perf_counter() - t_start:6.2f}s tier={e.tier} bpm={bpm} conf={e.confidence:.2f}")

    bus.subscribe(ServiceHealth, on_health)
    bus.subscribe(TerrorRadiusObserved, on_tr)

    svc = LoopbackHeartbeatService(bus, clock, mode=args.mode, device_name=args.device)
    tap: WavTap | None = None
    if args.wav:
        tap = WavTap(probe_dir() / f"{datetime.now():%Y%m%d-%H%M%S}.wav")
        svc.pipeline.taps.append(tap)

    pid = find_dbd_pid() if args.mode != "device" else None
    print(f"audio_probe: mode={args.mode} seconds={args.seconds:.0f} dbd_pid={pid or 'not running'}")
    print("tiers use UNCALIBRATED placeholder thresholds (calibrated: false) - watch the bpm trend")
    svc.set_dbd_pid(pid)
    svc.start()
    next_pid_check = time.perf_counter() + 2.0
    try:
        for sec in range(1, int(args.seconds) + 1):
            target = t_start + sec
            while (now := time.perf_counter()) < target:
                time.sleep(min(0.1, target - now))
            if args.mode != "device" and now >= next_pid_check:
                next_pid_check = now + 2.0
                new_pid = find_dbd_pid()
                if new_pid != pid:
                    pid = new_pid
                    svc.set_dbd_pid(pid)
            st = svc.status()
            s = st.stats
            name = st.source.name if st.source else "-"
            sr = st.source.sample_rate if st.source else 0
            print(
                f't={sec:4d}s mode={st.mode:<7} dev="{name[:40]}" sr={sr:5d} '
                f"rms={s.rms_dbfs:6.1f} dBFS in16k={s.in_rms_dbfs:6.1f} | {fmt_obs(s.last, s.diag)} | "
                f"pub={s.published:3d} q={s.queue_depth:2d} drop={s.dropped} xrun={s.xruns} blocks={s.blocks}",
                flush=True,
            )
    except KeyboardInterrupt:
        print("interrupted")
    finally:
        svc.stop()
        if tap is not None:
            tap.close()
    print("\npublished TerrorRadiusObserved timeline (send this back):")
    for line in timeline or ["(none)"]:
        print("  " + line)
    if tap is not None:
        print(f"\nWAV (analyser input, mono 16 kHz, {tap.frames / 16000:.1f} s; stays local): {tap.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
