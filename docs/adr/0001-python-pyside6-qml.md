# ADR 0001 - Python 3.12 + PySide6/Qt Quick (QML) for the app

**Status:** accepted (M0) · **Date:** 2026-11-08

## Context
The app needs:
- a crisp, animated, always-on-top overlay pane plus a dashboard;
- fast numerical work (particle filter, optical flow, DSP);
- Win32 access (Raw Input, WGC capture, window styles);
- a one-folder Windows executable.

It is built by AI agents working in parallel, plus one owner.

## Decision
- **Python 3.12** with numpy and OpenCV for compute.
- **PySide6 + Qt Quick/QML** for UI, with the GPU scene graph, `Shape` CurveRenderer, native text
  rendering, and per-monitor DPI.
- **PyInstaller** onedir build; `uv` for environments.

## Alternatives considered
- **C# / WPF or WinUI 3.** Excellent Windows integration and overlay story. But a second language
  for the numerics (or a heavier interop), and slower iteration for the localization work.
- **C++ / Qt.** Best performance, but much slower agent iteration and harder tests.
- **Electron or Tauri + web UI.** Great UI velocity, but heavier, and the overlay plus capture plus
  Raw Input plumbing still has to be written natively.
- **Dear ImGui overlay.** Fast, but not the polished, "sleek" glance UI the owner asked for.

## Consequences
- The hot paths are vectorised numpy. If profiling shows a gap, move one piece to native code
  without touching the event contracts:
  - Raw Input at 8 kHz (GetRawInputBuffer or a tiny native helper);
  - process loopback COM.
- Qt-specific gotchas are documented in CLAUDE.md.
- The GUI is testable offscreen in CI.
