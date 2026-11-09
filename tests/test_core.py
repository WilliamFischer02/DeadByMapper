from __future__ import annotations

import math
import threading
import time

import numpy as np
import pytest

from deadbymapper.core import events as ev
from deadbymapper.core.bus import EventBus
from deadbymapper.core.clock import ManualClock, ns_to_s, s_to_ns
from deadbymapper.core.geometry import (
    angle_diff_deg,
    circular_mean_deg,
    heading_of,
    heading_unit,
    intent_angle_deg,
    points_in_polygon,
    polygon_area,
    wrap_deg_signed,
)


# --------------------------------------------------------------------------- clock
def test_manual_clock_monotonic():
    c = ManualClock(5)
    c.advance_s(0.5)
    assert c.now_ns() == 5 + 500_000_000
    with pytest.raises(ValueError):
        c.set_ns(0)
    assert ns_to_s(s_to_ns(1.25)) == 1.25


# --------------------------------------------------------------------------- events
def _sample_events() -> list[ev.Event]:
    return [
        ev.Tick(ts_ns=1),
        ev.KeyEvent(ts_ns=2, vk=0x57, down=True),
        ev.MouseDelta(ts_ns=3, dx=-12, dy=4),
        ev.MatchStateChanged(ts_ns=4, state=ev.MatchState.IN_MATCH, role=ev.Role.SURVIVOR),
        ev.MapIdentified(ts_ns=5, map_id="macmillan_estate.coal_tower", confidence=0.9, source=ev.MapSource.OCR_INTRO),
        ev.InteractionObserved(ts_ns=6, kind=ev.InteractionKind.GENERATOR, active=True),
        ev.TerrorRadiusObserved(ts_ns=7, tier=2, bpm=95.0),
        ev.UserFix(ts_ns=8, pos_m=(10.5, 20.0)),
        ev.MapAmbiguous(ts_ns=9, candidates=("a.b", "c.d")),
        ev.PoseEstimate(
            ts_ns=10,
            status=ev.PoseStatus.LOCKED,
            x=1.0,
            y=2.0,
            heading_deg=90.0,
            sigma_m=1.5,
            hypotheses=((1.0, 2.0, 0.9), (30.0, 40.0, 0.1)),
            psi_deg=12.0,
            rel_xy=(3.0, 4.0),
        ),
        ev.PoseEstimate(ts_ns=11, status=ev.PoseStatus.NO_FIX),  # sigma inf, heading None
        ev.LogEvent(ts_ns=12, kind="map_hint", data={"token": "Ind_CoalTower"}),
        ev.UserMark(ts_ns=13, kind="generator"),
        ev.UserTrialControl(ts_ns=14, action="start", role=ev.Role.SURVIVOR),
        ev.MatchStateChanged(ts_ns=15, state=ev.MatchState.IN_MATCH, role=ev.Role.SURVIVOR, manual=True),
    ]


def test_event_record_roundtrip():
    for e in _sample_events():
        rec = ev.to_record(e)
        back = ev.from_record(rec)
        assert back == e, (e, back)
        assert type(back) is type(e)


def test_every_event_type_is_registered_and_frozen():
    types = ev.event_types()
    assert {"Tick", "PoseEstimate", "KeyEvent", "ScreenObserved"} <= set(types)
    e = ev.Tick(ts_ns=1)
    with pytest.raises(AttributeError):
        e.ts_ns = 2  # type: ignore[misc]


# --------------------------------------------------------------------------- bus
def test_bus_dispatch_by_type_mro_and_wildcard():
    bus = EventBus(synchronous=True)
    got: dict[str, list] = {"tick": [], "any": [], "base": []}
    bus.subscribe(ev.Tick, got["tick"].append)
    bus.subscribe(ev.Event, got["base"].append)
    bus.subscribe(None, got["any"].append)
    bus.publish(ev.Tick(ts_ns=1))
    bus.publish(ev.KeyEvent(ts_ns=2, vk=1, down=True))
    assert len(got["tick"]) == 1 and len(got["base"]) == 2 and len(got["any"]) == 2


def test_bus_handler_errors_are_isolated():
    bus = EventBus(synchronous=True)
    seen = []

    def boom(_e):
        raise RuntimeError("x")

    bus.subscribe(ev.Tick, boom)
    bus.subscribe(ev.Tick, seen.append)
    bus.publish(ev.Tick(ts_ns=1))
    assert seen and bus.error_count == 1


def test_bus_unsubscribe_and_threaded_order():
    bus = EventBus()
    seen: list[int] = []
    done = threading.Event()

    def h(e):
        seen.append(e.ts_ns)
        if e.ts_ns == 99:
            done.set()

    unsub = bus.subscribe(ev.Tick, h)
    bus.start()
    for i in range(100):
        bus.publish(ev.Tick(ts_ns=i))
    assert done.wait(2.0)
    bus.stop()
    assert seen == list(range(100))
    unsub()
    sync = EventBus(synchronous=True)
    u = sync.subscribe(ev.Tick, seen.append)
    u()
    sync.publish(ev.Tick(ts_ns=1000))
    assert 1000 not in seen
    time.sleep(0)


# --------------------------------------------------------------------------- geometry
def test_heading_conventions():
    assert heading_unit(0) == pytest.approx((0.0, -1.0))  # north = up = -y
    assert heading_unit(90) == pytest.approx((1.0, 0.0))  # east = +x
    assert heading_of(0, -5) == pytest.approx(0.0)
    assert heading_of(5, 0) == pytest.approx(90.0)
    assert heading_of(0, 5) == pytest.approx(180.0)
    assert angle_diff_deg(10, 350) == pytest.approx(20.0)
    assert wrap_deg_signed(190) == pytest.approx(-170.0)


@pytest.mark.parametrize(
    ("keys", "angle"),
    [
        ((True, False, False, False), 0.0),
        ((False, False, False, True), 90.0),
        ((False, True, False, False), 180.0),
        ((False, False, True, False), 270.0),
        ((True, False, False, True), 45.0),
        ((True, True, False, False), None),
    ],
)
def test_intent_angle(keys, angle):
    got = intent_angle_deg(*keys)
    assert (got is None and angle is None) or got == pytest.approx(angle)


def test_circular_mean_wraps():
    mean, std = circular_mean_deg(np.array([350.0, 10.0]))
    assert mean == pytest.approx(0.0, abs=1e-6) or mean == pytest.approx(360.0, abs=1e-6)
    assert std < 15


def test_points_in_polygon_and_area():
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    xs = np.array([5.0, 11.0, 0.5])
    ys = np.array([5.0, 5.0, 9.5])
    assert points_in_polygon(xs, ys, sq).tolist() == [True, False, True]
    assert polygon_area(sq) == pytest.approx(100.0)
    assert math.isclose(polygon_area([(0, 0), (4, 0), (0, 3)]), 6.0)
