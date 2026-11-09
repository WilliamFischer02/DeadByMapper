"""Win32 tweaks for the minimap pane so it can NEVER steal focus or input from the game.

Qt flags (FramelessWindowHint | WindowStaysOnTopHint | Tool | WindowDoesNotAcceptFocus, plus
WindowTransparentForInput when click-through) do most of the work. On Windows we additionally set
WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW (no taskbar button, never activated by clicks) and, in
click-through mode, WS_EX_LAYERED | WS_EX_TRANSPARENT. STATUS: verify on Windows (M7).
Overlays only draw above the game in Windowed Fullscreen; in exclusive fullscreen the pane belongs
on a second monitor (the dashboard explains this when display_mode == "fullscreen").
"""

from __future__ import annotations

import sys

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000


def apply_overlay_styles(hwnd: int, *, click_through: bool) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes  # pragma: no cover

    user32 = ctypes.windll.user32
    get = user32.GetWindowLongPtrW
    set_ = user32.SetWindowLongPtrW
    get.restype = ctypes.c_ssize_t
    style = get(hwnd, GWL_EXSTYLE) | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    style = (style | WS_EX_LAYERED | WS_EX_TRANSPARENT) if click_through else (style & ~WS_EX_TRANSPARENT)
    set_(hwnd, GWL_EXSTYLE, ctypes.c_ssize_t(style))
    return True
