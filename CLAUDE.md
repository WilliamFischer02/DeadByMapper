# CLAUDE.md - DeadByMapper operating manual (read fully before any change)

DeadByMapper is a Windows companion app for Dead by Daylight (DBD): a glanceable, always-on-top
**live minimap pane** during trials (where am I, where are the generators, which one next, how close
is the killer) and a **dashboard** outside trials (game link, sensor health, calibration, map data).
Owner: William Fischer / Goob Entertainment Co. Target machine: Windows 11, i5-13600K, RTX 5070.

## 0. The one rule that overrides everything - FAIR PLAY (docs/FAIR_PLAY.md)
DeadByMapper only OBSERVES what the player already perceives: pixels on their own screen (OS capture
APIs), the audio they hear (WASAPI loopback), their own keyboard/mouse (Raw Input, read-only), and the
log/config files DBD writes to disk (shared read). It NEVER reads/writes game memory, injects DLLs,
hooks DirectX/Present, sniffs packets, modifies game files, synthesizes input, or shows information
the player could not perceive (killer position/identity, hidden auras). Breaking this risks the
user's account under BHVR's "third-party software ... unfair advantage" rule. If a task seems to
require it, stop and ask. The fair-play-reviewer agent audits every milestone.

## 1. Current state (v0.1 scaffold)
| Area | State |
|---|---|
| Core (events, bus, clock, geometry, lifecycle) | Implemented + tested. `core/events.py` is THE contract. |
| Map data | 47 real maps as `stub` (names, realms, community sizes) + synthetic `test_arena`. No real coordinates yet (M5). |
| Localization (particle filter + global path matcher) | Implemented + tested in simulation. Bench: `tools/bench_localization.py`. |
| Simulator + `--demo` | Implemented: scripted survivor + killer on Test Arena drive the full pipeline + UI. |
| UI (PySide6/QML dashboard + minimap pane) | Working skeleton, renders crisp at 1x/2x. Needs M7 polish. |
| Game link: process watcher, log tail/parser, ini reader | Implemented + tested with fakes; log RULES are unverified placeholders. |
| Raw input (privacy-gated, F9/F10/F8 marks), WGC capture, overlay styles | Written against Win32/`windows-capture` APIs, UNVERIFIED on hardware (M1). Gate + hotkey logic tested. |
| Perception: OCR, state classifier, prompts, HUD, flow | Interfaces + pure logic tested; pixel-level detectors are M3/M4. |
| Audio: heartbeat DSP | Implemented + tested on synthetic audio; loopback capture is M6. |
| Packaging: PyInstaller spec, build/install scripts, icon | Written; first Windows build is M9 (try it early). |

## 2. Commands (Windows, from repo root; Claude Code's shell is Git Bash - these work there too)
```
uv sync --extra windows --group dev          # install (first time: scripts/bootstrap.ps1)
uv run pytest -q                             # all tests (GUI test runs offscreen)
uv run ruff check . ; uv run ruff format .   # lint / format (CI runs ruff check)
uv run python -m deadbymapper --demo         # see the whole pipeline without the game
uv run python -m deadbymapper                # live mode (needs DBD running for most sensors)
uv run python -m deadbymapper --record       # live + record session to %LOCALAPPDATA%\DeadByMapper\recordings
uv run python -m deadbymapper --replay <dir> # replay a recorded session through the pipeline
uv run python -m deadbymapper --demo --headless 8          # no UI, prints a status line
uv run python -m deadbymapper --demo --screenshot qa/screens/1 --screenshot-delay 45   # visual QA
uv run python tools/bench_localization.py    # localization benchmark (paste before/after in reports)
uv run python tools/validate_maps.py --verbose
uv run python tools/gen_map_stubs.py         # never overwrites authored maps
uv run python tools/gen_schemas.py           # after any change to world/map_model.py or settings.py
uv run python tools/log_probe.py <DeadByDaylight.log>
uv run python tools/recording_info.py <session dir> --marks   # first look at a user's recording
powershell -ExecutionPolicy Bypass -File packaging\build.ps1   # exe + install + Desktop shortcut
```

## 3. Repo map and ownership (parallel agents stay inside their lane)
| Path | Owner agent | Notes |
|---|---|---|
| `src/deadbymapper/core/`, `app.py`, `__main__.py`, `config/` | **lead (you)** | Contracts. Subagents propose changes, lead applies. |
| `capture/` (except regions.py), `gamelink/window_tracker.py`, `gamelink/win32_util.py` | capture-engineer | |
| `gamelink/process_watcher.py`, `log_tail.py`, `log_parser.py`, `game_config.py`, `data/game/log_rules.json` | lead (or vision for map rules) | |
| `input/` | input-engineer | observe-only |
| `localization/`, `sim/` | localization-engineer | bench numbers in every report |
| `perception/`, `capture/regions.py`, `data/game/prompts.*.json`, `hud_regions*.json` | vision-engineer | |
| `audio/` | audio-engineer | |
| `ui/` | ui-engineer | docs/UI_DESIGN.md is law |
| `world/map_model.py`, `world/map_registry.py`, `data/maps/`, `data/schemas/`, `tools/` | map-data-engineer | schema changes need lead sign-off |
| `world/match_model.py`, `world/gen_strategy.py` | lead | |
| `packaging/`, `scripts/`, `.github/` | release-engineer | |
| `docs/` | everyone updates their section; lead owns ROADMAP/HANDOFF/DECISIONS | |

