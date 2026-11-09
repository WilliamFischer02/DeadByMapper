# Data collection - how the user feeds the agents ground truth

Agents can't run Dead by Daylight. The user can. Every session the user records serves several
lanes at once, so batch your asks.

## Recording a session
```
uv run python -m deadbymapper --record          # dev
"%LOCALAPPDATA%\Programs\DeadByMapper\DeadByMapper.exe" --record   # installed exe
```
- Sessions are written to `%LOCALAPPDATA%\DeadByMapper\recordings\<YYYY-MM-DD_HHMMSS>\`. They
  contain `meta.json`, `events.jsonl` and (M3+) an optional downscaled `frames.mp4` plus
  `frames.idx`.
- Recordings are **private and local**:
  - they are never committed (git-ignored);
  - they are never uploaded;
  - they are deleted whenever the user wants.
- Only gated input is recorded: movement/action keys and app hotkeys, and only while DBD is
  foreground (docs/FAIR_PLAY.md, "Not a keylogger").
- First look at a session:
  `uv run python tools/recording_info.py <session> --marks`.

## Hotkeys (observe-only; the key still reaches the game)
| Key | Mark | Use it when |
|---|---|---|
| **F9** | `generator` | you start repairing a generator (also gives the localizer a fix) |
| **F10** | `exit_gate` | you are at an exit gate switch |
| **F8** | `note` | anything worth a look later: "dot is wrong now", "chase starts", "killer at 20 m" |

## Standard scripts (copy, adjust, and send the user ONE script per session)

**S1 - Sensor smoke test (M1, 3 min)**

1. Start DBD in **Windowed Fullscreen**. Run `--record`.
2. Main menu: wait 10 s. Open a custom game (or KYF) with bots on any map.
3. In the trial:
   - walk, sprint and crouch for 60 s;
   - spin slowly 360 degrees twice;
   - alt-tab out and back once;
   - press F9 at 2 generators while repairing for a few seconds;
   - open and close the pause menu once.
4. Leave the match and quit DBD. Tell us the session folder name, the map, and your mouse DPI and
   in-game sensitivity.

**S2 - Map identification + states (M3, ~15 min)**

Do 3 short custom games on 3 different maps. In each one:
- watch the intro until the map name shows;
- open the pause menu once (it shows the map name);
- F9 at every generator you touch;
- F10 at an exit gate if you get there.

Also copy `%LOCALAPPDATA%\DeadByDaylight\Saved\Logs\DeadByDaylight.log` after each game, because
DBD starts a NEW log every launch.

**S3 - Heartbeat calibration (M6, KYF with a friend, or bots if they approach)**

Survivor stands still. The killer walks straight in from about 40 m and stops for 10 s at 30, 20
and 10 m. Press F8 at each stop and say the distance out loud (for your own notes). Then do one
chase with F8 at its start and end.

**S4 - Map verification (M4, per map)**

Custom game on the map:
- touch every generator spot you can find (F9 at each);
- visit both gates (F10);
- walk the outer border once.

The editor overlays the marks on the authored map.

**S5 - Localization tuning (M5)**

Normal play, recorded, on verified maps. Press F8 whenever the dot is visibly wrong.

## What agents do with a session
1. Run `recording_info.py`. Check durations, states, marks and maps.
2. Replay through the pipeline:
   `uv run python -m deadbymapper --replay <session>` (UI), or a test harness with `ManualClock`.
3. Turn small, approved crops into test fixtures. Ask the user first and never include other
   players' names. Every other analysis stays local.
4. Report numbers back in the lane report, then update HANDOFF.md.
