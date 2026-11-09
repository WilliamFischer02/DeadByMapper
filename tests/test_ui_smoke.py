"""QML must load with ZERO warnings against a live (demo) app. Runs offscreen."""

from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.gui


def test_qml_loads_without_warnings(monkeypatch):
    pytest.importorskip("PySide6.QtQuick")
    monkeypatch.setenv("QT_QPA_PLATFORM", os.environ.get("QT_QPA_PLATFORM", "offscreen"))
    monkeypatch.setenv("QT_QUICK_BACKEND", "software")
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine

    from deadbymapper.app import App
    from deadbymapper.config.paths import qml_dir
    from deadbymapper.config.settings import Settings
    from deadbymapper.ui.bridge import AppBridge
    from deadbymapper.ui.theme import theme

    qapp = QGuiApplication.instance() or QGuiApplication([])
    app = App("demo", Settings())
    app.start()
    try:
        time.sleep(1.0)
        bridge = AppBridge(app)
        engine = QQmlApplicationEngine()
        warnings: list[str] = []
        engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
        engine.rootContext().setContextProperty("bridge", bridge)
        engine.rootContext().setContextProperty("theme", theme())
        engine.load(QUrl.fromLocalFile(str(qml_dir() / "Main.qml")))
        assert engine.rootObjects(), warnings
        for _ in range(20):
            qapp.processEvents()
            bridge.refresh()
            time.sleep(0.05)
        assert warnings == []
        titles = {w.title() for w in qapp.topLevelWindows() if w.isVisible()}
        assert "DeadByMapper - Minimap" in titles and "DeadByMapper" in titles
        del engine
        del bridge
    finally:
        app.stop()
