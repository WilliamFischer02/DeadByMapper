"""Optical-flow motion summary: are we REALLY moving (vs holding W into a wall / animation lock)?

Third-person camera geometry:
    * pure camera yaw  -> nearly uniform horizontal flow across the frame;
    * forward/back     -> radial expansion/contraction about a focus of expansion near the centre;
    * strafing         -> horizontal flow whose magnitude varies with depth (parallax).
So after removing the median (rotation) flow, the residual's radial component and its spread
indicate translation. The own character and HUD are masked out (they move with the camera).

Outputs a calibrated-ish probability via a logistic squash; the thresholds MUST be re-fitted on
recorded sessions (labels from known stuck/moving segments) in M4. Also reports the measured yaw
rate (deg/s) from the rotation component for continuous mouse-calibration refinement.
"""

from __future__ import annotations

import math

import numpy as np

from ..capture.regions import Region
from ..core.events import MotionObserved


class FlowMotionEstimator:
    def __init__(
        self,
        masks: list[Region] | None = None,
        *,
        width: int = 256,
        hfov_deg: float = 87.0,
        w_expansion: float = 9.0,
        w_residual: float = 4.0,
        bias: float = 2.2,
    ) -> None:
        self.masks = masks or []
        self.width = width
        self.hfov = hfov_deg
        self.w_exp, self.w_res, self.bias = w_expansion, w_residual, bias
        self._prev: np.ndarray | None = None
        self._prev_ts: int | None = None
        self._mask: np.ndarray | None = None
        self._rx: np.ndarray | None = None
        self._ry: np.ndarray | None = None

    def _prepare(self, h: int, w: int) -> None:
        mask = np.ones((h, w), dtype=bool)
        for r in self.masks:
            x, y, cw, ch = r.to_pixels(w, h)
            mask[y : y + ch, x : x + cw] = False
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
        dx, dy = xx - cx, yy - cy
        n = np.sqrt(dx * dx + dy * dy) + 1e-6
        self._rx, self._ry = dx / n, dy / n
        mask &= n > 0.08 * w  # ignore the FOE neighbourhood (radial direction ill-defined)
        self._mask = mask

    def update(self, image_bgr: np.ndarray, ts_ns: int) -> MotionObserved | None:
        import cv2

        h0, w0 = image_bgr.shape[:2]
        s = self.width / w0
        gray = cv2.cvtColor(
            cv2.resize(image_bgr, (self.width, max(8, round(h0 * s))), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY
        )
        if self._prev is None or self._prev.shape != gray.shape:
            self._prev, self._prev_ts = gray, ts_ns
            self._prepare(*gray.shape)
            return None
        flow = cv2.calcOpticalFlowFarneback(self._prev, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        dt = max((ts_ns - (self._prev_ts or ts_ns)) / 1e9, 1e-3)
        self._prev, self._prev_ts = gray, ts_ns
        assert self._mask is not None and self._rx is not None and self._ry is not None
        u, v = flow[..., 0][self._mask], flow[..., 1][self._mask]
        texture = float(np.mean(np.abs(cv2.Laplacian(gray, cv2.CV_32F))[self._mask]))
        quality = float(min(1.0, texture / 6.0))
        mu, mv = float(np.median(u)), float(np.median(v))
        ru, rv = u - mu, v - mv
        radial = ru * self._rx[self._mask] + rv * self._ry[self._mask]
        expansion = float(np.mean(radial))  # px/frame, + forward, - backward
        resid = float(np.median(np.hypot(ru, rv)))
        z = self.w_exp * abs(expansion) + self.w_res * resid - self.bias
        p = 1.0 / (1.0 + math.exp(-z))
        p = 0.5 + (p - 0.5) * quality  # low texture (dark / fog) -> uninformative
        yaw_rate = mu * (self.hfov / self.width) / dt
        return MotionObserved(ts_ns=ts_ns, translating_prob=float(p), yaw_rate_dps=float(yaw_rate), quality=quality)
