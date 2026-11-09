"""Regions of interest, normalised to the game client area and aware of HUD scale.

DBD lets players scale HUD and UI; elements shrink toward their anchor. A region authored at HUD
scale 1.0 is transformed by scaling its offset from the anchor point. Calibrated per-resolution
regions (captured by the wizard) always win over these defaults.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..config.paths import data_dir

_ANCHORS = {
    "top_left": (0.0, 0.0),
    "top_center": (0.5, 0.0),
    "top_right": (1.0, 0.0),
    "center": (0.5, 0.5),
    "bottom_left": (0.0, 1.0),
    "bottom_center": (0.5, 1.0),
    "bottom_right": (1.0, 1.0),
}


@dataclass(frozen=True, slots=True)
class Region:
    name: str
    x: float
    y: float
    w: float
    h: float
    anchor: str = "top_left"
    calibrated: bool = False

    def scaled(self, hud_scale: float) -> Region:
        if hud_scale == 1.0:
            return self
        ax, ay = _ANCHORS[self.anchor]
        nx = ax + (self.x - ax) * hud_scale
        ny = ay + (self.y - ay) * hud_scale
        return Region(self.name, nx, ny, self.w * hud_scale, self.h * hud_scale, self.anchor, self.calibrated)

    def to_pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        x0 = int(round(min(max(self.x, 0.0), 1.0) * width))
        y0 = int(round(min(max(self.y, 0.0), 1.0) * height))
        x1 = int(round(min(max(self.x + self.w, 0.0), 1.0) * width))
        y1 = int(round(min(max(self.y + self.h, 0.0), 1.0) * height))
        return x0, y0, max(x1 - x0, 1), max(y1 - y0, 1)

    def crop(self, image: np.ndarray) -> np.ndarray:
        h, w = image.shape[:2]
        x, y, cw, ch = self.to_pixels(w, h)
        return image[y : y + ch, x : x + cw]


def load_regions(path: Path | None = None) -> dict[str, Region]:
    raw = json.loads((path or data_dir() / "game" / "hud_regions.default.json").read_text(encoding="utf-8"))
    return {
        name: Region(name, r["x"], r["y"], r["w"], r["h"], r.get("anchor", "top_left"), bool(r.get("calibrated")))
        for name, r in raw["regions"].items()
    }
