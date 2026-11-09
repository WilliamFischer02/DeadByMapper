"""Design tokens (single source of truth, exposed to QML as the ``theme`` context property).

Colour roles use an Okabe-Ito based palette (colour-blind safe) and every marker is ALSO coded
by shape, so meaning never depends on colour alone. QML colours are #AARRGGBB.
Change tokens here, never hard-code colours in QML.
"""

from __future__ import annotations

OKABE_ITO = {
    # surfaces
    "bg": "#0B0E13",
    "bgPane": "#EB0B0E13",  # 92% - solid enough to read over any scene, no blur (crisp by rule)
    "surface": "#121821",
    "surface2": "#18202B",
    "surfaceHover": "#1E2733",
    "hairline": "#1FFFFFFF",
    "hairlineStrong": "#33FFFFFF",
    # text
    "text": "#F2F5F8",
    "textDim": "#A7B0BC",
    "textMute": "#6E7886",
    # map layer (desaturated so semantic markers pop pre-attentively)
    "mapFill": "#131A23",
    "mapStroke": "#2C3846",
    "structFill": "#222C39",
    "structStroke": "#3A4859",
    "tileStroke": "#2A3542",
    "grid": "#09FFFFFF",
    "gridMajor": "#13FFFFFF",
    "trail": "#66F2F5F8",
    # semantic markers
    "player": "#F2F5F8",
    "gen": "#E69F00",  # orange   - generator (diamond)
    "genDone": "#009E73",  # green    - completed (diamond + tick)
    "genRegress": "#D55E00",  # vermilion- regressing (diamond + pulse)
    "gate": "#56B4E9",  # sky blue - exit gate (bar on the edge)
    "hatch": "#CC79A7",  # purple   - hatch (ring)
    "danger": "#E5484D",  # terror radius ring
    # derived alpha variants (#AARRGGBB) - keep the hue of their base token
    "dangerFill": "#21E5484D",  # killer band fill, 13 %
    "dangerFillChase": "#33E5484D",  # killer band fill in chase, 20 %
    "dangerEdge": "#BFE5484D",  # band outer edge, 75 %
    "dangerInner": "#59E5484D",  # band inner edge, 35 %
    "gateCandidate": "#6156B4E9",  # possible (unconfirmed) gate spot, 38 %
    "playerHalo": "#38F2F5F8",  # uncertainty halo centre, 22 %
    "playerCone": "#73F2F5F8",  # heading cone base, 45 %
    "playerClear": "#00F2F5F8",  # gradient end: same hue at 0 % (no grey fringe)
    "badgeVerified": "#2E009E73",
    "badgePartial": "#29F5B83D",
    "badgeSynthetic": "#2956B4E9",
    "badgeStub": "#0FFFFFFF",
    "demoFill": "#2456B4E9",
    "demoStroke": "#7356B4E9",
    # chrome
    "rail": "#0D1117",
    "brandTile": "#151C26",
    "brandStroke": "#59F2F5F8",
    "onLight": "#0B0E13",  # text / outline drawn on light fills (button label, player dot outline)
    "white": "#FFFFFF",  # hover highlight, completed-gen tick
    # status
    "ok": "#3DD68C",
    "warn": "#F5B83D",
    "down": "#E5484D",
    "off": "#5B6573",
    # metrics
    "radius": 14,
    "radiusSmall": 8,
    "paneW": 380,
    "paneH": 452,
    # type
    "font": "Segoe UI Variable Text",
    "fontDisplay": "Segoe UI Variable Display",
    "fontMono": "Cascadia Mono",
}

THEMES = {"okabe_ito": OKABE_ITO}


def theme(name: str = "okabe_ito") -> dict[str, object]:
    return dict(THEMES.get(name, OKABE_ITO))
