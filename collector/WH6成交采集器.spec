# PyInstaller specification for the Windows x64 collector bundle.
# Build on Windows, from this directory, with a pinned PyInstaller version.
from pathlib import Path


ROOT = Path(SPECPATH)

a = Analysis(
    [str(ROOT / "wh6_collector" / "cli.py")],
    pathex=[str(ROOT.parent)],
    binaries=[],
    datas=[],
    hiddenimports=["requests"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="WH6成交采集器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
