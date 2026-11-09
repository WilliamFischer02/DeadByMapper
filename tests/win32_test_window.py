"""A self-contained Win32 test window (ctypes only) for windows_only capture tests.

Runs its own message loop on a thread, keeps repainting (so WGC keeps delivering frames), and paints a
known colour layout: the top half solid ``TOP`` and the bottom half alternating, so a test can check
that a captured frame contains exactly the client area (no title bar / border pixels).
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

TOP = (40, 90, 200)  # RGB
BOTTOM = ((200, 40, 40), (40, 200, 40))

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
kernel32 = ctypes.WinDLL("kernel32")

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
WM_DESTROY, WM_PAINT, WM_TIMER, WM_CLOSE, WM_ERASEBKGND = 0x0002, 0x000F, 0x0113, 0x0010, 0x0014


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


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", wintypes.RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]  # fmt: skip
user32.CreateWindowExW.restype = wintypes.HWND
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.AdjustWindowRectEx.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.BeginPaint.restype = wintypes.HDC
user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

_CLASS = "DeadByMapperCaptureTestWindow"
_registered: list[bool] = []
_reg_lock = threading.Lock()


def _rgb(c: tuple[int, int, int]) -> int:
    return c[0] | (c[1] << 8) | (c[2] << 16)


class TestWindow:
    """``with TestWindow("title", 640, 360) as w: ... w.hwnd``; ``w.close()`` destroys it early."""

    __test__ = False  # not a pytest class

    def __init__(self, title: str, client_w: int, client_h: int, x: int = 160, y: int = 160) -> None:
        self.title, self.client_w, self.client_h, self.x, self.y = title, client_w, client_h, x, y
        self.hwnd: int = 0
        self._ready = threading.Event()
        self._tick = 0
        self._thread: threading.Thread | None = None
        self._brushes = [gdi32.CreateSolidBrush(_rgb(TOP))] + [gdi32.CreateSolidBrush(_rgb(c)) for c in BOTTOM]

    def __enter__(self) -> TestWindow:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def open(self, timeout: float = 5.0) -> None:
        self._thread = threading.Thread(target=self._run, name="test-window", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout) or not self.hwnd:
            raise RuntimeError("could not create the test window")

    def close(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self.hwnd = 0

    def _wndproc(self, hwnd, msg, wparam, lparam):  # type: ignore[no-untyped-def]
        if msg == WM_TIMER:
            self._tick += 1
            user32.InvalidateRect(hwnd, None, False)
            return 0
        if msg == WM_ERASEBKGND:
            return 1
        if msg == WM_PAINT:
            ps = PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
            rc = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rc))
            top = wintypes.RECT(0, 0, rc.right, rc.bottom // 2)
            bottom = wintypes.RECT(0, rc.bottom // 2, rc.right, rc.bottom)
            user32.FillRect(hdc, ctypes.byref(top), self._brushes[0])
            user32.FillRect(hdc, ctypes.byref(bottom), self._brushes[1 + self._tick % 2])
            user32.EndPaint(hwnd, ctypes.byref(ps))
            return 0
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self) -> None:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2: physical pixels
        hinst = kernel32.GetModuleHandleW(None)
        with _reg_lock:
            if not _registered:
                wc = WNDCLASSW()
                wc.lpfnWndProc = _DISPATCH
                wc.hInstance = hinst
                wc.lpszClassName = _CLASS
                wc.hCursor = None
                if user32.RegisterClassW(ctypes.byref(wc)):
                    _registered.append(True)
        rc = wintypes.RECT(0, 0, self.client_w, self.client_h)
        user32.AdjustWindowRectEx(ctypes.byref(rc), WS_OVERLAPPEDWINDOW, False, 0)
        _PENDING.append(self)
        hwnd = user32.CreateWindowExW(
            0, _CLASS, self.title, WS_OVERLAPPEDWINDOW | WS_VISIBLE,
            self.x, self.y, rc.right - rc.left, rc.bottom - rc.top, None, None, hinst, None,
        )  # fmt: skip
        if not hwnd:
            _PENDING.remove(self)
            self._ready.set()
            return
        self.hwnd = int(hwnd)
        user32.SetTimer(hwnd, 1, 15, None)
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        _WINDOWS.pop(self.hwnd, None)
        for b in self._brushes:
            gdi32.DeleteObject(b)


# One class-level WNDPROC routes messages to the TestWindow that owns the HWND.
_WINDOWS: dict[int, TestWindow] = {}
_PENDING: list[TestWindow] = []


@WNDPROC
def _DISPATCH(hwnd, msg, wparam, lparam):  # type: ignore[no-untyped-def]
    key = int(hwnd or 0)
    win = _WINDOWS.get(key)
    if win is None and _PENDING:
        win = _PENDING.pop()
        _WINDOWS[key] = win
    if win is not None:
        return win._wndproc(hwnd, msg, wparam, lparam)
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
