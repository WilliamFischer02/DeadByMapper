"""Minimal ctypes/COM plumbing for WASAPI (Windows only; importable everywhere).

Used by:
  * ``process_loopback`` - ``ActivateAudioInterfaceAsync`` with
    ``AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK`` (Windows 10 2004+/11) -> ``IAudioClient`` ->
    ``IAudioCaptureClient``. Captures only the audio a given process tree RENDERS (what the player hears
    from that app). No access to the target process itself: no OpenProcess, no memory, no hooks.
  * ``default_render_endpoint_id`` - ``IMMDeviceEnumerator::GetDefaultAudioEndpoint`` so device
    loopback can notice when the user switches speakers/headset.

Only the vtable slots we call are described. Indices come from the SDK headers (audioclient.h,
mmdeviceapi.h); IUnknown occupies slots 0-2 of every interface.
"""

from __future__ import annotations

import sys
import threading
import uuid
from typing import Any

IS_WINDOWS = sys.platform == "win32"

# ---------------------------------------------------------------------- constants (SDK headers)
VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"
AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0
VT_BLOB = 65
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY = 0x1
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
WAVE_FORMAT_PCM = 1
COINIT_MULTITHREADED = 0x0
CLSCTX_ALL = 0x17
E_NOINTERFACE = -2147467262  # 0x80004002
S_OK = 0
RPC_E_CHANGED_MODE = -2147417850  # 0x80010106
E_RENDER, E_CONSOLE = 0, 0  # EDataFlow.eRender, ERole.eConsole

IID_IUnknown = "{00000000-0000-0000-C000-000000000046}"
IID_IAgileObject = "{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}"
IID_IActivateAudioInterfaceCompletionHandler = "{41D949AB-9862-444A-80F6-C261334DA5EB}"
IID_IAudioClient = "{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}"
IID_IAudioCaptureClient = "{C8ADBD64-E71E-48A0-A4DE-185C395CD317}"
CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"

# vtable slots
IUNKNOWN_RELEASE = 2
ASYNCOP_GET_ACTIVATE_RESULT = 3
AUDIOCLIENT_INITIALIZE = 3
AUDIOCLIENT_GET_BUFFER_SIZE = 4
AUDIOCLIENT_START = 10
AUDIOCLIENT_STOP = 11
AUDIOCLIENT_SET_EVENT_HANDLE = 13
AUDIOCLIENT_GET_SERVICE = 14
CAPTURE_GET_BUFFER = 3
CAPTURE_RELEASE_BUFFER = 4
CAPTURE_GET_NEXT_PACKET_SIZE = 5
ENUM_GET_DEFAULT_AUDIO_ENDPOINT = 4
MMDEVICE_GET_ID = 5


def process_loopback_supported() -> tuple[bool, str]:
    """(supported, reason). Process loopback needs Windows 10 2004 (build 19041) or newer."""
    if not IS_WINDOWS:
        return False, "not Windows"
    build = sys.getwindowsversion().build  # type: ignore[attr-defined]
    if build < 19041:
        return False, f"Windows build {build} < 19041 (needs Windows 10 2004+/11)"
    return True, f"Windows build {build}"


