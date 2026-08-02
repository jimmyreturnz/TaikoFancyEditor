
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH)
asset_entries = []
assets = root / "assets"
if assets.exists():
    asset_entries.append((str(assets), "assets"))

analysis = Analysis(
    [str(root / "gui.py")],
    pathex=[str(root)],
    binaries=[],
    datas=asset_entries + [(str(root / "VERSION"), ".")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="TaikoFancyArranger",
    icon=str(root / "assets" / "icons" / "FancyTaikoEditor_Logo.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TaikoFancyArranger",
)
