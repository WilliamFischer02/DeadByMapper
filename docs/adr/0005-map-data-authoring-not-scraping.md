# ADR 0005 - Author map data ourselves; never bundle third-party map art

**Status:** accepted (M0) · **Date:** 2026-11-08

## Context
Community map overheads exist (fan-made tile overlays, Steam guides, wiki pages) and are
extremely useful. They are also other people's work. There is no public dataset of generator coordinates in
metres, and DBD maps are partially procedural: tiles rotate, and generators spawn at 7 of N possible
spots.

## Decision
- Store maps as our own JSON (`MapData`): metric bounds, solid structures, tile slots, and generator
  and exit-gate *candidate* spots with an uncertainty (sigma).
- The data is authored in our editor (M4) on top of the user's **personal reference images** that
  live outside the repo (`%LOCALAPPDATA%\DeadByMapper\overlays`), and verified in custom games
  (F9/F10 marks).
- Status ladder: stub, partial, verified.
- The repo ships the 45 live maps as `stub` (names, realms, aliases) plus a synthetic Test Arena.

## Alternatives considered
- **Bundle community overhead images.** Rejected: copyright and the authors' wishes.
- **Scrape coordinates automatically.** There is no such source, and it would carry the same
  rights issue.
- **Learn maps purely from the user's play (SLAM).** A long-term complement, but too slow to be
  useful on its own.

## Consequences
- The minimap is only as good as the authored maps, so M4 is on the critical path.
- Stub maps fall back to breadcrumb mode.
- Sources and the verified patch are recorded per map, and a BHVR map rework drops the map back to
  `partial`.
