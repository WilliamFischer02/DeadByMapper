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
