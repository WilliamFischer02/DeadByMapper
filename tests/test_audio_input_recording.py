from __future__ import annotations

import json
import math

import numpy as np
import pytest

from deadbymapper.audio.heartbeat import HeartbeatAnalyzer
from deadbymapper.core.bus import EventBus
from deadbymapper.core.clock import ManualClock
from deadbymapper.core.events import (
    GenCounterObserved,
    InputFocusChanged,
    InteractionKind,
    InteractionObserved,
    KeyEvent,
    MapIdentified,
    MapSource,
    MouseDelta,
    PoseEstimate,
    PoseStatus,
    TerrorRadiusObserved,
    Tick,
    UserFix,
    UserMapOverride,
)
from deadbymapper.input.calibration import combine_spins, counts_per_360_from_similarity, deg_per_count
from deadbymapper.input.input_state import InputState
from deadbymapper.input.keymap import VK, KeyMap
from deadbymapper.localization.localizer import Localizer
from deadbymapper.recording.recorder import SessionRecorder
from deadbymapper.recording.replayer import read_events, replay
from deadbymapper.world.match_model import MatchModel


# --------------------------------------------------------------------------- heartbeat DSP
def _heartbeat(bpm: float, seconds: float = 6.0, sr: int = 16000, noise: float = 0.02) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    x = np.zeros_like(t)
    period = 60.0 / bpm
    for start in np.arange(0.1, seconds, period):
        for offset, amp in ((0.0, 1.0), (0.22, 0.6)):  # "lub-dub"
            idx = (t >= start + offset) & (t < start + offset + 0.09)
            tt = t[idx] - (start + offset)
            x[idx] += amp * np.sin(2 * np.pi * 60 * tt) * np.exp(-tt * 35)
    rng = np.random.default_rng(0)
    music = 0.15 * np.sin(2 * np.pi * 440 * t)  # out-of-band tone must not matter
    return (x + music + noise * rng.standard_normal(t.size)).astype(np.float32)


@pytest.mark.parametrize("bpm", [70.0, 100.0, 130.0])
def test_heartbeat_tempo(bpm):
    a = HeartbeatAnalyzer()
    sig = _heartbeat(bpm)
    for i in range(0, sig.size, 1600):
        a.feed(sig[i : i + 1600])
    out = a.analyze(1)
    assert out is not None and out.bpm is not None
    assert out.bpm == pytest.approx(bpm, rel=0.05)
    assert out.tier >= 1 and out.confidence <= 0.5  # uncalibrated thresholds -> low confidence


def test_heartbeat_silence_is_tier_zero():
    a = HeartbeatAnalyzer()
    a.feed(np.zeros(16000 * 4, dtype=np.float32))
    out = a.analyze(1)
    assert out is not None and out.tier == 0


# --------------------------------------------------------------------------- input
def test_input_state_gating_and_toggles():
    km = KeyMap.load()
    s = InputState(km)
    s.apply(KeyEvent(ts_ns=1, vk=VK["W"], down=True))
    s.apply(MouseDelta(ts_ns=2, dx=100, dy=5))
    snap = s.snapshot()
    assert snap.forward and snap.yaw_counts == 100
    s.apply(InputFocusChanged(ts_ns=3, camera_control=False))
    s.apply(MouseDelta(ts_ns=4, dx=500, dy=0))
    snap = s.snapshot()
    assert not snap.forward and snap.yaw_counts == 100  # alt-tab must not spin the map
    km.sprint_toggle = True
    s2 = InputState(km)
    s2.apply(KeyEvent(ts_ns=1, vk=VK["LSHIFT"], down=True))
    s2.apply(KeyEvent(ts_ns=2, vk=VK["LSHIFT"], down=False))
    assert s2.snapshot().sprint


