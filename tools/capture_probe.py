"""Capture probe: verify WGC capture of the game window on YOUR machine and measure its cost.

Run it WITH Dead by Daylight open (Windowed Fullscreen first, then Windowed), from the repo root:

    uv run python tools/capture_probe.py                       # find DBD by title/exe, 10 s
    uv run python tools/capture_probe.py --seconds 20 --save 5
    uv run python tools/capture_probe.py --list                # list candidate windows, no capture
    uv run python tools/capture_probe.py --title Notepad       # any window, e.g. without the game
    uv run python tools/capture_probe.py --pid 12345

Prints one line per second (fps into the mailbox, frame size before/after downscale, callback
p50/p95, CPU, display mode, client rect, health) and a summary. Saves up to ``--save`` downscaled PNGs
to %LOCALAPPDATA%\\DeadByMapper\\probes\\capture\\<timestamp>\\ - local only, never commit them.
Read-only: OS capture APIs and window queries only (docs/FAIR_PLAY.md).
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from deadbymapper.capture.metrics import StatsWindow  # noqa: E402
from deadbymapper.config.paths import user_dir  # noqa: E402
from deadbymapper.core.bus import EventBus  # noqa: E402
from deadbymapper.core.clock import MonotonicClock  # noqa: E402
from deadbymapper.core.events import ServiceHealth  # noqa: E402
from deadbymapper.gamelink import win32_util  # noqa: E402


def _fmt_rect(r: tuple[int, int, int, int] | None) -> str:
    return "-" if r is None else f"{r[2]}x{r[3]}@({r[0]},{r[1]})"


def _ms(x: float) -> str:
    return "  -  " if math.isnan(x) else f"{x:5.2f}"


def _describe(info: win32_util.WindowInfo) -> str:
    return (
        f"  hwnd        {info.hwnd:#x}\n"
        f"  title       {info.title!r}\n"
        f"  class       {info.class_name!r}\n"
        f"  exe / pid   {info.exe} / {info.pid}\n"
        f"  client      {_fmt_rect(info.client_rect)} (physical px)\n"
        f"  frame       {_fmt_rect(info.frame_rect)} (DWM extended frame bounds = WGC frame)\n"
        f"  window      {_fmt_rect(info.window_rect)}\n"
        f"  monitor     {_fmt_rect(info.monitor_rect)}  dpi {info.dpi} ({info.dpi / 96:.0%})\n"
        f"  style       {info.style:#010x}  caption={bool(info.style & win32_util.WS_CAPTION)}"
        f"  minimized={info.minimized}  focused={info.focused}\n"
        f"  display     {info.display_mode} (heuristic; exclusive fullscreen is not detectable from outside)"
    )


def _list_windows() -> int:
    rows = []
    for hwnd in win32_util.top_level_windows():
        info = win32_util.window_info(hwnd)
        if info is not None and info.title:
            rows.append(info)
    rows.sort(key=lambda i: -i.area)
    for i in rows:
        print(f"{i.hwnd:#010x}  {_fmt_rect(i.client_rect):>22}  {i.display_mode:<19} {i.exe or '?':<36} {i.title!r}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", help="window title to match (case-insensitive, substring); default: DBD")
    ap.add_argument("--pid", type=int, help="capture the largest window of this process")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--save", type=int, default=0, help="downscaled PNGs to save locally (opt-in; 0 = none)")
    ap.add_argument("--width", type=int, default=960, help="analysis width (settings.capture.analysis_width)")
    ap.add_argument("--fps", type=float, default=30.0, help="target delivery rate")
    ap.add_argument("--list", action="store_true", help="list visible top-level windows and exit")
    ap.add_argument("--cv-threads", type=int, default=None, help="cv2.setNumThreads (default: OpenCV's)")
    args = ap.parse_args()

    if sys.platform != "win32":
        print("capture_probe: Windows only")
        return 2
    win32_util.set_dpi_awareness()
    if args.list:
        return _list_windows()

    me = frozenset({os.getpid()})
    if args.pid:
        matcher = win32_util.WindowMatcher(pids=frozenset({args.pid}))
    elif args.title:
        matcher = win32_util.WindowMatcher(titles=(args.title,), exe_names=(), title_mode="contains", exclude_pids=me)
    else:
        matcher = win32_util.WindowMatcher(exclude_pids=me)

    info = win32_util.find_window(matcher)
    if info is None:
        print(f"No window found for {matcher.describe()}.")
        print("Is the game running and not minimized? Candidates (use --title/--pid):")
        _list_windows()
        return 2
    print(f"Target window ({matcher.describe()}):\n{_describe(info)}\n")

    import cv2
    import psutil

    from deadbymapper.capture.wgc_source import WgcFrameSource

    bus = EventBus(synchronous=True, name="probe")
    clock = MonotonicClock()
    health: list[ServiceHealth] = []
    bus.subscribe(ServiceHealth, health.append)
    src = WgcFrameSource(
        bus, clock, hwnd=info.hwnd, analysis_width=args.width, target_fps=args.fps, follow_tracker=False
    )
    proc = psutil.Process()
    proc.cpu_percent(None)
    n_cpus = psutil.cpu_count() or 1

    out_dir: Path | None = None
    seconds = max(int(math.ceil(args.seconds)), 1)
    save_at: set[int] = set()
    if args.save > 0:
        out_dir = user_dir() / "probes" / "capture" / datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
        k = min(args.save, seconds)
        save_at = {max(1, round((i + 1) * seconds / k)) for i in range(k)}
    saved: list[Path] = []

    import cv2 as _cv

    if args.cv_threads is not None:
        _cv.setNumThreads(args.cv_threads)
    print(f"OpenCV threads: {_cv.getNumThreads()}  |  cpus: {n_cpus}  |  target {args.fps:g} fps, width {args.width}")
    print(
        " t | fps WGC -> mbox | client -> analysis    | callback p50/p95 | convert p50/p95 | capture CPU | "
        "process CPU              | mode / client rect | health"
    )
    src.start()
    t_start = time.perf_counter_ns()
    first = prev = src.stats.snapshot(t_start)
    puts0 = puts_prev = src.mailbox.puts
    cpu_samples: list[float] = []
    try:
        for sec in range(1, seconds + 1):
            time.sleep(max(t_start + sec * 1_000_000_000 - time.perf_counter_ns(), 0) / 1e9)
            now = time.perf_counter_ns()
            snap = src.stats.snapshot(now)
            w = StatsWindow.between(prev, snap, n_cpus)
            prev = snap
            puts = src.mailbox.puts
            mbox_fps = (puts - puts_prev) / w.duration_s
            puts_prev = puts
            pcpu = proc.cpu_percent(None)
            cpu_samples.append(pcpu)
            cur = win32_util.window_info(info.hwnd)
            mode = f"{cur.display_mode} {_fmt_rect(cur.client_rect)}" if cur else "window gone"
            h = health[-1] if health else None
            hs = f"{h.status}: {h.detail}" if h else "-"
            print(
                f"{sec:2d} | {w.fps_in:5.1f} -> {mbox_fps:5.1f} | "
                f"{w.src_size[0]:4d}x{w.src_size[1]:<4d} -> {w.out_size[0]:4d}x{w.out_size[1]:<4d} | "
                f"{_ms(w.cb_p50_ms)} / {_ms(w.cb_p95_ms)} ms | {_ms(w.conv_p50_ms)} / {_ms(w.conv_p95_ms)} ms | "
                f"{w.cpu_pct:5.2f} %     | {pcpu / n_cpus:5.2f} % ({pcpu:5.1f} % of a core) | {mode} | {hs}",
                flush=True,
            )
            if out_dir is not None and sec in save_at:
                frame = src.mailbox.latest()
                if frame is not None:
                    path = out_dir / f"frame_{sec:03d}_{frame.width}x{frame.height}.png"
                    cv2.imwrite(str(path), frame.image)
                    saved.append(path)
    except KeyboardInterrupt:
        print("interrupted")
    finally:
        end = time.perf_counter_ns()
        last = src.stats.snapshot(end)
        src.stop()

    total = StatsWindow.between(first, last, n_cpus)
    print("\nSummary")
    print(f"  delivered into mailbox  {total.fps:.1f} fps (WGC callbacks {total.fps_in:.1f}/s)")
    print(
        f"  mailbox                 {src.mailbox.puts - puts0} frames, {src.mailbox.dropped} overwritten unread "
        f"(this probe reads ~1/s); converter dropped {src.handoff.dropped}"
    )
    print(
        f"  frame size              {total.src_size[0]}x{total.src_size[1]} -> {total.out_size[0]}x{total.out_size[1]}"
    )
    print(f"  WGC callback (copy)     p50 {_ms(total.cb_p50_ms)} ms, p95 {_ms(total.cb_p95_ms)} ms")
    print(f"  convert (downscale+BGR) p50 {_ms(total.conv_p50_ms)} ms, p95 {_ms(total.conv_p95_ms)} ms")
    print(f"  capture threads CPU     {total.cpu_pct:.2f} % of machine ({total.cpu_core_pct:.1f} % of one core)")
    if cpu_samples:
        avg = sum(cpu_samples) / len(cpu_samples)
        print(f"  probe process CPU       {avg / n_cpus:.2f} % of machine ({avg:.1f} % of one core), {n_cpus} cpus")
    if total.errors:
        print(f"  callback errors         {total.errors}: {total.last_error}")
    print(f"  window                  {info.title!r} class {info.class_name!r} exe {info.exe}")
    shown = [h for h in health if h.status != "ok"] + [h for h in health if h.status == "ok"][-1:]
    for h in sorted(shown, key=lambda e: e.ts_ns):
        print(f"  health                  {h.status}: {h.detail}")
    if out_dir is not None:
        print(f"  PNGs ({len(saved)})              {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
