# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ["app_launcher.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("web", "web"),
    ],
    hiddenimports=[
        "easyserp_client",
        "enhanced_book_smart_v2",
        "web_console",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Daydayup",
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

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Daydayup",
)

app = BUNDLE(
    coll,
    name="Daydayup.app",
    icon=None,
    bundle_identifier="cn.billchen.daydayup",
    info_plist={
        "CFBundleName": "Daydayup",
        "CFBundleDisplayName": "Daydayup",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "NSHighResolutionCapable": True,
    },
)
