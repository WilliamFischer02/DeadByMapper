"""HUD readers: generator counter, own survivor status (healthy/injured/downed/hooked...).

STATUS: interface + plan only (M3/M5).
  * Gen counter: digit templates captured at the player's HUD scale (0-7), matched in the
    calibrated ``gen_counter`` ROI; publish GenCounterObserved only on change, debounced 2 reads.
  * Own status: the player's portrait is the bottom-most (verify) in ``survivor_status``; classify
    state icons (injured, dying, hooked, carried) with templates. Publish PlayerStatusObserved.
These matter for localization: hooked/carried/downed change the motion model.
"""

from __future__ import annotations

import numpy as np

from ..core.events import GenCounterObserved, PlayerStatusObserved


class GenCounterReader:
    def read(self, image_bgr: np.ndarray, ts_ns: int) -> GenCounterObserved | None:
        raise NotImplementedError("M3: digit templates in calibrated gen_counter ROI")


class OwnStatusReader:
    def read(self, image_bgr: np.ndarray, ts_ns: int) -> PlayerStatusObserved | None:
        raise NotImplementedError("M5: status-icon templates in calibrated survivor_status ROI")
