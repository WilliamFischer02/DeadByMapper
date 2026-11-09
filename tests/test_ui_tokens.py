"""UI law (docs/UI_DESIGN.md): every colour comes from ui/theme.py - no literals in QML."""

from __future__ import annotations

import re
from pathlib import Path

from deadbymapper.ui.theme import OKABE_ITO

QML = Path(__file__).resolve().parent.parent / "src" / "deadbymapper" / "ui" / "qml"
HEX = re.compile(r"[\"']#[0-9A-Fa-f]{3,8}[\"']")
RGBA_LITERAL = re.compile(r"Qt\.rgba\(\s*[0-9.]+\s*,")


def test_qml_has_no_colour_literals():
    offenders = []
    for f in QML.rglob("*.qml"):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("//", 1)[0]
            if HEX.search(code) or RGBA_LITERAL.search(code):
                offenders.append(f"{f.relative_to(QML)}:{n}: {line.strip()}")
    assert not offenders, "use theme tokens:\n" + "\n".join(offenders)


def test_theme_tokens_referenced_in_qml_exist():
    used = set()
    for f in QML.rglob("*.qml"):
        used |= set(re.findall(r"\btheme\.([A-Za-z0-9_]+)", f.read_text(encoding="utf-8")))
    missing = sorted(used - set(OKABE_ITO))
    assert not missing, f"QML uses undefined theme tokens: {missing}"


def test_colour_tokens_are_well_formed():
    for k, v in OKABE_ITO.items():
        if isinstance(v, str) and v.startswith("#"):
            assert re.fullmatch(r"#([0-9A-F]{6}|[0-9A-F]{8})", v), (k, v)
