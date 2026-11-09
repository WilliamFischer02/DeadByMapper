# PyInstaller spec - one-folder, windowed build of DeadByMapper.
#   uv run python tools/make_icon.py            # writes packaging/build/app.ico (build.ps1 does this)
#   uv run pyinstaller packaging/deadbymapper.spec --noconfirm --clean
# Output: dist/DeadByMapper/DeadByMapper.exe  (install with packaging/install.ps1)
#
# One-folder (not one-file) on purpose: instant startup (no temp unpacking), fewer AV false
# positives, and Qt's QML plugins load from disk normally.
# ruff: noqa
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

ROOT = Path(SPECPATH).parent  # noqa: F821  (SPECPATH is injected by PyInstaller)
ICON = ROOT / "packaging" / "build" / "app.ico"
if not ICON.exists():
    raise SystemExit(f"{ICON} is missing - run `uv run python tools/make_icon.py` first (build.ps1 does).")

# ---------------------------------------------------------------- version stamp (exe Properties > Details)
_init = (ROOT / "src" / "deadbymapper" / "__init__.py").read_text(encoding="utf-8")
VERSION = re.search(r'^__version__\s*=\s*"([^"]+)"', _init, re.M).group(1)
_nums = tuple(int(n) for n in re.findall(r"\d+", VERSION)[:4])
_nums = _nums + (0,) * (4 - len(_nums))
VERSION_INFO = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_nums, prodvers=_nums),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", "Goob Entertainment Co."),
                        StringStruct("FileDescription", "DeadByMapper"),
                        StringStruct("FileVersion", VERSION),
                        StringStruct("InternalName", "DeadByMapper"),
                        StringStruct("LegalCopyright", "Proprietary - Goob Entertainment Co."),
                        StringStruct("OriginalFilename", "DeadByMapper.exe"),
                        StringStruct("ProductName", "DeadByMapper"),
                        StringStruct("ProductVersion", VERSION),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=[
        (str(ROOT / "data" / "maps"), "data/maps"),
        (str(ROOT / "data" / "game"), "data/game"),
        (str(ROOT / "data" / "schemas"), "data/schemas"),
        (str(ROOT / "src" / "deadbymapper" / "ui" / "qml"), "deadbymapper/ui/qml"),
    ],
    hiddenimports=collect_submodules("deadbymapper")
    + ["PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtSvg", "PySide6.QtQuickControls2"],
    excludes=[
        "tkinter", "matplotlib", "IPython", "pytest", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore",
        # Build-time only; pulled in transitively (numpy.f2py, pkg_resources) and never used at run time.
        "setuptools", "pkg_resources", "distutils", "numpy.f2py",
    ],
    noarchive=False,
)

# ---------------------------------------------------------------- trim Qt payload the app never loads
# The QML uses QtQuick, QtQuick.Controls (default style: FluentWinUI3 on Win11, Windows on Win10,
# Basic as fallback - all kept), QtQuick.Shapes, QtQuick.Window. Anything below is never imported.
# Keep cv2's ffmpeg DLL: --replay decodes recorded session video with cv2.VideoCapture.
_DROP = re.compile(
    r"^PySide6[\\/]("
    r"opengl32sw\.dll"  # Mesa software OpenGL; Qt Quick renders through D3D11 (WARP fallback) on Windows
    r"|plugins[\\/]qmltooling[\\/]"  # QML debugger/profiler server - not shipped in a release
    r"|qml[\\/]QtTest[\\/]|Qt6(Quick)?Test\.dll"
    r"|qml[\\/]QtQuick[\\/](VirtualKeyboard|Particles|Timeline|LocalStorage|Dialogs)[\\/]"
    r"|Qt6(QuickParticles|QuickTimeline\w*|QmlLocalStorage|QuickDialogs2\w*)\.dll"
    r"|qml[\\/]QtQuick[\\/]Controls[\\/](Imagine|Material|Universal)[\\/]"
    r"|Qt6QuickControls2(Imagine|Material|Universal)\w*\.dll"
    r"|qml[\\/]Qt[\\/]labs[\\/](StyleKit|wavefrontmesh)[\\/]|Qt6Labs(StyleKit\w*|WavefrontMesh)\.dll"
    r")",
    re.I,
)
_n0 = len(a.binaries) + len(a.datas)
a.binaries = [e for e in a.binaries if not _DROP.match(e[0])]
a.datas = [e for e in a.datas if not _DROP.match(e[0])]
print(f"deadbymapper.spec: trimmed {_n0 - len(a.binaries) - len(a.datas)} unused Qt files")

pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DeadByMapper",
    console=False,
    icon=str(ICON),
    version=VERSION_INFO,
    upx=False,  # UPX-packed binaries trigger antivirus heuristics
)
coll = COLLECT(exe, a.binaries, a.datas, name="DeadByMapper", upx=False)  # noqa: F821
