"""Capture layer: pure logic everywhere, real WGC capture of our own test window on Windows."""

from __future__ import annotations

import math
import threading
import time
from typing import ClassVar

import numpy as np
import pytest

from deadbymapper.capture.base import FrameMailbox
from deadbymapper.capture.crop import (
    CaptureGeometry,
    analysis_size,
    bgra_to_analysis,
    client_crop,
    downscale_plan,
    is_black,
)
from deadbymapper.capture.frame import Frame
from deadbymapper.capture.handoff import FrameHandoff
from deadbymapper.capture.metrics import (
    NS,
    BlackTracker,
    CaptureStats,
    FrameThrottle,
    StatsWindow,
    assess_health,
    format_ok_detail,
    percentile,
)
from deadbymapper.core.bus import EventBus
from deadbymapper.core.clock import ManualClock, MonotonicClock
from deadbymapper.core.events import GameProcessChanged, GameWindowChanged, InputFocusChanged, ServiceHealth
from deadbymapper.gamelink import win32_util
from deadbymapper.gamelink.win32_util import (
    WS_CAPTION,
    WS_THICKFRAME,
    WindowInfo,
    WindowMatcher,
    classify_display_mode,
)


# --------------------------------------------------------------------------- crop / scale maths
def test_client_crop_captioned_window_measured_on_win11():
    # Measured: 1280x720 client at (100,100); DWM frame bounds 1282x752 at (99,69).
    geom = CaptureGeometry(frame_rect=(99, 69, 1282, 752), client_rect=(100, 100, 1280, 720))
    assert client_crop(1282, 752, geom) == (1, 31, 1281, 751)


def test_client_crop_windowed_fullscreen_is_identity():
    geom = CaptureGeometry(frame_rect=(0, 0, 1920, 1080), client_rect=(0, 0, 1920, 1080))
    assert client_crop(1920, 1080, geom) == (0, 0, 1920, 1080)


def test_client_crop_negative_screen_coords_secondary_monitor():
    geom = CaptureGeometry(frame_rect=(-1901, 29, 2562, 1446), client_rect=(-1900, 60, 2560, 1414))
    assert client_crop(2562, 1446, geom) == (1, 31, 2561, 1445)


def test_client_crop_unknown_geometry_and_degenerate_cases():
    assert client_crop(800, 600, None) == (0, 0, 800, 600)
    # resize in flight: frame still the old (smaller) size -> clamp, never index out of range
    geom = CaptureGeometry(frame_rect=(0, 0, 1282, 752), client_rect=(1, 31, 1280, 720))
    assert client_crop(1000, 600, geom) == (1, 31, 1000, 600)
    # client outside the frame -> whole frame instead of an empty crop
    geom = CaptureGeometry(frame_rect=(0, 0, 100, 100), client_rect=(500, 500, 50, 50))
    assert client_crop(100, 100, geom) == (0, 0, 100, 100)
    assert client_crop(0, 0, geom) == (0, 0, 0, 0)


@pytest.mark.parametrize(
    ("src", "width", "expected"),
    [
        ((1280, 720), 960, (960, 540, 0.75)),
        ((1920, 1080), 960, (960, 540, 0.5)),
        ((2560, 1440), 960, (960, 540, 0.375)),
        ((800, 450), 960, (800, 450, 1.0)),  # never upscale
        ((960, 540), 960, (960, 540, 1.0)),
        ((1280, 720), 0, (1280, 720, 1.0)),
        ((0, 0), 960, (0, 0, 1.0)),
    ],
)
def test_analysis_size(src, width, expected):
    assert analysis_size(*src, width) == expected


def test_downscale_plan_integer_prefilter_and_exact_scale():
    assert downscale_plan(1920, 1080, 960) == (2, (1920, 1080), (960, 540), 0.5)
    assert downscale_plan(1280, 720, 960) == (1, (1280, 720), (960, 540), 0.75)
    k, used, out, scale = downscale_plan(2561, 1441, 960)
    assert k == 2 and used == (2560, 1440) and out[0] == 960
    assert scale == 960 / 2560  # x_client = x_analysis / scale exactly over the sampled area
    assert downscale_plan(3840, 2160, 960)[0] == 4
    assert downscale_plan(800, 450, 960) == (1, (800, 450), (800, 450), 1.0)


