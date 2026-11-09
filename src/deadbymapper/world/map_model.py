"""Map data model - the single source of truth for ``data/maps/**.json``.

``data/schemas/map.schema.json`` is GENERATED from this model (``tools/gen_schemas.py``) and a
test asserts they match. Edit the model, regenerate the schema, never the other way round.

Coordinates: metres in the map frame (see core/geometry.py). The natural scale reference in
any overhead image is the border wall: post-centre to post-centre is 8 m (16 m double sections
on some maps). Map size in community "tiles" uses 8x8 m units.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Vec = tuple[float, float]
MapStatus = Literal["stub", "partial", "verified", "synthetic"]
StructureType = Literal[
    "main_building",
    "killer_shack",
    "building",
    "landmark",
    "wall",
    "blocker",
    "tile",
    "stairs",
    "drop",
    "basement",
]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Source(_Base):
    title: str
    url: str | None = None
    accessed: str | None = None  # ISO date


class Floor(_Base):
    id: str
    name: str
    z_m: float | None = None


class SpawnCandidate(_Base):
    """A *possible* spawn location. DBD spawns 7 generators per 1v4 trial out of a larger,
    predetermined set of locations, so most candidates are 'maybe'."""

    id: str
    pos_m: Vec
    floor: str = "ground"
    sigma_m: float = Field(1.0, gt=0, description="Positional uncertainty (tile-dependent spots: 3-6 m).")
    fixed: bool = Field(False, description="Always spawns in every trial on this map.")
    slot: str | None = Field(None, description="TileSlot id this candidate belongs to, if tile-dependent.")
    note: str | None = None


class ExitGateCandidate(SpawnCandidate):
    facing_deg: float | None = Field(None, description="Outward normal heading (0 = map up, clockwise).")
    edge: Literal["north", "south", "east", "west", "interior"] | None = None


class Structure(_Base):
    id: str
    type: StructureType
    name: str | None = None
    polygon_m: list[Vec] = Field(min_length=3)
    floor: str = "ground"
    solid: bool = Field(False, description="Impassable footprint used by the localizer's occupancy grid.")
    fixed: bool = Field(True, description="Same position every trial (main buildings usually are).")


class TileSlot(_Base):
    """A cell region whose contents vary per trial (tile iteration and rotation are random)."""

    id: str
    origin_m: Vec
    size_m: Vec = (16.0, 16.0)
    allowed: list[str] = Field(default_factory=list, description="Tile type ids, see docs/MAP_SPEC.md#tiles")
    note: str | None = None


class Overlay(_Base):
    """Personal reference image placed in the user's overlays dir. Never bundled or committed."""

    image: str
    px_to_m: tuple[float, float, float, float, float, float] = Field(
        description="Affine a,b,c,d,e,f: x_m = a*px + b*py + c ; y_m = d*px + e*py + f"
    )
    opacity: float = Field(0.85, ge=0, le=1)


class MapData(_Base):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-z0-9_]+\.[a-z0-9_]+$", description="<realm_slug>.<map_slug>")
    name: str
    realm: str
    status: MapStatus
    available: bool = Field(True, description="False for removed maps or PTB-only maps.")
    kind: Literal["outdoor", "indoor", "mixed"] = "outdoor"
    modes: list[Literal["1v4", "2v8"]] = Field(default_factory=lambda: ["1v4"])
    aliases: list[str] = Field(default_factory=list, description="Names as shown in-game (all languages) for OCR.")
    log_ids: list[str] = Field(default_factory=list, description="Identifiers seen in DeadByDaylight.log.")
    size_tiles_reported: float | None = Field(None, description="Community measurement, 8x8 m tiles (1v4).")
    size_tiles_2v8: float | None = None
    width_m: float | None = Field(None, gt=0)
    height_m: float | None = Field(None, gt=0)
    bounds_polygon_m: list[Vec] | None = None
    floors: list[Floor] = Field(default_factory=lambda: [Floor(id="ground", name="Ground")])
    structures: list[Structure] = Field(default_factory=list)
    tile_slots: list[TileSlot] = Field(default_factory=list)
    generators: list[SpawnCandidate] = Field(default_factory=list)
    exit_gates: list[ExitGateCandidate] = Field(default_factory=list)
    hatches: list[SpawnCandidate] = Field(default_factory=list)
    survivor_spawns: list[SpawnCandidate] = Field(default_factory=list)
    overlay: Overlay | None = None
    sources: list[Source] = Field(default_factory=list)
    verified_patch: str | None = None
    notes: str | None = None

    # ------------------------------------------------------------------ validation
    @model_validator(mode="after")
    def _validate(self) -> MapData:
        floor_ids = {f.id for f in self.floors}
        seen: set[str] = set()
        groups: list[list[SpawnCandidate]] = [
            self.generators,
            list(self.exit_gates),
            self.hatches,
            self.survivor_spawns,
        ]
        for group in groups:
            for c in group:
                if c.id in seen:
                    raise ValueError(f"duplicate candidate id {c.id!r}")
                seen.add(c.id)
                if c.floor not in floor_ids:
                    raise ValueError(f"candidate {c.id!r} references unknown floor {c.floor!r}")
        for s in self.structures:
            if s.floor not in floor_ids:
                raise ValueError(f"structure {s.id!r} references unknown floor {s.floor!r}")
        slot_ids = {t.id for t in self.tile_slots}
        for c in self.generators:
            if c.slot is not None and c.slot not in slot_ids:
                raise ValueError(f"generator {c.id!r} references unknown tile slot {c.slot!r}")

        if self.status in ("partial", "verified", "synthetic") and (
            self.width_m is None or self.height_m is None or self.bounds_polygon_m is None
        ):
            raise ValueError(f"{self.id}: status {self.status!r} requires width_m, height_m, bounds_polygon_m")
        if self.status == "verified":
            need_gens = 7 if "1v4" in self.modes else 13
            if len(self.generators) < need_gens:
                raise ValueError(f"{self.id}: verified maps need >= {need_gens} generator candidates")
            if len(self.exit_gates) < 2:
                raise ValueError(f"{self.id}: verified maps need >= 2 exit gate candidates")
        if self.width_m and self.height_m:
            tol = 2.0
            for group in groups:
                for c in group:
                    x, y = c.pos_m
                    if not (-tol <= x <= self.width_m + tol and -tol <= y <= self.height_m + tol):
                        raise ValueError(f"{self.id}: candidate {c.id!r} at {c.pos_m} is outside the map bbox")
        return self

    # ------------------------------------------------------------------ helpers
    @property
    def has_geometry(self) -> bool:
        return self.bounds_polygon_m is not None and self.width_m is not None and self.height_m is not None

    @property
    def display_name(self) -> str:
        return self.name

    def all_names(self) -> list[str]:
        names = [self.name, *self.aliases]
        return list(dict.fromkeys(n for n in names if n))
