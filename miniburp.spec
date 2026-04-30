# PyInstaller spec for MiniBurp — builds a macOS .app bundle.
#
# Build:
#   pip install pyinstaller
#   pyinstaller miniburp.spec --clean --noconfirm
#
# The bundled app will live at ./dist/MiniBurp.app
#
# Notes on the bundle:
#   * Python 3.11 is embedded; your system Python isn't used.
#   * The app will write the CA + minted certs into ~/Library/Application
#     Support/MiniBurp/ca/ on first run (see miniburp/ca.py).
#   * Flask is excluded because the .app defaults to the Qt UI.
#   * Brotli is included for gzip/brotli decoding.

# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hidden = []
hidden += collect_submodules("miniburp")
hidden += collect_submodules("PySide6")
hidden += ["cryptography", "brotli"]

a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=[],
    datas=[
        # Bundle the PNG icon so the QApplication can set it as the window /
        # Dock icon at runtime. The .icns is consumed by BUNDLE below.
        ("assets/icon.png", "assets"),
    ],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "pytest",
        "numpy",
        "IPython",
        # We ship the Qt UI; the old Flask UI is not part of the bundle.
        "flask",
        "werkzeug",
        "jinja2",
    ],
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
    name="MiniBurp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # windowed
    disable_windowed_traceback=False,
    argv_emulation=True,     # macOS: forward CLI args into sys.argv
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/MiniBurp.icns",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MiniBurp",
)

app = BUNDLE(
    coll,
    name="MiniBurp.app",
    icon="assets/MiniBurp.icns",
    bundle_identifier="com.miniburp.app",
    info_plist={
        "CFBundleName": "MiniBurp",
        "CFBundleDisplayName": "MiniBurp",
        "CFBundleShortVersionString": "0.2.0",
        "CFBundleVersion": "0.2.0",
        "NSHighResolutionCapable": True,
        # No Internet permission prompts — we bind to 127.0.0.1 only.
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)
