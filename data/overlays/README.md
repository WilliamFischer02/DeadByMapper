# Personal reference overlays (never committed)

Community top-down map images (callout maps, tile atlases, wiki outlines) are other people's
work and are derived from Behaviour Interactive's assets. DeadByMapper never bundles or
redistributes them.

If you want an image under the vector minimap while you author map data:

1. Save the image for personal use into `%LOCALAPPDATA%\DeadByMapper\overlays\`.
2. In the map editor (M5), click two border-wall posts 8 m apart (or any two points of known
   distance) to set the scale, then two known landmarks to set rotation/offset.
3. The editor writes the affine transform into that map's JSON as `overlay.px_to_m`, referencing
   the image by file name only.

What IS committed: the coordinates you author (generator spots, gate spots, building outlines) -
facts measured by you, stored in `data/maps/**.json`, with `sources` noting where they came from.

This folder in the repo only holds this README (`.gitignore` excludes everything else).
