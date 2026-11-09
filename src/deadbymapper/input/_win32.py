"""ctypes bindings for the Raw Input listener (Windows only; importable everywhere).

Lives in input/ (not gamelink/win32_util.py, which belongs to the capture lane) because it is input
specific: Raw Input structures and calls, the hidden sink window's message loop, and two read-only
helpers (own-token elevation, foreground window pid/class) for the UIPI heuristic and the probe's ``--self`` mode.

OBSERVE ONLY: nothing here sends, injects, blocks or hooks input. No SendInput / keybd_event /
mouse_event / SetWindowsHookEx - tests/test_input_privacy.py greps the input package for them.
"""

from __future__ import annotations

import functools
import sys
from typing import Any

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    # ------------------------------------------------------------------ structures (x64 sizes in comments)
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

    class RAWINPUTDEVICE(ctypes.Structure):  # 16
        _fields_ = [
            ("usUsagePage", wintypes.USHORT),
            ("usUsage", wintypes.USHORT),
            ("dwFlags", wintypes.DWORD),
            ("hwndTarget", wintypes.HWND),
        ]

    class RAWINPUTHEADER(ctypes.Structure):  # 24
        _fields_ = [
            ("dwType", wintypes.DWORD),
            ("dwSize", wintypes.DWORD),
            ("hDevice", wintypes.HANDLE),
            ("wParam", wintypes.WPARAM),
        ]

    class _RAWMOUSE_BUTTONS(ctypes.Structure):  # noqa: N801
        _fields_ = [("usButtonFlags", wintypes.USHORT), ("usButtonData", wintypes.USHORT)]

    class _RAWMOUSE_U(ctypes.Union):  # noqa: N801
        _anonymous_ = ("s",)  # C: union { ULONG ulButtons; struct { USHORT usButtonFlags, usButtonData; }; }
        _fields_ = [("ulButtons", wintypes.ULONG), ("s", _RAWMOUSE_BUTTONS)]

    class RAWMOUSE(ctypes.Structure):  # 24 (the union is 4-aligned -> 2 bytes of padding after usFlags)
        _anonymous_ = ("u",)
        _fields_ = [
            ("usFlags", wintypes.USHORT),
            ("u", _RAWMOUSE_U),
            ("ulRawButtons", wintypes.ULONG),
            ("lLastX", wintypes.LONG),
            ("lLastY", wintypes.LONG),
            ("ulExtraInformation", wintypes.ULONG),
        ]

    class RAWKEYBOARD(ctypes.Structure):  # 16
        _fields_ = [
            ("MakeCode", wintypes.USHORT),
            ("Flags", wintypes.USHORT),
            ("Reserved", wintypes.USHORT),
            ("VKey", wintypes.USHORT),
            ("Message", wintypes.UINT),
            ("ExtraInformation", wintypes.ULONG),
        ]

    class RAWHID(ctypes.Structure):  # 12 with the 1-byte placeholder array; the header part is 8
        _fields_ = [("dwSizeHid", wintypes.DWORD), ("dwCount", wintypes.DWORD), ("bRawData", wintypes.BYTE * 1)]

    class _RAWINPUT_DATA(ctypes.Union):  # noqa: N801
        _fields_ = [("mouse", RAWMOUSE), ("keyboard", RAWKEYBOARD), ("hid", RAWHID)]

    class RAWINPUT(ctypes.Structure):  # 48 (union sized by RAWMOUSE)
        _fields_ = [("header", RAWINPUTHEADER), ("data", _RAWINPUT_DATA)]

    class RAWINPUT_KEYBOARD(ctypes.Structure):  # noqa: N801 (40 bytes: a keyboard message)
        _fields_ = [("header", RAWINPUTHEADER), ("keyboard", RAWKEYBOARD)]

    class RAWINPUT_MOUSE(ctypes.Structure):  # noqa: N801 (48 bytes: a mouse message)
        _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]

    class TOKEN_ELEVATION(ctypes.Structure):  # noqa: N801
        _fields_ = [("TokenIsElevated", wintypes.DWORD)]

    # ------------------------------------------------------------------ prototypes
    # NB: every handle-returning call gets an explicit restype. The scaffold left GetModuleHandleW at the
    # default c_int, which truncates the 64-bit HMODULE of python.exe (based above 4 GB) - bug fixed here.
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetConsoleWindow.restype = wintypes.HWND
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]

    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.MsgWaitForMultipleObjectsEx.argtypes = [
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    user32.MsgWaitForMultipleObjectsEx.restype = wintypes.DWORD
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
    user32.RegisterRawInputDevices.restype = wintypes.BOOL
    user32.GetRegisteredRawInputDevices.argtypes = [
        ctypes.POINTER(RAWINPUTDEVICE),
        ctypes.POINTER(wintypes.UINT),
        wintypes.UINT,
    ]
    user32.GetRegisteredRawInputDevices.restype = wintypes.UINT
    user32.GetRawInputData.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.UINT),
        wintypes.UINT,
    ]
    user32.GetRawInputData.restype = wintypes.UINT
    user32.GetRawInputBuffer.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.UINT), wintypes.UINT]
    user32.GetRawInputBuffer.restype = wintypes.UINT

# ---------------------------------------------------------------------- constants
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
WM_INPUT = 0x00FF
PM_REMOVE = 0x0001
QS_ALLINPUT = 0x04FF  # includes QS_RAWINPUT (0x0400)
MWMO_INPUTAVAILABLE = 0x0004
WAIT_TIMEOUT = 0x102
WS_EX_TOOLWINDOW = 0x00000080
RIDEV_REMOVE = 0x00000001
RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
UINT_ERROR = 0xFFFFFFFF  # (UINT)-1
ERROR_INSUFFICIENT_BUFFER = 122
GA_ROOTOWNER = 3
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION_CLASS = 20  # TokenElevation
HID_USAGE_PAGE_GENERIC = 0x01
HID_USAGE_MOUSE = 0x02
HID_USAGE_KEYBOARD = 0x06


