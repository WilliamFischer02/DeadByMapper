"""Geometry conventions used EVERYWHERE (see docs/MAP_SPEC.md).

Map frame
    metres; origin = top-left corner of the map's bounding box; +x = right (east),
    +y = down (south). Matches image pixel axes so overlays need only scale + offset.
Headings
    degrees, 0 = map up (north, -y), clockwise positive, wrapped to [0, 360).
    Unit vector for heading h: (sin h, -cos h).
Camera-relative intent
    angle clockwise from camera forward: W = 0, D = 90, S = 180, A = 270.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

Point = tuple[float, float]
Polygon = Sequence[Point]


def wrap_deg(a: float) -> float:
    return a % 360.0


def wrap_deg_signed(a: float) -> float:
    """Wrap to (-180, 180]."""
    a = (a + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


def angle_diff_deg(a: float, b: float) -> float:
    """Smallest signed difference a - b in degrees."""
    return wrap_deg_signed(a - b)


def heading_unit(heading_deg: float) -> Point:
    r = math.radians(heading_deg)
    return (math.sin(r), -math.cos(r))


def heading_of(dx: float, dy: float) -> float:
    """Heading (deg) of a displacement in map frame."""
    return wrap_deg(math.degrees(math.atan2(dx, -dy)))


def intent_angle_deg(forward: bool, back: bool, left: bool, right: bool) -> float | None:
    """Camera-relative movement angle for WASD state, or None if no net movement."""
    fy = (1.0 if forward else 0.0) - (1.0 if back else 0.0)
    rx = (1.0 if right else 0.0) - (1.0 if left else 0.0)
    if fy == 0.0 and rx == 0.0:
        return None
    return wrap_deg(math.degrees(math.atan2(rx, fy)))


def circular_mean_deg(
    angles_deg: NDArray[np.float64], weights: NDArray[np.float64] | None = None
) -> tuple[float, float]:
    """Weighted circular mean and circular std (deg)."""
    rad = np.radians(angles_deg)
    w = np.ones_like(rad) if weights is None else weights
    sw = float(np.sum(w))
    if sw <= 0:
        return 0.0, 180.0
    s = float(np.sum(w * np.sin(rad))) / sw
    c = float(np.sum(w * np.cos(rad))) / sw
    r = math.hypot(s, c)
    mean = wrap_deg(math.degrees(math.atan2(s, c)))
    std = math.degrees(math.sqrt(max(0.0, -2.0 * math.log(max(r, 1e-12))))) if r < 1.0 else 0.0
    return mean, min(std, 180.0)


def points_in_polygon(xs: NDArray[np.float64], ys: NDArray[np.float64], poly: Polygon) -> NDArray[np.bool_]:
    """Vectorised even-odd ray casting. ``poly`` vertices in order (open or closed ring)."""
    pts = np.asarray(poly, dtype=np.float64)
    if len(pts) >= 2 and np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
    inside = np.zeros(np.shape(xs), dtype=bool)
    n = len(pts)
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        cond = (yi > ys) != (yj > ys)
        with np.errstate(divide="ignore", invalid="ignore"):
            x_cross = (xj - xi) * (ys - yi) / (yj - yi + 1e-300) + xi
        inside ^= cond & (xs < x_cross)
        j = i
    return inside


def polygon_area(poly: Polygon) -> float:
    pts = np.asarray(poly, dtype=np.float64)
    x, y = pts[:, 0], pts[:, 1]
    return float(0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def polygon_bbox(poly: Polygon) -> tuple[float, float, float, float]:
    pts = np.asarray(poly, dtype=np.float64)
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


def rect_polygon(x: float, y: float, w: float, h: float) -> list[Point]:
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])
