"""Raw Input parsing, L/R modifier resolution, batching and the parse -> gate -> publish path (no hardware)."""

from __future__ import annotations

import ctypes

import pytest

from deadbymapper.core.bus import EventBus
from deadbymapper.core.events import (
    Event,
    GameProcessChanged,
    GameWindowChanged,
    KeyEvent,
    MouseButton,
    MouseDelta,
    ServiceHealth,
    UserMark,
)
from deadbymapper.input import _win32
from deadbymapper.input import rawparse as rp
from deadbymapper.input.hotkeys import HotkeyMapper, hotkey_collisions
from deadbymapper.input.keymap import VK, Action, KeyMap
from deadbymapper.input.privacy import InputGate
from deadbymapper.input.raw_input import UIPI_DETAIL, RawInputListener, RawInputProcessor

GAME = 0x1234
OTHER = 0x9999


def _buf(data: bytes) -> ctypes.Array[ctypes.c_uint64]:
    arr = (ctypes.c_uint64 * ((len(data) + 7) // 8))()
    ctypes.memmove(arr, data, len(data))
    return arr


def _setup(fg: dict[str, int | None], allowed: frozenset[int] | None = None) -> tuple[RawInputProcessor, list[Event]]:
    bus = EventBus(synchronous=True)
    out: list[Event] = []
    bus.subscribe(None, out.append)
    gate = InputGate(allowed if allowed is not None else KeyMap.load().allowed_vks(), lambda: fg["hwnd"])
    gate.on_game_window(GameWindowChanged(ts_ns=0, hwnd=GAME, client_rect=None, focused=True))
    return RawInputProcessor(bus, gate), out


def _feed(proc: RawInputProcessor, msg: bytes, ts: int = 1) -> None:
    proc.process_message(_buf(msg), len(msg), ts)


# --------------------------------------------------------------------------- layouts as struct formats
def test_struct_formats_match_x64_layout():
    assert rp.IS_64BIT
    assert rp.HEADER_SIZE == 24
    assert rp.MOUSE.size == 24
    assert rp.KEYBOARD.size == 16
    assert rp.HID_HEAD.size == 8
    assert rp.MOUSE_MSG_SIZE == 48
    assert rp.KEYBOARD_MSG_SIZE == 40
    assert len(rp.pack_mouse(1, 2)) == 48
    assert len(rp.pack_keyboard(VK["W"], 0x11)) == 40


def test_mouse_fields_land_at_documented_offsets():
    raw = rp.pack_mouse(-7, 9, us_flags=0, button_flags=0x0001, button_data=-120)
    data = raw[24:]
    assert int.from_bytes(data[0:2], "little") == 0  # usFlags
    assert int.from_bytes(data[4:6], "little") == 0x0001  # usButtonFlags @4 (after 2 bytes of padding)
    assert int.from_bytes(data[6:8], "little", signed=True) == -120  # usButtonData @6
    assert int.from_bytes(data[12:16], "little", signed=True) == -7  # lLastX @12
    assert int.from_bytes(data[16:20], "little", signed=True) == 9  # lLastY @16


# --------------------------------------------------------------------------- L/R modifier resolver
@pytest.mark.parametrize(
    ("vk", "make", "flags", "expected"),
    [
        (rp.VK_SHIFT, 0x2A, 0, rp.VK_LSHIFT),
        (rp.VK_SHIFT, 0x36, 0, rp.VK_RSHIFT),
        (rp.VK_SHIFT, 0x2A, rp.RI_KEY_BREAK, rp.VK_LSHIFT),
        (rp.VK_SHIFT, 0x36, rp.RI_KEY_BREAK, rp.VK_RSHIFT),
        (rp.VK_SHIFT, 0x2A, rp.RI_KEY_E0, rp.VK_LSHIFT),  # scan code decides for shift
        (rp.VK_SHIFT, 0x11, 0, None),  # not a shift scan code -> drop
        (rp.VK_CONTROL, 0x1D, 0, rp.VK_LCONTROL),
        (rp.VK_CONTROL, 0x1D, rp.RI_KEY_E0, rp.VK_RCONTROL),
        (rp.VK_CONTROL, 0x1D, rp.RI_KEY_E0 | rp.RI_KEY_BREAK, rp.VK_RCONTROL),
        (rp.VK_MENU, 0x38, 0, rp.VK_LMENU),
        (rp.VK_MENU, 0x38, rp.RI_KEY_E0, rp.VK_RMENU),
        (rp.VK_FAKE, 0x2A, rp.RI_KEY_E0, None),  # fake shift around extended keys
        (rp.VK_FAKE, 0x00, 0, None),
        (0x00, 0x00, 0, None),
        (0x13, 0x1D, rp.RI_KEY_E1, None),  # Pause/Break E1 prefix
        (rp.VK_CONTROL, 0x1D, rp.RI_KEY_E1, None),
        (VK["W"], 0x11, 0, VK["W"]),
        (0x25, 0x4B, rp.RI_KEY_E0, 0x25),  # arrow key: E0 does not change a non-modifier
        (rp.VK_LSHIFT, 0x2A, 0, rp.VK_LSHIFT),  # already sided (some drivers) -> unchanged
    ],
)
def test_resolve_vk(vk, make, flags, expected):
    assert rp.resolve_vk(vk, make, flags) == expected


def test_key_down_from_break_flag():
    assert rp.key_down(0) and rp.key_down(rp.RI_KEY_E0)
    assert not rp.key_down(rp.RI_KEY_BREAK) and not rp.key_down(rp.RI_KEY_BREAK | rp.RI_KEY_E0)


# --------------------------------------------------------------------------- GetRawInputBuffer alignment
def test_next_block_is_8_byte_aligned():
    assert rp.next_block(0, 48) == 48
    assert rp.next_block(0, 40) == 40
    assert rp.next_block(0, 37) == 40
    assert rp.next_block(40, 33) == 80
    data = rp.pack_buffer([rp.pack_hid(b"\x01\x02\x03\x04\x05"), rp.pack_mouse(1, 1)])
    hid_size = rp.HEADER_SIZE + 8 + 5
    assert hid_size % 8 != 0
    assert len(data) == rp.align_up(hid_size) + 48


def test_buffer_with_hid_entry_keeps_parsing_aligned_entries():
    fg = {"hwnd": GAME}
    proc, out = _setup(fg)
    data = rp.pack_buffer([rp.pack_hid(b"\xaa\xbb\xcc"), rp.pack_mouse(5, 0), rp.pack_keyboard(VK["W"], 0x11)])
    proc.process_buffer(_buf(data), len(data), 3, 7)
    assert proc.other_msgs == 1 and proc.mouse_msgs == 1 and proc.key_msgs == 1 and proc.malformed == 0
    assert out == [MouseDelta(ts_ns=7, dx=5, dy=0), KeyEvent(ts_ns=7, vk=VK["W"], down=True)]


# --------------------------------------------------------------------------- processor
def test_gate_closed_publishes_nothing_but_counts():
    fg: dict[str, int | None] = {"hwnd": OTHER}
    proc, out = _setup(fg)
    for msg in (rp.pack_mouse(3, 4), rp.pack_mouse(button_flags=0x0001), rp.pack_keyboard(VK["W"], 0x11)):
        _feed(proc, msg)
    data = rp.pack_buffer([rp.pack_mouse(1, 1)] * 10 + [rp.pack_keyboard(VK["F9"], 0x43)])
    proc.process_buffer(_buf(data), len(data), 11, 2)
    assert out == []
    assert proc.published == 0
    assert proc.mouse_msgs == 12 and proc.key_msgs == 2
    assert proc.gate.dropped == 14


def test_gate_open_publishes_allowed_keys_resolved_left_right():
    proc, out = _setup({"hwnd": GAME})
    _feed(proc, rp.pack_keyboard(rp.VK_SHIFT, 0x2A))
    _feed(proc, rp.pack_keyboard(rp.VK_SHIFT, 0x36, rp.RI_KEY_BREAK))
    _feed(proc, rp.pack_keyboard(rp.VK_CONTROL, 0x1D))
    _feed(proc, rp.pack_keyboard(ord("P"), 0x19))  # not needed by the app -> dropped by the gate
    _feed(proc, rp.pack_keyboard(rp.VK_FAKE, 0x2A, rp.RI_KEY_E0))  # fake -> ignored before the gate
    _feed(proc, rp.pack_keyboard(0x13, 0x1D, rp.RI_KEY_E1))  # pause prefix -> ignored
    assert [(e.vk, e.down) for e in out if isinstance(e, KeyEvent)] == [
        (rp.VK_LSHIFT, True),
        (rp.VK_RSHIFT, False),
        (rp.VK_LCONTROL, True),
    ]
    assert proc.ignored_keys == 2 and proc.gate.dropped == 1


def test_mouse_buttons_and_absolute_moves():
    proc, out = _setup({"hwnd": GAME})
    _feed(proc, rp.pack_mouse(button_flags=0x0001 | 0x0008))  # left down + right up in one message
    _feed(proc, rp.pack_mouse(30000, 20000, us_flags=rp.MOUSE_MOVE_ABSOLUTE))  # RDP/tablet: no raw counts
    assert [(e.button, e.down) for e in out if isinstance(e, MouseButton)] == [("left", True), ("right", False)]
    assert not any(isinstance(e, MouseDelta) for e in out)
    assert proc.absolute_msgs == 1


def test_batch_coalesces_moves_and_keeps_order():
    proc, out = _setup({"hwnd": GAME})
    msgs = [rp.pack_mouse(2, -1)] * 5 + [rp.pack_mouse(button_flags=0x0001)] + [rp.pack_mouse(-3, 0)] * 4
    msgs += [rp.pack_keyboard(VK["W"], 0x11), rp.pack_mouse(1, 1)]
    data = rp.pack_buffer(msgs)
    proc.process_buffer(_buf(data), len(data), len(msgs), 99)
    assert out == [
        MouseDelta(ts_ns=99, dx=10, dy=-5),
        MouseButton(ts_ns=99, button="left", down=True),
        MouseDelta(ts_ns=99, dx=-12, dy=0),
        KeyEvent(ts_ns=99, vk=VK["W"], down=True),
        MouseDelta(ts_ns=99, dx=1, dy=1),
    ]
    assert proc.mouse_msgs == 11 and proc.published == 5


def test_batch_sum_equals_per_message_sum():
    moves = [(i % 7 - 3, (i * 5) % 11 - 5) for i in range(500)]
    proc_a, out_a = _setup({"hwnd": GAME})
    for dx, dy in moves:
        _feed(proc_a, rp.pack_mouse(dx, dy))
    proc_b, out_b = _setup({"hwnd": GAME})
    data = rp.pack_buffer([rp.pack_mouse(dx, dy) for dx, dy in moves])
    proc_b.process_buffer(_buf(data), len(data), len(moves), 1)

    def total(evts: list[Event]) -> tuple[int, int]:
        ds = [e for e in evts if isinstance(e, MouseDelta)]
        return sum(e.dx for e in ds), sum(e.dy for e in ds)

    assert total(out_a) == total(out_b) == (sum(m[0] for m in moves), sum(m[1] for m in moves))
    assert len(out_b) == 1


def test_malformed_buffers_are_counted_not_raised():
    proc, out = _setup({"hwnd": GAME})
    proc.process_message(_buf(b"\0" * 8), 8, 1)  # shorter than a header
    m = bytearray(rp.pack_mouse(1, 1))
    m[4:8] = (200).to_bytes(4, "little")  # dwSize larger than what we got
    proc.process_message(_buf(bytes(m)), 48, 1)
    k = bytearray(rp.pack_keyboard(VK["W"]))
    k[4:8] = (30).to_bytes(4, "little")  # keyboard message too short for RAWKEYBOARD
    proc.process_message(_buf(bytes(k)), 40, 1)
    data = rp.pack_buffer([rp.pack_mouse(1, 1)])
    proc.process_buffer(_buf(data), len(data), 3, 1)  # count claims more entries than the buffer holds
    assert proc.malformed == 4
    assert out == [MouseDelta(ts_ns=1, dx=1, dy=1)]


# --------------------------------------------------------------------------- hotkeys through the real path
def test_hotkeys_from_gated_raw_keyboard_produce_user_marks():
    bus = EventBus(synchronous=True)
    marks: list[UserMark] = []
    bus.subscribe(UserMark, marks.append)
    km = KeyMap.load()
    HotkeyMapper(km).attach(bus)
    fg: dict[str, int | None] = {"hwnd": GAME}
    gate = InputGate(km.allowed_vks(), lambda: fg["hwnd"])
    gate.on_game_window(GameWindowChanged(ts_ns=0, hwnd=GAME, client_rect=None, focused=True))
    proc = RawInputProcessor(bus, gate)
    for vk, sc in ((VK["F9"], 0x43), (VK["F10"], 0x44), (VK["F8"], 0x42)):
        _feed(proc, rp.pack_keyboard(vk, sc))
        _feed(proc, rp.pack_keyboard(vk, sc))  # auto-repeat
        _feed(proc, rp.pack_keyboard(vk, sc, rp.RI_KEY_BREAK))
    fg["hwnd"] = OTHER  # alt-tabbed: hotkeys must not fire from other apps
    _feed(proc, rp.pack_keyboard(VK["F9"], 0x43))
    assert [m.kind for m in marks] == ["generator", "exit_gate", "note"]


def test_default_hotkeys_do_not_collide_with_game_binds():
    assert hotkey_collisions(KeyMap.load()) == []


def test_hotkey_collision_is_reported():
    km = KeyMap(
        bindings={Action.INTERACT: {VK["F9"]}, Action.SPRINT: {VK["SHIFT"]}},
        hotkeys={VK["F9"]: "generator", VK["LSHIFT"]: "note", VK["F10"]: "exit_gate"},
    )
    warnings = hotkey_collisions(km)
    assert warnings == [
        "WARNING hotkey F9 (generator) is also the game bind for interact",
        "WARNING hotkey LSHIFT (note) is also the game bind for sprint",
    ]
    assert HotkeyMapper(km).collisions == warnings


# --------------------------------------------------------------------------- listener health (no Win32 needed)
def _listener(fg: dict[str, int | None], km: KeyMap | None = None) -> tuple[RawInputListener, list[ServiceHealth]]:
    bus = EventBus(synchronous=True)
    health: list[ServiceHealth] = []
    bus.subscribe(ServiceHealth, health.append)

    class _Clock:
        def now_ns(self) -> int:
            return 1

    lst = RawInputListener(bus, _Clock(), keymap=km, foreground_hwnd=lambda: fg["hwnd"])
    return lst, health


def test_health_detail_says_whether_input_is_gated():
    fg: dict[str, int | None] = {"hwnd": OTHER}
    lst, health = _listener(fg)
    lst._health_tick(initial=True)
    assert health[-1].status == "ok"
    assert health[-1].detail.startswith("listening (mouse+keyboard, INPUTSINK")
    assert health[-1].detail.endswith("gated: DBD not foreground")
    lst.bus.publish(GameWindowChanged(ts_ns=2, hwnd=GAME, client_rect=None, focused=True))
    fg["hwnd"] = GAME
    lst._health_tick()
    assert health[-1].detail.endswith("passing: DBD foreground")
    n = len(health)
    lst._health_tick()
    assert len(health) == n  # unchanged health is not re-published every second


def test_health_detail_surfaces_hotkey_collision():
    km = KeyMap(bindings={Action.INTERACT: {VK["F9"]}}, hotkeys={VK["F9"]: "generator"})
    lst, health = _listener({"hwnd": OTHER}, km)
    lst._health_tick(initial=True)
    assert "WARNING hotkey F9 (generator) is also the game bind for interact" in health[-1].detail


def _fake_win32(monkeypatch: pytest.MonkeyPatch, *, elevated: bool, cls: str = "Chrome_WidgetWin_1") -> list[int]:
    opened: list[int] = []

    def process_elevated(pid: int) -> bool:
        opened.append(pid)
        return elevated

    monkeypatch.setattr(_win32, "self_elevated", lambda: False)
    monkeypatch.setattr(_win32, "window_pid", lambda hwnd: 4242 if hwnd == OTHER else 1111)
    monkeypatch.setattr(_win32, "window_class", lambda hwnd: cls)
    monkeypatch.setattr(_win32, "process_elevated", process_elevated)
    return opened


def test_uipi_elevated_foreground_degrades_health(monkeypatch):
    _fake_win32(monkeypatch, elevated=True)
    lst, health = _listener({"hwnd": OTHER})
    lst._health_tick()  # no WM_INPUT in the last interval + elevated foreground
    assert health[-1].status == "degraded"
    assert health[-1].detail == UIPI_DETAIL


def test_uipi_not_flagged_when_input_still_arrives(monkeypatch):
    _fake_win32(monkeypatch, elevated=True)
    fg: dict[str, int | None] = {"hwnd": OTHER}
    lst, health = _listener(fg)
    data = rp.pack_mouse(1, 1)
    lst.processor.process_message(_buf(data), len(data), 1)  # input arrived -> not blocked
    lst._health_tick()
    assert health[-1].status == "ok"


def test_uipi_check_never_inspects_the_game_process(monkeypatch):
    opened = _fake_win32(monkeypatch, elevated=True)
    fg: dict[str, int | None] = {"hwnd": OTHER}
    lst, health = _listener(fg)
    lst.bus.publish(GameProcessChanged(ts_ns=1, running=True, pid=4242))  # the foreground pid IS the game
    lst._health_tick()
    assert opened == [] and health[-1].status == "ok"
    opened2 = _fake_win32(monkeypatch, elevated=True, cls="UnrealWindow")  # unknown pid but an Unreal window
    lst2, health2 = _listener(fg)
    lst2._health_tick()
    assert opened2 == [] and health2[-1].status == "ok"
