"""Loopback pipeline logic without audio hardware: dtype/downmix, queue drop policy, change-only
publishing + keep-alive, mode selection, supervisor (open / fallback / reopen / backoff) with fakes."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from deadbymapper.audio.blocks import (
    AudioBlock,
    BlockQueue,
    TierPublisher,
    downmix,
    rms_dbfs,
    select_mode,
    to_float32,
)
from deadbymapper.audio.heartbeat import HeartbeatAnalyzer
from deadbymapper.audio.loopback import Backoff, LoopbackHeartbeatService
from deadbymapper.audio.pipeline import HeartbeatPipeline
from deadbymapper.audio.sources import NoLoopbackDeviceError, SourceInfo, pick_loopback_device
from deadbymapper.core.bus import EventBus
from deadbymapper.core.clock import ManualClock, s_to_ns
from deadbymapper.core.events import GameProcessChanged, ServiceHealth, TerrorRadiusObserved


# --------------------------------------------------------------------------- dtype + downmix
def test_int16_to_float32_scaling_and_copy():
    raw = np.array([0, 16384, -32768, 32767], dtype="<i2").tobytes()
    x = to_float32(raw, "int16")
    assert x.dtype == np.float32
    assert x.tolist() == pytest.approx([0.0, 0.5, -1.0, 32767 / 32768])


def test_float32_passthrough_is_a_copy():
    buf = bytearray(np.array([0.25, -0.5], dtype="<f4").tobytes())
    x = to_float32(buf, "float32")
    buf[:] = b"\x00" * len(buf)  # the driver reuses its buffer after the callback returns
    assert x.tolist() == [0.25, -0.5]


def test_int32_and_unknown_format():
    raw = np.array([1 << 30], dtype="<i4").tobytes()
    assert to_float32(raw, "int32")[0] == pytest.approx(0.5)
    with pytest.raises(ValueError):
        to_float32(raw, "int24")  # type: ignore[arg-type]


def test_downmix_stereo_mean_mono_passthrough_partial_frame():
    inter = np.array([1.0, 0.0, 0.5, 0.5, -1.0, 1.0, 0.3], dtype=np.float32)  # 3 frames + a stray sample
    assert downmix(inter, 2).tolist() == pytest.approx([0.5, 0.5, 0.0])
    assert downmix(inter[:4], 1).tolist() == pytest.approx(inter[:4].tolist())
    six = np.tile(np.arange(6, dtype=np.float32), 2)  # 5.1
    assert downmix(six, 6).tolist() == pytest.approx([2.5, 2.5])


def test_rms_dbfs():
    assert rms_dbfs(np.zeros(10, dtype=np.float32)) == -120.0
    assert rms_dbfs(np.ones(10, dtype=np.float32)) == pytest.approx(0.0, abs=1e-6)
    assert rms_dbfs(np.full(10, 0.1, dtype=np.float32)) == pytest.approx(-20.0, abs=1e-4)


# --------------------------------------------------------------------------- block queue
def _blk(i: int) -> AudioBlock:
    return AudioBlock(ts_ns=i, mono=np.full(4, i, dtype=np.float32))


def test_block_queue_drops_oldest_and_counts():
    q = BlockQueue(maxlen=3)
    for i in range(5):
        q.put(_blk(i))
    assert len(q) == 3 and q.dropped == 2 and q.put_count == 5
    assert [q.get(0).ts_ns for _ in range(3)] == [2, 3, 4]  # newest audio survives, order kept
    assert q.get(timeout=0.01) is None


def test_block_queue_wakes_consumer_across_threads():
    q = BlockQueue()
    got: list[int] = []

    def consumer() -> None:
        b = q.get(timeout=2.0)
        got.append(b.ts_ns if b else -1)

    t = threading.Thread(target=consumer)
    t.start()
    time.sleep(0.02)
    q.put(_blk(7))
    t.join(2.0)
    assert got == [7]


# --------------------------------------------------------------------------- change-only publishing
def _obs(t_s: float, tier: int, **kw) -> TerrorRadiusObserved:
    return TerrorRadiusObserved(ts_ns=s_to_ns(t_s), tier=tier, confidence=0.4, **kw)


def test_tier_publisher_change_only_with_keepalive():
    clock = ManualClock()
    p = TierPublisher(keepalive_s=1.0)
    out = []
    tiers = [0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 2, 2, 0, 0, 0, 0, 0, 0, 0, 0]
    for tier in tiers:  # 4 Hz analysis ticks
        r = p.offer(_obs(clock.advance_s(0.25) / 1e9, tier))
        if r is not None:
            out.append((round(r.ts_ns / 1e9, 2), r.tier))
    # 0 first, 1 on change, keep-alive 1.0 s later, 2 on change, 0 on change; no keep-alive at tier 0
    assert out == [(0.25, 0), (1.0, 1), (2.0, 1), (2.75, 2), (3.25, 0)]


def test_tier_publisher_keepalive_never_faster_than_1hz():
    p = TierPublisher(keepalive_s=1.0)
    stamps = [r.ts_ns for i in range(400) if (r := p.offer(_obs(i * 0.25, 3))) is not None]
    assert len(stamps) == 100  # 100 s at tier 3 -> exactly one event per second
    assert min(np.diff(stamps)) >= s_to_ns(1.0)


def test_tier_publisher_publishes_on_lullaby_or_chase_change_and_ignores_none():
    p = TierPublisher()
    assert p.offer(None) is None
    assert p.offer(_obs(0.0, 1)) is not None
    assert p.offer(_obs(0.25, 1, lullaby=True)) is not None
    assert p.offer(_obs(0.5, 1, lullaby=True, in_chase=True)) is not None
    assert p.offer(_obs(0.75, 1, lullaby=True, in_chase=True)) is None


# --------------------------------------------------------------------------- mode selection
@pytest.mark.parametrize(
    ("setting", "pid", "supported", "perr", "mode"),
    [
        ("auto", 1234, True, None, "process"),
        ("auto", None, True, None, "none"),  # privacy: nothing to listen to without the game
        ("auto", 1234, False, None, "device"),
        ("auto", 1234, True, "E_FAIL", "device"),
        ("process", 1234, True, None, "process"),
        ("process", None, True, None, "none"),
        ("process", 1234, False, None, "none"),
        ("process", 1234, True, "E_FAIL", "none"),
        ("device", 1234, True, None, "device"),
        ("DEVICE", None, False, None, "device"),
    ],
)
def test_select_mode(setting, pid, supported, perr, mode):
    c = select_mode(setting, pid, supported, "old windows", perr)
    assert c.mode == mode
    if mode == "process":
        assert c.pid == pid and c.reason == ""
    if mode == "device" and setting == "auto":
        assert c.reason  # the health detail must say WHY process loopback is not used
        if perr:
            assert c.reason == perr
        elif not supported:
            assert c.reason == "old windows"
        else:
            assert "not running" in c.reason


def test_pick_loopback_device():
    devs = [
        {"index": 1, "name": "Speakers (Realtek) [Loopback]", "isLoopbackDevice": True},
        {"index": 2, "name": "Headset (Arctis) [Loopback]", "isLoopbackDevice": True},
        {"index": 3, "name": "Microphone", "isLoopbackDevice": False},
    ]
    assert pick_loopback_device(devs, "arctis", devs[0])["index"] == 2
    assert pick_loopback_device(devs, None, devs[0])["index"] == 1
    assert pick_loopback_device(devs, None, None)["index"] == 1
    with pytest.raises(NoLoopbackDeviceError):
        pick_loopback_device(devs, "nope", devs[0])
    with pytest.raises(NoLoopbackDeviceError):
        pick_loopback_device([devs[2]], None, None)


def test_backoff_doubles_and_resets():
    b = Backoff(0.5, 4.0)
    assert [b.fail() for _ in range(5)] == [0.5, 1.0, 2.0, 4.0, 4.0]
    b.ok()
    assert b.fail() == 0.5


# --------------------------------------------------------------------------- pipeline (ManualClock)
def _heartbeat(bpm: float, seconds: float, sr: int) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    x = np.zeros_like(t)
    for start in np.arange(0.1, seconds, 60.0 / bpm):
        for off, amp in ((0.0, 1.0), (0.22, 0.6)):
            idx = (t >= start + off) & (t < start + off + 0.09)
            tt = t[idx] - (start + off)
            x[idx] += amp * np.sin(2 * np.pi * 60 * tt) * np.exp(-tt * 35)
    return (0.5 * x).astype(np.float32)


def _run_pipeline(pipe: HeartbeatPipeline, clock: ManualClock, sig: np.ndarray, sr: int, block: int = 480) -> None:
    for i in range(0, len(sig), block):
        clock.advance_s(block / sr)
        pipe.queue.put(AudioBlock(clock.now_ns(), sig[i : i + block]))
        pipe.step(timeout_s=0)


def test_pipeline_publishes_tier_changes_only_and_decays_to_zero_on_gap():
    clock = ManualClock()
    seen: list[TerrorRadiusObserved] = []
    pipe = HeartbeatPipeline(clock, HeartbeatAnalyzer(), seen.append)
    sr = 48000
    pipe.set_source_rate(sr)
    _run_pipeline(pipe, clock, _heartbeat(100.0, 10.0, sr), sr)
    st = pipe.stats()
    assert st.last is not None and st.last.tier >= 1 and st.last.bpm == pytest.approx(100.0, rel=0.05)
    assert st.diag is not None and st.diag.periodicity > 0.25
    assert st.last.confidence <= 0.5  # uncalibrated tier model
    assert st.blocks == 1000 and st.dropped == 0
    in_range = [e for e in seen if e.tier > 0]
    # ~8 s in range at 4 Hz analysis = 32 ticks; change-only + 1 Hz keep-alive keeps it near 1/s
    assert 1 <= len(in_range) <= 10
    assert all(np.diff([e.ts_ns for e in in_range]) >= s_to_ns(1.0) - 1) or len({e.tier for e in in_range}) > 1
    # the device stalls (no blocks at all): silence fill drives the tier to 0 instead of freezing
    n_before = len(seen)
    for _ in range(40):  # 6 s of nothing
        clock.advance_s(0.15)
        pipe.step(timeout_s=0)
    assert pipe.stats().gap_fills > 0
    assert seen[-1].tier == 0 and len(seen) > n_before


def test_pipeline_handles_sample_rate_change_and_counts_xruns():
    clock = ManualClock()
    pipe = HeartbeatPipeline(clock, HeartbeatAnalyzer(), lambda e: None)
    pipe.set_source_rate(44100)
    pipe.process_block(AudioBlock(clock.advance_s(0.01), np.zeros(441, dtype=np.float32), flags=1))
    pipe.set_source_rate(48000)
    pipe.process_block(AudioBlock(clock.advance_s(0.01), np.zeros(480, dtype=np.float32)))
    st = pipe.stats()
    assert st.blocks == 2 and st.xruns == 1


def test_pipeline_queue_overflow_is_counted_not_fatal():
    clock = ManualClock()
    pipe = HeartbeatPipeline(clock, HeartbeatAnalyzer(), lambda e: None, queue=BlockQueue(maxlen=8))
    pipe.set_source_rate(48000)
    for _ in range(20):  # analysis thread stalled while the callback keeps delivering
        pipe.queue.put(AudioBlock(clock.advance_s(0.01), np.zeros(480, dtype=np.float32)))
    pipe.step(timeout_s=0)
    st = pipe.stats()
    assert st.dropped == 12 and st.blocks == 8 and st.queue_depth == 0


# --------------------------------------------------------------------------- service supervisor (fakes)
class FakeSource:
    def __init__(self, kind: str, name: str, fail: str | None = None, sr: int = 48000) -> None:
        self.kind, self.name, self.fail, self.sr = kind, name, fail, sr
        self.is_alive = True
        self.closed = False
        self.sink = None

    def open(self, sink):
        if self.fail:
            raise OSError(self.fail)
        self.sink = sink
        return SourceInfo(self.kind, self.name, self.sr, 2, "float32")

    def alive(self) -> bool:
        return self.is_alive and not self.closed

    def close(self) -> None:
        self.closed = True


class Harness:
    def __init__(self, mode: str = "auto", supported: bool = True, **kw) -> None:
        self.clock = ManualClock()
        self.bus = EventBus(synchronous=True)
        self.health: list[ServiceHealth] = []
        self.bus.subscribe(ServiceHealth, self.health.append)
        self.opened: list[FakeSource] = []
        self.fail_process: str | None = None
        self.fail_device: str | None = None
        self.endpoint = "ep-1"
        self.svc = LoopbackHeartbeatService(
            self.bus,
            self.clock,
            mode=mode,
            source_factory=self.factory,
            process_support=(supported, "Windows build 18363 < 19041"),
            endpoint_id=lambda: self.endpoint,
            **kw,
        )

    def factory(self, choice, device_name):
        if choice.mode == "process":
            src = FakeSource("process", f"Dead by Daylight (pid {choice.pid})", self.fail_process)
        else:
            src = FakeSource("device", device_name or "Speakers (Realtek)", self.fail_device)
        self.opened.append(src)
        return src

    def tick(self, s: float = 0.5) -> None:
        self.clock.advance_s(s)
        self.svc.supervise_once()

    @property
    def last(self) -> ServiceHealth:
        return self.health[-1]


def test_auto_mode_waits_then_process_when_dbd_appears_then_back():
    """Privacy: auto mode captures nothing while the game is not running (device loopback would hear
    Discord/music for no purpose); it only falls back to device loopback while DBD runs."""
    h = Harness("auto")
    h.tick()
    assert h.svc.status().mode == "none" and not h.opened
    assert h.last.status == "degraded"
    assert "Dead by Daylight not running" in h.last.detail
    h.svc._on_process(GameProcessChanged(ts_ns=1, running=True, pid=4242))
    h.tick()
    assert h.svc.status().mode == "process" and len(h.opened) == 1
    assert h.last.status == "ok" and "pid 4242" in h.last.detail
    n = len(h.health)
    h.tick()
    h.tick()
    assert len(h.health) == n  # steady state: no health spam
    h.svc._on_process(GameProcessChanged(ts_ns=2, running=False))
    h.tick()
    assert h.svc.status().mode == "none" and h.opened[0].closed


def test_auto_mode_falls_back_to_device_when_process_loopback_fails_and_retries_later():
    h = Harness("auto", process_retry_s=30.0)
    h.fail_process = "HRESULT 0x80070005"
    h.svc.set_dbd_pid(99)
    h.tick()  # process attempt fails
    h.tick()  # falls back immediately
    st = h.svc.status()
    assert st.mode == "device"
    assert "process loopback unavailable: OSError: HRESULT 0x80070005" in h.last.detail
    h.fail_process = None
    for _ in range(10):
        h.tick(1.0)
    assert h.svc.status().mode == "device"  # no flapping inside the retry window
    for _ in range(25):
        h.tick(1.0)
    assert h.svc.status().mode == "process" and h.last.status == "ok"


def test_unsupported_windows_uses_device_with_reason():
    h = Harness("auto", supported=False)
    h.svc.set_dbd_pid(5)
    h.tick()
    assert h.svc.status().mode == "device"
    assert "Windows build 18363 < 19041" in h.last.detail


def test_forced_process_waits_for_the_game_and_never_uses_device():
    h = Harness("process")
    h.tick()
    assert h.svc.status().mode == "none" and h.opened == []
    assert h.last.status == "degraded" and "waiting" in h.last.detail
    h.svc.set_dbd_pid(7)
    h.tick()
    assert h.svc.status().mode == "process"


def test_forced_device_ignores_the_game():
    h = Harness("device")
    h.svc.set_dbd_pid(7)
    h.tick()
    assert h.svc.status().mode == "device" and "forced" in h.last.detail


def test_dead_stream_reopens_with_backoff_and_reports_down():
    h = Harness("device")
    h.tick()
    first = h.opened[-1]
    first.is_alive = False  # unplugged
    h.fail_device = "no WASAPI loopback device found"
    h.tick()
    assert first.closed and h.last.status == "down" and "retrying in 0.5 s" in h.last.detail
    attempts = len(h.opened)
    h.tick(0.2)  # inside the 0.5 s backoff -> no new attempt
    assert len(h.opened) == attempts
    h.tick(0.5)
    assert len(h.opened) == attempts + 1 and "retrying in 1.0 s" in h.last.detail
    h.fail_device = None
    h.tick(1.5)
    assert h.svc.status().mode == "device" and h.last.status == "degraded"


def test_default_output_device_change_reopens_device_loopback():
    h = Harness("device", endpoint_poll_s=2.0)
    h.tick()
    assert len(h.opened) == 1
    h.endpoint = "ep-2"  # user switched from speakers to headset
    h.tick(0.5)
    h.tick(2.0)
    assert len(h.opened) == 2 and h.opened[0].closed


def test_disabled_in_settings():
    class S:
        enabled = False
        mode = "auto"
        device_name = None

    bus = EventBus(synchronous=True)
    seen: list[ServiceHealth] = []
    bus.subscribe(ServiceHealth, seen.append)
    LoopbackHeartbeatService(bus, ManualClock(), settings=S()).start()
    assert seen[-1].status == "disabled"


def test_service_threads_end_to_end_with_fake_source_and_bus_subscription():
    """Real threads: GameProcessChanged on the bus switches auto mode to process loopback; blocks pushed
    through the sink reach the analyser; stop() closes everything."""
    from deadbymapper.core.clock import MonotonicClock

    clock = MonotonicClock()
    bus = EventBus(synchronous=True)
    opened: list[FakeSource] = []

    def factory(choice, _name):
        opened.append(FakeSource(choice.mode, str(choice.pid)))
        return opened[-1]

    svc = LoopbackHeartbeatService(
        bus,
        clock,
        mode="auto",
        source_factory=factory,
        process_support=(True, ""),
        endpoint_id=lambda: None,
        supervise_s=0.02,
    )
    svc.start()
    try:
        deadline = time.monotonic() + 3
        while svc.status().mode != "none" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not opened  # auto mode opens nothing before the game runs
        bus.publish(GameProcessChanged(ts_ns=clock.now_ns(), running=True, pid=31337))
        while svc.status().mode != "process" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert svc.status().mode == "process"
        src = opened[-1]
        for _ in range(50):
            src.sink(np.zeros(480, dtype=np.float32), 0)
        while svc.status().stats.blocks < 50 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert svc.status().stats.blocks == 50
    finally:
        svc.stop()
    assert all(s.closed for s in opened) and svc.health == "down"


def test_startup_pid_lookup_covers_the_missed_game_process_event():
    """ProcessWatcher may publish GameProcessChanged before this service subscribes (start order):
    the supervisor does one read-only lookup at start-up so auto mode still picks process loopback."""
    from deadbymapper.core.clock import MonotonicClock

    opened: list[FakeSource] = []

    def factory(choice, _name):
        opened.append(FakeSource(choice.mode, str(choice.pid)))
        return opened[-1]

    svc = LoopbackHeartbeatService(
        EventBus(synchronous=True),
        MonotonicClock(),
        mode="auto",
        source_factory=factory,
        process_support=(True, ""),
        endpoint_id=lambda: None,
        pid_lookup=lambda: 2024,
        supervise_s=0.02,
    )
    svc.start()
    try:
        deadline = time.monotonic() + 3
        while not opened and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        svc.stop()
    assert opened and opened[0].kind == "process" and opened[0].name == "2024"


def test_process_retry_backs_off_when_it_keeps_failing():
    h = Harness("auto", process_retry_s=30.0)
    h.fail_process = "E_FAIL"
    h.svc.set_dbd_pid(99)
    attempts = []
    for _ in range(200):  # 100 s
        n = sum(1 for s in h.opened if s.kind == "process")
        h.tick(0.5)
        if sum(1 for s in h.opened if s.kind == "process") > n:
            attempts.append(h.clock.now_ns() / 1e9)
    gaps = np.diff(attempts)
    assert len(attempts) == 3 and gaps[0] == pytest.approx(30.5, abs=0.6) and gaps[1] == pytest.approx(60.5, abs=0.6)
    assert h.svc.status().mode == "device"
