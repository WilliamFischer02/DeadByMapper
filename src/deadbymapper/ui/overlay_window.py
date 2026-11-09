"""Win32 tweaks for the minimap pane so it can NEVER steal focus or input from the game.

Qt flags (FramelessWindowHint | WindowStaysOnTopHint | Tool | WindowDoesNotAcceptFocus, plus
WindowTransparentForInput when click-through) do most of the work. On Windows we additionally make
sure of WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW (no taskbar button, never activated by clicks) and, in
click-through mode, WS_EX_LAYERED | WS_EX_TRANSPARENT (mouse input falls through to the game).

The HWND only exists once the window is shown, and Qt recomputes GWL_EXSTYLE whenever QML changes
the window flags (the click-through toggle), so ``install_pane_styles`` re-applies the styles after
every show and every toggle. Verified on Windows 11 / Qt 6.12 (M1, read back from another process):
Qt alone already produces TOPMOST | TOOLWINDOW | LAYERED | NOACTIVATE (+ TRANSPARENT when
click-through) and WindowFromPoint falls through the pane, so this is a guard that only writes when
a future Qt or flag change drifts from the contract.
Overlays only draw above the game in Windowed Fullscreen; in exclusive fullscreen the pane belongs
on a second monitor (the dashboard explains this when display_mode == "fullscreen").
"""

from __future__ import annotations

import sys
from typing import Any

PANE_TITLE = "DeadByMapper - Minimap"

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20


def overlay_exstyle(current: int, *, click_through: bool) -> int:
    """Pure: the extended style the pane must have, given the one Qt set."""
    style = current | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    return (style | WS_EX_LAYERED | WS_EX_TRANSPARENT) if click_through else (style & ~WS_EX_TRANSPARENT)


def apply_overlay_styles(hwnd: int, *, click_through: bool) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes  # pragma: no cover
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    get = user32.GetWindowLongPtrW
    set_ = user32.SetWindowLongPtrW
    get.restype = ctypes.c_ssize_t
    get.argtypes = [wintypes.HWND, ctypes.c_int]
    set_.restype = ctypes.c_ssize_t
    set_.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    current = get(hwnd, GWL_EXSTYLE)
    wanted = overlay_exstyle(current, click_through=click_through)
    if wanted != current:
        set_(hwnd, GWL_EXSTYLE, wanted)
        # Style changes take effect on the next frame change; never move, resize, re-order or activate.
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND] + [ctypes.c_int] * 4 + [ctypes.c_uint]
        user32.SetWindowPos(
            hwnd, None, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
        )
    return True


def install_pane_styles(qapp: Any, bridge: Any) -> bool:
    """Keep the pane's Win32 styles right for the lifetime of the app (Qt thread only)."""
    if sys.platform != "win32" or qapp.platformName() != "windows":
        return False  # offscreen/minimal platforms have no real HWND
    from PySide6.QtCore import QTimer

    pane = next((w for w in qapp.topLevelWindows() if w.title() == PANE_TITLE), None)
    if pane is None:
        return False

    def apply() -> None:
        if pane.isVisible():  # visible => the native window (HWND) exists
            apply_overlay_styles(int(pane.winId()), click_through=bool(bridge.property("clickThrough")))

    # Queued: run after Qt has finished its own show / setFlags work, which rewrites GWL_EXSTYLE.
    pane.visibleChanged.connect(lambda _visible: QTimer.singleShot(0, apply))
    bridge.uiChanged.connect(lambda: QTimer.singleShot(0, apply))
    apply()
    return True
