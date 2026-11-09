# Screen capture (capture-engineer)

DeadByMapper sees the game only through OS capture APIs: Windows.Graphics.Capture (WGC, primary) and,
later, DXGI Desktop Duplication (fallback). It never hooks Present, injects, or opens the game process
(docs/FAIR_PLAY.md). The code lives in `src/deadbymapper/capture/` and `gamelink/window_tracker.py` /
`gamelink/win32_util.py`.

## Data flow and threads
```
WGC native thread  on_frame: throttle -> client crop -> memcpy into a pooled buffer (FrameHandoff)
converter thread   INTER_AREA downscale to analysis_width -> BGRA->BGR -> FrameMailbox.put (latest-only)
supervisor thread  find/attach every 2 s, re-attach after the window closes, crop geometry every 0.5 s,
                   ServiceHealth at most every 2 s
perception workers FrameMailbox.latest() / wait_newer() at their own rate
```
- Window capture by HWND (never the monitor): no other window, including our pane, can appear.
- Cursor capture off. Yellow border off (Windows 11; older builds fall back and the detail says so).
- Pacing: WGC `minimum_update_interval` = 0.75 x target period (25 ms for 30 fps), so the compositor
  skips frames before the GPU->CPU readback; `FrameThrottle` trims the rest. 60 Hz -> exactly 30 fps;
  120/144 Hz -> 30 +/- 0.5 fps (tests).
- Client crop: WGC delivers the DWM extended frame bounds (title bar + 1 px borders on a captioned window;
  exactly the client area in Windowed Fullscreen). Offset = client origin - frame origin, both physical px.
- Downscale: integer ratios use INTER_AREA's exact box path (1920 -> 960); others pre-reduce by an integer
  factor then a residual INTER_AREA (`crop.RESIDUAL_INTERPOLATION` can switch the residual to linear).
  `Frame.scale = out_w / sampled client width`, so `x_client = x_analysis / scale`.
- Timestamps: `clock.now_ns()` when the frame is accepted (the shared monotonic clock). WGC's own
  `SystemRelativeTime` is not on the same base as `perf_counter_ns` here (measured offset about -12 ms),
  so it is not used.
- Mailbox counters: `puts`, `dropped` (overwritten before any consumer read it - normal for latest-only),
  `reads`. Hand-off counter: `handoff.dropped` (the converter fell behind; should stay 0).

### Why the callback never runs OpenCV
With OpenCV's default parallel backend on Windows (pip builds use the MS Concurrency Runtime), running
`cv2.resize`/`cvtColor` on the WGC native thread made the process die with an access violation inside the
NEXT session's `start_free_threaded` after the captured window had closed (windows-capture 2.0.1, Win11,
3/3 reproductions; never with OpenCV unused on that thread, never with `cv2.setNumThreads(1)`). The
callback therefore only copies; a long-lived converter thread does all OpenCV work. The regression test is
`tests/test_capture.py::test_real_wgc_capture_crops_client_and_reattaches`.

## Health (ServiceHealth "capture.wgc")
| status | detail (examples) | meaning / user action |
|---|---|---|
| ok | `WGC 1920x1080 -> 960x540, 30.1 fps, 0.5 % CPU` | frames flow. CPU = our capture threads (WGC thread incl. readback + converter), % of the whole machine; excludes OpenCV's worker pool |
| ok | `WGC attached to 'DeadByDaylight  ' (DeadByDaylight-Win64-Shipping.exe), waiting for frames` | just attached |
| degraded | `WGC only 0.0 fps (window idle, occluded by exclusive fullscreen, or minimized) - set DBD Display Mode to Windowed Fullscreen` | < 5 fps over 2 s |
| degraded | `WGC frames are black (exclusive fullscreen?) - set DBD Display Mode to Windowed Fullscreen` | delivered frames black for >= 3 s |
| degraded | `game window minimized - no frames until it is restored` | |
| down | `game window not found (title prefix 'DeadByDaylight' or exe ...) - retrying every 2 s` | game not running |
| down | `game window closed - waiting for it to reappear` | re-attaches automatically |
| down | `WGC could not capture '...': <error>` / `windows-capture unavailable (...)` | API/package problem |

