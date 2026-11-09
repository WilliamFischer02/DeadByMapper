from __future__ import annotations

import numpy as np
import pytest

from deadbymapper.capture.regions import Region, load_regions
from deadbymapper.core.bus import EventBus
from deadbymapper.core.clock import ManualClock
from deadbymapper.core.events import (
    GameProcessChanged,
    InputFocusChanged,
    InteractionKind,
    MapAmbiguous,
    MapCandidate,
    MapIdentified,
    MapSource,
    MatchState,
    MatchStateChanged,
    Role,
    ScreenObserved,
    UserMapOverride,
)
from deadbymapper.perception.map_identifier import MapIdentifier, candidates_from_text
from deadbymapper.perception.motion_flow import FlowMotionEstimator
from deadbymapper.perception.prompt_reader import PromptInterpreter, load_prompts
from deadbymapper.perception.state_machine import MatchStateMachine

CT = "macmillan_estate.coal_tower"


@pytest.fixture
def ident(registry):
    bus = EventBus(synchronous=True)
    mi = MapIdentifier(registry, ManualClock())
    mi.attach(bus)
    out: list = []
    bus.subscribe(MapIdentified, out.append)
    bus.subscribe(MapAmbiguous, out.append)
    return bus, mi, out


# --------------------------------------------------------------------------- map identification
def test_ocr_needs_two_agreeing_reads(ident, registry):
    bus, mi, out = ident
    for c in candidates_from_text(registry, "COAL T0WER", MapSource.OCR_INTRO, 1)[:1]:
        bus.publish(c)
    assert not [e for e in out if isinstance(e, MapIdentified)] or out[0].confidence >= 0.97
    for c in candidates_from_text(registry, "Coal Tower", MapSource.OCR_INTRO, 2)[:1]:
        bus.publish(c)
    ids = [e for e in out if isinstance(e, MapIdentified)]
    assert ids and ids[-1].map_id == CT


def test_manual_override_wins_and_sticks(ident):
    bus, mi, out = ident
    bus.publish(UserMapOverride(ts_ns=1, map_id="ormond.ormond_lake_mine"))
    bus.publish(MapCandidate(ts_ns=2, map_id=CT, confidence=0.99, source=MapSource.OCR_MENU))
    bus.publish(MapCandidate(ts_ns=3, map_id=CT, confidence=0.99, source=MapSource.OCR_MENU))
    assert mi.current == "ormond.ormond_lake_mine"


def test_ambiguous_ocr_asks_the_player(ident):
    bus, mi, out = ident
    bus.publish(MapCandidate(ts_ns=1, map_id=CT, confidence=0.90, source=MapSource.OCR_INTRO))
    bus.publish(
        MapCandidate(ts_ns=2, map_id="macmillan_estate.suffocation_pit", confidence=0.88, source=MapSource.OCR_INTRO)
    )
    assert any(isinstance(e, MapAmbiguous) for e in out)


def test_lobby_resets_identification(ident):
    bus, mi, _ = ident
    bus.publish(MapCandidate(ts_ns=1, map_id=CT, confidence=1.0, source=MapSource.DEMO))
    assert mi.current == CT
    bus.publish(MatchStateChanged(ts_ns=2, state=MatchState.LOBBY))
    assert mi.current is None


# --------------------------------------------------------------------------- state machine
def test_state_machine_hysteresis_and_overlay():
    bus = EventBus(synchronous=True)
    sm = MatchStateMachine(ManualClock(), agree=3)
    sm.attach(bus)
    states: list[MatchStateChanged] = []
    focus: list[InputFocusChanged] = []
    bus.subscribe(MatchStateChanged, states.append)
    bus.subscribe(InputFocusChanged, focus.append)
    bus.publish(GameProcessChanged(ts_ns=0, running=True))
    assert states[-1].state == MatchState.MENU
    bus.publish(ScreenObserved(ts_ns=1, screen="in_match_survivor"))
    bus.publish(ScreenObserved(ts_ns=2, screen="loading"))  # noise breaks the streak
    for t in range(3, 6):
        bus.publish(ScreenObserved(ts_ns=t, screen="in_match_survivor"))
    assert states[-1].state == MatchState.IN_MATCH and states[-1].role == Role.SURVIVOR
    bus.publish(ScreenObserved(ts_ns=7, screen="in_match_survivor", menu_overlay=True))
    assert focus and focus[-1].camera_control is False
    bus.publish(GameProcessChanged(ts_ns=8, running=False))
    assert states[-1].state == MatchState.OFFLINE


# --------------------------------------------------------------------------- prompts
def test_prompt_interpreter_debounces():
    pi = PromptInterpreter(load_prompts("en"), start_frames=2, stop_frames=2)
    assert pi.feed(["[M1] REPAIR"], 1, progress_visible=False) == []  # offered, not happening
    assert pi.feed(["REPAIRING"], 2, progress_visible=True) == []
    started = pi.feed(["REPAIRING"], 3, progress_visible=True)
    assert started and started[0].kind == InteractionKind.GENERATOR and started[0].active
    assert pi.feed([], 4, progress_visible=False) == []
    stopped = pi.feed([], 5, progress_visible=False)
    assert stopped and not stopped[0].active
    assert pi.classify("OPEN") is None  # ambiguous words never become landmarks


# --------------------------------------------------------------------------- regions
def test_region_hud_scaling_shrinks_toward_anchor():
    r = Region("x", 0.01, 0.80, 0.10, 0.10, "bottom_left")
    s = r.scaled(0.5)
    assert s.x == pytest.approx(0.005) and s.y == pytest.approx(0.90) and s.w == pytest.approx(0.05)
    assert r.to_pixels(1920, 1080) == (19, 864, 192, 108)
    assert "gen_counter" in load_regions()


# --------------------------------------------------------------------------- optical flow
def _texture(h=144, w=256, seed=0):
    rng = np.random.default_rng(seed)
    import cv2

    base = (rng.random((h // 4, w // 4)) * 255).astype(np.uint8)
    img = cv2.resize(base, (w, h), interpolation=cv2.INTER_CUBIC)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def test_flow_static_vs_pan_vs_zoom():
    import cv2

    img = _texture()
    h, w = img.shape[:2]

    def run(frames):
        est = FlowMotionEstimator(width=256)
        out = None
        for i, f in enumerate(frames):
            out = est.update(f, i * 66_000_000) or out
        return out

    static = run([img, img, img])
    pan = run([img, np.roll(img, 6, axis=1), np.roll(img, 12, axis=1)])
    M = cv2.getRotationMatrix2D((w / 2, h / 2), 0, 1.06)
    zoom1 = cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REFLECT)
    zoom2 = cv2.warpAffine(zoom1, M, (w, h), borderMode=cv2.BORDER_REFLECT)
    zoom = run([img, zoom1, zoom2])
    assert static is not None and pan is not None and zoom is not None
    assert static.translating_prob < 0.5
    assert pan.translating_prob < 0.6  # pure rotation is not translation
    assert abs(pan.yaw_rate_dps or 0.0) > 10
    assert zoom.translating_prob > 0.7  # expansion = moving forward
