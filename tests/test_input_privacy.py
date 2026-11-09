"""DeadByMapper must never behave like a keylogger: only needed keys, only while the game is foreground."""

from __future__ import annotations

import ctypes
import importlib.util
import logging
import re
from pathlib import Path

from deadbymapper.core.bus import EventBus
from deadbymapper.core.events import Event, GameWindowChanged, KeyEvent, UserMark
from deadbymapper.input import rawparse as rp
from deadbymapper.input.hotkeys import HotkeyMapper
from deadbymapper.input.keymap import VK, KeyMap
from deadbymapper.input.privacy import MOUSE_RECHECK_S, InputGate
from deadbymapper.input.raw_input import RawInputProcessor

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
    bus.publish(KeyEvent(ts_ns=4, vk=VK["F8"], down=True))
    assert [m.kind for m in marks] == ["generator", "exit_gate", "note"]


def test_hotkeys_f8_note_and_release_rearms():
    bus = EventBus(synchronous=True)
    marks: list[UserMark] = []
    bus.subscribe(UserMark, marks.append)
    HotkeyMapper(KeyMap.load()).attach(bus)
    for vk, down in ((VK["F8"], True), (VK["F8"], False), (VK["F8"], True), (VK["F9"], False)):
        bus.publish(KeyEvent(ts_ns=1, vk=vk, down=down))
    assert [m.kind for m in marks] == ["note", "note"]


# --------------------------------------------------------------------------- the real parse -> gate -> publish path
def _raw_processor(fg: dict[str, int | None]) -> tuple[RawInputProcessor, list[Event]]:
    bus = EventBus(synchronous=True)
    out: list[Event] = []
    bus.subscribe(None, out.append)
    g = InputGate(KeyMap.load().allowed_vks(), lambda: fg["hwnd"])
    g.on_game_window(GameWindowChanged(ts_ns=1, hwnd=GAME, client_rect=None, focused=True))
    return RawInputProcessor(bus, g), out


def _every_keyboard_message() -> list[bytes]:
    flag_sets = (0, rp.RI_KEY_BREAK, rp.RI_KEY_E0, rp.RI_KEY_E0 | rp.RI_KEY_BREAK, rp.RI_KEY_E1)
    makes = (0x00, 0x1D, 0x2A, 0x36)
    return [rp.pack_keyboard(vk, make, flags) for vk in range(256) for make in makes for flags in flag_sets]


def _run_all(proc: RawInputProcessor, msgs: list[bytes]) -> None:
    for m in msgs:  # one at a time (GetRawInputData path) ...
        arr = (ctypes.c_uint64 * (len(m) // 8))()
        ctypes.memmove(arr, m, len(m))
        proc.process_message(arr, len(m), 1)
    data = rp.pack_buffer(msgs)  # ... and all at once (GetRawInputBuffer path)
    arr = (ctypes.c_uint64 * (len(data) // 8))()
    ctypes.memmove(arr, data, len(data))
    proc.process_buffer(arr, len(data), len(msgs), 2)


def test_every_key_from_another_window_is_dropped_by_the_raw_path():
    for fg_hwnd in (OTHER, None):
        proc, out = _raw_processor({"hwnd": fg_hwnd})
        _run_all(proc, [*_every_keyboard_message(), rp.pack_mouse(5, 5), rp.pack_mouse(button_flags=0x0001)])
        assert out == []
        assert proc.published == 0


def test_only_allowed_keys_ever_leave_the_raw_path():
    allowed = KeyMap.load().allowed_vks()
    proc, out = _raw_processor({"hwnd": GAME})
    _run_all(proc, _every_keyboard_message())
    published = {e.vk for e in out if isinstance(e, KeyEvent)}
    assert published, "the game window is foreground: allowed keys must pass"
    assert published <= allowed
    assert published & {0x10, 0x11, 0x12} == set()  # generic modifiers are always resolved to left/right


def test_raw_path_never_logs(caplog):
    caplog.set_level(logging.DEBUG)
    for fg in (GAME, OTHER):
        proc, _out = _raw_processor({"hwnd": fg})
        _run_all(proc, _every_keyboard_message())
    assert caplog.records == []


def test_input_code_never_injects_or_hooks():
    """Observe only: no SendInput / keybd_event / mouse_event / SetWindowsHookEx / BlockInput calls in input/
    or in the probe (a docstring may name them; an attribute access or getattr may not)."""
    root = Path(__file__).resolve().parent.parent
    files = _all_app_sources(root)
    api = r"(SendInput|keybd_event|mouse_event|SetWindowsHookEx\w*|BlockInput)\b"
    forbidden = re.compile(rf"(\.{api}|getattr\([^)]*['\"]{api})")
    for f in files:
        assert not forbidden.search(f.read_text(encoding="utf-8")), f"{f.name} references an injection/hook API"


def _all_app_sources(root: Path) -> list[Path]:
    """Every shipped Python file: src/, tools/ and packaging/ (docs/FAIR_PLAY.md review checklist)."""
    return [
        *sorted((root / "src").rglob("*.py")),
        *sorted((root / "tools").glob("*.py")),
        *sorted((root / "packaging").glob("*.py")),
    ]


def test_input_package_opens_no_process():
    """Fair play: the input package never opens a handle to another process (not even
    PROCESS_QUERY_LIMITED_INFORMATION). Our own token via GetCurrentProcess() is the only token read."""
    root = Path(__file__).resolve().parent.parent
    files = _all_app_sources(root)
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert "OpenProcess(" not in text, f"{f.name} calls OpenProcess"
        assert "OpenProcess." not in text, f"{f.name} declares OpenProcess"
        for line in text.splitlines():
            if "OpenProcessToken(" in line:
                assert "GetCurrentProcess()" in line, f"{f.name}: OpenProcessToken only on our own process"


def test_probe_names_only_allowed_keys():
    path = Path(__file__).resolve().parent.parent / "tools" / "input_probe.py"
    spec = importlib.util.spec_from_file_location("input_probe", path)
    assert spec and spec.loader
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    allowed = KeyMap.load().allowed_vks()
    assert probe.seen_key_names({VK["W"], ord("P"), ord("1"), 0x0D, VK["F9"]}, allowed) == "F9,W"
    assert probe.seen_key_names({ord("P")}, allowed) == "-"
