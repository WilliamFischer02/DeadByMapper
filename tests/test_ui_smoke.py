"""QML must load with ZERO warnings against a live (demo) app. Runs offscreen.

Also covers the M1 dashboard controls end to end through the bridge: manual trial start/stop, the
map picker's data and intent, and the sensor chips (labels, order, human-readable reasons).
"""

from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.gui

SENSOR_LABELS = ["Process", "Window", "Log", "Input", "Capture", "Audio"]


@pytest.fixture
def qt(monkeypatch):
    pytest.importorskip("PySide6.QtQuick")
    monkeypatch.setenv("QT_QPA_PLATFORM", os.environ.get("QT_QPA_PLATFORM", "offscreen"))
    monkeypatch.setenv("QT_QUICK_BACKEND", "software")
    from PySide6.QtGui import QGuiApplication

    return QGuiApplication.instance() or QGuiApplication([])


def _load(bridge):
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    from deadbymapper.config.paths import qml_dir
    from deadbymapper.ui.theme import theme

    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.rootContext().setContextProperty("theme", theme())
    engine.load(QUrl.fromLocalFile(str(qml_dir() / "Main.qml")))
    assert engine.rootObjects(), warnings
    return engine, warnings


def _pump(qapp, bridge, n: int = 20) -> None:
    for _ in range(n):
        qapp.processEvents()
        bridge.refresh()
        time.sleep(0.05)


def _live_app(monkeypatch):
    import deadbymapper.app as app_mod
    from deadbymapper.config.settings import Settings

    monkeypatch.setattr(app_mod.App, "_build_live", lambda self: None)  # no Win32 sensors in CI
    return app_mod.App("live", Settings())


def test_qml_loads_without_warnings(qt):
    from deadbymapper.app import App
    from deadbymapper.config.settings import Settings
    from deadbymapper.ui.bridge import AppBridge

    qapp = qt
    app = App("demo", Settings())
    app.start()
    try:
        time.sleep(1.0)
        bridge = AppBridge(app)
        engine, warnings = _load(bridge)
        _pump(qapp, bridge)
        assert warnings == []
        titles = {w.title() for w in qapp.topLevelWindows() if w.isVisible()}
        assert "DeadByMapper - Minimap" in titles and "DeadByMapper" in titles
        # Manual trial control round-trips QML -> bridge -> bus -> state machine -> model -> bridge.
        bridge.stopTrial()
        _pump(qapp, bridge)
        assert bridge.hud["inTrial"] is False and bridge.mode == "idle"
        bridge.startTrial()
        _pump(qapp, bridge)
        assert bridge.hud["inTrial"] is True and bridge.hud["trialManual"] is True
        assert bridge.showDashboardInMatch is True
        assert warnings == []
        del engine
        del bridge
    finally:
        app.stop()


def test_pickable_maps_put_the_test_arena_first(qt):
    from deadbymapper.app import TEST_MAP_ID, App
    from deadbymapper.config.settings import Settings
    from deadbymapper.ui.bridge import TEST_GROUP, AppBridge

    app = App("demo", Settings())
    bridge = AppBridge(app)
    maps = bridge.pickableMaps
    assert maps[0]["id"] == TEST_MAP_ID and maps[0]["group"] == TEST_GROUP
    assert maps[0]["status"] == "synthetic"
    rest = maps[1:]
    assert len(rest) == len(app.registry.available()) and all(m["id"] != TEST_MAP_ID for m in rest)
    assert {"id", "name", "realm", "status", "group"} <= set(rest[0])
    realms = [m["realm"].removeprefix("The ").casefold() for m in rest]
    assert realms == sorted(realms)
    assert all(m["group"] == m["realm"] for m in rest)


def test_confirm_map_through_the_bridge_changes_the_current_map(qt, monkeypatch):
    from deadbymapper.ui.bridge import AppBridge

    qapp = qt
    app = _live_app(monkeypatch)
    app.start()
    try:
        bridge = AppBridge(app)
        assert bridge.map["id"] == ""
        stub = next(m for m in bridge.pickableMaps if m["status"] == "stub")
        bridge.confirmMap(stub["id"])
        _pump(qapp, bridge)
        assert bridge.map["id"] == stub["id"] and bridge.map["status"] == "stub"
        chip = next(c for c in bridge.chips if c["key"] == "map")
        assert chip["status"] == "degraded" and "no layout yet" in chip["detail"]
        bridge.confirmMap(bridge.pickableMaps[0]["id"])
        _pump(qapp, bridge)
        assert bridge.map["id"] == bridge.pickableMaps[0]["id"]
    finally:
        app.stop()


def test_live_chips_show_every_sensor_with_a_reason(qt, monkeypatch):
    from deadbymapper.core.events import ServiceHealth
    from deadbymapper.ui.bridge import AppBridge

    qapp = qt
    app = _live_app(monkeypatch)
    app.start()
    try:
        bridge = AppBridge(app)
        labels = [c["label"] for c in bridge.chips]
        assert labels == [*SENSOR_LABELS, "Map data"]  # no Demo chip, no healthy-internal Clock chip
        assert all(c["detail"] for c in bridge.chips), bridge.chips
        now = app.clock.now_ns()
        app.bus.publish(
            ServiceHealth(ts_ns=now, service="capture.wgc", status="down", detail="windows-capture not installed")
        )
        app.bus.publish(ServiceHealth(ts_ns=now, service="gamelink.process", status="ok"))
        app.bus.publish(ServiceHealth(ts_ns=now, service="ticker", status="degraded", detail="RuntimeError: boom"))
        _pump(qapp, bridge, 10)
        chips = {c["label"]: c for c in bridge.chips}
        assert chips["Capture"]["status"] == "down" and chips["Capture"]["detail"] == "windows-capture not installed"
        assert chips["Process"]["detail"] == "Game not running"  # "ok" without a detail gets a readable one
        assert chips["Clock"]["status"] == "degraded"  # internal services surface only when failing
        assert [c["label"] for c in bridge.chips][-1] == "Map data"
    finally:
        app.stop()


def test_demo_chips_include_demo_and_quiet_sensors(qt):
    from deadbymapper.app import App
    from deadbymapper.config.settings import Settings
    from deadbymapper.ui.bridge import AppBridge

    app = App("demo", Settings())
    bridge = AppBridge(app)
    chips = bridge.chips
    assert [c["label"] for c in chips] == [*SENSOR_LABELS, "Demo", "Map data"]
    assert all(c["status"] == "off" for c in chips[: len(SENSOR_LABELS)])


def test_live_dashboard_and_map_picker_popup_have_no_qml_warnings(qt, monkeypatch):
    from PySide6.QtCore import QObject

    from deadbymapper.ui.bridge import AppBridge

    qapp = qt
    app = _live_app(monkeypatch)
    app.start()
    try:
        bridge = AppBridge(app)
        engine, warnings = _load(bridge)
        _pump(qapp, bridge, 5)
        dash = next(w for w in qapp.topLevelWindows() if w.title() == "DeadByMapper")
        popup = dash.findChild(QObject, "mapPickerPopup")
        assert popup is not None
        popup.setProperty("visible", True)
        _pump(qapp, bridge, 5)
        query = dash.findChild(QObject, "mapPickerQuery")
        query.setProperty("text", "farm")
        _pump(qapp, bridge, 5)
        query.setProperty("text", "no such map")
        _pump(qapp, bridge, 5)
        popup.setProperty("visible", False)
        bridge.startTrial()
        _pump(qapp, bridge, 10)
        assert bridge.hud["inTrial"] is True
        assert warnings == []
        del engine
        del bridge
    finally:
        app.stop()
