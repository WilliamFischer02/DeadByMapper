"""Qt bootstrap: crisp text + true high-DPI, load Main.qml, optional offscreen screenshots for QA."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from ..app import App
from ..config.paths import qml_dir

log = logging.getLogger(__name__)


def run_ui(app: App, *, screenshot_dir: Path | None = None, screenshot_delay_ms: int = 7000) -> int:
    if screenshot_dir is not None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        # Headless: software scene graph. With QT_QPA_PLATFORM=windows the real GPU backend renders,
        # so a screenshot is pixel-identical to what the user sees (use that for crispness QA).
        os.environ.setdefault("QT_QUICK_BACKEND", "software")
        if sys.platform == "win32":
            # The offscreen plugin uses a FreeType font database that only sees QT_QPA_FONTDIR;
            # without it every glyph renders as a box and screenshots cannot be reviewed.
            fonts = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "Fonts")
            os.environ.setdefault("QT_QPA_FONTDIR", fonts)

    from PySide6.QtCore import Qt, QTimer, QUrl
    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow

    from .bridge import AppBridge
    from .theme import theme

    # Crispness rules (docs/UI_DESIGN.md#crispness): no fractional-DPI rounding blur, native text.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QQuickWindow.setTextRenderType(QQuickWindow.TextRenderType.NativeTextRendering)

    qapp = QGuiApplication.instance() or QGuiApplication(sys.argv)
    qapp.setApplicationName("DeadByMapper")
    qapp.setOrganizationName("Goob Entertainment Co.")
    icon = qml_dir() / "icons" / "app_icon.svg"
    if icon.exists():
        qapp.setWindowIcon(QIcon(str(icon)))

    bridge = AppBridge(app)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.rootContext().setContextProperty("theme", theme(app.settings.ui.palette))
    engine.warnings.connect(lambda ws: [log.warning("QML: %s", w.toString()) for w in ws])
    engine.load(QUrl.fromLocalFile(str(qml_dir() / "Main.qml")))
    roots = engine.rootObjects()
    if not roots:
        log.error("Main.qml failed to load")
        return 1
    # The pane must never take focus and must let clicks through to the game (WS_EX_* styles).
    from .overlay_window import install_pane_styles

    install_pane_styles(qapp, bridge)

    if screenshot_dir is not None:
        names = {"DeadByMapper": "dashboard", "DeadByMapper - Minimap": "pane"}

        def grab() -> None:
            try:
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                for win in qapp.topLevelWindows():
                    name = names.get(win.title())
                    if name and isinstance(win, QQuickWindow) and win.isVisible():
                        img = win.grabWindow()
                        path = screenshot_dir / f"{name}.png"
                        img.save(str(path))
                        log.info("saved %s (%dx%d @%.2fx)", path, img.width(), img.height(), img.devicePixelRatio())
            finally:
                qapp.quit()

        QTimer.singleShot(screenshot_delay_ms, grab)
    ret = int(qapp.exec())
    # Tear down QML before the bridge it binds to (avoids 'Cannot read property of null' at exit).
    del engine
    del bridge
    return ret
