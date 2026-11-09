"""Walkable-area grid built from map data (bounds polygon minus solid structures)."""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

from ..core.geometry import points_in_polygon
from ..world.map_model import MapData


class Occupancy:
    def __init__(self, walkable: NDArray[np.bool_], res_m: float) -> None:
        self.walkable = walkable
        self.res = float(res_m)
        self.rows, self.cols = walkable.shape
        self._free_idx = np.flatnonzero(walkable.ravel())
        if self._free_idx.size == 0:
            raise ValueError("occupancy grid has no walkable cells")

    # ------------------------------------------------------------------ construction
    @classmethod
    def from_map(
        cls, m: MapData, *, res_m: float = 0.25, floor: str = "ground", supersample: int = 2
    ) -> Occupancy | None:
        if not m.has_geometry:
            return None
        assert m.width_m is not None and m.height_m is not None and m.bounds_polygon_m is not None
        cols = math.ceil(m.width_m / res_m)
        rows = math.ceil(m.height_m / res_m)
        # Supersample so walls thinner than a cell still block (any sub-sample inside -> blocked).
        offs = (np.arange(supersample) + 0.5) / supersample
        inside_bounds = np.ones((rows, cols), dtype=bool)
        blocked = np.zeros((rows, cols), dtype=bool)
        jj, ii = np.meshgrid(np.arange(cols), np.arange(rows))
        solids = [s for s in m.structures if s.solid and s.floor == floor]
        for oy in offs:
            for ox in offs:
                xs = (jj + ox) * res_m
                ys = (ii + oy) * res_m
                inside_bounds &= points_in_polygon(xs, ys, m.bounds_polygon_m)
                for s in solids:
                    blocked |= points_in_polygon(xs, ys, s.polygon_m)
        return cls(inside_bounds & ~blocked, res_m)

    # ------------------------------------------------------------------ queries
    def is_walkable(self, x: NDArray[np.float64], y: NDArray[np.float64]) -> NDArray[np.bool_]:
        ix = np.floor(x / self.res).astype(np.int64)
        iy = np.floor(y / self.res).astype(np.int64)
        valid = (ix >= 0) & (ix < self.cols) & (iy >= 0) & (iy < self.rows)
        out = np.zeros(np.shape(x), dtype=bool)
        out[valid] = self.walkable[iy[valid], ix[valid]]
        return out

    def is_walkable_pt(self, x: float, y: float) -> bool:
        return bool(self.is_walkable(np.array([x]), np.array([y]))[0])

    def sample_uniform(self, n: int, rng: np.random.Generator) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        idx = rng.choice(self._free_idx, size=n, replace=True)
        iy, ix = np.divmod(idx, self.cols)
        x = (ix + rng.random(n)) * self.res
        y = (iy + rng.random(n)) * self.res
        return x, y

    @property
    def free_area_m2(self) -> float:
        return float(self._free_idx.size) * self.res * self.res

    def segment_free_fraction(self, xs: NDArray[np.float64], ys: NDArray[np.float64]) -> NDArray[np.float64]:
        """Fraction of walkable samples along each row of a (N, S) array of path samples."""
        ok = self.is_walkable(xs, ys)
        return ok.mean(axis=-1)
