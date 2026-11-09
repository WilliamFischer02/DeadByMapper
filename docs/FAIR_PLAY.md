# Fair play, anti-cheat and data policy (BINDING for every contributor and agent)

DeadByMapper exists to help one player **learn** maps, loops and generator macro. It must never become
a cheat. Two reasons: it is the right thing to do, and the user's account depends on it (BHVR bans
third-party tools that grant an unintended unfair advantage; Easy Anti-Cheat bans tools that touch the
game process).

## The test for every feature
> Could the player get this information themselves, right now, from their own screen, their own
> ears, their own hands, or public knowledge - and write it on a notepad?
If yes, DeadByMapper may *organise and visualise* it. If no, it is out of scope, full stop.

| Feature | Where the information comes from | Verdict |
|---|---|---|
| Possible generator / gate / hatch spots | Public map knowledge (wiki, guides), authored by the user | OK |
| Map name | Shown on screen at trial start and in the pause menu | OK |
| Your position | Dead reckoning from YOUR inputs + YOUR screen + public map layout | OK |
| Generators you saw/repaired, gen counter | Your HUD and your own actions | OK |
| Completed vs regressing gens (future) | Only gens currently or previously visible on your screen | OK |
| Exit gate found (future) | Gates you saw or touched | OK |
| Killer distance ring | Heartbeat / terror-radius layers you hear (BHVR ships a visual version) | OK - band only, no direction |
| Tiles, pallets, windows (future) | Objects visible on your screen, remembered | OK |
| Killer position / direction / identity before reveal | Not perceivable | **FORBIDDEN** |
| Other survivors' positions (no aura) | Not perceivable | **FORBIDDEN** |
| Hidden auras, totem/hex locations you have not seen | Not perceivable | **FORBIDDEN** |
| Lobby log parsing for killer/perks/add-ons | Not perceivable before the trial | **FORBIDDEN** |
| Auto skill checks, macros, auto-wiggle, input automation | Synthesised input | **FORBIDDEN** |

## Allowed techniques
- Screen capture via Windows.Graphics.Capture or DXGI Desktop Duplication (what OBS/Discord use).
- Audio via WASAPI loopback (process-specific preferred).
- Raw Input registration with RIDEV_INPUTSINK on our own window (observe only).
- Reading `DeadByDaylight.log` and the game's `.ini` files with shared read access.
- Our own windows (always-on-top, click-through) drawn by our own process.
- App hotkeys (F9 generator / F10 exit gate / F8 note) observed from the same gated Raw Input stream:
  never consumed, never blocked, only while the game is foreground; choose keys the game does not use.

## Forbidden techniques (BLOCK in review)
OpenProcess with VM rights, ReadProcessMemory/WriteProcessMemory, pymem/Frida/Cheat-Engine-style
scanning, DLL injection, SetWindowsHookEx into the game, D3D/DXGI Present hooks, ReShade-style shader
injection, packet capture/decryption, editing game files (pak, ini, saves), SendInput/keybd_event/
mouse_event or any input synthesis, attaching debuggers, reading GPU memory of the game, or anything
that tries to hide from anti-cheat. Also no telemetry or network calls without the user's explicit OK.

## Honesty about risk
Passive observation is the pattern used by tolerated tools (crosshair overlays, hook counters, map
overlay apps). It is still third-party software, and BHVR alone decides what counts as an unfair
advantage. The README tells the user this plainly; features stay inside the table above; the
fair-play-reviewer agent blocks anything outside it.

## Data, copyright and privacy
- Do not bundle or commit third-party images (community map art, wiki images, game textures/icons).
  Personal reference images live in `%LOCALAPPDATA%\DeadByMapper\overlays\` and are referenced by
  file name only.
- Committed map data = coordinates the user authored or measured, with `sources` noting what they
  were checked against.
- Session recordings contain the user's screen and audio. They stay local and git-ignored; tests may
  only include small cropped fixtures the user explicitly approved.
- **Not a keylogger.** Raw Input with INPUTSINK sees input system-wide, so `input/privacy.py`
  (`InputGate`) drops everything at the source except the keys the app needs
  (`KeyMap.allowed_vks()`: bound game actions + app hotkeys), and only while the DBD window is the
  foreground window. Mouse deltas are gated the same way. Never widen this without the user's OK.
- Other players' names on screen must never be stored by perception code (crop ROIs exclude them;
  OCR never runs on name plates).

## Review checklist (fair-play-reviewer)
- [ ] No forbidden API or package added (grep: OpenProcess, ReadProcessMemory, pymem, frida,
      SetWindowsHookEx, SendInput, keybd_event, mouse_event, CreateRemoteThread, VirtualAllocEx).
- [ ] Every new on-screen element maps to a row marked OK above.
- [ ] No new network access, no telemetry.
- [ ] Input gate intact: only `KeyMap.allowed_vks()`, only while DBD is foreground (tests/test_input_privacy.py).
- [ ] Recordings/overlays stay out of git; fixtures approved.
