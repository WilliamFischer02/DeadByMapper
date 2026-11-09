"""Measurement models -> per-particle log-likelihoods.

All functions are vectorised over particles (x, y arrays of shape (N,)).
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

F64 = NDArray[np.float64]

# Typical distance between the survivor's root and an interactable's centre while using it.
GENERATOR_STANDOFF_M = 1.0
EXIT_GATE_STANDOFF_M = 1.5


def near_candidates_loglik(
    x: F64,
    y: F64,
    centers: F64,  # (K, 2)
    sigmas: F64,  # (K,)
    weights: F64 | None = None,  # (K,) prior that each candidate actually spawned
    *,
    standoff_m: float = GENERATOR_STANDOFF_M,
    floor_rel: float = 1e-4,
) -> F64:
    """'I am using an object of this kind' -> mixture of Gaussians over candidate positions.

    ``floor_rel`` keeps a small likelihood everywhere so a missing/mis-authored candidate does
    not annihilate the true hypothesis (robust to imperfect map data).
    """
    if centers.size == 0:
        return np.zeros_like(x)
    k = centers.shape[0]
    w = np.full(k, 1.0 / k) if weights is None else weights / np.sum(weights)
    se2 = sigmas**2 + standoff_m**2  # (K,)
    dx = x[:, None] - centers[None, :, 0]
    dy = y[:, None] - centers[None, :, 1]
    d2 = dx * dx + dy * dy
    norm = w / (2.0 * math.pi * se2)
    dens = np.sum(norm[None, :] * np.exp(-d2 / (2.0 * se2[None, :])), axis=1)
    floor = floor_rel * float(np.max(norm))
    return np.log(dens + floor)


def gaussian_fix_loglik(x: F64, y: F64, pos: tuple[float, float], radius_m: float) -> F64:
    """User tap 'I am here' (radius ~ 2 sigma)."""
    s2 = max(radius_m / 2.0, 0.5) ** 2
    d2 = (x - pos[0]) ** 2 + (y - pos[1]) ** 2
    return -d2 / (2.0 * s2)


def heading_loglik(heading_deg: F64, obs_deg: float, sigma_deg: float) -> F64:
    d = (heading_deg - obs_deg + 180.0) % 360.0 - 180.0
    return -(d * d) / (2.0 * sigma_deg * sigma_deg)