def _bgra(h: int, w: int, bgr: tuple[int, int, int]) -> np.ndarray:
    img = np.empty((h, w, 4), np.uint8)
    img[:, :, :3] = bgr
    img[:, :, 3] = 255
    return img


@pytest.mark.parametrize("size", [(720, 1280), (1080, 1920), (1440, 2560), (450, 800)])
def test_bgra_to_analysis_shape_order_and_ownership(size):
    h, w = size
    frame = _bgra(h + 32, w + 2, (10, 20, 30))  # with a fake title bar + border
    frame[:31] = (255, 255, 255, 255)
    view = frame[:, :]  # strided parent, like the native buffer
    img, scale = bgra_to_analysis(view, (1, 31, w + 1, h + 31), 960)
    ow, oh, s = analysis_size(w, h, 960)
    assert img.shape == (oh, ow, 3) and img.dtype == np.uint8
    assert img.flags["C_CONTIGUOUS"] and not np.shares_memory(img, frame)
    assert math.isclose(scale, s, rel_tol=2e-3)
    assert (img[0, 0] == (10, 20, 30)).all()  # BGR kept, no title-bar pixels leaked in
    assert (img[-1, -1] == (10, 20, 30)).all()
    frame[:] = 0  # the analysis image must not alias the source
    assert (img[0, 0] == (10, 20, 30)).all()


def test_bgra_to_analysis_area_average():
    # 2x2 checker of 0/200 averages to 100 under INTER_AREA at exactly 1/2 scale.
    src = np.zeros((4, 4, 4), np.uint8)
    src[0::2, 0::2, :3] = 200
    src[1::2, 1::2, :3] = 200
    img, scale = bgra_to_analysis(src, (0, 0, 4, 4), 2)
    assert scale == 0.5 and img.shape == (2, 2, 3)
    assert (img == 100).all()


def test_is_black():
    assert is_black(np.zeros((540, 960, 3), np.uint8))
    assert is_black(np.full((540, 960, 3), 3, np.uint8))
    dark = np.full((540, 960, 3), 3, np.uint8)
    dark[100:140, 100:400] = 200  # a HUD element
    assert not is_black(dark)
    assert is_black(np.zeros((0, 0, 3), np.uint8))


# --------------------------------------------------------------------------- mailbox + hand-off
def _frame(i: int) -> Frame:
    return Frame(ts_ns=i, image=np.zeros((2, 2, 3), np.uint8))


def test_mailbox_latest_only_and_drop_counting():
    mb = FrameMailbox()
    assert mb.latest() is None
    for i in range(3):
        mb.put(_frame(i))
    f = mb.latest()
    assert f is not None and f.ts_ns == 2 and f.seq == 3
    assert (mb.puts, mb.dropped, mb.reads) == (3, 2, 1)
    assert mb.latest() is f and mb.reads == 1  # re-reading the same frame is not a new read
    mb.put(_frame(3))  # previous one was read -> not a drop
    assert mb.dropped == 2
    mb.put(_frame(4))  # frame 3 never read -> drop
    assert mb.dropped == 3


def test_mailbox_wait_newer_and_clear():
    mb = FrameMailbox()
    assert mb.wait_newer(0, timeout=0.01) is None
    threading.Timer(0.05, lambda: mb.put(_frame(7))).start()
    f = mb.wait_newer(0, timeout=2.0)
    assert f is not None and f.ts_ns == 7
    assert mb.wait_newer(f.seq, timeout=0.01) is None
    mb.clear()
    assert mb.latest() is None


def test_handoff_copies_is_latest_only_and_recycles_buffers():
    ho = FrameHandoff()
    src = np.full((4, 6, 4), 9, np.uint8)
    ho.copy_in(src[1:3, 1:5], 100)  # strided view
    src[:] = 0  # the published copy must not alias the source
    ho.copy_in(np.full((2, 4, 4), 5, np.uint8), 200)
    assert ho.dropped == 1 and ho.published == 2
    item = ho.take(timeout=0.01)
    assert item is not None
    buf, ts = item
    assert ts == 200 and (buf == 5).all()
    assert ho.take(timeout=0.01) is None
    ho.release(buf)
    ho.copy_in(np.full((2, 4, 4), 1, np.uint8), 300)
    buf2, _ = ho.take(timeout=0.01)  # type: ignore[misc]
    assert (buf2 == 1).all()
    assert buf2 is buf or buf2.shape == buf.shape  # recycled from the pool


