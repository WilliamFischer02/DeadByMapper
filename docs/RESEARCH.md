# Research notes - what is known about DBD maps, signals and policy (Nov 2026)

Compiled 2026-11-08 for DeadByMapper v0.1. Everything here is paraphrased from public sources (list
at the bottom). **Re-verify after major patches.** Items marked *unverified* must be confirmed from the
user's own recordings before any code relies on them.

## 1. How DBD maps are built
- **Procedural, tile-based.** Maps are assembled from square/rectangular tiles that stay roughly in
  place but can rotate between trials. The wiki gives structure-tile footprints of 16x16, 16x32 and
  32x32 m. BHVR has said map generation follows rules (map shape, where unique buildings/landmarks go).
- **Maze tiles ("jungle gyms")** spawn in the same general area of a map each trial, but which
  iteration appears can change; most maze-tile types can hold a generator.
- **The game's own ruler:** one outer border-wall section, post centre to post centre, is exactly 8 m
  (some maps, e.g. The Pale Rose, use 16 m double sections). The community measures map area in
  8x8 m "square tiles"; the average map is about 144 of them. -> DeadByMapper's grid is 8 m and the
  map editor calibrates scale by clicking two border posts.
- **Indoor / multi-floor maps:** Treatment Theatre, The Game, Midwich, The Underground Complex and the
  three Raccoon City Police Station variants are indoor; several are multi-level. Hartshorne Mall (PTB
  10.3.0, expected 24 Nov 2026 with the Terrifier chapter) has two floors with drop-downs.

### Live map list (patch 10.2.2a, re-checked 2026-11-01) - 45 maps, 20 realms
Sizes in 8x8 m tiles (1v4; 2v8 in brackets where known). Shelter Woods and Azarov's 1v4 sizes are
inconsistent in the source. Full RPD is 2v8-only. Haddonfield (Lampkin Lane) left the pool in January
2026 (Halloween licence ended).

| Realm | Maps (tiles) |
|---|---|
| MacMillan Estate | Coal Tower 132 (180), Groaning Storehouse 156, Ironworks of Misery 160, Shelter Woods ? (180), Suffocation Pit 160 (184) |
| Autohaven Wreckers | Azarov's Resting Place ? (184), Blood Lodge 156, Gas Heaven 156 (184), Wreckers' Yard 144 (176), Wretched Shop 164 |
| Coldwind Farm | Fractured Cowshed 152, Rancid Abattoir 140 (192), Rotten Fields 160, The Thompson House 152 (180), Torment Creek 156 (180) |
| Crotus Prenn Asylum | Disturbed Ward 152 (188), Father Campbell's Chapel 140 (180) |
| Backwater Swamp | The Pale Rose 161, Grim Pantry 168 |
| Léry's Memorial Institute | Treatment Theatre 98 |
| Red Forest | Mother's Dwelling 152 (200), The Temple of Purgation 136 |
| Springwood | Badham Preschool 144 |
| Gideon Meat Plant | The Game 142 |
| Yamaoka Estate | Family Residence 156 (184), Sanctum of Wrath 156 (192) |
| Ormond | Mount Ormond Resort 156 (180), Ormond Lake Mine 132 |
| Hawkins National Laboratory | The Underground Complex 138 |
| Grave of Glenvale | Dead Dawg Saloon 136 (180) |
| Silent Hill | Midwich Elementary School 113.5 |
| Raccoon City | RPD (2v8 only), RPD East Wing, RPD West Wing - not measured |
| Forsaken Boneyard | Dead Sands 140, Eyrie of Crows 148 |
| Withered Isle | Garden of Joy 164, Greenville Square 160, Freddy Fazbear's Pizza 148, Fallen Refuge 128 |
| The Decimated Borgo | The Shattered Square 144, Forgotten Ruins 132 |
| Dvarka Deepwood | Toba Landing 136, Nostromo Wreckage 152 |
| Sleepless District | Trickster's Delusion - not measured yet |

All of these exist as `status: "stub"` files in `data/maps/` (names, realms, sizes, sources). **No
public, machine-readable source of generator/gate coordinates was found** - those must be authored
(docs/MAP_SPEC.md). Community map images exist (callout maps, overlay apps), but they are other
people's work: use them only as personal reference, never commit them.

## 2. Objectives
- **Generators:** 7 spawn in a 1v4 trial, 5 must be repaired (2v8: 13 and 8; custom games scale
  down). Spawn spots are predetermined per map with more spots than generators, so a spot may or
  may not hold one in a given trial. Base repair is 90 charges (~90 s solo). Survivors do not see
  generator auras by default; completed generators show steady lights, incomplete ones flicker,
  regressing ones emit reddish sparks.
