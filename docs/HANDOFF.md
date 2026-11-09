# HANDOFF - session log (newest first)

Each session, the lead agent appends an entry at the top. Keep it to 1 screen: state, what changed,
what's next, what we need from the user. Details go in DECISIONS.md, ROADMAP.md and the PRs.

```
## <YYYY-MM-DD> - <milestone> - <one-line summary>
**State:** tests <n> passing · ruff clean · bench (fix3/fix4/false-lock): <..>
**Changed:** <bullets, with paths>
**Verified on hardware:** <what the user ran, results> | none
**Next (in order):** <1-5 bullets, each with owner lane>
**Needs from the user:** <exact scripts (docs/DATA_COLLECTION.md) or decisions>
**Open questions / risks:** <bullets>
```

---

## 2026-11-08 - M1 - Sensors verified on hardware, first exe on the Desktop, manual trial control
**State:**
- 249 tests passing, 2 skipped (speaker-playing audio tests, opt-in with `DBM_AUDIO_HW_TESTS=1`); ruff clean.
- Localization bench untouched this session (30 seeds, 4 visits): fix 3 76.7 %, fix 4 93.3 %, median 1.13 m,
  false-LOCKED 0.00 %, 0.89 ms/tick.
- `DeadByMapper.exe` builds (PyInstaller one-folder, ~275 MB, 1,713 files, ~2 min build), installs to
  `%LOCALAPPDATA%\Programs\DeadByMapper` with Desktop + Start Menu shortcuts, cold start 0.75-0.93 s to the
  dashboard. Crash reports go to `%LOCALAPPDATA%\DeadByMapper\logs\crash-*.txt`.
- Fair-play review of the merged diff: BLOCK on one privacy regression (a title-only window match could make
  a Notepad/Explorer window titled `DeadByDaylight.log - ...` the InputGate's game window and the capture
  target) - fixed: app matchers now require the game exe (title only counts for an unreadable exe on an
  `UnrealWindow`), regression tests added. All other findings fixed the same session: state machine tracks
  every self-published event (async bus), audio activation timeout parks the COM handler, stop() cannot
  leave a live stream, `auto` audio captures nothing without the game, probes save media only on request,
  replay skips recorded `MatchStateChanged`, the no-injection/no-OpenProcess greps cover src/tools/packaging.

**Changed:**
- Baseline fix: zero-arg `super()` in a slots dataclass broke test collection on CPython 3.12 (`core/events.py`).
- Contracts (lead): `UserTrialControl(action, role)`; `MatchStateChanged.manual`; `MatchSnapshot.manual_trial`;
  `CaptureSettings.capture_fps`; `App.capture` handle; `MouseDelta` may be a batched sum (<= ~4 ms).
- `perception/state_machine.py`: manual start/stop, ignores screen observations during a manual trial, adopts
  foreign `MatchStateChanged` (demo/replay); attached in every mode.
- `input/`: Raw Input rewritten and verified on x64 (struct sizes, registration, INPUTSINK, L/R modifiers,
  GetRawInputBuffer batching: 0.72 % of a core at 8 kHz extrapolated vs 5.3 % unbatched). Gate verified:
  600 raw messages, 0 published without the game. No OpenProcess anywhere (test enforces). `tools/input_probe.py`.
- `capture/`: WGC window capture via windows-capture 2.0.1, verified on the real game (1920x1080 windowed
  fullscreen, 30.0 fps, 0.4-1.1 % CPU, callback p95 1.4 ms). OpenCV never runs on the native callback thread
  (crash on re-attach fixed). Window tracker publishes physical rects + display mode. `tools/capture_probe.py`,
  `docs/CAPTURE.md`. DXGI fallback is an honest "down".
- `audio/`: process-specific WASAPI loopback (ctypes COM, verified isolated per process and attached to the
  live DBD pid) with device-loopback fallback ("degraded"); tier mapping still `calibrated: false`.
  `tools/audio_probe.py`. Beep-to-analyser latency 3-17 ms; whole process ~4 % of one core.
- `ui/`: Start/Stop trial control, map picker (Test Arena first, stubs say "breadcrumb only"), sensor tiles
  with inline reasons, dashboard nits fixed, pane pixel pass at 1x/1.5x/2x, overlay ex-styles read back
  (TOPMOST | TRANSPARENT | TOOLWINDOW | LAYERED | NOACTIVATE in live click-through).
- `packaging/`: version resource, Qt trim (-440 files), crash handler, robust smoke test, install warm-up,
  `uv.lock` committed. `__main__` caps OpenCV at 1 thread. `--screenshot` sets `QT_QPA_FONTDIR` on Windows.

**Verified on hardware:** everything above marked "verified" ran on the user's PC (i5-13600K, RTX 5070, Windows
11, 1920x1080 at 100 %, three monitors). Dead by Daylight happened to be running during the session, so the
window title (`'DeadByDaylight  '`, class `UnrealWindow`, exe `DeadByDaylight-Win64-Shipping.exe`, Steam),
display mode, WGC capture and process loopback were observed against the real game. Not yet verified by the
user: F9/F10/F8 marks in a trial, L/R modifiers on a real keyboard, 8 kHz mouse, exclusive fullscreen,
heartbeat tiers (no bpm data yet), Desktop icon double-click.

