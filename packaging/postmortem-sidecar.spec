# -*- mode: python ; coding: utf-8 -*-

import os

root = os.path.dirname(SPECPATH)

a = Analysis(
    [os.path.join(SPECPATH, "sidecar_entry.py")],
    pathex=[root],
    binaries=[],
    datas=[],
    hiddenimports=[
        "anthropic",
        "pydantic",
        "postmortem.providers.anthropic_provider",
        # reaperd.py is installed separately and executed with runpy inside
        # the frozen interpreter, so PyInstaller cannot discover its imports.
        "argparse",
        "datetime",
        "glob",
        "platform",
        "secrets",
        "struct",
        "tempfile",
        "wave",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="postmortem-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="postmortem-sidecar",
)