- *Unverified community claim:* generators spawn at least 16 m apart.
- **Exit gates:** two per trial (three in 2v8), on the map edge; indoor maps use predetermined gate
  spots; opening takes 20 s and starts the Endgame Collapse.
- **3-gen strategy:** a "3-gen" is three remaining generators close enough for the killer to
  patrol. Common advice: do central / clustered gens early, leave isolated edge gens for last.
  (So "near vs far" in this project means *distance between generators*, not distance to the exit
  gates.) -> `world/gen_strategy.py`.

## 3. Movement (datamined, DBD wiki "Movement Speeds")
| Survivor action | Speed |
|---|---|
| Running | 4.0 m/s (the 100 % reference) |
| Walking | 2.26 m/s |
| Crouching | 1.13 m/s |
| Crawling | 0.7 m/s initially, up to 1.05 m/s |
| On-hit sprint | 6.6 m/s for 1.8 s |
| Fast vault | +0.5 m/s during the animation |
| Acceleration | 10 m/s² (near-instant) |
Haste/hindered percentages apply to the base speed. Killers mostly move at 4.6 or 4.4 m/s.
Survivors move relative to the camera (third person) - see docs/LOCALIZATION.md.

## 4. Terror radius and heartbeat
- The terror radius has a heartbeat plus music layers. Layers 1-3 split the radius into **three
  equal-width distance bands** (far, middle, near third); during a chase, chase music replaces the
  near layer. -> the pane's killer ring is an annulus for the current third, centred on you (the
  heartbeat has no direction, so no fake direction is shown).
- Typical radius: 32 m for 4.6 m/s killers, 24 m for 4.4 m/s killers; Mastermind uses 40 m. Perks,
  add-ons, Undetectable and Oblivious change or remove it.
- **Visual Terror Radius** (Settings > Accessibility, off by default): a heart in the survivor's chest
  that beats in sync with the heartbeat, only when the radius is audible; lullabies show as a haze;
  colour-blind modes recolour it; an indicator appears if your character is off-screen. BHVR has said
  the option gives no competitive advantage over the audio. -> optional second source for the ring.
- *Unknown:* bpm or loudness per band. Must be measured (docs/AUDIO_HEARTBEAT.md).

## 5. Files the game writes (read-only use)
- **Log:** `%LOCALAPPDATA%\DeadByDaylight\Saved\Logs\DeadByDaylight.log`, a new file every launch;
  older runs are kept as backup logs. *Unverified:* which lines name the map at load. Earlier
  community tools that read the lobby log to reveal the killer were controversial, so DeadByMapper
  only uses log lines for information the player sees anyway (the map name).
