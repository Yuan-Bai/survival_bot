# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 单 exe spec：main.py 统一入口（框选/答题子命令），共享一份 _internal
# 用法：pyinstaller --noconfirm survival_bot.spec
# 输出 dist/生存大冒险bot/
a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[('venv/Lib/site-packages/rapidocr_onnxruntime/models', 'rapidocr_onnxruntime/models')],
    hiddenimports=['rapidfuzz', 'win32api', 'win32gui', 'win32con', 'win32process', 'win32ui',
                   'secrets', 'hashlib', 'binascii'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['winsdk', 'pandas', 'scipy', 'matplotlib', 'test', 'unittest'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='生存大冒险bot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='生存大冒险bot',
)