**Next (in order):**
1. **user:** hardware session S1 + probes (this handoff's "Needs from the user").
2. **lead:** replay S1 through the pipeline (`tools/recording_info.py --marks`); confirm the breadcrumb + marks.
3. **input-engineer (M2):** calibration wizard backend (spin, keybind capture, ini parsing).
4. **vision-engineer (M3):** first perception worker on `App.capture.mailbox` (state classifier + map title OCR).
5. **audio-engineer (M6):** fit `TierModel` from S3; level-based tier (tempo cannot meet the 400 ms budget).
6. **capture-engineer:** DXGI duplication fallback if S1 shows exclusive-fullscreen use.

**Needs from the user:** see the session message (script S1, probe commands, settings questions).

**Open questions / risks:**
- Log rules, prompt strings, HUD positions, bpm per tier: still unverified placeholders.
- `windows-capture` pulls full `opencv-python` next to `opencv-python-headless`; last install wins. Pick one (M9).
- AltGr sends a fake LCtrl (could read as crouch) - watch in S1.
- UIPI detection is a passive heuristic (3 s of silence after focusing a non-game window) - can false-positive.
- Process loopback may hear silence if DBD is routed to a non-default device or muted in the mixer; health
  would still say ok.
- Windows 11 rounds windowed-mode client corners (a few black pixels) - ROIs must avoid extreme corners.
- Project agent types are not registered in this environment; lanes ran as general-purpose Opus/high agents.

---

## 2026-11-08 - M0 - Scaffold delivered (pre-agent baseline)
**State:**
- 84 tests passing (Linux, offscreen Qt); ruff clean.
- Localization bench (30 seeds, 4 visits, 1.5 % yaw error):
  - fix 3: 76.7 % within 3 m; fix 4: 93.3 %;
  - final error median 1.13 m, p90 1.61 m;
  - false-LOCKED 0.00 %;
  - 1.15 ms per tick;
  - only 16/30 runs reached LOCKED within 4 visits (the others stay CONVERGING, which is honest
    but slow).

**Changed (everything is new):**
- `core/` (events, bus, clock, geometry, lifecycle)
- `config/`
- `world/` (MapData, registry with fuzzy matching, MatchModel, 3-gen strategy)
- `localization/` (occupancy, motion and measurement models, particle filter, PathMatcher,
  Localizer)
- `sim/` (A* planner, survivor/killer sim, demo driver)
- `gamelink/` (process, window, log tail and parser, ini reader)
- `input/` (Raw Input listener, keymap, input state, calibration maths, privacy gate, F9/F10/F8
  hotkeys)
- `capture/` (WGC scaffold, mailbox, ROIs)
- `perception/` (state machine, map identifier, OCR backends, prompt interpreter, optical flow,
  interfaces for the HUD and state classifier)
- `audio/` (heartbeat DSP, streaming resampler, loopback scaffold)
- `recording/`
- `ui/` (bridge, theme tokens, QML dashboard + pane, components)
- packaging (PyInstaller spec, build/install scripts, icon tool)
- scripts, CI
- 47 stub maps + the Test Arena
- docs + ADRs
- `.claude/agents/` (9 Opus high-effort subagents) and `.claude/settings.json`

**Verified on hardware:** none yet. Everything Win32-specific is written against the documented APIs
but is unverified. That is M1.

**Next (in order):**
1. **release-engineer:** run `scripts/bootstrap.ps1` and then `packaging/build.ps1` on the user's PC,
   so the Desktop shortcut works in week 1. Commit the generated `uv.lock`.
2. **input-engineer:** Raw Input on hardware (structs, CPU at the user's polling rate, gate
   behaviour).
3. **capture-engineer:** WGC on the DBD window (borderless), frames into the mailbox, cost.
4. **lead:** wire the first perception worker skeleton to the mailbox; collect session S1.
5. **audio-engineer** (parallel): process-loopback capture behind `LoopbackHeartbeatService`.

**Needs from the user:**
- Run `scripts/bootstrap.ps1`, then `uv run python -m deadbymapper --demo` (sanity check).
- Tell us: mouse DPI, in-game sensitivity, display mode, resolution, HUD scale, keybind changes
  from the defaults, and which maps you play most (for M4).
- Then record session S1 (docs/DATA_COLLECTION.md).

**Open questions / risks:**
- Log lines that name the map are unknown (M3 probe).
- Interaction prompt strings and HUD positions are unknown (M3 capture).
- bpm per heartbeat tier is unknown (M6).
- No real map coordinates exist yet (M4 is on the critical path for a useful minimap).
- Exclusive fullscreen blocks overlays: recommend Windowed Fullscreen.
- Python CPU at 8 kHz mouse polling is unmeasured.
- UI nits:
  - the dashboard map list clips its last row mid-text (add a fade);
  - the game-link card's status dot has no label.
