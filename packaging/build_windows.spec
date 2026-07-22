# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec - Windows 专用。"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
import importlib

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

# JPype 支持 jar（JPype 在 __file__ 上两级目录查找 org.jpype.jar）
_jpype_mod = importlib.import_module("jpype")
_jpype_jar = Path(_jpype_mod.__file__).parent.parent / "org.jpype.jar"
if _jpype_jar.is_file():
    datas.append((str(_jpype_jar), "."))

# 收集 cryptography 所有子模块（默认 hook 只收集 hazmat.backends，
# 但 oracledb thin 模式需要 cryptography.x509 等子包）
_crypto_mods = collect_submodules("cryptography")
# 排除 CFFI 时代的 openssl 绑定（已废弃），避免无关警告
_crypto_mods = [
    m for m in _crypto_mods
    if not m.startswith("cryptography.hazmat.bindings.openssl.")
]

hiddenimports = [
    "dm.jdbc.driver.DmDriver",
    "jpype", "jaydebeapi",
    "oracledb", "oracledb.thin_impl", "oracledb.base_impl",
    "pymysql",
    "psycopg2",
    "sqlglot",
    "sqlglot.dialects",
    "sqlglot.dialects.oracle",
    "sqlglot.dialects.mysql",
    "sqlglot.dialects.sqlite",
    "sqlglot.dialects.postgres",
    "pydantic", "pydantic_settings", "sqlparse", "typer", "yaml",
    "cryptography",
    "dbutils", "dbutils.pooled_db",
    "mcp",
] + _crypto_mods

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
    name="open-db-mcp", debug=False, strip=False, upx=True, console=True,
)
