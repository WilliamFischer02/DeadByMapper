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
  -> HeartbeatAnalyzer.feed()  -> every 250 ms analyze():
       band energy 30-140 Hz (1024-pt FFT, 10 ms hop) -> onset = positive diff of the log envelope
       -> autocorrelation over 4 s -> tempo (40-180 bpm) + periodicity score
       -> TierModel(bpm) -> tier 0..3   (silence / no periodicity -> tier 0)
       (M6: level-based TierModel if tempo turns out constant; chase flag from a chase-music detector)
  -> TerrorRadiusObserved(tier, bpm, confidence, source=AUDIO)   [publish on change or 1 Hz]
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
Analysis takes about 1 ms per 250 ms tick. The audio callback only does resample-and-append. The
latency target from heartbeat change to ring change is 400 ms or less.
