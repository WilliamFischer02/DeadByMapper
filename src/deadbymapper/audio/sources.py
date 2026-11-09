"""Capture backends. Each source delivers mono float32 blocks at its native rate to a sink callback.

* ``DeviceLoopbackSource``: WASAPI loopback of a render endpoint via PyAudioWPatch (PortAudio fork).
  Captures EVERYTHING played on that device (Discord, music, ...), hence health "degraded".
* ``ProcessLoopbackSource``: WASAPI process loopback (``ActivateAudioInterfaceAsync`` +
  ``AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK``, Windows 10 2004+/11) of the DBD process tree only.

Both are pure observers of audio the player already hears. The sink runs on the driver/capture
thread: it must only copy + enqueue (``BlockQueue.put``); analysis happens elsewhere.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from .blocks import SampleFormat, downmix, to_float32

log = logging.getLogger(__name__)

# sink(mono_float32, status_flags) - flags != 0 means an xrun/discontinuity happened before this block
BlockSink = Callable[[np.ndarray, int], None]


@dataclass(frozen=True, slots=True)
class SourceInfo:
    kind: str  # "device" | "process"
    name: str
    sample_rate: int
    channels: int
    sample_format: SampleFormat


class AudioSource(Protocol):
    def open(self, sink: BlockSink) -> SourceInfo: ...

    def alive(self) -> bool: ...

    def close(self) -> None: ...


class NoLoopbackDeviceError(RuntimeError):
    pass


# ---------------------------------------------------------------------- device loopback (PyAudioWPatch)
def pick_loopback_device(devices: list[dict[str, Any]], wanted: str | None, default: dict[str, Any] | None) -> dict:
    """Choose a loopback device: ``wanted`` (case-insensitive substring of the name) or the default one."""
    loopbacks = [d for d in devices if d.get("isLoopbackDevice")]
    if wanted:
        w = wanted.lower()
        for d in loopbacks:
            if w in str(d.get("name", "")).lower():
                return d
        raise NoLoopbackDeviceError(f"no loopback device matches {wanted!r}")
    if default is not None:
        return default
    if loopbacks:
        return loopbacks[0]
    raise NoLoopbackDeviceError("no WASAPI loopback device found")


class DeviceLoopbackSource:
    kind = "device"

    def __init__(self, device_name: str | None = None) -> None:
        self.device_name = device_name
        self._pa: Any = None
        self._stream: Any = None
        self._error: str | None = None
        self.info: SourceInfo | None = None

    def open(self, sink: BlockSink) -> SourceInfo:
        if sys.platform != "win32":
            raise NoLoopbackDeviceError("Windows only")
        import pyaudiowpatch as pyaudio  # type: ignore[import-not-found]

        self._pa = pyaudio.PyAudio()
        try:
            try:
                default = self._pa.get_default_wasapi_loopback()
            except (OSError, LookupError):
                default = None
            dev = pick_loopback_device(list(self._pa.get_loopback_device_info_generator()), self.device_name, default)
            sr = int(dev["defaultSampleRate"])
            ch = max(1, int(dev["maxInputChannels"]))
            stream = None
            last_exc: Exception | None = None
            for fmt_name, pa_fmt in (("float32", pyaudio.paFloat32), ("int16", pyaudio.paInt16)):
                fmt: SampleFormat = fmt_name  # type: ignore[assignment]
                try:
                    stream = self._pa.open(
                        format=pa_fmt,
                        channels=ch,
                        rate=sr,
                        input=True,
                        input_device_index=int(dev["index"]),
                        frames_per_buffer=pyaudio.paFramesPerBufferUnspecified,
                        stream_callback=self._make_callback(sink, fmt, ch, pyaudio.paContinue),
                    )
                    break
                except OSError as exc:  # format not accepted -> try the next one
                    last_exc = exc
            if stream is None:
                raise NoLoopbackDeviceError(f"cannot open {dev['name']}: {last_exc}")
            self._stream = stream
            name = str(dev["name"]).replace(" [Loopback]", "")
            self.info = SourceInfo("device", name, sr, ch, fmt)
            return self.info
        except Exception:
            self.close()
            raise

    def _make_callback(self, sink: BlockSink, fmt: SampleFormat, ch: int, cont: int) -> Callable[..., tuple]:
        def cb(in_data: bytes | None, frame_count: int, _time_info: dict, status: int) -> tuple:
            try:
                if in_data:
                    sink(downmix(to_float32(in_data, fmt), ch), int(status))
            except Exception as exc:  # never let an exception abort the stream
                self._error = f"{type(exc).__name__}: {exc}"
            return (None, cont)

        return cb

    def alive(self) -> bool:
        try:
            return self._stream is not None and bool(self._stream.is_active())
        except Exception:
            return False

    def close(self) -> None:
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop_stream()
            with contextlib.suppress(Exception):
                self._stream.close()
            self._stream = None
        if self._pa is not None:
            with contextlib.suppress(Exception):
                self._pa.terminate()
            self._pa = None


# ---------------------------------------------------------------------- process loopback (ctypes COM)
class ProcessLoopbackSource:
    """Captures the render audio of process ``pid`` and its children (INCLUDE_TARGET_PROCESS_TREE).

    The audio engine converts the process mix to the format we ask for (AUTOCONVERTPCM): 16-bit PCM,
    stereo, ``sample_rate``. Event-driven capture on a dedicated MTA thread.
    """

    kind = "process"

    def __init__(self, pid: int, sample_rate: int = 48000, channels: int = 2, name: str = "") -> None:
        self.pid = int(pid)
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.name = name or f"pid {pid}"
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: str | None = None
        self._running = False
        self.info: SourceInfo | None = None
        self.discontinuities = 0

    def open(self, sink: BlockSink) -> SourceInfo:
        if sys.platform != "win32":
            raise OSError("process loopback: Windows only")
        self._stop.clear()
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, args=(sink,), name="audio.process_loopback", daemon=True)
        self._thread.start()
        if not self._ready.wait(5.0):
            self.close()
            raise OSError("process loopback: activation timed out")
        if self._error:
            err = self._error
            self.close()
            raise OSError(err)
        self.info = SourceInfo("process", self.name, self.sample_rate, self.channels, "int16")
        return self.info

    def alive(self) -> bool:
        return self._running and self._error is None

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._running = False

    def _run(self, sink: BlockSink) -> None:  # pragma: no cover - Windows hardware path
        import ctypes
        from ctypes import wintypes

        from . import wasapi_com as wc

        client = ctypes.c_void_p()
        capture = ctypes.c_void_p()
        event = None
        must_uninit = False
        started = False
        try:
            must_uninit = wc.co_initialize_mta()
            client = wc.activate_process_loopback_client(self.pid)
            wfx = wc.WAVEFORMATEX()
            wfx.wFormatTag = wc.WAVE_FORMAT_PCM
            wfx.nChannels = self.channels
            wfx.nSamplesPerSec = self.sample_rate
            wfx.wBitsPerSample = 16
            wfx.nBlockAlign = self.channels * 2
            wfx.nAvgBytesPerSec = self.sample_rate * wfx.nBlockAlign
            wfx.cbSize = 0
            flags = (
                wc.AUDCLNT_STREAMFLAGS_LOOPBACK
                | wc.AUDCLNT_STREAMFLAGS_EVENTCALLBACK
                | wc.AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
                | wc.AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY
            )
            hns_buffer = 200 * 10_000  # 200 ms engine buffer; we drain every ~10 ms
            wc.check(
                wc.vcall(
                    client,
                    wc.AUDIOCLIENT_INITIALIZE,
                    [
                        ctypes.c_int,
                        wintypes.DWORD,
                        ctypes.c_longlong,
                        ctypes.c_longlong,
                        ctypes.c_void_p,
                        ctypes.c_void_p,
                    ],
                    wc.AUDCLNT_SHAREMODE_SHARED,
                    flags,
                    hns_buffer,
                    0,
                    ctypes.addressof(wfx),
                    None,
                ),
                "IAudioClient::Initialize",
            )
            event = wc.kernel32.CreateEventW(None, False, False, None)
            wc.check(
                wc.vcall(client, wc.AUDIOCLIENT_SET_EVENT_HANDLE, [ctypes.c_void_p], event),
                "IAudioClient::SetEventHandle",
            )
            iid = wc.GUID.from_str(wc.IID_IAudioCaptureClient)
            wc.check(
                wc.vcall(
                    client,
                    wc.AUDIOCLIENT_GET_SERVICE,
                    [ctypes.POINTER(wc.GUID), ctypes.POINTER(ctypes.c_void_p)],
                    ctypes.byref(iid),
                    ctypes.byref(capture),
                ),
                "IAudioClient::GetService(IAudioCaptureClient)",
            )
            wc.check(wc.vcall(client, wc.AUDIOCLIENT_START, []), "IAudioClient::Start")
            started = True
            self._running = True
            self._ready.set()
            self._capture_loop(sink, capture, event)
        except Exception as exc:
            self._error = f"process loopback: {exc}"
            log.warning("%s", self._error)
        finally:
            self._running = False
            self._ready.set()
            if started:
                with contextlib.suppress(Exception):
                    wc.vcall(client, wc.AUDIOCLIENT_STOP, [])
            wc.release(capture)
            wc.release(client)
            if event:
                wc.kernel32.CloseHandle(event)
            if must_uninit:
                wc.ole32.CoUninitialize()

    def _capture_loop(self, sink: BlockSink, capture: Any, event: Any) -> None:  # pragma: no cover
        import ctypes
        from ctypes import wintypes

        from . import wasapi_com as wc

        wc.kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        wc.kernel32.WaitForSingleObject.restype = wintypes.DWORD
        packet = ctypes.c_uint32()
        data = ctypes.c_void_p()
        frames = ctypes.c_uint32()
        bflags = wintypes.DWORD()
        bpf = self.channels * 2
        get_buffer_args = [
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        while not self._stop.is_set():
            wc.kernel32.WaitForSingleObject(event, 100)
            while True:
                wc.check(
                    wc.vcall(
                        capture,
                        wc.CAPTURE_GET_NEXT_PACKET_SIZE,
                        [ctypes.POINTER(ctypes.c_uint32)],
                        ctypes.byref(packet),
                    ),
                    "GetNextPacketSize",
                )
                if packet.value == 0:
                    break
                wc.check(
                    wc.vcall(
                        capture,
                        wc.CAPTURE_GET_BUFFER,
                        get_buffer_args,
                        ctypes.byref(data),
                        ctypes.byref(frames),
                        ctypes.byref(bflags),
                        None,
                        None,
                    ),
                    "IAudioCaptureClient::GetBuffer",
                )
                n = int(frames.value)
                status = 0
                if bflags.value & wc.AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY:
                    self.discontinuities += 1
                    status = 1
                if bflags.value & wc.AUDCLNT_BUFFERFLAGS_SILENT or not data.value:
                    mono = np.zeros(n, dtype=np.float32)
                else:
                    raw = ctypes.string_at(data.value, n * bpf)
                    mono = downmix(to_float32(raw, "int16"), self.channels)
                wc.check(
                    wc.vcall(capture, wc.CAPTURE_RELEASE_BUFFER, [ctypes.c_uint32], n),
                    "IAudioCaptureClient::ReleaseBuffer",
                )
                if n:
                    sink(mono, status)
