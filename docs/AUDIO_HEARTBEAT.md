# Killer distance from the heartbeat (audio lane, M6)

## What the player already hears (docs/RESEARCH.md)
- The **terror radius (TR)** is centred on the killer: 32 m for most 4.6 m/s killers, 24 m for
  4.4 m/s killers, and exceptions such as Mastermind's 40 m. Perks, add-ons, powers and Undetectable
  change it or remove it.
- The **heartbeat** gets louder and faster as the killer gets closer. The TR music has layers:
  layers 1-3 split the radius into three equal bands (far, middle and near thirds), and in a chase
  the chase music replaces layer 3.
- The heartbeat is **non-directional**: you know *how far*, not *where*. So the pane draws an
  **annulus around you** (from inner to outer radius of the current band) and never a direction or a
  dot.

## Pipeline
```
WASAPI loopback (process-specific preferred)  ->  MonoResampler16k (downmix, anti-alias, 16 kHz, stateful)
  -> HeartbeatAnalyzer.feed()  -> every 100 ms analyze():
       band energy 30-140 Hz (1024-pt FFT, 10 ms hop) -> onset = positive diff of the log envelope
       -> autocorrelation over 4 s -> tempo (40-180 bpm) + periodicity score
       -> TierModel(bpm) -> tier 0..3   (silence / no periodicity -> tier 0)
       (M6: level-based TierModel if tempo turns out constant; chase flag from a chase-music detector)
  -> TerrorRadiusObserved(tier, bpm, confidence, source=AUDIO)   [publish on change + <= 1 Hz keep-alive while tier > 0]
  -> MatchModel: inner = TR*(3 - tier)/3, outer = TR*(4 - tier)/3   (tier 3: 0..TR/3; tier 1: 2TR/3..TR)
  -> TerrorRing.qml: annulus, eased 380 ms, opacity beats at bpm
```

## Unknowns that MUST be measured (never invent them)
| Unknown | How to measure (one KYF / custom game with a friend or bots) |
|---|---|
| bpm (or level) per tier | Survivor stands still while the killer walks in from 40 m in a straight line, stopping for 10 s at 30, 20 and 10 m. Record with `--record` and F8 marks at each stop, then fit the thresholds in `TierModel`. |
| Does tempo change, or only loudness/layering? | The same recording shows it. If only the level changes, switch `TierModel` to level ratios against a per-session baseline. |
| Chase music signature | Have the killer chase you, F8 at chase start and end, then train a tiny template matcher on the chase stinger. |
| Interference | Record with Discord voice, music and other killers' power sounds (Nurse blinks, Huntress lullaby). Process loopback isolates DBD from other apps, but not from the game's own sounds. |

Until then, `TierModel.calibrated = False`, confidence is capped at 0.5, and the ring is drawn as
"approximate".

## Alternative / complementary input: Visual Terror Radius
DBD has an accessibility option (Settings > Accessibility, off by default) that draws a beating heart
in the survivor's chest, in sync with the heartbeat. If the player enables it, the vision lane can
read the heart's beat and colour from a fixed ROI. That path is more robust than audio in noisy
setups. Make it a dashboard option ("I use Visual Terror Radius").

## Killer-specific radius
`KillerTerrorRadiusSet` comes from the player choosing the killer in the dashboard once they
recognise it in the trial. The player can perceive that, so it is allowed. **Never** infer the
killer from lobby logs or files (docs/FAIR_PLAY.md).

The default is 32 m. Show a hint chip: "Smaller ring? Set the killer (24 m for 4.4 m/s killers)."

## Failure behaviour
- No audio device, or loopback not available: the ring is hidden and the `Audio` chip turns amber
  or red.
- Device loopback only, so other apps are mixed in: health shows `degraded` with the hint "mute
  music/voice for best results".
- Undetectable or an empty band: no ring. Never draw a ring the player could not have heard.

## Budget
`analyze()` takes about 0.05 ms per tick (measured), so the pipeline analyses at 10 Hz. The audio
callback only copies, downmixes and enqueues; resampling and analysis run on the `audio.heartbeat`
thread. The latency target from heartbeat change to ring change is 400 ms or less.

