"""Raw Input listener (Windows) - OBSERVES keyboard/mouse; never sends or blocks input.

Registers for HID usage page 0x01 usages 0x02 (mouse) and 0x06 (keyboard) with RIDEV_INPUTSINK on a
hidden window owned by a dedicated thread, so we receive the same raw mouse counts the game gets
even while it has focus. No hooks (SetWindowsHookEx), no SendInput, no injection.

PRIVACY: INPUTSINK sees input system-wide, so every message passes ``InputGate`` (input/privacy.py)
first - only keys the app needs, and only while the DBD window is foreground, are ever published.

STATUS: implemented carefully but UNVERIFIED on hardware - M1 task for the input agent:
  * verify struct layouts on x64 (RAWINPUTHEADER is 24 bytes; RAWMOUSE union alignment),
  * measure CPU at 1000/4000/8000 Hz polling; if Python per-message overhead is too high, batch
    with GetRawInputBuffer or move this loop to a tiny native helper (keep the same events),
  * confirm left/right Shift/Ctrl resolution via MakeCode / RI_KEY_E0,
  * confirm nothing is received when another elevated app is foreground (UIPI) and surface that.
"""

from __future__ import annotations

import logging
import sys
import threading

from ..core.bus import EventBus
from ..core.clock import Clock
from ..core.events import GameWindowChanged, KeyEvent, MouseButton, MouseDelta
from ..core.lifecycle import Service
from .keymap import KeyMap
from .privacy import InputGate

log = logging.getLogger(__name__)

if sys.platform == "win32":  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    WM_INPUT = 0x00FF
    WM_CLOSE = 0x0010
    WM_DESTROY = 0x0002
    RIDEV_INPUTSINK = 0x00000100
    RID_INPUT = 0x10000003
    RIM_TYPEMOUSE = 0
    RIM_TYPEKEYBOARD = 1
    MOUSE_MOVE_ABSOLUTE = 0x01
    RI_KEY_BREAK = 0x01
    RI_KEY_E0 = 0x02
    VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
    VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU = 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class RAWINPUTDEVICE(ctypes.Structure):
        _fields_ = [
            ("usUsagePage", wintypes.USHORT),
            ("usUsage", wintypes.USHORT),
            ("dwFlags", wintypes.DWORD),
            ("hwndTarget", wintypes.HWND),
        ]

    class RAWINPUTHEADER(ctypes.Structure):
        _fields_ = [
            ("dwType", wintypes.DWORD),
            ("dwSize", wintypes.DWORD),
            ("hDevice", wintypes.HANDLE),
            ("wParam", wintypes.WPARAM),
        ]

    class _RAWMOUSE_BUTTONS(ctypes.Structure):
        _fields_ = [("usButtonFlags", wintypes.USHORT), ("usButtonData", wintypes.USHORT)]

    class _RAWMOUSE_U(ctypes.Union):
        _fields_ = [("ulButtons", wintypes.ULONG), ("s", _RAWMOUSE_BUTTONS)]

    class RAWMOUSE(ctypes.Structure):
        _fields_ = [
            ("usFlags", wintypes.USHORT),
            ("u", _RAWMOUSE_U),
            ("ulRawButtons", wintypes.ULONG),
            ("lLastX", wintypes.LONG),
            ("lLastY", wintypes.LONG),
            ("ulExtraInformation", wintypes.ULONG),
        ]

    class RAWKEYBOARD(ctypes.Structure):
        _fields_ = [
            ("MakeCode", wintypes.USHORT),
            ("Flags", wintypes.USHORT),
            ("Reserved", wintypes.USHORT),
            ("VKey", wintypes.USHORT),
            ("Message", wintypes.UINT),
            ("ExtraInformation", wintypes.ULONG),
        ]

    class _RAWINPUT_DATA(ctypes.Union):
        _fields_ = [("mouse", RAWMOUSE), ("keyboard", RAWKEYBOARD)]

    class RAWINPUT(ctypes.Structure):
        _fields_ = [("header", RAWINPUTHEADER), ("data", _RAWINPUT_DATA)]

    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.GetRawInputData.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.UINT),
        wintypes.UINT,
    ]
    user32.GetRawInputData.restype = wintypes.UINT
    user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
    user32.CreateWindowExW.restype = wintypes.HWND

    _BUTTONS = [
        (0x0001, "left", True),
        (0x0002, "left", False),
        (0x0004, "right", True),
        (0x0008, "right", False),
        (0x0010, "middle", True),
        (0x0020, "middle", False),
        (0x0040, "x1", True),
        (0x0080, "x1", False),
        (0x0100, "x2", True),
        (0x0200, "x2", False),
    ]