def test_handoff_copy_of_dropped_frame_is_correct():
    ho = FrameHandoff()
    a = np.full((2, 2, 4), 1, np.uint8)
    ho.copy_in(a, 1)
    ho.copy_in(np.full((2, 2, 4), 2, np.uint8), 2)  # recycles the dropped buffer
    ho.copy_in(np.full((2, 2, 4), 3, np.uint8), 3)
    buf, ts = ho.take(0.01)  # type: ignore[misc]
    assert ts == 3 and (buf == 3).all()


# --------------------------------------------------------------------------- pacing + stats
def _accepted(fps_src: float, seconds: float, target: float = 30.0, jitter_ms: float = 0.0) -> int:
    th = FrameThrottle(target)
    rng = np.random.default_rng(1)
    n = int(fps_src * seconds)
    times = [i * NS / fps_src + rng.uniform(-jitter_ms, jitter_ms) * 1e6 for i in range(n)]
    return sum(th.accept(int(t)) for t in times)


def test_throttle_60hz_gives_exactly_every_other_frame():
    assert _accepted(60, 10) == 300
    assert _accepted(60, 10, jitter_ms=2.0) == 300


def test_throttle_144hz_and_120hz_average_target():
    assert 295 <= _accepted(144, 10) <= 305
    assert 295 <= _accepted(120, 10, jitter_ms=1.0) <= 305


def test_throttle_passes_slow_sources_and_restarts_after_gap():
    assert _accepted(20, 10) == 200
    th = FrameThrottle(30)
    assert th.accept(0)
    assert not th.accept(10_000_000)
    assert th.accept(5 * NS)  # long gap
    assert not th.accept(5 * NS + 10_000_000)  # schedule restarted from the gap, not from t=0
    assert FrameThrottle(0).accept(1)


def test_percentile():
    assert math.isnan(percentile([], 50))
    assert percentile([5.0], 95) == 5.0
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile(list(range(101)), 95) == 95


def test_stats_window_rates_percentiles_and_cpu():
    st = CaptureStats()
    a = st.snapshot(0)
    st.cpu("wgc", tid=7, thread_cpu_ns=1_000)  # first sample: baseline only
    for i in range(60):
        t = int(i * NS / 30)
        st.arrival()
        st.arrival()
        st.callback(t + 1, 500_000)
        st.delivery(t + 2, 1_000_000 + i * 1000, (1280, 720), (960, 540))
    st.cpu("wgc", tid=7, thread_cpu_ns=1_000 + 100_000_000)  # 0.1 s CPU
    st.cpu("convert", tid=8, thread_cpu_ns=50)  # baseline
    st.cpu("convert", tid=8, thread_cpu_ns=50 + 100_000_000)
    b = st.snapshot(2 * NS)
    w = StatsWindow.between(a, b, n_cpus=20)
    assert w.fps_in == 60 and w.fps == 30
    assert w.cb_p50_ms == 0.5 and 1.0 < w.conv_p50_ms < 1.06
    assert math.isclose(w.cpu_core_pct, 10.0) and math.isclose(w.cpu_pct, 0.5)
    assert w.src_size == (1280, 720) and w.out_size == (960, 540)


def test_stats_cpu_new_thread_starts_fresh_baseline():
    st = CaptureStats()
    st.cpu("wgc", 1, 10 * NS)
    st.cpu("wgc", 2, 50 * NS)  # re-attach: a new native thread with its own clock
    st.cpu("wgc", 2, 50 * NS + 1000)
    assert st.snapshot(0).cpu_ns == 1000


def test_stats_window_only_counts_samples_inside_the_window():
    st = CaptureStats()
    st.callback(5, 9_000_000)
    a = st.snapshot(10)
    st.callback(20, 1_000_000)
    w = StatsWindow.between(a, st.snapshot(NS))
    assert w.cb_p50_ms == 1.0 and w.cb_p95_ms == 1.0