## 4. Architecture in one breath (details: docs/ARCHITECTURE.md)
Sensors (process/window, log tail, Raw Input, WGC frames, WASAPI audio) -> **typed events on the
EventBus** -> perception (state machine, map identifier, prompts/HUD/flow readers, heartbeat) ->
**Localizer** (particle filter + PathMatcher) -> **MatchModel** (gens, gates, terror ring, strategy) ->
**AppBridge** (30 Hz, change-only signals) -> QML (Dashboard window, Minimap pane window).
Frames and audio blocks never go on the bus (latest-frame mailbox / callbacks); only observations do.
Everything is replayable: record events -> replay -> same outputs.

## 5. Conventions (violations are bugs)
- **Units/frames**: metres; map frame origin top-left of the map bbox, +x right, +y DOWN; headings in
  degrees, 0 = map up, clockwise. Camera-relative intent: W=0, D=90, S=180, A=270. Timestamps: `ts_ns`
  from the single monotonic clock. See `core/geometry.py`.
- **Events** are frozen dataclasses with defaults for new fields; they must round-trip through
  `to_record/from_record` (tests/test_core.py). Renames need RECORD_SCHEMA_VERSION bump + migration.
- **Threads**: bus handlers run on one dispatcher thread (keep them < 1 ms); sensors on their own threads;
  UI reads immutable snapshots. Never touch Qt objects off the Qt thread.
- **Failure policy**: optional services degrade (ServiceHealth "degraded"/"down" + human detail) and the
  app keeps running. Never crash the pane mid-trial.
- **Data honesty**: unverified knowledge is labelled (`verified: false`, `status: stub`, `calibrated: false`)
  and the UI shows lower confidence. Never invent coordinates, log formats, prompt strings or bpm values.
- **Style**: Python 3.12, type hints everywhere, ruff (config in pyproject), small pure functions for
  testability, Windows-only code guarded by `sys.platform == "win32"` and tests marked `windows_only`.
- **UI**: tokens from `ui/theme.py` only; vector, pixel-snapped, no blur; zero QML warnings.

## 6. How we work (multi-agent protocol)
1. Lead reads `docs/HANDOFF.md` (latest entry) and `docs/ROADMAP.md`, plans the milestone, freezes any
   contract changes FIRST (events, settings, map schema) and commits them before fanning out.
2. Fan out subagents (definitions in `.claude/agents/`, all `model: opus`, `effort: high`) on
   independent lanes. Use `isolation: worktree` for lanes that touch many files; one branch per lane
   (`m3/vision-state-classifier`). Give each a precise brief: goal, files, acceptance tests, constraints.
3. Each lane: tests first, implementation, `uv run pytest -q` + `uv run ruff check .` green, report with
   what was verified on hardware vs. what needs the user.
4. Lead merges lanes one at a time, re-running the full suite after each merge.
5. **fair-play-reviewer** reviews the merged diff; BLOCK findings are fixed before the milestone closes.
6. Lead appends to `docs/HANDOFF.md` (state, next steps, open questions) and `docs/DECISIONS.md`
   (decision, reason, alternatives) at the end of every session. Commit with a clear message.

## 7. Hardware-in-the-loop (you cannot run DBD - the user can)
Things only the user can do: run the game, play custom games/KYF, enable in-game options, look at the
screen. When you need ground truth, ask with an exact script, e.g. "Start a Custom Game on Coal Tower
with bots, run `uv run python -m deadbymapper --record`, press F9 at each generator you touch, play 3 min,
quit, tell me the folder name." Recordings land in `%LOCALAPPDATA%\DeadByMapper\recordings\`
(docs/DATA_COLLECTION.md). Batch your asks: one session should serve several lanes.

## 8. Gotchas we already hit
- QML: an `id` may not shadow a JS global (`gc`, `print`, `escape`...). Item has a built-in `state`
  property - use `genState`. Root `QtObject` window properties can't be read via `QObject.property()`
  from Python (QQuickWindowQmlImpl) - find windows with `QGuiApplication.topLevelWindows()` by title.
- Tear down `QQmlApplicationEngine` before the bridge, or QML logs "Cannot read property of null".
- Offscreen Qt for tests/screenshots: `QT_QPA_PLATFORM=offscreen`, `QT_QUICK_BACKEND=software`.
- DBD runs on UE5 since patch 7.7.0: configs live in `...\Saved\Config\WindowsClient\` (older:
  `WindowsNoEditor`; Game Pass may use `WinGDK`). DBD writes a NEW `DeadByDaylight.log` every launch.
- Transparent overlays cannot draw over exclusive fullscreen: recommend Windowed Fullscreen, or put the
  pane on a second monitor. Capture still works.
- Particle-filter "stuck" handling must be a shared gate, not per-particle coin flips (biases speed).
- Raw Input INPUTSINK is system-wide: never bypass `InputGate` (input/privacy.py) - it is what keeps
  `--record` from logging keystrokes typed in other apps.
- F9/F10 hotkey marks are landmark fixes AND labels; the localizer de-duplicates a mark and a perceived
  interaction within 3 s. In replay, marks come from the recording (HotkeyMapper is live-only).
- Choose the HEAVIEST cluster for the estimate, not the densest bin; keep hysteresis between clusters.
