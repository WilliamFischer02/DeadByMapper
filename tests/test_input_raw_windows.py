"""Raw Input on real Windows: struct layouts, registration, message loop, teardown and the gate on hardware.

These never synthesize input (that is forbidden). They run while the user may be typing or moving the
mouse in other apps - which is exactly what the gate assertions are about.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest

from deadbymapper.core.bus import EventBus
from deadbymapper.core.clock import MonotonicClock
from deadbymapper.core.events import Event, GameWindowChanged, KeyEvent, MouseButton, MouseDelta, ServiceHealth
from deadbymapper.input import _win32 as w
from deadbymapper.input import rawparse as rp
from deadbymapper.input.privacy import InputGate
from deadbymapper.input.raw_input import RawInputListener

pytestmark = pytest.mark.windows_only

INPUT_EVENTS = (KeyEvent, MouseDelta, MouseButton)


# --------------------------------------------------------------------------- struct layouts (x64)
def test_struct_sizes_x64():
    import ctypes

    assert ctypes.sizeof(ctypes.c_void_p) == 8, "64-bit Python required"
    assert ctypes.sizeof(w.RAWINPUTHEADER) == 24
    assert ctypes.sizeof(w.RAWMOUSE) == 24
    assert ctypes.sizeof(w.RAWKEYBOARD) == 16
    assert ctypes.sizeof(w.RAWHID) == 12  # 8-byte header + 1-byte placeholder, padded to 4
    assert ctypes.sizeof(w.RAWINPUT) == 48
    assert ctypes.sizeof(w.RAWINPUT_MOUSE) == 48
    assert ctypes.sizeof(w.RAWINPUT_KEYBOARD) == 40
    assert ctypes.sizeof(w.RAWINPUTDEVICE) == 16


def test_struct_offsets_x64():
    lay = w.layout()
    assert lay["RAWINPUTHEADER.hDevice@"] == 8
    assert lay["RAWINPUTHEADER.wParam@"] == 16
    assert lay["RAWMOUSE.usButtonFlags@"] == 4  # union is 4-aligned: 2 bytes padding after usFlags
    assert lay["RAWMOUSE.usButtonData@"] == 6
    assert lay["RAWMOUSE.ulRawButtons@"] == 8
    assert lay["RAWMOUSE.lLastX@"] == 12
    assert lay["RAWMOUSE.lLastY@"] == 16
    assert lay["RAWMOUSE.ulExtraInformation@"] == 20
    assert lay["RAWKEYBOARD.VKey@"] == 6
    assert lay["RAWKEYBOARD.Message@"] == 8
    assert lay["RAWHID.bRawData@"] == 8
    assert lay["RAWINPUT.data@"] == 24
    assert w.RAWMOUSE.ulButtons.offset == 4  # the ULONG arm of the union overlays both USHORTs


def test_ctypes_and_struct_parsers_agree():
    import ctypes

    m = w.RAWINPUT_MOUSE.from_buffer_copy(rp.pack_mouse(-5, 7, button_flags=0x0005, button_data=-120))
    assert (m.header.dwType, m.header.dwSize) == (rp.RIM_TYPEMOUSE, 48)
    assert (m.mouse.lLastX, m.mouse.lLastY, m.mouse.usButtonFlags) == (-5, 7, 0x0005)
    assert m.mouse.usButtonData == 0xFF88  # -120 (one wheel notch back) as the USHORT Windows declares
    assert m.mouse.ulButtons == 0x0005 | (0xFF88 << 16)
    k = w.RAWINPUT_KEYBOARD.from_buffer_copy(rp.pack_keyboard(0x10, 0x36, rp.RI_KEY_BREAK))
    assert (k.header.dwType, k.header.dwSize) == (rp.RIM_TYPEKEYBOARD, 40)
    assert (k.keyboard.VKey, k.keyboard.MakeCode, k.keyboard.Flags, k.keyboard.Message) == (0x10, 0x36, 1, 0x0101)
    full = w.RAWINPUT.from_buffer_copy(rp.pack_mouse(1, 2))
    assert ctypes.addressof(full.data) - ctypes.addressof(full) == 24


# --------------------------------------------------------------------------- listener on this machine
def _start(
    fg=None,  # type: ignore[no-untyped-def]
    allowed: frozenset[int] | None = None,
    batch: bool = True,
) -> tuple[RawInputListener, list[Event], list[ServiceHealth]]:
    bus = EventBus(synchronous=True)  # handlers run inline on the listener thread
    events: list[Event] = []
    health: list[ServiceHealth] = []
    bus.subscribe(None, lambda e: events.append(e) if isinstance(e, INPUT_EVENTS) else None)
    bus.subscribe(ServiceHealth, health.append)
    gate = None
    if allowed is not None or fg is not None:
        from deadbymapper.input.keymap import KeyMap

        gate = InputGate(KeyMap.load().allowed_vks() if allowed is None else allowed, fg or w.foreground_hwnd)
    lst = RawInputListener(bus, MonotonicClock(), gate=gate, batch=batch)
    lst.start()
    return lst, events, health


def _stop(lst: RawInputListener) -> float:
    t0 = time.perf_counter()
    lst.stop()
    return time.perf_counter() - t0


@pytest.mark.parametrize("batch", [True, False])
def test_registration_health_and_clean_stop(batch):
    lst, _events, health = _start(batch=batch)
    try:
        assert lst.registered, f"RegisterRawInputDevices failed: {lst.register_error}"
        assert lst.alive
        regs = {(p, u): (f, h) for p, u, f, h in w.registered_devices()}
        for usage in (w.HID_USAGE_MOUSE, w.HID_USAGE_KEYBOARD):
            flags, hwnd = regs[(w.HID_USAGE_PAGE_GENERIC, usage)]
            assert flags & w.RIDEV_INPUTSINK
            assert hwnd == lst._hwnd
        assert health[-1].status == "ok"
        mode = "batched" if batch else "per-message"
        assert health[-1].detail == f"listening (mouse+keyboard, INPUTSINK, {mode}) - gated: DBD not foreground"
        time.sleep(0.3)
    finally:
        took = _stop(lst)
    assert took < 1.0
    assert not lst.alive
    assert w.registered_devices() == []  # RIDEV_REMOVE on teardown
    assert health[-1].status == "down"


def test_restart_in_same_process():
    """Regression: the scaffold re-registered one window class name, so a second start reused a class whose
    WNDPROC belonged to the first (collected) listener."""
    for _ in range(3):
        lst, _e, health = _start()
        assert lst.registered and health[-1].status == "ok"
        assert _stop(lst) < 1.0


def test_get_raw_input_data_error_paths_do_not_crash():
    lst, _e, _h = _start()
    try:
        before = lst.read_errors
        assert lst.read_raw_input_data(0x1234) is None  # stale / invalid HRAWINPUT (e.g. device removed)
        assert lst.read_raw_input_data(0x1234, header_size=16) is None  # wrong cbSizeHeader
        assert lst.read_errors == before + 2
        assert lst.alive
    finally:
        _stop(lst)


def test_nothing_published_when_foreground_is_not_the_game():
    """2 s on real hardware with the gate's foreground predicate pinned to a non-game window: even if the
    user types or moves the mouse right now, nothing may reach the bus."""
    game, other = 0x1234, 0x9999
    lst, events, _h = _start(fg=lambda: other)
    lst.bus.publish(GameWindowChanged(ts_ns=1, hwnd=game, client_rect=None, focused=True))
    try:
        time.sleep(2.0)
    finally:
        _stop(lst)
    assert events == []
    assert lst.processor.published == 0
    assert lst.gate.dropped >= lst.processor.mouse_msgs + lst.processor.key_msgs - lst.processor.ignored_keys


def test_no_game_window_means_nothing_published():
    lst, events, _h = _start(fg=w.foreground_hwnd)  # real foreground, but no GameWindowChanged at all
    try:
        time.sleep(1.0)
    finally:
        _stop(lst)
    assert events == [] and lst.processor.published == 0


def test_real_foreground_only_leaks_allowed_modifiers():
    """Gate open for whatever window is foreground right now, with only Shift/Ctrl allowed. Zero events is
    fine; any KeyEvent that does get through must be one of the four allowed modifier codes."""
    allowed = frozenset({rp.VK_LSHIFT, rp.VK_RSHIFT, rp.VK_LCONTROL, rp.VK_RCONTROL})
    lst, events, _h = _start(fg=w.foreground_hwnd, allowed=allowed)
    fg = w.foreground_hwnd()
    lst.bus.publish(GameWindowChanged(ts_ns=1, hwnd=fg, client_rect=None, focused=True))
    try:
        time.sleep(2.0)
    finally:
        _stop(lst)
    keys = [e for e in events if isinstance(e, KeyEvent)]
    assert all(e.vk in allowed for e in keys)


def test_input_is_processed_on_the_listener_thread_only():
    lst, _e, _h = _start()
    try:
        time.sleep(1.0)
        assert lst.thread_id is not None and lst.thread_id != threading.get_native_id()
    finally:
        _stop(lst)
    assert lst.input_thread_ids <= {lst.thread_id}  # empty if nobody touched a device - that is fine


def test_batched_loop_does_not_spin():
    """MsgWaitForMultipleObjectsEx must only wake for input/posted messages: an idle or lightly used
    listener may not busy-loop after GetRawInputBuffer drained the queue."""
    lst, _e, _h = _start(batch=True)
    try:
        time.sleep(2.0)
    finally:
        _stop(lst)
    assert lst.wakes <= lst.processor.messages + 10


def test_mouse_parsing_on_hardware_is_sane():
    """Gate open for the mouse (foreground pinned to the 'game'), NO keys allowed: real mouse data, if the
    user happens to move it, parses to plausible values; no key can ever be published."""
    game = 0x1234
    lst, events, _h = _start(fg=lambda: game, allowed=frozenset())
    lst.bus.publish(GameWindowChanged(ts_ns=1, hwnd=game, client_rect=None, focused=True))
    try:
        time.sleep(2.0)
    finally:
        _stop(lst)
    assert not any(isinstance(e, KeyEvent) for e in events)
    for e in events:
        if isinstance(e, MouseDelta):
            assert abs(e.dx) < 1_000_000 and abs(e.dy) < 1_000_000
        elif isinstance(e, MouseButton):
            assert e.button in {"left", "right", "middle", "x1", "x2"}
    assert lst.processor.malformed == 0 and lst.read_errors == 0 and lst.buffer_errors == 0


def test_console_root_window_is_a_window_or_none():
    hwnd = w.console_root_window()
    assert hwnd is None or hwnd > 0


def test_platform_guard():
    assert sys.platform == "win32"