def _foreground_hwnd() -> int | None:
    if sys.platform != "win32":
        return None
    return user32.GetForegroundWindow() or None  # pragma: no cover


class RawInputListener(Service):
    name = "input.raw"

    def __init__(
        self, bus: EventBus, clock: Clock, *, keymap: KeyMap | None = None, gate: InputGate | None = None
    ) -> None:
        super().__init__(bus, clock)
        self.gate = gate or InputGate((keymap or KeyMap.load()).allowed_vks(), _foreground_hwnd)
        bus.subscribe(GameWindowChanged, self.gate.on_game_window)
        self._thread: threading.Thread | None = None
        self._hwnd = None
        self._wndproc = None  # keep a reference: ctypes callbacks must not be garbage-collected
        self._ready = threading.Event()
        self.messages = 0

    def start(self) -> None:
        if sys.platform != "win32":
            self.set_health("disabled", "Windows only")
            return
        self._thread = threading.Thread(target=self._run, name="input.raw", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)

    def stop(self) -> None:
        if sys.platform == "win32" and self._hwnd:  # pragma: no cover
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(2.0)
        self.set_health("down")

    # ------------------------------------------------------------------ Windows thread
    def _run(self) -> None:  # pragma: no cover - Windows only
        hinst = kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self._proc)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = "DeadByMapperRawInputSink"
        user32.RegisterClassW(ctypes.byref(wc))
        # A real (hidden, never shown) top-level window - INPUTSINK requires a target hwnd.
        self._hwnd = user32.CreateWindowExW(
            0x00000080, wc.lpszClassName, "dbm-raw", 0, 0, 0, 0, 0, None, None, hinst, None
        )
        devices = (RAWINPUTDEVICE * 2)(
            RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, self._hwnd),
            RAWINPUTDEVICE(0x01, 0x06, RIDEV_INPUTSINK, self._hwnd),
        )
        if not user32.RegisterRawInputDevices(devices, 2, ctypes.sizeof(RAWINPUTDEVICE)):
            self.set_health("down", f"RegisterRawInputDevices failed ({ctypes.get_last_error()})")
            self._ready.set()
            return
        self.set_health("ok")
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _proc(self, hwnd, msg, wparam, lparam):  # type: ignore[no-untyped-def]  # pragma: no cover
        if msg == WM_INPUT:
            try:
                self._handle(lparam)
            except Exception:
                log.exception("raw input parse failed")
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _handle(self, lparam) -> None:  # type: ignore[no-untyped-def]  # pragma: no cover
        size = wintypes.UINT(0)
        hdr = ctypes.sizeof(RAWINPUTHEADER)
        user32.GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(size), hdr)
        buf = ctypes.create_string_buffer(size.value)
        if user32.GetRawInputData(lparam, RID_INPUT, buf, ctypes.byref(size), hdr) != size.value:
            return
        ri = ctypes.cast(buf, ctypes.POINTER(RAWINPUT)).contents
        ts = self.clock.now_ns()
        self.messages += 1
        if ri.header.dwType == RIM_TYPEMOUSE:
            if not self.gate.allow_mouse():
                return
            m = ri.data.mouse
            if not (m.usFlags & MOUSE_MOVE_ABSOLUTE) and (m.lLastX or m.lLastY):
                self.bus.publish(MouseDelta(ts_ns=ts, dx=int(m.lLastX), dy=int(m.lLastY)))
            flags = m.u.s.usButtonFlags
            if flags:
                for bit, name, down in _BUTTONS:
                    if flags & bit:
                        self.bus.publish(MouseButton(ts_ns=ts, button=name, down=down))
        elif ri.header.dwType == RIM_TYPEKEYBOARD:
            k = ri.data.keyboard
            vk = int(k.VKey)
            if vk == 0xFF:  # fake key from escaped sequences
                return
            e0 = bool(k.Flags & RI_KEY_E0)
            if vk == VK_SHIFT:
                vk = VK_RSHIFT if k.MakeCode == 0x36 else VK_LSHIFT
            elif vk == VK_CONTROL:
                vk = VK_RCONTROL if e0 else VK_LCONTROL
            elif vk == VK_MENU:
                vk = VK_RMENU if e0 else VK_LMENU
            if not self.gate.allow_key(vk):
                return
            self.bus.publish(KeyEvent(ts_ns=ts, vk=vk, down=not (k.Flags & RI_KEY_BREAK)))
