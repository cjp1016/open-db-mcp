# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec - Linux 专用。"""

from pathlib import Path

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
    "dm.jdbc.driver.DmDriver", "jpype", "jaydebeapi", "oracledb",
    "pydantic", "pydantic_settings", "sqlparse", "typer", "yaml",
    "cryptography", "_cryptography",
]

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
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="open-db-mcp", debug=False, strip=True, upx=True, console=True,
)
