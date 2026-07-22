# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec - macOS 专用。"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
PROJECT_ROOT = Path(SPECPATH).resolve().parent
SRC = PROJECT_ROOT / "src"
LIBS = PROJECT_ROOT / "libs"

datas = [
    (str(SRC / "open_db_mcp" / "config.py"), "open_db_mcp"),
    (str(PROJECT_ROOT / "config" / "whitelist.json"), "config"),
    (str(PROJECT_ROOT / "config" / "datasources.json"), "config"),
]
if LIBS.is_dir():
    for jar in LIBS.glob("*.jar"):
        datas.append((str(jar), "libs"))

hiddenimports = [
    "dm.jdbc.driver.DmDriver",
    "jpype",
    "jaydebeapi",
    "oracledb",
    "pydantic",
    "pydantic_settings",
    "sqlparse",
    "typer",
    "yaml",
] + collect_submodules("cryptography")

a = Analysis(
    [str(SRC / "open_db_mcp" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=None,
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PIL", "pytest"],
    cipher=block_cipher,
    noarchive=False,
    target_arch="universal2" if sys.platform == "darwin" else None,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="open-db-mcp",
    debug=False,
    strip=True,
    upx=True,
    console=True,
)
app = BUNDLE(
    exe,
    name="open-db-mcp.app",
    icon=None,
    bundle_identifier="io.github.cjp1016.open-db-mcp",
)