# --------------------------------------------------------------------------- health
def _window(fps: float = 30.1, cpu: float = 1.8, errors: int = 0) -> StatsWindow:
    return StatsWindow(
        duration_s=2.0,
        fps_in=fps,
        fps=fps,
        cb_p50_ms=0.4,
        cb_p95_ms=0.9,
        conv_p50_ms=1.0,
        conv_p95_ms=1.3,
        cpu_pct=cpu,
        cpu_core_pct=cpu * 20,
        src_size=(1280, 720),
        out_size=(960, 540),
        errors=errors,
        last_error="ValueError: boom" if errors else "",
    )


def test_health_ok_detail_format():
    assert format_ok_detail(_window()) == "WGC 1280x720 -> 960x540, 30.1 fps, 1.8 % CPU"
    assert assess_health(_window()) == ("ok", "WGC 1280x720 -> 960x540, 30.1 fps, 1.8 % CPU")


def test_health_degraded_and_waiting_cases():
    status, detail = assess_health(_window(fps=2.0))
    assert status == "degraded" and "2.0 fps" in detail and "Windowed Fullscreen" in detail
    status, detail = assess_health(_window(), black_for_s=5.0)
    assert status == "degraded" and "black" in detail and "Windowed Fullscreen" in detail
    assert assess_health(_window(), black_for_s=1.0)[0] == "ok"  # short fades are fine
    status, detail = assess_health(_window(), minimized=True)
    assert status == "degraded" and "minimized" in detail
    assert assess_health(None) == ("ok", "WGC attached, waiting for frames")
    status, detail = assess_health(_window(fps=0.0, errors=3))
    assert status == "degraded" and "boom" in detail
    assert assess_health(_window(errors=1))[0] == "ok"  # a rare error while frames flow is not an outage


def test_black_tracker():
    bt = BlackTracker()
    assert bt.black_for_s(10 * NS) == 0.0
    bt.update(True, 1 * NS)
    bt.update(True, 2 * NS)
    assert bt.black_for_s(4 * NS) == 3.0
    bt.update(False, 5 * NS)
    assert bt.black_for_s(6 * NS) == 0.0


# --------------------------------------------------------------------------- display mode + matcher
WS_POPUP_VISIBLE = 0x94000000  # observed on DBD (UnrealWindow) in Windowed Fullscreen, 2026-11-08
WS_OVERLAPPEDWINDOW = 0x14CF0000


@pytest.mark.parametrize(
    ("client", "monitor", "style", "minimized", "expected"),
    [
        ((0, 0, 1920, 1080), (0, 0, 1920, 1080), WS_POPUP_VISIBLE, False, "windowed_fullscreen"),
        ((-1920, 82, 1920, 1080), (-1920, 82, 1920, 1080), 0x96000000, False, "windowed_fullscreen"),
        ((126, 120, 1280, 720), (0, 0, 1920, 1080), WS_OVERLAPPEDWINDOW, False, "windowed"),  # Notepad restored
        ((-2, -8, 1924, 1042), (0, 0, 1920, 1080), 0x15CF0000, False, "windowed"),  # Notepad maximized
        ((0, 0, 1920, 1080), (0, 0, 1920, 1080), WS_CAPTION, False, "windowed"),  # captioned cover
        ((0, 0, 1920, 1080), (0, 0, 1920, 1080), WS_THICKFRAME, False, "windowed"),
        ((100, 100, 1280, 720), (0, 0, 1920, 1080), WS_POPUP_VISIBLE, False, "windowed"),  # borderless, smaller
        ((-31994, -32000, 148, 22), (0, 0, 1920, 1080), 0x34CF0000, True, "unknown"),  # minimized
        ((0, 0, 0, 0), (0, 0, 1920, 1080), WS_POPUP_VISIBLE, False, "unknown"),
    ],
)
def test_classify_display_mode(client, monitor, style, minimized, expected):
    assert classify_display_mode(client, monitor, style, minimized=minimized) == expected


def test_display_mode_never_claims_exclusive_fullscreen():
    modes = {
        classify_display_mode((0, 0, w, h), (0, 0, w, h), s)
        for w, h in ((1920, 1080), (1280, 720))
        for s in (0, WS_POPUP_VISIBLE, WS_CAPTION, WS_OVERLAPPEDWINDOW)
    }
    assert "fullscreen" not in modes


