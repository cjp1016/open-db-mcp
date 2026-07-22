# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec - 当前平台（macOS/Linux/Windows 自动识别）。"""

import sys
from pathlib import Path

block_cipher = None

# 兼容 uv / pip 安装后的 src 布局
PROJECT_ROOT = Path(SPECPATH).resolve().parent  # packaging/ 的父目录 = 项目根
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
    "pymysql",
    "psycopg2",
    "pydantic",
    "pydantic_settings",
    "sqlparse",
    "typer",
    "yaml",
    # cryptography 子模块（oracledb 需要）
    "cryptography",
    "cryptography.hazmat",
    "cryptography.hazmat.backends",
    "cryptography.hazmat.backends.openssl",
    "cryptography.hazmat.primitives",
    "cryptography.hazmat.primitives.ciphers",
    "cryptography.hazmat.primitives.kdf",
    "cryptography.hazmat.primitives.kdf.pbkdf2",
    "cryptography.hazmat.primitives.hashes",
    "cryptography.hazmat.primitives.serialization",
    "cryptography.hazmat.bindings",
    "cryptography.hazmat.bindings.openssl",
    "cryptography.fernet",
    "_cryptography",
    # sqlglot 方言（SQL 安全校验需要）
    "sqlglot",
    "sqlglot.dialects",
    "sqlglot.dialects.oracle",
    "sqlglot.dialects.mysql",
    "sqlglot.dialects.sqlite",
    "sqlglot.dialects.postgres",
]

a = Analysis(
    [str(SRC / "open_db_mcp" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=None,
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "PIL",
        "pytest",
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="open-db-mcp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
