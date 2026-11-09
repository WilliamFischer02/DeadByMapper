"""Windows Graphics Capture of the game WINDOW (primary backend). Verified on Windows 11 (M1) against
``windows-capture`` 2.0.1 with stand-in windows; not yet against DBD itself (docs/CAPTURE.md).

Threads and data flow:
  WGC native thread --on_frame--> throttle -> client crop -> memcpy into a pooled buffer (FrameHandoff)
  converter thread  --take------> INTER_AREA downscale -> BGRA->BGR -> FrameMailbox.put (latest-only)
  supervisor thread: (re-)attach every ``poll_s``, refresh the crop geometry, publish ServiceHealth at
  most every ``health_interval_s``.

  * capture the game WINDOW by HWND (never the monitor), so other windows - incl. our pane - never
    appear; cursor capture off; yellow border off (Windows 11; older builds fall back with the border);
  * native pacing: ``minimum_update_interval`` (~0.75 x the target period) makes the compositor skip
    frames BEFORE the GPU->CPU readback; ``FrameThrottle`` trims the rest to ~``target_fps``;
  * the callback never blocks and never runs OpenCV (see capture/handoff.py for the crash that rule
    prevents): it copies and hands off;
  * failure policy: nothing raises into the app or the native callback; problems become ServiceHealth
    "degraded"/"down" with an actionable detail; a closed window is re-attached when it reappears.

Exclusive fullscreen: WGC window capture of an exclusive-fullscreen swapchain typically delivers
black or no frames -> health "degraded" recommending Windowed Fullscreen; DXGI duplication of the
monitor is the fallback (dxgi_source.py, docs/CAPTURE.md).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..core.bus import EventBus
from ..core.clock import Clock
from ..core.events import GameWindowChanged
from ..core.lifecycle import Service
from ..gamelink import win32_util
from .base import FrameMailbox
from .crop import CaptureGeometry, bgra_to_analysis, client_crop, is_black
from .frame import Frame
from .handoff import FrameHandoff
from .metrics import NS, BlackTracker, CaptureStats, FrameThrottle, StatsSnapshot, StatsWindow, assess_health

log = logging.getLogger(__name__)


@dataclass
class _Session:
    hwnd: int
    title: str
    control: Any = None
    capture: Any = None  # the WindowsCapture object; released deterministically in _detach
    closed: threading.Event = field(default_factory=threading.Event)
    stopping: bool = False
    border_disabled: bool = True
    native_pacing: bool = True


class WgcFrameSource(Service):
    name = "capture.wgc"
    poll_s = 2.0  # re-attach / window search period
    health_interval_s = 2.0  # ServiceHealth refresh period
    geometry_interval_s = 0.5  # client-area crop refresh period
    tick_s = 0.1

    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        *,
        window_title: str | None = win32_util.DBD_WINDOW_TITLE,
        hwnd: int | None = None,
        analysis_width: int = 960,
        target_fps: float = 30.0,
        matcher: win32_util.WindowMatcher | None = None,
        follow_tracker: bool = True,
    ) -> None:
        super().__init__(bus, clock)
        self.window_title = window_title
        self.hwnd = hwnd
        self.analysis_width = analysis_width
        self.target_fps = target_fps
        self.matcher = matcher or win32_util.WindowMatcher(
            titles=(window_title,) if window_title else (), exclude_pids=frozenset({os.getpid()})
        )
        self._mailbox = FrameMailbox()
        self.handoff = FrameHandoff()
        self.stats = CaptureStats()
        self._throttle = FrameThrottle(target_fps)
        self._black = BlackTracker()
        self._next_black_check = 0
        self._geom: CaptureGeometry | None = None
        self._session: _Session | None = None
        self._stop_evt = threading.Event()
        self._threads: list[threading.Thread] = []
        self._hint_hwnd: int | None = None
        self._last_report: tuple[str, str] | None = None
        self._wc: Any = None
        self.window: win32_util.WindowInfo | None = None  # attached window (probe/UI)
        self.last_window: StatsWindow | None = None  # last health measurement window
        if follow_tracker:
            bus.subscribe(GameWindowChanged, self._on_window_changed)

    @property
    def mailbox(self) -> FrameMailbox:
        return self._mailbox

    @property
    def attached(self) -> bool:
        return self._session is not None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if sys.platform != "win32":
            self.set_health("disabled", "Windows only")
            return
        try:
            from windows_capture import WindowsCapture  # type: ignore[import-not-found]
        except Exception as exc:  # ImportError, or a broken native module
            self.set_health("down", f"windows-capture unavailable ({exc}) - run: uv sync --extra windows")
            return
        self.start_with(WindowsCapture)

    def start_with(self, backend: Any) -> None:
        """Start with a ``windows_capture.WindowsCapture``-compatible class (tests inject a fake)."""
        self._wc = backend
        self._stop_evt.clear()
        self._threads = [
            threading.Thread(target=self._convert_loop, name="capture.wgc.convert", daemon=True),
            threading.Thread(target=self._supervise, name="capture.wgc.supervisor", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop_evt.set()
        self.handoff.wake()
        for t in self._threads:
            t.join(timeout=5.0)
        self._threads = []
        self._detach()
        self._last_report = None
        self.set_health("down", "stopped")

    # ------------------------------------------------------------------ bus (dispatcher thread, < 1 ms)
    def _on_window_changed(self, e: GameWindowChanged) -> None:
        self._hint_hwnd = e.hwnd

    # ------------------------------------------------------------------ supervisor thread
    def _supervise(self) -> None:
        next_find = 0
        next_geom = 0
        next_health = 0
        last_snap: StatsSnapshot | None = None
        while not self._stop_evt.is_set():
            try:
                now = time.perf_counter_ns()
                s = self._session
                if s is not None and self._session_dead(s):
                    self._detach()
                    self._report("down", "game window closed - waiting for it to reappear")
                    next_find = now + int(self.poll_s * NS)
                    s = None
                if s is None and now >= next_find:
                    next_find = now + int(self.poll_s * NS)
                    if self._try_attach():
                        last_snap = self.stats.snapshot(time.perf_counter_ns())
                        next_health = now + int(self.health_interval_s * NS)
                        next_geom = now + int(self.geometry_interval_s * NS)
                s = self._session
                if s is not None and now >= next_geom:
                    next_geom = now + int(self.geometry_interval_s * NS)
                    self._refresh_geometry(s.hwnd)
                if s is not None and now >= next_health and last_snap is not None:
                    next_health = now + int(self.health_interval_s * NS)
                    snap = self.stats.snapshot(now)
                    w = StatsWindow.between(last_snap, snap)
                    last_snap = snap
                    self.last_window = w
                    status, detail = assess_health(
                        w,
                        black_for_s=self._black.black_for_s(now),
                        minimized=bool(self.window and self.window.minimized),
                    )
                    if status == "ok" and not s.border_disabled:
                        detail += " (capture border could not be disabled on this Windows build)"
                    self._report(status, detail)
            except Exception as exc:  # degrade, never crash
                log.exception("WGC supervisor failed")
                self._report("degraded", f"capture supervisor error: {type(exc).__name__}: {exc}")
            self._stop_evt.wait(self.tick_s)
        self._detach()

    def _session_dead(self, s: _Session) -> bool:
        if s.closed.is_set() or not win32_util.is_window(s.hwnd):
            return True
        try:
            return bool(s.control is not None and s.control.is_finished())
        except Exception:
            return True

    def _resolve_window(self) -> win32_util.WindowInfo | None:
        if self.hwnd is not None:  # explicit HWND: only that window
            return win32_util.window_info(self.hwnd) if win32_util.is_window(self.hwnd) else None
        hint = self._hint_hwnd
        if hint and win32_util.is_window(hint):
            info = win32_util.window_info(hint)
            if info is not None:
                return info
        return win32_util.find_window(self.matcher)

    def _try_attach(self) -> bool:
        info = self._resolve_window()
        if info is None:
            self._report(
                "down", f"game window not found ({self.matcher.describe()}) - retrying every {self.poll_s:g} s"
            )
            return False
        self.window = info
        self._set_geometry(info)
        try:
            session = self._start_capture(info)
        except Exception as exc:
            log.warning("WGC start failed on hwnd %#x: %s", info.hwnd, exc)
            self._report("down", f"WGC could not capture {info.title!r}: {exc}"[:200])
            return False
        self._session = session
        self._report("ok", f"WGC attached to {info.title!r} ({info.exe or 'unknown exe'}), waiting for frames")
        return True

    def _start_capture(self, info: win32_util.WindowInfo) -> _Session:
        mui = max(int(1000.0 / self.target_fps * 0.75), 1) if self.target_fps > 0 else None
        attempts: list[dict[str, Any]] = [
            {"draw_border": False, "minimum_update_interval": mui},
            {"draw_border": None, "minimum_update_interval": mui},  # border toggle unsupported (Win10)
            {"draw_border": None, "minimum_update_interval": None},  # MinUpdateInterval unsupported
        ]
        last_exc: Exception | None = None
        for kw in attempts:
            session = _Session(
                hwnd=info.hwnd,
                title=info.title,
                border_disabled=kw["draw_border"] is False,
                native_pacing=kw["minimum_update_interval"] is not None,
            )
            try:
                cap = self._wc(cursor_capture=False, window_hwnd=info.hwnd, **kw)
                cap.frame_handler = self._make_frame_handler(session)
                cap.closed_handler = session.closed.set
                self._throttle.reset()
                self._black.reset()
                session.capture = cap
                session.control = cap.start_free_threaded()
                log.info("WGC attached to hwnd %#x %r (%s)", info.hwnd, info.title, kw)
                return session
            except Exception as exc:
                _release(session)
                last_exc = exc
                log.info("WGC start with %s failed: %s", kw, exc)
        assert last_exc is not None
        raise last_exc

    # ------------------------------------------------------------------ WGC native thread
    def _make_frame_handler(self, session: _Session):  # type: ignore[no-untyped-def]
        def on_frame_arrived(frame: Any, control: Any) -> None:
            self._on_frame(session, frame, control)

        return on_frame_arrived

    def _on_frame(self, session: _Session, frame: Any, control: Any) -> None:
        """Runs on the WGC capture thread. Must never raise, never block and never call OpenCV."""
        t0 = time.perf_counter_ns()
        try:
            self.stats.arrival()
            self.stats.cpu("wgc", threading.get_native_id(), time.thread_time_ns())
            if session.stopping:
                control.stop()
                return
            if not self._throttle.accept(t0):
                return
            ts = self.clock.now_ns()
            x0, y0, x1, y1 = client_crop(frame.width, frame.height, self._geom)
            self.handoff.copy_in(frame.frame_buffer[y0:y1, x0:x1], ts)
            t1 = time.perf_counter_ns()
            self.stats.callback(t1, t1 - t0)
        except Exception as exc:
            self.stats.error(f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ converter thread
    def _convert_loop(self) -> None:
        tid = threading.get_native_id()
        while not self._stop_evt.is_set():
            item = self.handoff.take(timeout=0.25)
            if item is None:
                continue
            buf, ts = item
            try:
                t0 = time.perf_counter_ns()
                h, w = buf.shape[:2]
                img, scale = bgra_to_analysis(buf, (0, 0, w, h), self.analysis_width)
                self._mailbox.put(Frame(ts, img, scale))
                t1 = time.perf_counter_ns()
                self.stats.delivery(t1, t1 - t0, (w, h), (img.shape[1], img.shape[0]))
                if t1 >= self._next_black_check:
                    self._next_black_check = t1 + NS
                    self._black.update(is_black(img), t1)
            except Exception as exc:
                self.stats.error(f"{type(exc).__name__}: {exc}")
            finally:
                self.handoff.release(buf)
                self.stats.cpu("convert", tid, time.thread_time_ns())

    # ------------------------------------------------------------------ helpers
    def _set_geometry(self, info: win32_util.WindowInfo) -> None:
        self._geom = CaptureGeometry(info.frame_rect, info.client_rect) if info.frame_rect else None

    def _refresh_geometry(self, hwnd: int) -> None:
        info = win32_util.window_info(hwnd, exe=self.window.exe if self.window else None)
        if info is not None:
            self.window = info
            self._set_geometry(info)

    def _detach(self) -> None:
        s, self._session = self._session, None
        if s is None:
            return
        s.stopping = True
        if s.control is not None:
            try:
                if not s.control.is_finished():
                    s.control.stop()
            except Exception:
                log.debug("stopping WGC session failed", exc_info=True)
        _release(s)
        self.handoff.clear()
        self._mailbox.clear()
        self._black.reset()

    def _report(self, status: str, detail: str) -> None:
        if (status, detail) == self._last_report:
            return
        self._last_report = (status, detail)
        self.set_health(status, detail)


def _release(s: _Session) -> None:
    """Drop the native capture objects now, on this thread, instead of whenever the cyclic GC runs.

    ``WindowsCapture`` and its native session form a reference cycle (native -> bound callback ->
    WindowsCapture -> native); breaking it frees the dead session deterministically, after its capture
    thread has finished.
    """
    cap, s.capture, s.control = s.capture, None, None
    if cap is not None:
        for attr in ("capture", "frame_handler", "closed_handler"):
            try:
                setattr(cap, attr, None)
            except Exception:
                log.debug("releasing WGC %s failed", attr, exc_info=True)
