"""Pane window styles: never activated, no taskbar button, mouse falls through in click-through mode."""

from __future__ import annotations

from deadbymapper.ui.overlay_window import (
    WS_EX_LAYERED,
    WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW,
    WS_EX_TRANSPARENT,
    overlay_exstyle,
)

WS_EX_TOPMOST = 0x00000008


def test_click_through_adds_layered_transparent_and_keeps_qt_bits():
    ex = overlay_exstyle(WS_EX_TOPMOST, click_through=True)
    assert ex & WS_EX_TOPMOST
    for bit in (WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_LAYERED, WS_EX_TRANSPARENT):
        assert ex & bit


def test_interactive_mode_drops_only_transparent():
    qt_set = WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    ex = overlay_exstyle(qt_set, click_through=False)
    assert not ex & WS_EX_TRANSPARENT
    assert ex & WS_EX_NOACTIVATE and ex & WS_EX_TOOLWINDOW and ex & WS_EX_LAYERED


def test_already_correct_styles_are_left_alone():
    qt_set = WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    assert overlay_exstyle(qt_set, click_through=True) == qt_set
