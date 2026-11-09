"""Entry point: ``python -m deadbymapper [--demo | --replay DIR] [--record] [--headless SECONDS]``."""

from __future__ import annotations

import argparse
import contextlib
import logging
import logging.handlers
import sys
import time
from pathlib import Path

from . import __version__


def _setup_logging(level: str) -> None:
    from .config.paths import logs_dir

    # Windowed (frozen) builds have no console: sys.stderr is None there.
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)] if sys.stderr else []
    with contextlib.suppress(OSError):
        handlers.append(
            logging.handlers.RotatingFileHandler(
                logs_dir() / "deadbymapper.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
        )
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )


def _limit_opencv_threads(n: int = 1) -> None:
    """OpenCV's default pool (20 threads here) spins after every resize and doubled process CPU
    during capture (docs/CAPTURE.md). Our CV work is small per frame; revisit in M3 if perception needs more."""
    with contextlib.suppress(Exception):
        import cv2

        cv2.setNumThreads(n)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="deadbymapper", description="Glanceable live minimap companion for Dead by Daylight"
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true", help="simulated survivor on the Test Arena (no game needed)")
    mode.add_argument("--replay", type=Path, metavar="DIR", help="replay a recorded session in real time")
    ap.add_argument(
        "--record", action="store_true", help="record this session to %%LOCALAPPDATA%%/DeadByMapper/recordings"
    )
    ap.add_argument("--headless", type=float, metavar="SECONDS", help="run without UI for N seconds (diagnostics)")
    ap.add_argument("--screenshot", type=Path, metavar="DIR", help="render UI offscreen after a delay, save PNGs, exit")
    ap.add_argument("--screenshot-delay", type=float, default=7.0, metavar="SECONDS")
    ap.add_argument("--log-level", default="INFO")
    ap.add_argument("--version", action="version", version=f"DeadByMapper {__version__}")
    args = ap.parse_args(argv)

    _setup_logging(args.log_level)
    _limit_opencv_threads()
    from .gamelink.win32_util import set_dpi_awareness

    set_dpi_awareness()

    from .app import App
    from .config.settings import load_settings

    run_mode = "demo" if args.demo else ("replay" if args.replay else "live")
    app = App(run_mode, load_settings(), replay_dir=args.replay, record=args.record)
    app.start()
    try:
        if args.headless:
            time.sleep(args.headless)
            snap = app.snapshot()
            print(
                f"mode={snap.mode} game={snap.game_running} map={snap.match.map.id if snap.match.map else None} "
                f"pose={snap.match.pose.status if snap.match.pose else None} health={snap.health}"
            )
            return 0
        from .ui.qt_app import run_ui

        return run_ui(app, screenshot_dir=args.screenshot, screenshot_delay_ms=int(args.screenshot_delay * 1000))
    finally:
        app.stop()


if __name__ == "__main__":
    raise SystemExit(main())