- **Config:** DBD moved to Unreal Engine 5 in patch 7.7.0 (Apr 2024); a 2025 forum post places
  `Input.ini` under `...\Saved\Config\WindowsClient\` (older builds: `WindowsNoEditor`).
  GameUserSettings.ini holds graphics/control settings incl. mouse-sensitivity keys; DBD's sensitivity
  scaling does not follow stock UE4 maths, so we calibrate empirically.
- **On screen:** the trial starts with an introductory map-name text, and the pause menu shows the
  map name (BHVR bug report, Fallen Refuge, patch 9.2.0 PTB). A community overlay tool (dbd-overlay)
  detects the map by reading on-screen text and offers a manual mode via the TAB menu.

## 6. Controls
Left Shift sprints on PC by default; WASD and Left Ctrl (crouch) are standard. Other bindings vary and
players rebind them - DeadByMapper treats every binding as configurable (data/game/keybinds.default.json,
`verified: false`).

## 7. Policy and precedent (not legal advice)
- BHVR's ban policy lists using third-party software or tools to gain an unintended unfair advantage
  as a bannable offence (with an exception for communication apps).
- Players report a BHVR developer saying crosshair overlays are not bannable; Easy Anti-Cheat has
  banned players over an injected third-party chat plugin, and BHVR redirected appeals to EAC. Lesson:
  anything that touches the game process is dangerous; passive screen/audio observation is the
  pattern used by tolerated tools.
- **Precedents (all external, read-only):** `dbd-hook-counter` (C# WinForms + EmguCV, reads the HUD,
  transparent overlay, requires Windowed Fullscreen); `dbd-overlay` (screenshots + text recognition to
  auto-detect the map, then shows a map overlay); Fog Companion (Overwolf, in-game callout graphics);
  NightLight Desktop (captures end-of-match screenshots for stats). None of them tracks the player's
  live position - that is DeadByMapper's new part, and it is computed only from the player's own inputs
  and screen.
- **Residual risk is never zero.** A tool that only observes cannot be detected by EAC the way injectors
  are, but BHVR decides what "unfair advantage" means. Our mitigation is to show only what the player
  already knows or could note down (docs/FAIR_PLAY.md).

## 8. Open questions (resolved with the user's recordings, see docs/DATA_COLLECTION.md)
1. Exact log lines for lobby / loading / map / match start / match end (patch-specific).
2. Interaction prompt and progress-label strings, positions and fonts per HUD scale and language.
3. Heartbeat bpm/loudness per terror-radius band; chase-music signature.
4. Generator / exit-gate / hatch spawn coordinates per map (authoring, M5).
5. Survivor spawn rules (spread vs together; Shroud offerings change them).
6. Whether WGC window capture returns frames in exclusive fullscreen on the user's setup.
7. Camera behaviour during vaults, pallet drops, being carried/hooked (yaw control or not).

## Sources
- [DBD Wiki - Distances](https://deadbydaylight.wiki.gg/wiki/Distances)
- [DBD Wiki - Maze Tiles](https://deadbydaylight.wiki.gg/wiki/Maze_Tiles)
- [DBD Wiki - Generators](https://deadbydaylight.wiki.gg/wiki/Generator)
- [DBD Wiki - Exit Gates](https://deadbydaylight.wiki.gg/wiki/Exit_Gate)
- [DBD Wiki - Movement Speeds](https://deadbydaylight.wiki.gg/wiki/Movement_speed)
- [DBD Wiki - Terror Radius](https://deadbydaylight.wiki.gg/wiki/Terror_Radius)
- [DBD Wiki - Lampkin Lane](https://deadbydaylight.wiki.gg/wiki/Lampkin_Lane)
- [DBD Maps List 2026 (timesaver.gg)](https://timesaver.gg/blog/dbd-maps-list-all-maps-realms)
- [BHVR - Halloween Is Leaving Dead by Daylight](https://deadbydaylight.com/news/halloween-leaving-dead-by-daylight)
- [BHVR Support - How to collect your DBD logs](https://support.deadbydaylight.com/hc/en-us/articles/37271663858836-How-to-collect-your-Dead-by-Daylight-logs)
- [BHVR Support - Game Bans](https://support.deadbydaylight.com/hc/en-us/articles/45521322753428-Game-Bans)
- [BHVR Forums - Fallen Refuge labelled Greenville Square (intro text + pause menu)](https://forums.bhvr.com/dead-by-daylight/discussion/455823/fallen-refuge-has-been-renamed-to-greenville-square)
- [BHVR Forums - Visual Terror Radius knowledge base](https://forums.bhvr.com/dead-by-daylight/kb/articles/382)
- [BHVR Forums - New Visual Terror Radius thread](https://forums.bhvr.com/dead-by-daylight/discussion/368680/new-visual-terror-radius)
- [BHVR Forums - Input.ini under WindowsClient](https://forums.bhvr.com/dead-by-daylight/discussion/comment/3972922)
- [BHVR Forums - Is a crosshair overlay bannable?](https://forums.bhvr.com/dead-by-daylight/discussion/153983/is-it-bannable-to-use-a-cross-hair-overlay)
- [BHVR Forums - gens spawn >= 16 m apart (community claim)](https://forums.bhvr.com/dead-by-daylight/discussion/229361/survivor-generator-and-killer-spawn-locations)
- [Comicbook - Patch 7.7.0 notes (UE5)](https://comicbook.com/gaming/news/dead-by-daylight-dbd-update-patch-notes-april-23)
- [mouse-sensitivity.com - DBD config sensitivity](https://mouse-sensitivity.com/forums/topic/7028-dead-by-daylight-config-sensitivity)
- [PCGamesN - Mastermind 40 m terror radius](https://www.pcgamesn.com/dead-by-daylight/wesker-terror-radius-resident-evil)
- [GINX - What is a 3-gen](https://www.ginx.tv/en/3-gen-how-to-counter)
- [dbd-hook-counter (ecosyste.ms mirror of the README)](https://awesome.ecosyste.ms/projects/github.com%2Folm1ghty%2Fdbd-hook-counter)
- [dbd-overlay by H4RDC0RN (mirror)](https://gh.r-mj.com/H4RDC0RN/dbd-overlay)
- [Fog Companion (Overwolf)](https://www.overwolf.com/app/merlin-fog_companion)
- [NightLight Desktop](https://nightlight.gg/desktop)
- [Change.org - EAC bans over a third-party chat plugin](https://www.change.org/p/stop-the-unjust-bans-in-dead-by-daylight-transparency-and-justice-for-loyal-players)
