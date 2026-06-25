# PyInstaller spec for the VCDS Toolkit desktop GUI.
#
# Build (one-folder, windowed):
#     pyinstaller installer/vcds_gui.spec --clean --noconfirm
#
# Output: dist/VCDS Toolkit/  (a self-contained folder; "VCDS Toolkit.exe" inside)
#
# One-folder mode is used deliberately: it is what the Inno Setup installer packs,
# starts faster than one-file, and avoids the one-file temp-extraction antivirus
# friction common on locked-down Windows machines.

import os

from PyInstaller.utils.hooks import collect_all, copy_metadata

# Resolve paths relative to this spec file (PyInstaller sets SPECPATH).
SPEC_DIR = os.path.abspath(SPECPATH)
ROOT = os.path.dirname(SPEC_DIR)
SRC = os.path.join(ROOT, "src")
ENTRY = os.path.join(SRC, "vcds_gui", "__main__.py")
MCP_ENTRY = os.path.join(SRC, "vcds_mcp", "__main__.py")
ICON = os.path.join(SPEC_DIR, "app.ico")
ICON = ICON if os.path.isfile(ICON) else None

# pyqtgraph does a lot of dynamic importing; pull it in wholesale to be safe.
pg_datas, pg_binaries, pg_hidden = collect_all("pyqtgraph")

# The bundled exe can also serve the MCP server (`VCDS Toolkit.exe --mcp`), so
# pull in the FastMCP stack.
try:
    mcp_datas, mcp_binaries, mcp_hidden = collect_all("mcp")
except Exception:
    mcp_datas, mcp_binaries, mcp_hidden = [], [], []

# Bundle the installed dist metadata so importlib.metadata.version("vcds-toolkit")
# resolves at runtime (otherwise __version__ falls back and shows the wrong
# version). Requires `pip install -e .` before building (build_installer.ps1 does).
try:
    meta_datas = copy_metadata("vcds-toolkit")
except Exception:
    meta_datas = []

# Ship the example files alongside the app so users have something to open.
example_datas = []
examples_dir = os.path.join(ROOT, "examples")
if os.path.isdir(examples_dir):
    for name in os.listdir(examples_dir):
        full = os.path.join(examples_dir, name)
        if os.path.isfile(full):
            example_datas.append((full, "examples"))

block_cipher = None

icon_datas = [(ICON, ".")] if ICON else []

a = Analysis(
    [ENTRY],
    pathex=[SRC],
    binaries=pg_binaries + mcp_binaries,
    datas=pg_datas + example_datas + icon_datas + meta_datas + mcp_datas,
    hiddenimports=pg_hidden
    + mcp_hidden
    + [
        "vcds_core",
        "vcds_core.parse",
        "vcds_obd",
        "vcds_obd.live",
        "vcds_obd.mcp_tools",
        "vcds_obd.enhanced",
        "vcds_mcp",
        "vcds_mcp.server",
        "vcds_mcp.install",
        "certifi",  # CA bundle for the in-app updater's HTTPS check
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Trim heavy Qt modules we never use to keep the bundle smaller.
    excludes=[
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtQuick",
        "PySide6.QtQml",
        "PySide6.Qt3DCore",
        "PySide6.QtMultimedia",
        "PySide6.QtPdf",
        "PyQt5",
        "PyQt6",
        "tkinter",
        "matplotlib",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Second target: a CONSOLE exe that serves the MCP server over stdio. A windowed
# (GUI) exe has no stdin/stdout, so it cannot speak the stdio protocol — hence a
# dedicated console binary that Claude Desktop/Code launches. MERGE shares all
# the common dependencies with the GUI bundle (no duplication on disk).
a_mcp = Analysis(
    [MCP_ENTRY],
    pathex=[SRC],
    binaries=mcp_binaries,
    datas=meta_datas + mcp_datas,
    hiddenimports=mcp_hidden
    + [
        "vcds_core",
        "vcds_core.parse",
        "vcds_obd",
        "vcds_obd.live",
        "vcds_obd.mcp_tools",
        "vcds_mcp",
        "vcds_mcp.server",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6", "pyqtgraph", "PyQt5", "PyQt6", "tkinter", "matplotlib"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

MERGE((a, "VCDS Toolkit", "VCDS Toolkit"), (a_mcp, "vcds-mcp", "vcds-mcp"))

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VCDS Toolkit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,  # installer/app.ico — see scripts/make_icon.py
)

pyz_mcp = PYZ(a_mcp.pure, a_mcp.zipped_data, cipher=block_cipher)
exe_mcp = EXE(
    pyz_mcp,
    a_mcp.scripts,
    [],
    exclude_binaries=True,
    name="vcds-mcp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # MUST be a console app for stdio
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    exe_mcp,
    a_mcp.binaries,
    a_mcp.zipfiles,
    a_mcp.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VCDS Toolkit",
)
