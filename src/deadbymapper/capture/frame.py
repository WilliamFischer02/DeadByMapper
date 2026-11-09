from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class Frame:
    ts_ns: int
    image: NDArray[np.uint8]  # H x W x 3, BGR (OpenCV order), already cropped to the game client area
    scale: float = 1.0  # analysis image width / true client width (frames are downscaled for CV)
    seq: int = 0

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    @property
    def width(self) -> int:
        return int(self.image.shape[1])