def layout() -> dict[str, int]:
    """Struct sizes / offsets as ctypes computes them (printed by tools/input_probe.py, asserted in tests)."""
    if not IS_WINDOWS:
        return {}
    o: dict[str, Any] = {}  # pragma: no cover - Windows only below
    o["RAWINPUTHEADER"] = ctypes.sizeof(RAWINPUTHEADER)
    o["RAWINPUTHEADER.hDevice@"] = RAWINPUTHEADER.hDevice.offset
    o["RAWINPUTHEADER.wParam@"] = RAWINPUTHEADER.wParam.offset
    o["RAWMOUSE"] = ctypes.sizeof(RAWMOUSE)
    o["RAWMOUSE.usButtonFlags@"] = RAWMOUSE.u.offset + _RAWMOUSE_BUTTONS.usButtonFlags.offset
    o["RAWMOUSE.usButtonData@"] = RAWMOUSE.u.offset + _RAWMOUSE_BUTTONS.usButtonData.offset
    o["RAWMOUSE.ulRawButtons@"] = RAWMOUSE.ulRawButtons.offset
    o["RAWMOUSE.lLastX@"] = RAWMOUSE.lLastX.offset
    o["RAWMOUSE.lLastY@"] = RAWMOUSE.lLastY.offset
    o["RAWMOUSE.ulExtraInformation@"] = RAWMOUSE.ulExtraInformation.offset
    o["RAWKEYBOARD"] = ctypes.sizeof(RAWKEYBOARD)
    o["RAWKEYBOARD.VKey@"] = RAWKEYBOARD.VKey.offset
    o["RAWKEYBOARD.Message@"] = RAWKEYBOARD.Message.offset
    o["RAWHID"] = ctypes.sizeof(RAWHID)
    o["RAWHID.bRawData@"] = RAWHID.bRawData.offset
    o["RAWINPUT"] = ctypes.sizeof(RAWINPUT)
    o["RAWINPUT.data@"] = RAWINPUT.data.offset
    o["RAWINPUT(mouse)"] = ctypes.sizeof(RAWINPUT_MOUSE)
    o["RAWINPUT(keyboard)"] = ctypes.sizeof(RAWINPUT_KEYBOARD)
    o["RAWINPUTDEVICE"] = ctypes.sizeof(RAWINPUTDEVICE)
    return o


# ---------------------------------------------------------------------- read-only helpers
def foreground_hwnd() -> int | None:
    if not IS_WINDOWS:
        return None
    return int(user32.GetForegroundWindow() or 0) or None  # pragma: no cover


def window_pid(hwnd: int) -> int | None:
    if not IS_WINDOWS:
        return None
    pid = wintypes.DWORD()  # pragma: no cover - Windows only below
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value) or None


def window_class(hwnd: int) -> str:
    if not IS_WINDOWS:
        return ""
    buf = ctypes.create_unicode_buffer(256)  # pragma: no cover - Windows only below
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def console_root_window() -> int | None:
    """The top-level window that hosts this process's console (for ``input_probe --self``).

    Under conhost this is GetConsoleWindow() itself; under Windows Terminal GetConsoleWindow() returns a
    hidden ConPTY pseudo-window that is *owned by* the terminal window, so walk to the root owner."""
    if not IS_WINDOWS:
        return None
    hwnd = kernel32.GetConsoleWindow()  # pragma: no cover - Windows only below
    if not hwnd:
        return None
    root = user32.GetAncestor(hwnd, GA_ROOTOWNER)
    return int(root or hwnd)


@functools.cache
def self_elevated() -> bool:
    """Whether THIS process runs elevated (then UIPI never blocks us). Cached: it cannot change.

    Uses only our own token via the GetCurrentProcess() pseudo-handle: no other process is ever opened
    and no other token is ever queried (docs/FAIR_PLAY.md)."""
    if not IS_WINDOWS:
        return False
    tok = wintypes.HANDLE()  # pragma: no cover - Windows only below
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(tok)):
        return False
    try:
        te = TOKEN_ELEVATION()
        ret = wintypes.DWORD()
        ok = advapi32.GetTokenInformation(
            tok, TOKEN_ELEVATION_CLASS, ctypes.byref(te), ctypes.sizeof(te), ctypes.byref(ret)
        )
        return bool(ok and te.TokenIsElevated)
    finally:
        kernel32.CloseHandle(tok)


def registered_devices() -> list[tuple[int, int, int, int]]:
    """This process's Raw Input registrations as (usage_page, usage, flags, hwnd_target)."""
    if not IS_WINDOWS:
        return []
    n = wintypes.UINT(0)  # pragma: no cover - Windows only below
    size = ctypes.sizeof(RAWINPUTDEVICE)
    w_ = user32.GetRegisteredRawInputDevices(None, ctypes.byref(n), size)
    if w_ == UINT_ERROR or not n.value:
        return []
    arr = (RAWINPUTDEVICE * n.value)()
    got = user32.GetRegisteredRawInputDevices(arr, ctypes.byref(n), size)
    if got == UINT_ERROR:
        return []
    return [(d.usUsagePage, d.usUsage, d.dwFlags, int(d.hwndTarget or 0)) for d in arr[:got]]