## Implementation status (M1, branch m1/audio-loopback)
Code: `audio/loopback.py` (service + supervisor), `audio/sources.py` (backends),
`audio/wasapi_com.py` (ctypes COM), `audio/pipeline.py` (analysis thread), `audio/blocks.py` (pure
helpers), `audio/resample.py`, `audio/heartbeat.py`. Probe: `tools/audio_probe.py`.

| Piece | State |
|---|---|
| Device loopback (PyAudioWPatch 0.2.12.9, `get_default_wasapi_loopback`, callback stream, float32 with int16 fallback) | **Working, verified on the user's PC** (Volt 2 interface, 44.1 kHz stereo). |
| Process loopback (`ActivateAudioInterfaceAsync` + `AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK`, `INCLUDE_TARGET_PROCESS_TREE`, 16-bit 48 kHz stereo via AUTOCONVERTPCM, event-driven `IAudioCaptureClient`) | **Working, verified on the user's PC** (Windows build 26300): a child process's tone is captured (RMS 0.14), a tone from another process is not (RMS 1e-5), silence after the child stops is 1e-5. Also opened against the real `DeadByDaylight-Win64-Shipping.exe` (menu music, about -37 dBFS). |
| Mode selection (`auto` / `process` / `device`) | `auto` = process while the DBD pid is known (`GameProcessChanged` + one start-up psutil lookup), device otherwise or when process loopback fails (retried after 30 s). `process` = never falls back (health "degraded: waiting"). |
| Device change / unplug | Default endpoint id polled every 2 s (IMMDeviceEnumerator); a dead stream or a changed default reopens with exponential backoff (0.5 s to 10 s). Health "down" with the error while retrying. |
| Gaps / underruns | Gaps longer than 200 ms are filled with silence so the tier decays to 0. PortAudio status flags and WASAPI `DATA_DISCONTINUITY` are counted as xruns. The queue drops the OLDEST block when full (64 blocks, about 640 ms) and counts drops. |
| Tier mapping | Still the placeholder `TierModel` (`calibrated: false`, confidence <= 0.5). No bpm values are known. |
| Lullaby / chase flags | Not detected yet (always False). |

Health strings (dashboard Audio chip):
- `ok`: `process loopback: Dead by Daylight (pid N), 48000 Hz`
- `degraded`: `device loopback: <device> (captures ALL system audio; process loopback unavailable: <reason>)`
- `degraded`: `process loopback: waiting (<reason>)` (forced process mode, game not running)
- `down`: `no loopback capture: <error>; retrying in <s> s`

Measured on the user's PC (i5-13600K, idle desktop + background audio):
- CPU: about 4 % of ONE core for the whole process in steady state: analysis thread 2.3 %, PortAudio
  callback thread 1.3 %, supervisor 0.1 %.
- Capture latency (PlaySound call -> block reaches the analyser): 3-17 ms.
- In-band latency (80 Hz tone start -> 0.3 s band level +6 dB at an analysis tick): about 130 ms.
- The 4 s band level needs 230-410 ms for +6 dB, and the TEMPO estimate needs 1-2 s (it is an
  autocorrelation over 4 s). So a tempo-based tier cannot meet the 400 ms budget; a level-based tier
  (`HeartbeatDiag.level_fast_db`, relative to a per-session baseline) can. Decide in M6 from S3.

### Probe during a custom game (user)
`uv run python tools/audio_probe.py --mode auto --seconds 120` while a killer bot or friend walks
toward you (stop at about 30, 20 and 10 m). Each line shows the mode, device, levels, the bpm
candidate, periodicity, tier and the queue stats. It ends with the published tier timeline. The WAV
(the analyser input, mono 16 kHz) is written to `%LOCALAPPDATA%\DeadByMapper\probes\audio\` and
never leaves the machine.

### Next step (M6)
1. Session S3 (see above) -> fit `TierModel` (bpm and/or `level_fast_db` vs. distance) from the
   probe WAVs/recordings. Mark `calibrated: true` only then.
2. Choose the tier feature (tempo vs. level) from the S3 data. If tempo changes, add tempo
   hysteresis. If only the level changes, add a level-ratio TierModel against the session baseline.
3. Chase-music detector, lullaby handling, tier hysteresis, confusion matrix on held-out clips.