def test_window_info_display_mode_property():
    info = WindowInfo(1, "UnrealWindow", (0, 0, 1920, 1080), (0, 0, 1920, 1080), (0, 0, 1920, 1080), True,
                      style=WS_POPUP_VISIBLE)  # fmt: skip
    assert info.display_mode == "windowed_fullscreen" and info.area == 1920 * 1080


def test_matcher_dbd_defaults():
    m = WindowMatcher(exclude_pids=frozenset({42}))
    exe = "DeadByDaylight-Win64-Shipping.exe"
    assert m.score("DeadByDaylight  ", exe, 1) == 3  # observed title has two trailing spaces
    assert m.score("deadbydaylight", None, 1) == 0  # exe unreadable but not an Unreal window: no
    assert m.score("deadbydaylight", None, 1, class_name="UnrealWindow") == 2  # exe unreadable, Unreal class
    assert m.score("", "deadbydaylight-wingdk-shipping.exe", 1) == 3
    # PRIVACY: a title-only match would make this the InputGate's game window and the capture target.
    assert m.score("DeadByDaylight - File Explorer", "explorer.exe", 1) == 0
    assert m.score("DeadByDaylight.log - Notepad", "notepad.exe", 7) == 0
    assert m.score("DeadByDaylight.log - Notepad", "notepad.exe", 7, class_name="Notepad") == 0
    assert m.score("DeadByDaylight.log - Notepad", None, 7, class_name="Notepad") == 0
    assert m.score("Dead by Daylight wiki - Firefox", "firefox.exe", 1) == 0
    assert m.score("DeadByDaylight", exe, 42) == 0  # never our own process
    assert "DeadByDaylight" in m.describe()


def test_app_matchers_require_the_game_exe():
    """The tracker's and WGC's default matchers must be the strict one (privacy regression guard)."""
    from deadbymapper.capture.wgc_source import WgcFrameSource
    from deadbymapper.core.bus import EventBus
    from deadbymapper.core.clock import ManualClock
    from deadbymapper.gamelink.window_tracker import WindowTracker

    bus, clock = EventBus(synchronous=True), ManualClock()
    for m in (WindowTracker(bus, clock).matcher, WgcFrameSource(bus, clock).matcher):
        assert m.require_exe and m.exe_names
        assert m.score("DeadByDaylight.log - Notepad", "notepad.exe", 7, class_name="Notepad") == 0


def test_matcher_contains_and_pid_modes():
    m = WindowMatcher(titles=("Notepad",), exe_names=(), title_mode="contains")
    assert m.score("Untitled - Notepad", "Notepad.exe", 5) == 1
    assert m.score("Notepad++", None, 5) == 2
    assert WindowMatcher(titles=("Notepad",), exe_names=()).score("Untitled - Notepad", None, 5) == 0
    p = WindowMatcher(pids=frozenset({99}))
    assert p.score("anything", None, 99) == 4 and p.score("DeadByDaylight", None, 1) == 0
    assert p.describe() == "pid 99"


# --------------------------------------------------------------------------- window tracker (faked Win32)
def _info(hwnd: int = 0x10, focused: bool = True, w: int = 1920, style: int = WS_POPUP_VISIBLE) -> WindowInfo:
    return WindowInfo(hwnd, "UnrealWindow", (0, 0, w, 1080), (0, 0, w, 1080), (0, 0, 1920, 1080), focused,
                      title="DeadByDaylight  ", pid=4242, style=style)  # fmt: skip