## Display modes
| DBD setting | What happens | Status |
|---|---|---|
| Windowed Fullscreen (borderless) | WGC window capture works; client = whole frame; overlays can draw on top. **Recommended.** | **Verified 2026-11-08 on the target PC** (Steam DBD, 1920x1080, 30 fps, not black) |
| Windowed | WGC works; the title bar and borders are cropped. On Windows 11 the two bottom client corners are rounded by DWM (a few black/transparent pixels) - ROIs must not rely on the extreme corners. | Verified with stand-in windows (Notepad, Win32/Qt test windows); with DBD: needs the user |
| Fullscreen (exclusive) | Expected: WGC window capture yields black or no new frames (the swapchain bypasses DWM composition; with Windows' "fullscreen optimizations" it may still work). Health turns "degraded" with the Windowed Fullscreen hint. DXGI Desktop Duplication of the monitor still works - that is the fallback (not implemented yet; `DxgiFrameSource` reports "down" honestly). It must crop to the client rect and mask DeadByMapper's own windows. Transparent overlays cannot draw over exclusive fullscreen anyway. | Needs the user (expected behaviour, unverified) |
| Minimized | No frames; health "degraded" (minimized). | Verified (Notepad) |

### Display-mode heuristic (`win32_util.classify_display_mode`, pure, tested)
- `windowed_fullscreen`: client rect == monitor rect and no WS_CAPTION / WS_THICKFRAME.
- `windowed`: has a caption or sizing frame, or does not cover its monitor.
- `unknown`: minimized or zero size.
- `fullscreen` (exclusive) is never reported: an exclusive swapchain sets the display mode to the game
  resolution and uses a captionless popup covering the monitor, which is indistinguishable from
  borderless by styles and rects. Only its effects (black/no frames) are observable, and health reports them.

Observed on the target PC: DBD window class `UnrealWindow`, title `'DeadByDaylight  '` (two trailing
spaces), style `0x94000000` (WS_POPUP | WS_VISIBLE | WS_CLIPSIBLINGS), exe
`DeadByDaylight-Win64-Shipping.exe` (Steam). The matcher (`WindowMatcher`) prefers the exe name, then a
case-insensitive title prefix `DeadByDaylight`; our own PID is excluded.

## DPI
All rects are physical pixels: `win32_util` runs its Win32 queries inside a per-monitor-v2 *thread* DPI
context, so results are right even if the process is not DPI aware (the app also calls
`set_dpi_awareness()` at startup). WGC frames are always physical. The target PC runs every monitor at
100 % (96 dpi), so scaling > 100 % is untested on hardware.

## Measured cost (target PC: i5-13600K, 20 logical CPUs, RTX 5070, Win11, 60 Hz monitors)
`uv run python tools/capture_probe.py`, 6-10 s runs, target 30 fps, analysis width 960. "Process" is the
whole probe process (Task Manager style, % of the machine).

| client size | delivered | callback (copy) p50/p95 | convert p50/p95 | capture threads | process, OpenCV default (20 thr) | process, `cv2.setNumThreads(1)` |
|---|---|---|---|---|---|---|
| 1280x720 (test window) | 30.0 fps | 0.37 / 0.84 ms | 1.5 / 1.8 ms (4.4 / 7.4 single-thread) | 0.3 % | 2.0 % | 1.0 % |
| 1920x1080 (borderless test window) | 30.0 fps | 0.54 / 1.2 ms | 1.1 / 1.3 ms (1.5 / 2.6) | 0.3 % | 1.5 % | 0.9 % |
| **1920x1080 DBD, Windowed Fullscreen** | **30.0 fps** | **0.86 / 1.4 ms** | **1.0 / 1.3 ms** | **0.35 %** | **1.1 %** | - |
| 2560x1414 (test window, height clamped by Windows) | 30.0 fps | 1.4 / 2.8 ms | 2.7 / 3.2 ms (7.8 / 11.0) | 0.6 % | 3.1 % | 2.1 % |

Most of the gap between "capture threads" and "process" is OpenCV's thread pool spinning after each
parallel resize. `cv2.setNumThreads(1)` halves process CPU at the cost of a longer (still off-thread)
conversion; the choice is app-wide (lead).

## Probe (run it with the game)
```
uv run python tools/capture_probe.py                    # finds DBD by exe/title, 10 s, saves 3 PNGs
uv run python tools/capture_probe.py --list             # all candidate windows with display mode
uv run python tools/capture_probe.py --title Notepad    # any other window
uv run python tools/capture_probe.py --pid 12345 --seconds 20 --save 5 --cv-threads 1
```
PNGs go to `%LOCALAPPDATA%\DeadByMapper\probes\capture\<timestamp>\` (local only; never commit them).
Note: WGC only delivers frames when the window content changes - an idle Notepad shows ~0 fps and a
"degraded" health line; that is expected.

## Open risks
- Exclusive fullscreen: behaviour above is expected, not measured; DXGI fallback not implemented.
- HDR: WGC is asked for BGRA8; on an HDR display Windows converts to SDR (may look dim/washed out),
  which can shift colour thresholds. Untested (no HDR monitor here).
- Multi-monitor: display mode uses the monitor nearest the window; a window spanning monitors is
  "windowed". DXGI fallback must pick the monitor containing most of the client rect.
- DPI scaling > 100 %, Windows 10 (border toggle / MinUpdateInterval may be unsupported: the source
  falls back and says so), Game Pass (`WinGDK`) and Epic builds: untested.
- High refresh / VRR / frame generation: pacing is time-based and was tested with 60 Hz sources plus
  simulated 120/144 Hz; untested on a real high-refresh monitor.
