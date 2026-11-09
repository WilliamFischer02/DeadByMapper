"""DeadByMapper - glanceable live minimap companion for Dead by Daylight.

Hard rule (see docs/FAIR_PLAY.md): this program only *observes* what the player already
perceives - pixels on their own screen, their own audio output, their own keyboard/mouse,
and the log/config files the game writes to disk. It never reads or writes game memory,
injects code, hooks the renderer, sniffs packets, modifies game files, or sends input.
"""

__version__ = "0.1.0"
APP_NAME = "DeadByMapper"
APP_ORG = "Goob Entertainment Co."
