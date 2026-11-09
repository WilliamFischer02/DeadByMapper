# Roadmap

The end goal: double-click **DeadByMapper** on the Desktop. It links to DBD on its own. Outside a
trial it shows the dashboard. In a trial it shows a glanceable minimap pane with:
- you
- the generators and the next one to do
- the exit gates
- killer distance from the heartbeat
- later: gen states and the tiles you discover

Each milestone ends with these steps:
- tests and ruff green;
- the localization bench pasted (if touched);
- a fair-play review;
- HANDOFF.md and DECISIONS.md updated;
- a short "what the user must do next" list.

| # | Milestone | Lanes (agents) | Needs the user for | Status |
|---|---|---|---|---|
| M0 | Scaffold: contracts, sim, localization, demo UI, CI, packaging scripts | - | - | **done** |
| M1 | Sensors on real hardware + first exe on the Desktop | capture, input, release, lead | run builds, 1 recorded custom game | next |
| M2 | Calibration wizard (mouse yaw, keybinds, HUD scale) | input, ui, vision | 2-minute wizard run | |
| M3 | Perception I: match state, map identification, interaction prompts, gen counter, optical flow | vision, lead | 3-5 recorded trials incl. pause-menu opens | |
| M4 | Map editor + first 3 verified maps | map-data, ui | reference images, one custom game per map | |
| M5 | Localization on real recordings (tuning + new cues) | localization | recorded trials on verified maps, with F9 marks | |
| M6 | Heartbeat ring (audio) | audio | one KYF/custom session with a killer walking in | can start after M1 |
| M7 | UI polish + interactions (tap, ghost rings, heading drag, hold-to-interact, review) | ui | visual sign-off | |
| M8 | Vision II: gen states, exit gates when seen, live tile/pallet/window discovery | vision, localization, ui | recordings | |
| M9 | Release hardening: installer, autostart, crash reporting (local), opt-in update check | release | install test on a clean user profile | |

## M1 - Sensors on hardware + first exe (get the end-to-end path real early)
**Goals**
- Raw Input verified:
  - struct sizes on x64;
  - left/right modifier resolution;
  - CPU at 1000/4000/8000 Hz;
  - `InputGate` working: nothing is published while another app is foreground.
- WGC capture verified. It captures the DBD window in borderless and windowed modes, and the
  exclusive-fullscreen behaviour is documented. Downscaled frames reach the mailbox at 30 fps,
  and the cost is measured.
- Process and window tracking, log tail (new file per launch, rotation), and display settings read
  from the ini are all verified.
- Hotkeys F9, F10 and F8 produce `UserMark` events.
- `packaging/build.ps1` produces `DeadByMapper.exe`, and `install.ps1` creates the Desktop and
  Start Menu shortcuts.

**Acceptance**
- The user double-clicks the Desktop icon and sees the dashboard. With DBD running, the Process,
  Window, Input and Capture chips are green.
- A 3-minute recorded custom game exists, and `tools/recording_info.py` shows its marks.
- With a map picked manually and F9 at generators, the pane shows a breadcrumb and then the dot.
  (Real maps are still stubs, so this check uses breadcrumb mode or the Test Arena in the demo.)

## M2 - Calibration
**Goals**
- Mouse deg/count from a slow 360-degree spin (capture-assisted, spread check, persisted).
- Keybind confirmation ("press your sprint key"), plus best-effort ini parsing.
- Resolution and HUD scale captured for ROI scaling.
- Dashboard calibration card with a re-run button.

**Acceptance:** three spins agree within 2 %, and a replayed spin reproduces the same value.

## M3 - Perception I
**Goals**
- Screen-state classifier (menu, lobby, loading, in-match survivor, tally, pause overlay).
- Map identification:
  - a verified log rule, if one exists;
  - otherwise intro-title OCR and pause-menu OCR.
- Interaction-prompt reader for generators and exit gates.
- Generator-counter reader.
- Optical-flow `translating_prob` running live.

**Acceptance (on held-out recordings, labelled by F9 marks and manual notes)**
- Match-state accuracy of 98 % or better per second.
- Map correct within 10 s of trial start in at least 95 % of trials.
- Interaction start detected within 1 s for at least 90 % of F9 marks.
- False interactions: fewer than 1 per 10 minutes.

## M4 - Map editor + first verified maps
**Goals**
- In-app editor for personal reference overlays:
  - scale from border posts (8 m);
  - trace bounds and solids;
  - place generator and gate candidates;
  - "mark here" when LOCKED.
- Author and verify 3 maps the user plays most.

**Acceptance:** `validate_maps.py` passes, and each map is verified in a custom game (generator
spots within 2 m of where F9 marks land when LOCKED).

## M5 - Localization on real data
**Goals:** tune on replays and add these cues:
- vault/pallet bursts;
- on-hit sprint;
- haste and hindered;
- the fixed-lag smoother for retro-marking generators;
- multi-floor handling where needed.

**Acceptance (replays on verified maps)**
- Within 3 m after the 3rd landmark in at least 75 % of trials.
- False-LOCKED (LOCKED while more than 5 m off) at most 1 % of locked time.
- Median lock time of 90 s or less from first generator touch.

## M6 - Heartbeat ring
**Goals**
- Process loopback (fallback: device loopback).
- `TierModel` fitted from a measured session.
- Chase detection.
- Optional Visual Terror Radius reader.

**Acceptance:** tier correct 90 % of the time against labelled stops, and 400 ms or less from a
heartbeat change to the ring changing.

## M7 - UI polish + interactions
**Goals**
- Tap "I am here" and tap a ghost ring to confirm it.
- Drag from the dot to fix heading.
- Long-press a gen to mark it completed or not spawned.
- Hold-modifier to make the pane interactive.
- Settings page.
- Post-match route review.
- First-run onboarding: 3 cards, under 60 s.

**Acceptance:** the docs/UI_DESIGN.md checklist passes at 100/150/200 %, and the user signs off on
screenshots.

## M8 - Vision II (the "future features" list)
- **Generator states:**
  - completed (its lights turn on, visible across the map);
  - regressing (only where the game shows it to the player);
  - the remaining-gens counter.
- **Exit gates:** detected when seen on screen, or confirmed by F10 / interaction.
- **Live tile discovery:**
  - recognise pallets, windows and walls in view;
  - stamp them into the map at the estimated pose and bearing (`ObjectSighted`);
  - persist only for the current trial.
- **Fair-play rule:** only things on the player's screen or already discovered by them. No auras the
  player can't see, nothing through walls.

## M9 - Release hardening
- Inno Setup or MSIX installer (or keep robocopy + shortcuts if the user prefers).
- Start with Windows (opt-in).
- Crash logs kept locally, with "open log folder".
- Opt-in update check.
- Code signing is optional and costs money, so ask.
- Clean-profile install test.

## Parallelism map
- M1 lanes: capture, input and release run in parallel. Audio (M6) can start as soon as M1
  capture lands, because it only needs audio recordings.
- M3 needs M1 recordings.
- M4 can start in parallel with M3. The map editor is pure UI plus world code.
- M5 needs M3 + M4.
- M7 runs alongside everything; ui-engineer owns it.
- M8 comes after M5.
