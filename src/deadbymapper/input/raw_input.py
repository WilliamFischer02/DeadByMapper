"""Raw Input listener (Windows) - OBSERVES keyboard/mouse; never sends or blocks input.

Registers for HID usage page 0x01 usages 0x02 (mouse) and 0x06 (keyboard) with RIDEV_INPUTSINK on a
hidden, never-shown window owned by a dedicated thread, so we receive the same raw mouse counts the game
gets while it has focus. No hooks (SetWindowsHookEx), no SendInput, no injection.

PRIVACY: INPUTSINK sees input system-wide, so every message passes ``InputGate`` (input/privacy.py)
inside ``RawInputProcessor`` before anything is published - only keys the app needs, and only while the
DBD window is foreground. Dropped messages are counted, never stored or logged.

Reading (M1: verified on Windows 11 x64 without the game - registration, WM_INPUT/GetRawInputBuffer delivery
on the listener thread, gate drops everything when DBD is not foreground; tests/test_input_raw_windows.py):
  * batched (default): the thread sleeps in MsgWaitForMultipleObjectsEx; on wake it waits a short
    coalescing window (``batch_window_s``, 4 ms), then drains everything with GetRawInputBuffer (one
    syscall for dozens of messages at 8 kHz) and publishes ONE MouseDelta per run of mouse moves (sums are
    exact; order relative to buttons/keys is preserved). Anything that still reaches the queue as WM_INPUT
    is read with GetRawInputData - both paths go through the same ``RawInputProcessor``.
  * unbatched (``batch=False`` or 32-bit Python): one GetRawInputData per WM_INPUT, no coalescing.
CPU numbers: ``uv run python tools/input_probe.py --bench``.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from ..core.bus import EventBus
from ..core.clock import Clock
from ..core.events import GameProcessChanged, GameWindowChanged, KeyEvent, MouseButton, MouseDelta
from ..core.lifecycle import Service
from . import _win32 as w
from . import rawparse as rp
from .hotkeys import hotkey_collisions
from .keymap import KeyMap
from .privacy import InputGate

log = logging.getLogger(__name__)

UIPI_DETAIL = (
    "elevated window in foreground - Raw Input not delivered (run DeadByMapper as the same user, not elevated)"
)
UNREAL_WINDOW_CLASS = "UnrealWindow"  # DBD's top-level window class - never treated as a UIPI suspect
UIPI_SILENCE_S = 3.0  # raw-input silence after a foreground change before UIPI is suspected
_MSG_BUF_SIZE = 256  # GetRawInputData target: mouse 48, keyboard 40; HID can be larger -> grows on demand
_BATCH_BUF_SIZE = 64 * 1024  # GetRawInputBuffer target (~1300 mouse messages)


def _foreground_hwnd() -> int | None:
    return w.foreground_hwnd()


# --------------------------------------------------------------------------- parse -> gate -> publish
class RawInputProcessor:
    """Turns RAWINPUT bytes into gated events. Pure Python (struct), so it runs and is benchmarked on any OS.

    ``process_message`` handles one RAWINPUT (GetRawInputData); ``process_buffer`` handles the packed,
    8-byte-aligned array GetRawInputBuffer returns and coalesces consecutive relative mouse moves.
    """

    def __init__(self, bus: EventBus, gate: InputGate) -> None:
        self.bus = bus
        self.gate = gate
        self.mouse_msgs = 0
        self.key_msgs = 0
        self.other_msgs = 0  # HID / unknown types (we only register mouse + keyboard)
        self.absolute_msgs = 0  # MOUSE_MOVE_ABSOLUTE (RDP, tablets, VMs): no raw counts -> not published
        self.ignored_keys = 0  # VKey 0xFF / 0, E1 (Pause) prefix, unresolvable shift scan codes
        self.malformed = 0
        self.published = 0

    @property
    def messages(self) -> int:
        return self.mouse_msgs + self.key_msgs + self.other_msgs

    # ------------------------------------------------------------------ entry points
    def process_message(self, buf: Any, size: int, ts: int) -> None:
        if size < rp.HEADER_SIZE:
            self.malformed += 1
            return
        dw_type, dw_size, _dev, _wp = rp.HEADER.unpack_from(buf, 0)
        if dw_size > size:
            self.malformed += 1
            return
        self._one(buf, 0, dw_type, dw_size, ts)

    def process_buffer(self, buf: Any, size: int, count: int, ts: int) -> None:
        off = 0
        acc_x = acc_y = 0
        hdr = rp.HEADER.unpack_from
        mouse = rp.MOUSE.unpack_from
        hs = rp.HEADER_SIZE
        allow_mouse = self.gate.allow_mouse
        for _ in range(count):
            if off + hs > size:
                self.malformed += 1
                break
            dw_type, dw_size, _dev, _wp = hdr(buf, off)
            if dw_size < hs or off + dw_size > size:
                self.malformed += 1
                break
            if dw_type == rp.RIM_TYPEMOUSE and dw_size >= rp.MOUSE_MSG_SIZE:
                # fast path: relative move without button transitions -> accumulate
                us_flags, bflags, _bdata, _raw, dx, dy, _extra = mouse(buf, off + hs)
                if not bflags and not us_flags & rp.MOUSE_MOVE_ABSOLUTE:
                    self.mouse_msgs += 1
                    if allow_mouse():
                        acc_x += dx
                        acc_y += dy
                    off = rp.next_block(off, dw_size)
                    continue
            if acc_x or acc_y:  # keep ordering: flush pending motion before a button/key/other event
                self._publish(MouseDelta(ts_ns=ts, dx=acc_x, dy=acc_y))
                acc_x = acc_y = 0
            self._one(buf, off, dw_type, dw_size, ts)
            off = rp.next_block(off, dw_size)
        if acc_x or acc_y:
            self._publish(MouseDelta(ts_ns=ts, dx=acc_x, dy=acc_y))

    # ------------------------------------------------------------------ one RAWINPUT
    def _one(self, buf: Any, off: int, dw_type: int, dw_size: int, ts: int) -> None:
        data = off + rp.HEADER_SIZE
        if dw_type == rp.RIM_TYPEMOUSE:
            if dw_size < rp.MOUSE_MSG_SIZE:
                self.malformed += 1
                return
            self.mouse_msgs += 1
            us_flags, bflags, _bdata, _raw, dx, dy, _extra = rp.MOUSE.unpack_from(buf, data)
            absolute = bool(us_flags & rp.MOUSE_MOVE_ABSOLUTE)
            self.absolute_msgs += absolute
            if not self.gate.allow_mouse():
                return
            if not absolute and (dx or dy):
                self._publish(MouseDelta(ts_ns=ts, dx=dx, dy=dy))
            if bflags & rp.BUTTON_MASK:
                for bit, name, down in rp.BUTTON_BITS:
                    if bflags & bit:
                        self._publish(MouseButton(ts_ns=ts, button=name, down=down))
        elif dw_type == rp.RIM_TYPEKEYBOARD:
            if dw_size < rp.KEYBOARD_MSG_SIZE:
                self.malformed += 1
                return
            self.key_msgs += 1
            make, flags, _res, vkey, _msg, _extra = rp.KEYBOARD.unpack_from(buf, data)
            vk = rp.resolve_vk(vkey, make, flags)
            if vk is None:
                self.ignored_keys += 1
                return
            if not self.gate.allow_key(vk):  # the privacy gate: allowed set + DBD foreground, else dropped
                return
            self._publish(KeyEvent(ts_ns=ts, vk=vk, down=rp.key_down(flags)))
        else:
            self.other_msgs += 1

    def _publish(self, e: MouseDelta | MouseButton | KeyEvent) -> None:
        self.published += 1
        self.bus.publish(e)


# --------------------------------------------------------------------------- the service
class RawInputListener(Service):
    name = "input.raw"

    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        *,
        keymap: KeyMap | None = None,
        gate: InputGate | None = None,
        batch: bool = True,
        batch_window_s: float = 0.004,
        health_interval_s: float = 1.0,
        foreground_hwnd: Callable[[], int | None] = _foreground_hwnd,
        now_s: Callable[[], float] = time.perf_counter,
    ) -> None:
        super().__init__(bus, clock)
        km = keymap or KeyMap.load()
        self.gate = gate or InputGate(km.allowed_vks(), foreground_hwnd)
        self.processor = RawInputProcessor(bus, self.gate)
        self.hotkey_warnings = hotkey_collisions(km)
        self.batch = batch and rp.IS_64BIT
        self.batch_window_s = batch_window_s
        self.health_interval_s = health_interval_s
        self._fg = foreground_hwnd
        bus.subscribe(GameWindowChanged, self.gate.on_game_window)
        bus.subscribe(GameProcessChanged, self._on_process)
        self._game_pid: int | None = None
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._ready = threading.Event()
        self._hwnd: int | None = None
        self._hinst: int | None = None
        self._class_name = f"DeadByMapperRawInputSink.{os.getpid()}.{id(self):x}"
        self._wndproc: Any = None  # keep a reference: ctypes callbacks must not be garbage-collected
        self._msg_buf: Any = None
        self._batch_buf: Any = None
        self._last_health: tuple[str, str] | None = None
        self._health_msgs = 0
        self._health_abs = 0
        self._health_mouse = 0
        self._now_s = now_s
        self._uipi_fg: int | None = None
        self._uipi_since_s = 0.0
        self._uipi_msgs = 0
        self.uipi_degraded = False
        # diagnostics (counts only)
        self.registered = False
        self.register_error = 0
        self.read_errors = 0
        self.buffer_errors = 0
        self.wm_input = 0  # WM_INPUT messages dispatched to the window procedure
        self.batches = 0  # GetRawInputBuffer calls that returned >= 1 message
        self.wakes = 0  # MsgWaitForMultipleObjectsEx returns other than timeout (must track input, never spin)
        self.thread_id: int | None = None
        self.input_thread_ids: set[int] = set()  # threads that processed input (must be {thread_id})

    # ------------------------------------------------------------------ counters for the probe
    @property
    def messages(self) -> int:
        return self.processor.messages

    def _on_process(self, e: GameProcessChanged) -> None:
        self._game_pid = e.pid if e.running else None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if sys.platform != "win32":
            self.set_health("disabled", "Windows only")
            return
        self._stop_evt.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="input.raw", daemon=True)
        self._thread.start()
        if not self._ready.wait(2.0):
            self.set_health("down", "listener thread did not start within 2 s")

    def stop(self) -> None:
        self._stop_evt.set()
        hwnd = self._hwnd
        if sys.platform == "win32" and hwnd:  # pragma: no cover - wakes MsgWaitForMultipleObjectsEx
            w.user32.PostMessageW(hwnd, w.WM_CLOSE, 0, 0)
        if self._thread is not None:
            self._thread.join(1.0)
            if self._thread.is_alive():
                log.warning("raw input thread did not exit within 1 s")
            self._thread = None
        self._last_health = None
        self.set_health("down", "stopped")

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------ Windows thread
    def _run(self) -> None:  # pragma: no cover - Windows only
        self.thread_id = threading.get_native_id()
        try:
            ok = self._setup()
        except Exception as exc:
            log.exception("raw input setup failed")
            self.set_health("down", f"setup failed: {type(exc).__name__}: {exc}")
            ok = False
        self._ready.set()
        try:
            if ok:
                self._loop()
        except Exception as exc:
            log.exception("raw input loop crashed")
            self.set_health("down", f"loop failed: {type(exc).__name__}: {exc}")
        finally:
            self._teardown()

    def _setup(self) -> bool:  # pragma: no cover - Windows only
        import ctypes

        self._hinst = w.kernel32.GetModuleHandleW(None)
        self._wndproc = w.WNDPROC(self._proc)
        wc = w.WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = self._hinst
        wc.lpszClassName = self._class_name
        if not w.user32.RegisterClassW(ctypes.byref(wc)):
            self.set_health("down", f"RegisterClassW failed ({ctypes.get_last_error()})")
            return False
        # A real (hidden, never shown) top-level tool window: INPUTSINK requires a target hwnd.
        hwnd = w.user32.CreateWindowExW(
            w.WS_EX_TOOLWINDOW, self._class_name, "dbm-raw", 0, 0, 0, 0, 0, None, None, self._hinst, None
        )
        if not hwnd:
            self.set_health("down", f"CreateWindowExW failed ({ctypes.get_last_error()})")
            return False
        self._hwnd = int(hwnd)
        self._msg_buf = (ctypes.c_uint64 * (_MSG_BUF_SIZE // 8))()
        self._batch_buf = (ctypes.c_uint64 * (_BATCH_BUF_SIZE // 8))()  # 8-byte aligned (NEXTRAWINPUTBLOCK)
        devices = (w.RAWINPUTDEVICE * 2)(
            w.RAWINPUTDEVICE(w.HID_USAGE_PAGE_GENERIC, w.HID_USAGE_MOUSE, w.RIDEV_INPUTSINK, self._hwnd),
            w.RAWINPUTDEVICE(w.HID_USAGE_PAGE_GENERIC, w.HID_USAGE_KEYBOARD, w.RIDEV_INPUTSINK, self._hwnd),
        )
        if not w.user32.RegisterRawInputDevices(devices, 2, ctypes.sizeof(w.RAWINPUTDEVICE)):
            self.register_error = ctypes.get_last_error()
            self.set_health("down", f"RegisterRawInputDevices failed (error {self.register_error})")
            return False
        self.registered = True
        self._health_tick(initial=True)
        return True

    def _loop(self) -> None:  # pragma: no cover - Windows only
        import ctypes
        from ctypes import wintypes

        msg = wintypes.MSG()
        pmsg = ctypes.byref(msg)
        wait = w.user32.MsgWaitForMultipleObjectsEx
        peek = w.user32.PeekMessageW
        dispatch = w.user32.DispatchMessageW
        next_health = time.perf_counter() + self.health_interval_s
        timeout_ms = 250
        while not self._stop_evt.is_set():
            r = wait(0, None, timeout_ms, w.QS_ALLINPUT, w.MWMO_INPUTAVAILABLE)
            if self._stop_evt.is_set():
                break
            if r != w.WAIT_TIMEOUT:
                self.wakes += 1
                if self.batch:
                    time.sleep(self.batch_window_s)  # coalescing window: let 1-8 kHz input pile up, then drain
                    self._drain()
            while peek(pmsg, None, 0, 0, w.PM_REMOVE):
                if msg.message == w.WM_QUIT:
                    self._stop_evt.set()
                    break
                dispatch(pmsg)
            now = time.perf_counter()
            if self.uipi_degraded and self.processor.messages != self._uipi_msgs:
                next_health = now  # input is flowing again: restore "ok" right away
            if now >= next_health:
                next_health = now + self.health_interval_s
                self._health_tick()

    def _drain(self) -> None:  # pragma: no cover - Windows only
        import ctypes
        from ctypes import wintypes

        buf = self._batch_buf
        for _ in range(64):  # bounded: 64 x 64 KiB is far more than any burst
            size = wintypes.UINT(_BATCH_BUF_SIZE)
            n = w.user32.GetRawInputBuffer(buf, ctypes.byref(size), rp.HEADER_SIZE)
            if n == 0:
                return
            if n == w.UINT_ERROR:
                self.buffer_errors += 1
                if self.buffer_errors >= 10:  # never fight the OS: fall back to one-message-at-a-time
                    err = ctypes.get_last_error()
                    log.warning("GetRawInputBuffer keeps failing (%d) - switching to WM_INPUT reads", err)
                    self.batch = False
                return
            self.batches += 1
            self.input_thread_ids.add(threading.get_native_id())
            self.processor.process_buffer(buf, _BATCH_BUF_SIZE, n, self.clock.now_ns())

    def _proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:  # pragma: no cover - Windows only
        if msg == w.WM_INPUT:
            self.wm_input += 1
            try:
                self._on_wm_input(lparam)
            except Exception:
                log.exception("raw input parse failed")  # never includes message contents
            return w.user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        if msg == w.WM_CLOSE:
            w.user32.DestroyWindow(hwnd)
            return 0
        if msg == w.WM_DESTROY:
            self._hwnd = None
            return 0
        return w.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def read_raw_input_data(self, handle: int, header_size: int = rp.HEADER_SIZE) -> int | None:  # pragma: no cover
        """GetRawInputData into the preallocated buffer. Returns the byte count, or None on any error
        (stale/invalid HRAWINPUT, device removed, wrong header size) - errors are counted, never raised."""
        import ctypes
        from ctypes import wintypes

        if self._msg_buf is None:
            self._msg_buf = (ctypes.c_uint64 * (_MSG_BUF_SIZE // 8))()
        cap = ctypes.sizeof(self._msg_buf)
        size = wintypes.UINT(cap)
        got = w.user32.GetRawInputData(handle, w.RID_INPUT, self._msg_buf, ctypes.byref(size), header_size)
        if got == w.UINT_ERROR and ctypes.get_last_error() == w.ERROR_INSUFFICIENT_BUFFER:
            need = wintypes.UINT(0)
            if w.user32.GetRawInputData(handle, w.RID_INPUT, None, ctypes.byref(need), header_size) == 0 and need.value:
                self._msg_buf = (ctypes.c_uint64 * ((need.value + 7) // 8))()
                size = wintypes.UINT(ctypes.sizeof(self._msg_buf))
                got = w.user32.GetRawInputData(handle, w.RID_INPUT, self._msg_buf, ctypes.byref(size), header_size)
        if got in (w.UINT_ERROR, 0) or got > ctypes.sizeof(self._msg_buf):
            self.read_errors += 1
            return None
        return int(got)

    def _on_wm_input(self, lparam: int) -> None:  # pragma: no cover - Windows only
        got = self.read_raw_input_data(lparam)
        if got is None:
            return
        self.input_thread_ids.add(threading.get_native_id())
        self.processor.process_message(self._msg_buf, got, self.clock.now_ns())

    def _teardown(self) -> None:  # pragma: no cover - Windows only
        import ctypes

        try:
            if self.registered:
                rm = (w.RAWINPUTDEVICE * 2)(
                    w.RAWINPUTDEVICE(w.HID_USAGE_PAGE_GENERIC, w.HID_USAGE_MOUSE, w.RIDEV_REMOVE, None),
                    w.RAWINPUTDEVICE(w.HID_USAGE_PAGE_GENERIC, w.HID_USAGE_KEYBOARD, w.RIDEV_REMOVE, None),
                )
                w.user32.RegisterRawInputDevices(rm, 2, ctypes.sizeof(w.RAWINPUTDEVICE))
                self.registered = False
            if self._hwnd:
                w.user32.DestroyWindow(self._hwnd)
                self._hwnd = None
            if self._hinst is not None:
                w.user32.UnregisterClassW(self._class_name, self._hinst)
        except Exception:
            log.exception("raw input teardown failed")

    # ------------------------------------------------------------------ health
    def _base_detail(self) -> str:
        mode = "batched" if self.batch else "per-message"
        return f"listening (mouse+keyboard, INPUTSINK, {mode})"

    def _health_tick(self, *, initial: bool = False) -> None:
        p = self.processor
        since = p.messages - self._health_msgs
        mouse_since = p.mouse_msgs - self._health_mouse
        abs_since = p.absolute_msgs - self._health_abs
        self._health_msgs, self._health_mouse, self._health_abs = p.messages, p.mouse_msgs, p.absolute_msgs
        game_fg = self.gate.game_is_foreground()
        parts = [self._base_detail()]
        parts.append("passing: DBD foreground" if game_fg else "gated: DBD not foreground")
        status = "ok"
        if initial:  # the window that is foreground at start-up is not a "change": arm, never flag
            self._uipi_fg, self._uipi_since_s, self._uipi_msgs = self._fg(), float("inf"), p.messages
        elif self._uipi_suspected() and since == 0:
            status, parts = "degraded", [UIPI_DETAIL]
        self.uipi_degraded = status == "degraded"
        if mouse_since and abs_since == mouse_since:
            parts.append("absolute pointer (RDP/tablet/VM) - no raw mouse counts")
        parts.extend(self.hotkey_warnings)
        self._publish_health(status, " - ".join(parts))

    def _publish_health(self, status: str, detail: str) -> None:
        if (status, detail) != self._last_health:
            self._last_health = (status, detail)
            self.set_health(status, detail)

    def _uipi_suspected(self) -> bool:
        """Passive UIPI heuristic - opens no process and queries no token but our own.

        If we are not elevated, the foreground changed to a non-game window, and ZERO raw messages arrived
        during the following ``UIPI_SILENCE_S`` while that window stayed foreground, Raw Input is probably
        being withheld by UIPI (an elevated window has focus). The first message that arrives clears it.
        Skips the DBD window/pid and any Unreal window. May false-positive on a user who sits idle for
        3 s after switching windows - acceptable, the gate drops that input anyway."""
        now = self._now_s()
        msgs = self.processor.messages
        fg = self._fg()
        if fg != self._uipi_fg:  # foreground changed: (re)arm the silence timer for the new window
            self._uipi_fg, self._uipi_since_s, self._uipi_msgs = fg, now, msgs
            return False
        if not fg or fg == self.gate.game_hwnd or w.self_elevated():
            return False
        if msgs != self._uipi_msgs or now - self._uipi_since_s < UIPI_SILENCE_S:
            return False
        pid = w.window_pid(fg)  # GetWindowThreadProcessId: reads the window, opens nothing
        if pid is not None and pid in (os.getpid(), self._game_pid):
            return False
        return w.window_class(fg) != UNREAL_WINDOW_CLASS