def test_window_tracker_pid_path_and_change_only(monkeypatch):
    from deadbymapper.gamelink import window_tracker as wt

    state = {"info": _info()}
    monkeypatch.setattr(wt.win32_util, "find_main_window", lambda pid: state["info"] if pid == 4242 else None)
    monkeypatch.setattr(wt.win32_util, "is_window", lambda h: state["info"] is not None and h == state["info"].hwnd)
    monkeypatch.setattr(wt.win32_util, "window_info", lambda h, **kw: state["info"])
    monkeypatch.setattr(wt.win32_util, "find_window", lambda m: None)
    bus = EventBus(synchronous=True)
    clock = ManualClock()
    seen: list = []
    bus.subscribe(GameWindowChanged, seen.append)
    bus.subscribe(InputFocusChanged, seen.append)
    tr = wt.WindowTracker(bus, clock)
    tr.loop_once()  # no PID, scan finds nothing
    bus.publish(GameProcessChanged(ts_ns=0, running=True, pid=4242))
    tr.loop_once()
    tr.loop_once()  # unchanged -> nothing new
    wins = [e for e in seen if isinstance(e, GameWindowChanged)]
    assert [e.hwnd for e in wins] == [0x10]
    assert wins[-1].display_mode == "windowed_fullscreen" and wins[-1].client_rect == (0, 0, 1920, 1080)
    state["info"] = _info(focused=False, w=1280, style=WS_OVERLAPPEDWINDOW)
    tr.loop_once()
    assert seen[-1] == InputFocusChanged(ts_ns=0, camera_control=False, reason="window focus")
    last = [e for e in seen if isinstance(e, GameWindowChanged)][-1]
    assert last.display_mode == "windowed" and not last.focused
    bus.publish(GameProcessChanged(ts_ns=0, running=False))
    assert [e for e in seen if isinstance(e, GameWindowChanged)][-1].hwnd is None


def test_window_tracker_scan_path_is_rate_limited(monkeypatch):
    from deadbymapper.gamelink import window_tracker as wt

    calls: list[int] = []
    monkeypatch.setattr(wt.win32_util, "find_window", lambda m: calls.append(1) or None)
    monkeypatch.setattr(wt.win32_util, "is_window", lambda h: False)
    bus = EventBus(synchronous=True)
    clock = ManualClock()
    tr = wt.WindowTracker(bus, clock)
    for _ in range(8):  # 2 s of 0.25 s ticks
        tr.loop_once()
        clock.advance_s(0.25)
    assert len(calls) == 1
    tr.loop_once()
    assert len(calls) == 2


# --------------------------------------------------------------------------- WgcFrameSource with a fake backend
class _FakeCapture:
    """Mimics windows_capture.WindowsCapture: a thread delivering BGRA frames at ``fps``."""

    instances: ClassVar[list[_FakeCapture]] = []
    fps = 60.0
    frame_size = (1282, 752)

    def __init__(self, cursor_capture=None, draw_border=None, minimum_update_interval=None, window_hwnd=None):  # type: ignore[no-untyped-def]
        self.kw = {"draw_border": draw_border, "mui": minimum_update_interval, "hwnd": window_hwnd}
        self.frame_handler = None
        self.closed_handler = None
        self.capture = object()
        self._stop = threading.Event()
        self._done = threading.Event()
        self._runner: threading.Thread | None = None
        _FakeCapture.instances.append(self)

    def start_free_threaded(self):  # type: ignore[no-untyped-def]
        w, h = self.frame_size
        buf = np.zeros((h, w, 4), np.uint8)
        buf[31:, 1:-1, :3] = (10, 20, 30)  # client area colour; title bar stays black

        class F:
            frame_buffer = buf
            width = w
            height = h

        def run() -> None:
            ctl = self
            while not self._stop.wait(1.0 / self.fps):
                if self.frame_handler is not None:
                    self.frame_handler(F, ctl)
            self._done.set()

        self._runner = threading.Thread(target=run, daemon=True)
        self._runner.start()
        return self

    def close_window(self) -> None:  # the captured window went away
        self._stop.set()
        self._done.wait(1)
        if self.closed_handler is not None:
            self.closed_handler()

    # CaptureControl / InternalCaptureControl API
    def stop(self) -> None:
        self._stop.set()
        if threading.current_thread() is not self._runner:
            self._done.wait(1)

    def is_finished(self) -> bool:
        return self._done.is_set()


