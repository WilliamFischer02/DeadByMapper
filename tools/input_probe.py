"""Raw Input probe: is the listener receiving input, is the privacy gate doing its job, what does it cost?

    uv run python tools/input_probe.py                  # with Dead by Daylight running (60 s)
    uv run python tools/input_probe.py --seconds 120
    uv run python tools/input_probe.py --self           # no game: THIS console window plays the "game window"
    uv run python tools/input_probe.py --bench          # CPU cost per message (no input needed)
    uv run python tools/input_probe.py --unbatched      # per-message WM_INPUT reads instead of GetRawInputBuffer

Once per second it prints: raw WM_INPUT messages/s (mouse, keyboard), events/s that passed the gate and
were published, the gate's dropped count, process CPU %, whether the DBD window is foreground, and the
NAMES of allowed keys seen in the last second.

PRIVACY (docs/FAIR_PLAY.md "Not a keylogger"): the probe only names keys that passed ``InputGate`` -
i.e. keys in ``KeyMap.allowed_vks()`` (bound game actions + F8/F9/F10) pressed while the game window
(or, with --self, this console) is foreground. Everything else is a count, never a key, never text.
The probe never sends, injects or blocks input (the bench feeds pre-built byte buffers to the parser).
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from deadbymapper.core.bus import EventBus  # noqa: E402
from deadbymapper.core.clock import MonotonicClock  # noqa: E402
from deadbymapper.core.events import GameWindowChanged, KeyEvent, ServiceHealth  # noqa: E402
from deadbymapper.input import _win32 as w  # noqa: E402
from deadbymapper.input import rawparse as rp  # noqa: E402
from deadbymapper.input.keymap import VK_NAME, KeyMap  # noqa: E402
from deadbymapper.input.privacy import InputGate  # noqa: E402
from deadbymapper.input.raw_input import RawInputListener, RawInputProcessor  # noqa: E402

RATES_HZ = (1000, 4000, 8000)
BUDGET_PCT = 3.0
ASSUMED_WAKE_KERNEL_US = 10.0  # conservative: a timer wake + two context switches on a modern desktop CPU


# --------------------------------------------------------------------------- startup info
def print_layout() -> None:
    print("struct layout (x64, ctypes):")
    for k, v in w.layout().items():
        print(f"  {k:<30} {v}")
    print(
        f"  struct formats: header {rp.HEADER_SIZE}, mouse msg {rp.MOUSE_MSG_SIZE}, keyboard msg {rp.KEYBOARD_MSG_SIZE}"
    )


def allowed_names(allowed: frozenset[int]) -> list[str]:
    return sorted(VK_NAME.get(vk, f"0x{vk:02X}") for vk in allowed)


def seen_key_names(seen: set[int], allowed: frozenset[int]) -> str:
    """Names of keys seen in the last second - ONLY keys in the allowed set, never anything else."""
    return ",".join(sorted(VK_NAME.get(vk, f"0x{vk:02X}") for vk in seen if vk in allowed)) or "-"


# --------------------------------------------------------------------------- live probe
def probe(seconds: float, self_mode: bool, batch: bool) -> int:
    if sys.platform != "win32":
        print("Raw Input is Windows only.")
        return 2
    import psutil

    w.user32  # noqa: B018 - make sure the bindings loaded
    km = KeyMap.load()
    allowed = km.allowed_vks()
    bus = EventBus(name="probe")
    clock = MonotonicClock()
    seen: set[int] = set()
    seen_lock = threading.Lock()
    health: list[ServiceHealth] = []

    def on_key(e: KeyEvent) -> None:
        if e.vk in allowed and e.down:  # defence in depth: the gate already guarantees this
            with seen_lock:
                seen.add(e.vk)

    bus.subscribe(KeyEvent, on_key)
    bus.subscribe(ServiceHealth, health.append)
    listener = RawInputListener(bus, clock, keymap=km, batch=batch)
    services: list = []
    bus.start()
    print_layout()
    print(f"python elevated: {w.self_elevated()}   batched reads: {listener.batch}")
    print(f"observed keys (allowed set): {', '.join(allowed_names(allowed))}")
    listener.start()
    print(f"registration: {'OK' if listener.registered else 'FAILED'} (error {listener.register_error})")
    for page, usage, flags, hwnd in w.registered_devices():
        kind = {2: "mouse", 6: "keyboard"}.get(usage, "?")
        sink = "INPUTSINK" if flags & w.RIDEV_INPUTSINK else "-"
        own = "our sink window" if hwnd and hwnd == listener._hwnd else f"hwnd {hwnd:#x}"
        print(f"  usage page 0x{page:02X} usage 0x{usage:02X} ({kind}) flags 0x{flags:X} {sink} -> {own}")
    if self_mode:
        hwnd = w.console_root_window()
        if hwnd is None:
            print("--self: this process has no console window; nothing can pass the gate (run it from a terminal)")
        else:
            print(f"--self: using this console's window {hwnd:#x} as the game window (click it to focus)")
        bus.publish(GameWindowChanged(ts_ns=clock.now_ns(), hwnd=hwnd, client_rect=None, focused=False))
    else:
        from deadbymapper.gamelink.process_watcher import ProcessWatcher
        from deadbymapper.gamelink.window_tracker import WindowTracker

        services = [ProcessWatcher(bus, clock), WindowTracker(bus, clock)]
        for s in services:
            s.start()
        print("waiting for Dead by Daylight (process watcher + window tracker, as in the app)")
    time.sleep(0.3)
    for h in health:
        print(f"health {h.service}: {h.status} | {h.detail}")
    n_health = len(health)

    proc = psutil.Process()
    proc.cpu_percent(None)
    p = listener.processor
    last = (p.mouse_msgs, p.key_msgs, p.published)
    print("\n  t |  mouse/s  kbd/s | publ/s | gate dropped |  cpu % | game fg | allowed keys seen")
    t_end = time.perf_counter() + seconds
    t0 = time.perf_counter()
    try:
        while time.perf_counter() < t_end:
            time.sleep(1.0)
            cur = (p.mouse_msgs, p.key_msgs, p.published)
            with seen_lock:
                names = seen_key_names(seen, allowed)
                seen.clear()
            fg = "yes" if listener.gate.game_is_foreground() else "no"
            print(
                f"{time.perf_counter() - t0:3.0f} | {cur[0] - last[0]:8d} {cur[1] - last[1]:6d} |"
                f" {cur[2] - last[2]:6d} | {listener.gate.dropped:12d} | {proc.cpu_percent(None):6.1f} |"
                f" {fg:>7} | {names}"
            )
            last = cur
            for h in health[n_health:]:
                print(f"     health {h.service}: {h.status} | {h.detail}")
            n_health = len(health)
    except KeyboardInterrupt:
        pass
    t_stop = time.perf_counter()
    listener.stop()
    stop_s = time.perf_counter() - t_stop
    for s in services:
        s.stop()
    bus.stop()
    print(
        f"\ntotals: mouse {p.mouse_msgs}, keyboard {p.key_msgs}, other {p.other_msgs}, absolute {p.absolute_msgs},"
        f" published {p.published}, gate dropped {listener.gate.dropped}, ignored keys {p.ignored_keys},"
        f" malformed {p.malformed}"
    )
    print(
        f"reads: wakes {listener.wakes}, WM_INPUT {listener.wm_input}, GetRawInputBuffer batches {listener.batches},"
        f" read errors {listener.read_errors}, buffer errors {listener.buffer_errors},"
        f" input thread == listener thread: {listener.input_thread_ids <= {listener.thread_id}}"
    )
    print(f"stop() took {stop_s * 1000:.1f} ms, thread alive after stop: {listener.alive}")
    return 0


# --------------------------------------------------------------------------- bench
def _cbuf(data: bytes):  # type: ignore[no-untyped-def]
    import ctypes

    arr = (ctypes.c_uint64 * ((len(data) + 7) // 8))()  # same 8-byte aligned buffers as the listener
    ctypes.memmove(arr, data, len(data))
    return arr


def _processor(gate_open: bool) -> tuple[EventBus, RawInputProcessor]:
    game, other = 0x1234, 0x9999
    bus = EventBus(name="bench")
    bus.subscribe(None, lambda e: None)  # one no-op subscriber: includes the dispatcher thread's cost
    bus.start()
    km = KeyMap.load()
    gate = InputGate(km.allowed_vks(), lambda: game if gate_open else other)
    gate.on_game_window(GameWindowChanged(ts_ns=0, hwnd=game, client_rect=None, focused=True))
    return bus, RawInputProcessor(bus, gate)


def _bench_single(n: int, gate_open: bool) -> tuple[float, float, int]:
    """process_message (GetRawInputData path). Returns (cpu us/msg incl. dispatcher, wall us/msg, published)."""
    bus, proc = _processor(gate_open)
    buf = _cbuf(rp.pack_mouse(3, -1))
    size = rp.MOUSE_MSG_SIZE
    c0, t0 = time.process_time(), time.perf_counter()
    for i in range(n):
        proc.process_message(buf, size, i)
    t1 = time.perf_counter()
    bus.stop(timeout=60)
    c1 = time.process_time()
    return (c1 - c0) / n * 1e6, (t1 - t0) / n * 1e6, proc.published


def _bench_batch(n: int, k: int, gate_open: bool) -> tuple[float, float, int]:
    """process_buffer (GetRawInputBuffer path) with k mouse messages per buffer. Returns per-buffer costs."""
    bus, proc = _processor(gate_open)
    msgs = [rp.pack_mouse(3, -1)] * k
    data = rp.pack_buffer(msgs)
    buf = _cbuf(data)
    iters = max(1, n // k)
    c0, t0 = time.process_time(), time.perf_counter()
    for i in range(iters):
        proc.process_buffer(buf, len(data), k, i)
    t1 = time.perf_counter()
    bus.stop(timeout=60)
    c1 = time.process_time()
    assert proc.mouse_msgs == iters * k
    return (c1 - c0) / iters * 1e6, (t1 - t0) / iters * 1e6, proc.published


def _bench_win32(n: int) -> dict[str, float]:
    """OS-side costs in microseconds. Posts an app-defined WM_APP message to OUR OWN hidden window to time
    message dispatch + the ctypes callback (a window message, not input - nothing is synthesized)."""
    import ctypes
    from ctypes import wintypes

    out: dict[str, float] = {}
    hits = [0]

    def proc(hwnd: int, msg: int, wp: int, lp: int) -> int:
        if msg == 0x8001:
            hits[0] += 1
            return 0
        return w.user32.DefWindowProcW(hwnd, msg, wp, lp)

    cb = w.WNDPROC(proc)
    hinst = w.kernel32.GetModuleHandleW(None)
    wc = w.WNDCLASSW()
    wc.lpfnWndProc = cb
    wc.hInstance = hinst
    wc.lpszClassName = "DeadByMapperProbeBench"
    w.user32.RegisterClassW(ctypes.byref(wc))
    hwnd = w.user32.CreateWindowExW(
        w.WS_EX_TOOLWINDOW, wc.lpszClassName, "bench", 0, 0, 0, 0, 0, None, None, hinst, None
    )
    msg = wintypes.MSG()
    pmsg = ctypes.byref(msg)
    t0 = time.perf_counter()
    for _ in range(n):
        w.user32.PostMessageW(hwnd, 0x8001, 0, 0)
        w.user32.PeekMessageW(pmsg, None, 0, 0, w.PM_REMOVE)
        w.user32.DispatchMessageW(pmsg)
    out["dispatch"] = (time.perf_counter() - t0) / n * 1e6  # includes the PostMessage: conservative
    size = wintypes.UINT(64)
    mbuf = (ctypes.c_uint64 * 8)()
    t0 = time.perf_counter()
    for _ in range(n):
        w.user32.GetRawInputData(0x1234, w.RID_INPUT, mbuf, ctypes.byref(size), rp.HEADER_SIZE)
    out["GetRawInputData"] = (time.perf_counter() - t0) / n * 1e6  # invalid handle: syscall cost proxy
    bbuf = (ctypes.c_uint64 * 1024)()
    t0 = time.perf_counter()
    for _ in range(n):
        w.user32.MsgWaitForMultipleObjectsEx(0, None, 0, w.QS_ALLINPUT, w.MWMO_INPUTAVAILABLE)
        bsize = wintypes.UINT(8192)
        w.user32.GetRawInputBuffer(bbuf, ctypes.byref(bsize), rp.HEADER_SIZE)
        w.user32.GetRawInputBuffer(bbuf, ctypes.byref(bsize), rp.HEADER_SIZE)
        w.user32.PeekMessageW(pmsg, None, 0, 0, w.PM_REMOVE)
    out["wake"] = (time.perf_counter() - t0) / n * 1e6  # per batch: wait + 2 x GetRawInputBuffer + empty peek
    # The 4 ms coalescing sleep's timer wake + context switches are kernel time that process_time cannot
    # resolve (15.6 ms ticks); charge a conservative assumed cost instead of a fake measured zero.
    out["sleep_cpu"] = ASSUMED_WAKE_KERNEL_US
    w.user32.DestroyWindow(hwnd)
    w.user32.UnregisterClassW(wc.lpszClassName, hinst)
    return out


def bench(n: int, window_s: float) -> int:
    print(f"bench: {n} pre-built mouse RAWINPUT buffers through RawInputProcessor (parse -> gate -> publish)")
    print("  publish = EventBus.publish on a started async bus with one no-op subscriber (dispatcher CPU included)\n")
    single_open = _bench_single(n, True)
    single_closed = _bench_single(n, False)
    print("per-message path (GetRawInputData -> process_message):")
    for label, (cpu, wall, _pub) in (
        ("gate OPEN  (DBD fg, every msg published)", single_open),
        ("gate CLOSED (DBD not fg, all dropped)   ", single_closed),
    ):
        print(f"  {label}: cpu {cpu:.2f} us/msg  wall {wall:.2f} us/msg")
    os_cost = _bench_win32(min(n, 100_000)) if sys.platform == "win32" else {}
    if os_cost:
        print("Win32 side (measured on this machine):")
        d = os_cost["dispatch"]
        print(f"  WM_INPUT-style dispatch + ctypes WNDPROC callback: {d:.2f} us/msg (incl. PostMessage)")
        print(f"  GetRawInputData syscall (proxy):                    {os_cost['GetRawInputData']:.2f} us/call")
        print(f"  batched wake (MsgWait + 2x GetRawInputBuffer + Peek): {os_cost['wake']:.2f} us/batch")
        s = os_cost["sleep_cpu"]
        print(f"  coalescing sleep timer wake + 2 context switches:    {s:.2f} us/batch (ASSUMED, not measurable)")
    per_msg = single_open[0] + os_cost.get("dispatch", 0.0) + os_cost.get("GetRawInputData", 0.0)
    print(f"\nUNBATCHED total ~{per_msg:.2f} us/msg = {per_msg:.2f} ms per 1000 msgs (gate open, worst case)")
    for hz in RATES_HZ:
        pct = hz * per_msg * 1e-6 * 100
        print(f"  {hz:5d} Hz: {pct:5.2f} % of one core {'(over budget)' if pct > BUDGET_PCT else ''}")
    print(f"\nBATCHED (GetRawInputBuffer, {window_s * 1000:.0f} ms coalescing window, gate open, worst case):")
    worst = 0.0
    for hz in RATES_HZ:
        k = max(1, round(hz * window_s))
        cpu_batch, _wall, published = _bench_batch(n, k, True)
        per_batch = cpu_batch + os_cost.get("wake", 0.0) + os_cost.get("sleep_cpu", 0.0)
        per_msg_b = per_batch / k
        pct = hz * per_msg_b * 1e-6 * 100
        worst = max(worst, pct)
        print(
            f"  {hz:5d} Hz: {k:3d} msgs/batch, parse+gate+publish {cpu_batch / k:.2f} us/msg,"
            f" total {per_msg_b:.2f} us/msg = {per_msg_b:.2f} ms/1000 msgs -> {pct:5.2f} % of one core"
            f" ({published / max(1, n // k):.0f} MouseDelta/batch)"
        )
    verdict = "OK" if worst <= BUDGET_PCT else "OVER"
    print(f"\nbudget {BUDGET_PCT} % of one core: batched worst case {worst:.2f} % -> {verdict}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=60.0, help="how long to probe (default 60)")
    ap.add_argument("--self", dest="self_mode", action="store_true", help="use this console window as the game")
    ap.add_argument("--bench", action="store_true", help="measure CPU cost per message and exit")
    ap.add_argument("--bench-n", type=int, default=200_000, help="messages per bench run")
    ap.add_argument("--unbatched", action="store_true", help="read one WM_INPUT at a time (no GetRawInputBuffer)")
    args = ap.parse_args()
    if args.bench:
        return bench(args.bench_n, 0.004)
    return probe(args.seconds, args.self_mode, batch=not args.unbatched)


if __name__ == "__main__":
    raise SystemExit(main())
