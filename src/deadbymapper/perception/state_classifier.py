"""Screen-state classifier: frame -> ScreenObserved.

STATUS: interface + plan only (M3, vision agent). Approach, cheapest first:
  1. HUD presence: survivor portraits / gen counter (bottom-left) and perk diamonds (bottom-right)
     via multi-scale template matching on edge maps (robust to colour-blind modes and lighting).
     Survivor HUD present -> in_match_survivor; killer HUD (power/perk layout) -> in_match_killer.
  2. Loading screen: loading bar + tip band (bottom) / mostly static frame + DBD logo region.
  3. Tally/scoreboard: large structured panels; OCR of the header when uncertain.
  4. Menu overlay mid-match: pause/TAB panel present while HUD is dimmed -> menu_overlay=True
     (gates input so the minimap does not spin while the player browses menus).
Templates are captured from the player's own screenshots by the calibration wizard (never
shipped from third-party sources). Runs at ~4 Hz; the state machine adds hysteresis.
"""

from __future__ import annotations

import numpy as np

from ..core.events import ScreenObserved


class ScreenClassifier:
    def classify(self, image_bgr: np.ndarray, ts_ns: int) -> ScreenObserved:
        raise NotImplementedError("M3: implement HUD/loading/tally detection from calibrated templates")
