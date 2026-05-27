# PyInstaller spec for `fmo.exe` on Windows.
# Build with:  pyinstaller packaging/fmo.spec
#
# We use --onefile so the result is a single redistributable EXE; switch to
# --onedir if you'd rather distribute a folder (faster startup).

# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hiddenimports = (
    collect_submodules("forum_orchestrator.resolvers")
    + collect_submodules("selectolax")
    + ["browser_cookie3"]
)

a = Analysis(
    ['../src/forum_orchestrator/__main__.py'],
    pathex=['../src'],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name='fmo', debug=False, bootloader_ignore_signals=False, strip=False,
    upx=True, console=True, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, upx_exclude=[], name='fmo',
)