def test_spin_calibration_from_similarity_curve():
    counts = np.arange(0, 9000, 30, dtype=np.float64)  # one spin = 7200 counts (0.05 deg/count)
    true_cpt = 7200.0
    sim = 0.5 + 0.5 * np.cos(2 * np.pi * counts / true_cpt)
    got = counts_per_360_from_similarity(counts, sim)
    assert got == pytest.approx(true_cpt, rel=0.01)
    assert deg_per_count(got) == pytest.approx(0.05, rel=0.01)
    assert combine_spins([7190, 7200, 7215]) == pytest.approx(7200)
    assert combine_spins([7000, 7400]) is None


# --------------------------------------------------------------------------- match model
def test_match_model_gen_completion_and_terror(registry):
    clock = ManualClock()
    bus = EventBus(synchronous=True)
    mm = MatchModel(registry, clock)
    mm.attach(bus)
    bus.publish(MapIdentified(ts_ns=0, map_id="test_realm.test_arena", confidence=1, source=MapSource.DEMO))
    bus.publish(GenCounterObserved(ts_ns=1, remaining=5))
    bus.publish(PoseEstimate(ts_ns=2, status=PoseStatus.LOCKED, x=12.5, y=11.0, sigma_m=1.0))
    bus.publish(InteractionObserved(ts_ns=3, kind=InteractionKind.GENERATOR, active=True))
    bus.publish(GenCounterObserved(ts_ns=4, remaining=4))
    snap = mm.snapshot()
    g01 = next(g for g in snap.gens if g.id == "g01")
    assert g01.state == "completed" and snap.gens_remaining == 4
    bus.publish(TerrorRadiusObserved(ts_ns=5, tier=2))
    t = mm.snapshot().terror
    assert t is not None and t.inner_m == pytest.approx(32 / 3) and t.outer_m == pytest.approx(64 / 3)
    bus.publish(TerrorRadiusObserved(ts_ns=6, tier=0))
    assert mm.snapshot().terror is None
    assert mm.snapshot().strategy is not None
    # the player confirms the same map from the ambiguity chip mid-trial: nothing may be wiped
    bus.publish(UserMapOverride(ts_ns=7, map_id="test_realm.test_arena"))
    assert next(g for g in mm.snapshot().gens if g.id == "g01").state == "completed"


def test_reconfirming_the_same_map_keeps_the_localizer_estimate(registry):
    clock = ManualClock()
    bus = EventBus(synchronous=True)
    loc = Localizer(clock, registry, deg_per_count=0.05, n_particles=1000, seed=1, require_match_state=False)
    loc.attach(bus)
    bus.publish(MapIdentified(ts_ns=0, map_id="test_realm.test_arena", confidence=1, source=MapSource.DEMO))
    bus.publish(UserFix(ts_ns=1, pos_m=(12.0, 12.0)))
    assert loc.pf is not None
    pf = loc.pf
    bus.publish(MapIdentified(ts_ns=2, map_id="test_realm.test_arena", confidence=1, source=MapSource.OCR_MENU))
    bus.publish(UserMapOverride(ts_ns=3, map_id="test_realm.test_arena"))
    assert loc.pf is pf


# --------------------------------------------------------------------------- record / replay
def test_record_and_replay_roundtrip(tmp_path):
    bus = EventBus(synchronous=True)
    rec = SessionRecorder(tmp_path, name="s1", notes="test")
    rec.attach(bus)
    evts = [
        Tick(ts_ns=1),
        KeyEvent(ts_ns=2, vk=VK["W"], down=True),
        MouseDelta(ts_ns=3, dx=7, dy=0),
        PoseEstimate(ts_ns=4, status=PoseStatus.NO_FIX),
    ]
    for e in evts:
        bus.publish(e)
    rec.close()
    meta = json.loads((tmp_path / "s1" / "meta.json").read_text())
    assert meta["record_schema"] == 1 and rec.count == 4
    back = list(read_events(tmp_path / "s1"))
    assert back == evts[:3]  # derived PoseEstimate skipped on replay
    bus2 = EventBus(synchronous=True)
    seen: list = []
    bus2.subscribe(None, seen.append)
    n = replay(tmp_path / "s1", bus2, ManualClock())
    assert n == 3 and seen == evts[:3]
    assert math.isfinite(1.0)