if IS_WINDOWS:  # pragma: no cover - exercised on the user's PC / Windows CI
    import ctypes
    from ctypes import wintypes

    ole32 = ctypes.OleDLL("ole32")
    _ole32_void = ctypes.WinDLL("ole32")
    co_task_mem_free = _ole32_void.CoTaskMemFree
    co_task_mem_free.argtypes = [ctypes.c_void_p]
    co_task_mem_free.restype = None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateEventW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

        @classmethod
        def from_str(cls, s: str) -> GUID:
            g = cls()
            raw = uuid.UUID(s).bytes_le
            ctypes.memmove(ctypes.byref(g), raw, 16)
            return g

        def __eq__(self, other: object) -> bool:
            return isinstance(other, GUID) and bytes(self) == bytes(other)

        def __hash__(self) -> int:
            return hash(bytes(self))

    class WAVEFORMATEX(ctypes.Structure):
        _pack_ = 1
        _fields_ = [
            ("wFormatTag", wintypes.WORD),
            ("nChannels", wintypes.WORD),
            ("nSamplesPerSec", wintypes.DWORD),
            ("nAvgBytesPerSec", wintypes.DWORD),
            ("nBlockAlign", wintypes.WORD),
            ("wBitsPerSample", wintypes.WORD),
            ("cbSize", wintypes.WORD),
        ]

    class AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS(ctypes.Structure):  # noqa: N801 - SDK name
        _fields_ = [("TargetProcessId", wintypes.DWORD), ("ProcessLoopbackMode", ctypes.c_int)]

    class AUDIOCLIENT_ACTIVATION_PARAMS(ctypes.Structure):  # noqa: N801 - SDK name
        _fields_ = [("ActivationType", ctypes.c_int), ("ProcessLoopbackParams", AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS)]

    class BLOB(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.ULONG), ("pBlobData", ctypes.c_void_p)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [
            ("vt", wintypes.USHORT),
            ("wReserved1", wintypes.WORD),
            ("wReserved2", wintypes.WORD),
            ("wReserved3", wintypes.WORD),
            ("blob", BLOB),
        ]

    HRESULT_T = ctypes.c_long

    def check(hr: int, what: str) -> None:
        if hr < 0:
            raise OSError(f"{what} failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")

    def vcall(ptr: Any, index: int, argtypes: list[Any], *args: Any) -> int:
        """Call vtable slot ``index`` of COM pointer ``ptr`` (a c_void_p or int); returns the HRESULT."""
        p = ptr.value if isinstance(ptr, ctypes.c_void_p) else ptr
        vtbl = ctypes.cast(p, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        fn = ctypes.WINFUNCTYPE(HRESULT_T, ctypes.c_void_p, *argtypes)(vtbl[index])
        return int(fn(p, *args))

    def release(ptr: ctypes.c_void_p | None) -> None:
        if ptr is not None and ptr.value:
            vtbl = ctypes.cast(ptr.value, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
            ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtbl[IUNKNOWN_RELEASE])(ptr.value)
            ptr.value = None

    def co_initialize_mta() -> bool:
        """Returns True when the caller must CoUninitialize."""
        try:
            ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
            return True
        except OSError as exc:  # already initialised in another mode on this thread
            if getattr(exc, "winerror", None) == RPC_E_CHANGED_MODE:
                return False
            raise

    # ------------------------------------------------------------------ completion handler (COM object)
    _QI_T = ctypes.WINFUNCTYPE(HRESULT_T, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
    _REF_T = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)
    _COMPLETED_T = ctypes.WINFUNCTYPE(HRESULT_T, ctypes.c_void_p, ctypes.c_void_p)

    class _HandlerVtbl(ctypes.Structure):
        _fields_ = [
            ("QueryInterface", _QI_T),
            ("AddRef", _REF_T),
            ("Release", _REF_T),
            ("ActivateCompleted", _COMPLETED_T),
        ]

    class _HandlerObj(ctypes.Structure):
        _fields_ = [("lpVtbl", ctypes.POINTER(_HandlerVtbl))]

    class CompletionHandler:
        """IActivateAudioInterfaceCompletionHandler + IAgileObject implemented in ctypes.

        Lifetime is owned by Python (the object is kept alive by this instance until activation
        finished), so AddRef/Release only count. ActivateCompleted just sets an event; the result is
        read on the caller's thread via GetActivateResult (the operation object is agile).
        """

        _ok_iids = frozenset(
            GUID.from_str(s) for s in (IID_IUnknown, IID_IAgileObject, IID_IActivateAudioInterfaceCompletionHandler)
        )

        def __init__(self) -> None:
            self.done = threading.Event()
            self._refs = 1

            def qi(this: int, riid: Any, ppv: Any) -> int:
                if riid.contents in self._ok_iids:
                    ppv[0] = this
                    self._refs += 1
                    return S_OK
                ppv[0] = None
                return E_NOINTERFACE

            def add_ref(_this: int) -> int:
                self._refs += 1
                return self._refs

            def rel(_this: int) -> int:
                self._refs -= 1
                return max(self._refs, 1)

            def completed(_this: int, _op: int) -> int:
                self.done.set()
                return S_OK

            self._vtbl = _HandlerVtbl(_QI_T(qi), _REF_T(add_ref), _REF_T(rel), _COMPLETED_T(completed))
            self._obj = _HandlerObj(ctypes.pointer(self._vtbl))

        @property
        def ptr(self) -> int:
            return ctypes.addressof(self._obj)

    _mmdevapi = ctypes.OleDLL("Mmdevapi")
    _activate = _mmdevapi.ActivateAudioInterfaceAsync
    _activate.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(GUID),
        ctypes.POINTER(PROPVARIANT),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]

    _PARKED: list[tuple[Any, Any]] = []  # handlers whose activation timed out (see below)

    def activate_process_loopback_client(pid: int, timeout_s: float = 3.0) -> ctypes.c_void_p:
        """IAudioClient for the render stream of process tree ``pid`` (caller releases it). MTA thread."""
        params = AUDIOCLIENT_ACTIVATION_PARAMS()
        params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
        params.ProcessLoopbackParams.TargetProcessId = int(pid)
        params.ProcessLoopbackParams.ProcessLoopbackMode = PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE
        pv = PROPVARIANT()
        pv.vt = VT_BLOB
        pv.blob.cbSize = ctypes.sizeof(params)
        pv.blob.pBlobData = ctypes.addressof(params)
        handler = CompletionHandler()
        op = ctypes.c_void_p()
        iid = GUID.from_str(IID_IAudioClient)
        _activate(
            VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK, ctypes.byref(iid), ctypes.byref(pv), handler.ptr, ctypes.byref(op)
        )
        try:
            if not handler.done.wait(timeout_s):
                # The audio service may still call ActivateCompleted later: parking the handler (and the
                # operation) keeps the ctypes vtable thunks alive, so a late callback cannot hit freed memory.
                _PARKED.append((handler, op))
                op = ctypes.c_void_p()  # nothing to release here any more
                raise TimeoutError("ActivateAudioInterfaceAsync did not complete")
            hr_act = HRESULT_T()
            unk = ctypes.c_void_p()
            check(
                vcall(
                    op,
                    ASYNCOP_GET_ACTIVATE_RESULT,
                    [ctypes.POINTER(HRESULT_T), ctypes.POINTER(ctypes.c_void_p)],
                    ctypes.byref(hr_act),
                    ctypes.byref(unk),
                ),
                "GetActivateResult",
            )
            check(hr_act.value, "process loopback activation")
            if not unk.value:
                raise OSError("process loopback activation returned no interface")
            return unk  # activated with riid = IID_IAudioClient, so this IS the IAudioClient
        finally:
            if op.value:
                release(op)
            del handler  # keep alive until here (or parked above)

    def default_render_endpoint_id() -> str | None:
        """Endpoint id string of the default console render device (None if there is none)."""
        enum = ctypes.c_void_p()
        dev = ctypes.c_void_p()
        must_uninit = co_initialize_mta()
        try:
            clsid = GUID.from_str(CLSID_MMDeviceEnumerator)
            iid = GUID.from_str(IID_IMMDeviceEnumerator)
            ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_ALL, ctypes.byref(iid), ctypes.byref(enum))
            hr = vcall(
                enum,
                ENUM_GET_DEFAULT_AUDIO_ENDPOINT,
                [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
                E_RENDER,
                E_CONSOLE,
                ctypes.byref(dev),
            )
            if hr < 0 or not dev.value:
                return None
            sid = ctypes.c_void_p()
            check(vcall(dev, MMDEVICE_GET_ID, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(sid)), "IMMDevice::GetId")
            try:
                return ctypes.wstring_at(sid.value) if sid.value else None
            finally:
                co_task_mem_free(sid)
        finally:
            release(dev)
            release(enum)
            if must_uninit:
                ole32.CoUninitialize()

else:

    def default_render_endpoint_id() -> str | None:
        return None
