"""Hardware loopback tests (Windows only). They play short, quiet tones on the default output device.

* device loopback: 3 x 0.5 s 440 Hz tones played from THIS process must show up in the analyser input
  (RMS and 440 Hz energy clearly above the surrounding silence), within a small latency.
* process loopback: a tone played by a child process is captured; a tone from THIS process is not.

Skipped cleanly when there is no loopback device / process loopback is unsupported. Background audio
(music, voice) may be playing on the user's machine, so the assertions use margins, not absolutes.
"""

from __future__ import annotations

import io
import subprocess
import sys
import textwrap
import time
import wave

import numpy as np
import pytest

from deadbymapper.core.bus import EventBus
from deadbymapper.core.clock import MonotonicClock

pytestmark = [pytest.mark.windows_only, pytest.mark.audio_hw]

TONE_HZ = 440.0


def _wav_bytes(freq: float, seconds: float, amp: float = 0.2, sr: int = 44100) -> bytes:
    t = np.arange(int(seconds * sr)) / sr
    ramp = np.minimum(1.0, np.minimum(t, seconds - t) / 0.005)  # 5 ms fades: no clicks
    y = (amp * ramp * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(y.tobytes())
    return buf.getvalue()


def _tone_power(y: np.ndarray, freq: float, sr: int = 16000) -> float:
    n = np.arange(y.size)
    z = np.sum(y.astype(np.float64) * np.exp(-2j * np.pi * freq * n / sr))
    return float(np.abs(z) ** 2) / max(1, y.size) ** 2


def _db(x: float) -> float:
    return 10.0 * float(np.log10(x + 1e-20))


def test_device_loopback_captures_beeps_from_this_process():
    pytest.importorskip("pyaudiowpatch")
    import winsound

    from deadbymapper.audio.loopback import LoopbackHeartbeatService

    clock = MonotonicClock()
    svc = LoopbackHeartbeatService(EventBus(synchronous=True), clock, mode="device")
    taps: list[tuple[int, np.ndarray]] = []
    svc.pipeline.taps.append(lambda y, _ts: taps.append((clock.now_ns(), y.copy())))
    svc.start()
    try:
        deadline = time.monotonic() + 5.0
        while svc.status().source is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if svc.status().source is None:
            pytest.skip(f"no WASAPI loopback device: {svc.health}")
        time.sleep(0.8)  # silence reference
        wav = _wav_bytes(TONE_HZ, 0.5)
        marks: list[tuple[int, int]] = []
        for _ in range(3):
            t0 = clock.now_ns()
            winsound.PlaySound(wav, winsound.SND_MEMORY)  # synchronous, ~0.5 s
            marks.append((t0, clock.now_ns()))
            time.sleep(0.5)
        time.sleep(0.2)
        stats = svc.status().stats
    finally:
        svc.stop()

    ts = np.array([t for t, _ in taps], dtype=np.int64)
    rms = np.array([np.sqrt(np.mean(np.square(y, dtype=np.float64))) if y.size else 0.0 for _, y in taps])
    tone = np.array([_tone_power(y, TONE_HZ) for _, y in taps])

    def sel(lo_ns: int, hi_ns: int) -> np.ndarray:
        return (ts >= lo_ns) & (ts < hi_ns)

    ms = 1_000_000
    on = np.zeros(ts.size, dtype=bool)
    off = sel(marks[0][0] - 700 * ms, marks[0][0] - 50 * ms)
    for i, (t0, t1) in enumerate(marks):
        on |= sel(t0 + 100 * ms, t1 - 50 * ms)
        nxt = marks[i + 1][0] if i + 1 < len(marks) else t1 + 500 * ms
        off |= sel(t1 + 150 * ms, nxt - 50 * ms)
    assert on.sum() > 20 and off.sum() > 20, (on.sum(), off.sum())
    rms_on, rms_off = float(np.median(rms[on])), float(np.median(rms[off]))
    margin_db = 20 * np.log10((rms_on + 1e-9) / (rms_off + 1e-9))
    tone_margin_db = _db(float(np.median(tone[on]))) - _db(float(np.median(tone[off])))

    # beep -> analyser latency: first analysed block whose 440 Hz energy crosses the midpoint (in dB)
    thr = 10 ** ((_db(float(np.median(tone[on]))) + _db(float(np.median(tone[off])))) / 20)
    lat_ms = []
    for t0, _t1 in marks:
        idx = np.nonzero((ts >= t0) & (tone > thr))[0]
        if idx.size:
            lat_ms.append((ts[idx[0]] - t0) / ms)
    on_db, off_db = 20 * np.log10(rms_on + 1e-9), 20 * np.log10(rms_off + 1e-9)
    lat = [round(float(x), 1) for x in lat_ms]
    print(
        f"\n[device loopback] rms on={on_db:.1f} dBFS off={off_db:.1f} dBFS margin={margin_db:.1f} dB, "
        f"440 Hz margin={tone_margin_db:.1f} dB, latency ms={lat}, "
        f"dropped={stats.dropped} xruns={stats.xruns} blocks={stats.blocks}"
    )
    assert margin_db > 3.0
    assert tone_margin_db > 10.0
    assert len(lat_ms) == 3 and max(lat_ms) < 250.0
    assert stats.dropped == 0


_PLAYER = textwrap.dedent(
    """
    import io, sys, time, wave, winsound
    import numpy as np
    sr = 44100; secs = float(sys.argv[2]); t = np.arange(int(secs * sr)) / sr
    y = (0.2 * np.sin(2 * np.pi * float(sys.argv[1]) * t) * 32767).astype("<i2")
    buf = io.BytesIO(); w = wave.open(buf, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes(y.tobytes()); w.close()
    print("ready", flush=True)
    sys.stdin.readline()
    winsound.PlaySound(buf.getvalue(), winsound.SND_MEMORY)
    print("done", flush=True)
    time.sleep(1.0)
    """
)


def test_process_loopback_captures_only_the_target_process():
    from deadbymapper.audio.sources import ProcessLoopbackSource
    from deadbymapper.audio.wasapi_com import process_loopback_supported

    ok, why = process_loopback_supported()
    if not ok:
        pytest.skip(why)
    import winsound

    child = subprocess.Popen(
        [sys.executable, "-c", _PLAYER, "330", "2.0"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout is not None and child.stdin is not None
        assert child.stdout.readline().strip() == "ready"
        rec: list[tuple[float, float]] = []
        src = ProcessLoopbackSource(child.pid)

        def sink(mono: np.ndarray, _flags: int) -> None:
            rec.append((time.perf_counter(), float(np.sqrt(np.mean(np.square(mono, dtype=np.float64))))))

        try:
            src.open(sink)
        except OSError as exc:
            pytest.skip(f"process loopback unavailable: {exc}")
        try:
            time.sleep(0.3)
            o0 = time.perf_counter()
            winsound.PlaySound(_wav_bytes(TONE_HZ, 0.5), winsound.SND_MEMORY)  # OUR tone: must not be captured
            o1 = time.perf_counter()
            child.stdin.write("go\n")
            child.stdin.flush()
            c0 = time.perf_counter()
            assert child.stdout.readline().strip() == "done"
            c1 = time.perf_counter()
            time.sleep(0.6)
            s1 = time.perf_counter()
        finally:
            src.close()
    finally:
        child.kill()
        child.wait(5)

    a = np.array(rec)

    def med(lo: float, hi: float) -> float:
        m = (a[:, 0] >= lo) & (a[:, 0] < hi)
        assert m.sum() > 5
        return float(np.median(a[m, 1]))

    own, playing, after = med(o0 + 0.1, o1 - 0.05), med(c0 + 0.2, c1 - 0.1), med(c1 + 0.2, s1)
    print(f"\n[process loopback] child playing rms={playing:.4f}, after={after:.5f}, other process tone={own:.5f}")
    assert playing > 0.05
    assert after < 0.005 and own < 0.005
    assert src.discontinuities <= 2