def _wait_for(pred, timeout: float = 3.0) -> bool:  # type: ignore[no-untyped-def]
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_wgc_source_attach_throttle_crop_health_and_reattach(monkeypatch):
    from deadbymapper.capture import wgc_source as ws

    alive = {"hwnd": 0x10}
    info = WindowInfo(0x10, "UnrealWindow", (100, 100, 1280, 720), (92, 69, 1296, 759), (0, 0, 1920, 1080), True,
                      title="DeadByDaylight  ", exe="DeadByDaylight-Win64-Shipping.exe", style=WS_OVERLAPPEDWINDOW,
                      frame_rect=(99, 69, 1282, 752))  # fmt: skip
    monkeypatch.setattr(ws.win32_util, "find_window", lambda m: info if alive["hwnd"] else None)
    monkeypatch.setattr(ws.win32_util, "is_window", lambda h: alive["hwnd"] == h)
    monkeypatch.setattr(ws.win32_util, "window_info", lambda h, **kw: info if alive["hwnd"] == h else None)
    _FakeCapture.instances = []
    bus = EventBus(synchronous=True)
    health: list[ServiceHealth] = []
    bus.subscribe(ServiceHealth, health.append)
    src = ws.WgcFrameSource(bus, MonotonicClock(), analysis_width=960, follow_tracker=False)
    src.poll_s = 0.2
    src.health_interval_s = 0.5
    src.tick_s = 0.02
    src.start_with(_FakeCapture)
    try:
        assert _wait_for(lambda: src.mailbox.puts >= 10)
        cap = _FakeCapture.instances[0]
        assert cap.kw == {"draw_border": False, "mui": 25, "hwnd": 0x10}
        f = src.mailbox.latest()
        assert f is not None and f.image.shape == (540, 960, 3) and f.scale == 0.75
        assert (f.image == (10, 20, 30)).all()  # cropped to the client area: no title bar / border
        assert _wait_for(lambda: any(h.detail.startswith("WGC 1280x720 -> 960x540") for h in health))
        w = src.last_window
        assert w is not None and 20 <= w.fps <= 35 and w.fps_in > w.fps  # 60 Hz in, ~30 fps out

        alive["hwnd"] = 0  # window closes
        cap.close_window()
        assert _wait_for(lambda: health[-1].status == "down" and "closed" in health[-1].detail)
        assert src.mailbox.latest() is None  # no stale frames for consumers
        assert cap.capture is None and cap.frame_handler is None  # native objects released

        n = src.mailbox.puts
        alive["hwnd"] = 0x10  # it reappears
        assert _wait_for(lambda: len(_FakeCapture.instances) == 2 and src.mailbox.puts > n + 5)
        assert any("attached" in h.detail for h in health[-3:]) or health[-1].status == "ok"
    finally:
        src.stop()
    assert health[-1].status == "down" and health[-1].detail == "stopped"
    assert all(c.is_finished() for c in _FakeCapture.instances)


def test_wgc_source_reports_missing_window_and_start_failures(monkeypatch):
    from deadbymapper.capture import wgc_source as ws

    info = _info()
    monkeypatch.setattr(ws.win32_util, "find_window", lambda m: None)
    monkeypatch.setattr(ws.win32_util, "is_window", lambda h: True)
    monkeypatch.setattr(ws.win32_util, "window_info", lambda h, **kw: info)
    bus = EventBus(synchronous=True)
    health: list[ServiceHealth] = []
    bus.subscribe(ServiceHealth, health.append)
    src = ws.WgcFrameSource(bus, MonotonicClock())
    src.tick_s = 0.02
    src.poll_s = 0.1

    class Broken(_FakeCapture):
        def start_free_threaded(self):  # type: ignore[no-untyped-def]
            raise OSError("Failed to convert item to `GraphicsCaptureItem`")

    src.start_with(Broken)
    try:
        assert _wait_for(lambda: bool(health) and "not found" in health[-1].detail)
        assert health[-1].status == "down" and "DeadByDaylight" in health[-1].detail
        bus.publish(GameWindowChanged(ts_ns=0, hwnd=0x10, client_rect=(0, 0, 1920, 1080), focused=True))
        assert _wait_for(lambda: "could not capture" in health[-1].detail)
        assert health[-1].status == "down" and "GraphicsCaptureItem" in health[-1].detail
    finally:
        src.stop()


def test_wgc_source_without_backend_is_down_or_disabled(monkeypatch):
    import sys

    from deadbymapper.capture import wgc_source as ws

    bus = EventBus(synchronous=True)
    health: list[ServiceHealth] = []
    bus.subscribe(ServiceHealth, health.append)
    monkeypatch.setitem(sys.modules, "windows_capture", None)  # import fails
    src = ws.WgcFrameSource(bus, MonotonicClock(), follow_tracker=False)
    src.start()
    expected = "down" if sys.platform == "win32" else "disabled"
    assert health[-1].status == expected
    src.stop()


