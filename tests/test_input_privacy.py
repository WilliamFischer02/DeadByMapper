"""DeadByMapper must never behave like a keylogger: only needed keys, only while the game is foreground."""

from __future__ import annotations

from deadbymapper.core.bus import EventBus
from deadbymapper.core.events import GameWindowChanged, KeyEvent, UserMark
from deadbymapper.input.hotkeys import HotkeyMapper
from deadbymapper.input.keymap import VK, KeyMap
from deadbymapper.input.privacy import MOUSE_RECHECK_S, InputGate

GAME = 0x1234
OTHER = 0x9999


def _gate(foreground: dict[str, int | None], now: dict[str, float]) -> InputGate:
    km = KeyMap.load()
    g = InputGate(km.allowed_vks(), lambda: foreground["hwnd"], now_s=lambda: now["t"])
    g.on_game_window(GameWindowChanged(ts_ns=1, hwnd=GAME, client_rect=(0, 0, 1920, 1080), focused=True))
    return g


def test_allowed_keys_cover_movement_modifiers_and_hotkeys_only():
    allowed = KeyMap.load().allowed_vks()
    for name in ("W", "A", "S", "D", "LSHIFT", "RSHIFT", "LCONTROL", "SPACE", "E", "ESCAPE", "TAB", "F8", "F9", "F10"):
        assert VK[name] in allowed, name
    for vk in (ord("P"), ord("Q"), ord("1"), 0x0D, VK["LBUTTON"]):  # text keys, Enter, mouse buttons
        assert vk not in allowed


def test_keys_outside_the_game_or_not_needed_are_dropped():
    fg: dict[str, int | None] = {"hwnd": GAME}
    now = {"t": 0.0}
    g = _gate(fg, now)
    assert g.allow_key(VK["W"])
    assert not g.allow_key(ord("P"))  # typing letters the app does not need
    fg["hwnd"] = OTHER  # alt-tabbed to a browser / password field
    assert not g.allow_key(VK["W"])
    fg["hwnd"] = None
    assert not g.allow_key(VK["W"])
    assert g.dropped == 3


def test_no_game_window_means_nothing_passes():
    km = KeyMap.load()
    g = InputGate(km.allowed_vks(), lambda: GAME)
    assert not g.allow_key(VK["W"]) and not g.allow_mouse()


def test_mouse_gate_rechecks_foreground_at_most_every_interval():
    fg: dict[str, int | None] = {"hwnd": GAME}
    now = {"t": 0.0}
    g = _gate(fg, now)
    assert g.allow_mouse()
    fg["hwnd"] = OTHER
    now["t"] += MOUSE_RECHECK_S / 2
    assert g.allow_mouse()  # cached
    now["t"] += MOUSE_RECHECK_S
    assert not g.allow_mouse()
    g.on_game_window(GameWindowChanged(ts_ns=2, hwnd=OTHER, client_rect=None, focused=True))
    assert g.allow_mouse()  # window change invalidates the cache


def test_hotkeys_publish_one_mark_per_press():
    bus = EventBus(synchronous=True)
    marks: list[UserMark] = []
    bus.subscribe(UserMark, marks.append)
    HotkeyMapper(KeyMap.load()).attach(bus)
    for down in (True, True, True, False):  # auto-repeat while held, then release
        bus.publish(KeyEvent(ts_ns=1, vk=VK["F9"], down=down))
    bus.publish(KeyEvent(ts_ns=2, vk=VK["F10"], down=True))
    bus.publish(KeyEvent(ts_ns=3, vk=VK["W"], down=True))
    assert [m.kind for m in marks] == ["generator", "exit_gate"]
