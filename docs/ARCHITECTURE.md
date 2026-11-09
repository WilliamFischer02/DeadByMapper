# Architecture

## Goals that shaped it
1. **Strictly external and read-only** (docs/FAIR_PLAY.md).
2. **Glanceable and fast:** pane at 60 fps, estimator <= 2 ms/tick, total CPU budget ~10 % of one core.
3. **Developable without the game:** every input is an event that can be recorded and replayed; a
   simulator produces the same event streams with ground truth.
4. **Honest under uncertainty:** every estimate carries a status/confidence; unverified knowledge is
   labelled; the UI shows uncertainty spatially.
5. **Parallelisable by agents:** narrow typed contracts, one owner per directory.

## Process / thread layout (single process, Python 3.12 + PySide6)
```
 ┌───────────────────────────── DeadByMapper.exe ─────────────────────────────┐
 │  Qt GUI thread ── AppBridge (30 Hz poll of App.snapshot()) ── QML windows    │
 │        ▲ immutable snapshots                                                 │
 │  Bus dispatcher thread ── handlers: Localizer, MatchModel, MapIdentifier,    │
 │        ▲  MatchStateMachine, LogParser, Recorder                             │
 │        │ events (small, typed, timestamped)                                  │
 │  Sensor threads:                                                             │
 │    gamelink.process (psutil, 0.5 Hz)    gamelink.window (Win32, 4 Hz)        │
 │    gamelink.log (tail, 4 Hz)            input.raw (Raw Input msg loop)       │
 │    capture.wgc (WGC callbacks) ──► FrameMailbox ──► perception workers       │
 │    audio loopback callback ──► HeartbeatAnalyzer ──► TerrorRadiusObserved    │
 │    ticker (60 Hz Tick)                                                       │
 └──────────────────────────────────────────────────────────────────────────────┘
```
- Frames and audio blocks never go on the bus. Perception workers pull the *latest* frame at their own
  rate (flow 15 Hz, prompts <= 5 Hz, HUD 4 Hz, OCR on demand) and publish observations.
- If profiling shows GIL contention, move perception into a worker *process* fed by shared memory;
  the event contracts do not change.

## Modules
| Package | Responsibility | Key types |
|---|---|---|
| `core` | clock, typed events + serialisation, bus, geometry, service lifecycle | `Event`, `EventBus`, `Service` |
| `config` | paths (dev vs frozen), user settings (pydantic) | `Settings` |
| `gamelink` | DBD process + window, log tail + rule parser, UE ini reader | `ProcessWatcher`, `LogTailer` |
| `capture` | WGC/DXGI frame sources, mailbox, ROIs | `FrameMailbox`, `Region` |
| `input` | Raw Input listener, keymap, input state + gating, yaw calibration maths | `InputState` |
| `perception` | match-state machine, map identification, OCR, prompts, HUD, optical flow | `MapIdentifier` |
| `audio` | loopback capture, heartbeat DSP | `HeartbeatAnalyzer` |
| `localization` | occupancy grid, motion/measurement models, particle filter, path matcher, localizer | `Localizer` |
| `world` | map data model + registry, live match model, generator strategy | `MapData`, `MatchModel` |
| `recording` | session recorder / replayer | `SessionRecorder` |
| `sim` | path planner, scripted survivor/killer, demo driver | `run_gen_scenario` |
| `ui` | view-model bridge, theme tokens, QML windows/components | `AppBridge` |
| `app.py` | composition root per run mode (live / demo / replay) | `App` |

## Event catalogue (core/events.py is authoritative)
| Group | Events | Producer -> consumer |
|---|---|---|
| System | `Tick`, `ServiceHealth` | ticker/sim -> localizer; services -> dashboard |
| Game link | `GameProcessChanged`, `GameWindowChanged`, `LogLine`, `LogEvent` | gamelink -> state machine, map identifier |
| Input | `KeyEvent`, `MouseDelta`, `MouseButton`, `InputFocusChanged` | raw input / window / state machine -> localizer |
| Perception | `ScreenObserved`, `MatchStateChanged`, `MapCandidate`, `MapIdentified`, `MapAmbiguous`, `InteractionObserved`, `PlayerStatusObserved`, `MotionObserved`, `GenCounterObserved`, `TerrorRadiusObserved`, `ObjectSighted` | perception/audio -> localizer, match model, UI |
| User intent | `UserFix`, `UserHeadingFix`, `UserMapOverride`, `UserMarkGenerator`, `KillerTerrorRadiusSet` | UI -> localizer, match model |
| Estimation | `PoseEstimate` | localizer -> match model -> UI |

## Run modes
- **live:** all sensors (Windows); the state machine decides when the pane appears.
- **demo:** `sim/demo.py` plays the Test Arena in real time (no game). Used for UI work and demos.
- **replay:** a recorded `events.jsonl` replayed in real time through the whole pipeline.
- **tests/bench:** synchronous bus + `ManualClock` - fully deterministic.

## Match lifecycle
`OFFLINE -> MENU -> LOBBY -> LOADING -> IN_MATCH -> POST_MATCH -> MENU`, with hysteresis (N agreeing
screen observations; verified log events jump directly). Entering `IN_MATCH` resets yaw counts, the
breadcrumb and the particle filter. A menu overlay mid-match publishes `InputFocusChanged(False)`.

## Failure handling
Every sensor is a `Service` that reports health. Missing optional pieces degrade gracefully:
no capture -> no OCR/flow (manual map pick + pure dead reckoning); no audio -> no killer ring; no map
data -> breadcrumb mode; uncalibrated mouse -> placeholder deg/count plus a dashboard chip. The pane
never shows an error dialog; it shows one line of guidance.

## Performance budgets (i5-13600K)
| Component | Budget |
|---|---|
| Localizer tick (5000 particles) | <= 2 ms at 60 Hz (measured 1.15 ms in sim) |
| PathMatcher per landmark event | <= 40 ms |
| Perception total | <= 10 % of one core |
| Raw input handling at 8000 Hz | <= 3 % of one core (else native helper) |
| UI | 60 fps, <= 2 % CPU |
| Event -> pane latency | <= 100 ms (pose), <= 400 ms (terror ring) |