def test_dxgi_source_reports_down_honestly():
    from deadbymapper.capture.dxgi_source import DxgiFrameSource

    bus = EventBus(synchronous=True)
    health: list[ServiceHealth] = []
    bus.subscribe(ServiceHealth, health.append)
    DxgiFrameSource(bus, MonotonicClock()).start()
    assert health[-1].status in ("down", "disabled") and health[-1].detail


# --------------------------------------------------------------------------- real capture (Windows only)
@pytest.fixture
def test_window():
    from win32_test_window import TestWindow  # type: ignore[import-not-found]

    win = TestWindow("DBM capture test window", 640, 360)
    try:
        win.open()
    except Exception as exc:
        pytest.skip(f"cannot create a test window: {exc}")
    yield win
    win.close()


def _real_source(**kw):  # type: ignore[no-untyped-def]
    pytest.importorskip("windows_capture")
    from deadbymapper.capture.wgc_source import WgcFrameSource

    bus = EventBus(synchronous=True)
    health: list[ServiceHealth] = []
    bus.subscribe(ServiceHealth, health.append)
    src = WgcFrameSource(bus, MonotonicClock(), follow_tracker=False, **kw)
    src.poll_s = 0.25
    src.health_interval_s = 0.5
    return src, health


@pytest.mark.windows_only
def test_real_window_info_is_physical_client_rect(test_window):
    m = WindowMatcher(titles=("DBM capture test window",), exe_names=(), title_mode="contains")
    info = win32_util.find_window(m)
    assert info is not None and info.hwnd == test_window.hwnd
    assert info.client_rect[2:] == (640, 360)
    assert info.display_mode == "windowed"
    assert info.frame_rect is not None
    fx, fy, fw, fh = info.frame_rect
    cx, cy, cw, ch = info.client_rect
    assert fx <= cx and fy <= cy and fx + fw >= cx + cw and fy + fh >= cy + ch


@pytest.mark.windows_only
def test_real_wgc_capture_crops_client_and_reattaches(test_window):
    from win32_test_window import TOP, TestWindow  # type: ignore[import-not-found]

    m = WindowMatcher(titles=("DBM capture test window",), exe_names=(), title_mode="contains")
    src, health = _real_source(matcher=m, analysis_width=960)
    src.start()
    try:
        if not _wait_for(lambda: src.mailbox.puts >= 5, timeout=8.0):
            pytest.skip(f"WGC delivered no frames here: {[h.detail for h in health]}")
        f = src.mailbox.latest()
        assert f is not None and f.image.shape == (360, 640, 3) and f.scale == 1.0
        # top-left / top-right client pixels are the painted colour: no title bar or border leaked in
        b, g, r = (int(v) for v in f.image[2, 2])
        assert (r, g, b) == TOP
        b, g, r = (int(v) for v in f.image[2, -3])
        assert (r, g, b) == TOP
        assert _wait_for(lambda: health[-1].status == "ok" and health[-1].detail.startswith("WGC 640x360"), 4.0)

        # the window closes -> down; a new one with the same title appears -> re-attach (this sequence
        # crashed the process when OpenCV ran on the WGC thread; see capture/handoff.py)
        test_window.close()
        assert _wait_for(lambda: health[-1].status == "down", 4.0)
        with TestWindow("DBM capture test window", 320, 200):
            n = src.mailbox.puts
            assert _wait_for(lambda: src.mailbox.puts > n + 3, 8.0)
            f = src.mailbox.latest()
            assert f is not None and f.image.shape == (200, 320, 3)
    finally:
        src.stop()
    assert health[-1].detail == "stopped"


@pytest.mark.windows_only
def test_real_wgc_downscale_and_restart(test_window):
    src, health = _real_source(hwnd=test_window.hwnd, analysis_width=320)
    for _ in range(2):  # stop/start in one process must also be safe
        src.start()
        try:
            if not _wait_for(lambda: src.mailbox.latest() is not None, timeout=8.0):
                pytest.skip(f"WGC delivered no frames here: {[h.detail for h in health]}")
            f = src.mailbox.latest()
            assert f is not None and f.image.shape == (180, 320, 3) and f.scale == 0.5
        finally:
            src.stop()
