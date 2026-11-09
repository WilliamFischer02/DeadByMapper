"""Composition-root behaviour that spans several modules: manual trial control end to end."""

from __future__ import annotations

import time

from deadbymapper.app import App
from deadbymapper.config.settings import Settings
from deadbymapper.core.events import MatchState, Role, UserTrialControl


def _wait_for(pred, timeout_s: float = 3.0) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_manual_trial_control_reaches_the_model_in_demo_mode():
    """Demo publishes IN_MATCH itself; the state machine adopts it, so Stop then Start both work."""
    app = App("demo", Settings())
    app.start()
    try:
        assert _wait_for(lambda: app.snapshot().match.match_state == MatchState.IN_MATCH)
        assert app.snapshot().match.manual_trial is False
        app.bus.publish(UserTrialControl(ts_ns=app.clock.now_ns(), action="stop"))
        assert _wait_for(lambda: app.snapshot().match.match_state != MatchState.IN_MATCH)
        app.bus.publish(UserTrialControl(ts_ns=app.clock.now_ns(), action="start", role=Role.SURVIVOR))
        assert _wait_for(lambda: app.snapshot().match.manual_trial)
        snap = app.snapshot().match
        assert snap.match_state == MatchState.IN_MATCH and snap.role == Role.SURVIVOR
    finally:
        app.stop()


def test_manual_trial_in_live_mode_without_the_game(monkeypatch):
    """Live mode on a box without DBD: Start trial must still show the pane (M1 acceptance)."""
    import deadbymapper.app as app_mod

    monkeypatch.setattr(app_mod.App, "_build_live", lambda self: None)  # no Win32 sensors in CI
    app = App("live", Settings())
    app.start()
    try:
        assert app.snapshot().match.match_state == MatchState.OFFLINE
        app.bus.publish(UserTrialControl(ts_ns=app.clock.now_ns(), action="start"))
        assert _wait_for(lambda: app.snapshot().match.manual_trial)
        assert app.snapshot().match.match_state == MatchState.IN_MATCH
        app.bus.publish(UserTrialControl(ts_ns=app.clock.now_ns(), action="stop"))
        assert _wait_for(lambda: app.snapshot().match.match_state == MatchState.OFFLINE)
        assert app.snapshot().match.manual_trial is False
    finally:
        app.stop()
