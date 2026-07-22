"""Oracle JDBC 驱动适配：JayDeBeApi + JPype1 桥接（Oracle 11g 兼容）。

使用 Oracle 官方 JDBC 驱动（ojdbc6.jar / ojdbc8.jar），
无需安装 Oracle Client 库，只需 Java 运行环境。
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import jaydebeapi
import jpype

from .base import (
    ColumnInfo,
    DriverAdapter,
    ExplainPlan,
    IndexInfo,
    SchemaInfo,
    TableInfo,
)

log = logging.getLogger(__name__)

# Oracle JDBC URL 格式（兼容 Oracle 11g+）:
# 1. SID 格式:      jdbc:oracle:thin:@host:port:SID
# 2. Service Name:  jdbc:oracle:thin:@//host:port/service_name
# 3. Service Name:  jdbc:oracle:thin:@host:port/service_name  (无 //)
SID_RE = re.compile(r"^jdbc:oracle:thin:@([^:/]+):(\d+):([^:/]+)$")
SVC_RE = re.compile(r"^jdbc:oracle:thin:@//([^:/]+):(\d+)/([^?#/]+)$")
SVC_NO_SLASH_RE = re.compile(r"^jdbc:oracle:thin:@([^:/]+):(\d+)/([^?#/]+)$")

# JVM 启动标志（与 DM 驱动共享，每进程只能启动一次）
_jvm_started_for_oracle = False


def _start_jvm_for_oracle(jar_path: str) -> None:
    """惰性启动 JVM（若 DM 驱动已启动则复用）。"""
    global _jvm_started_for_oracle
    if jpype.isJVMStarted():
        _jvm_started_for_oracle = True
        return
    if _jvm_started_for_oracle:
        return
    if not Path(jar_path).is_file():
        raise FileNotFoundError(f"Oracle JDBC 驱动不存在: {jar_path}")
    import inspect

    kwargs = {
        "jvmpath": _resolve_jvm_path(),
        "classpath": [jar_path],
    }
    sig = inspect.signature(jpype.startJVM)
    if "convertStrings" in sig.parameters:
        kwargs["convertStrings"] = True
    jpype.startJVM(**kwargs)
    _jvm_started_for_oracle = True
    log.info("JVM 已启动（Oracle JDBC），驱动: %s", jar_path)


def _resolve_jvm_path() -> str:
    """解析 JVM 共享库路径，兼容 JDK 8～21+。"""
    for env in ("JPYPE_JVM_PATH", "JAVA_HOME", "JRE_HOME"):
        p = os.environ.get(env)
        if p:
            for candidate in (
                Path(p) / "jre" / "bin" / "server" / "jvm.dll",    # JDK 8 (Windows)
                Path(p) / "bin" / "server" / "jvm.dll",            # JDK 11+ (Windows)
                Path(p) / "lib" / "server" / "libjvm.so",          # Linux
                Path(p) / "jre" / "lib" / "server" / "libjvm.so",  # JDK 8 (Linux)
                Path(p) / "lib" / "server" / "libjvm.dylib",       # macOS
                Path(p) / "jre" / "lib" / "server" / "libjvm.dylib",  # JDK 8 (macOS)
            ):
                if candidate.is_file():
                    return str(candidate)
    # macOS 常见路径
    jvm_root = Path("/Library/Java/JavaVirtualMachines")
    if jvm_root.is_dir():
        candidates = sorted(
            jvm_root.glob("jdk-*.jdk"),
            key=lambda p: _jdk_version_key(p.name),
            reverse=True,
        )
        for c in candidates:
            lib = c / "Contents" / "Home" / "lib" / "server" / "libjvm.dylib"
            if lib.is_file():
                return str(lib)
    return jpype.getDefaultJVMPath()


def _jdk_version_key(name: str) -> tuple:
    """从 'jdk-17.0.9.jdk' 提取 (17, 0, 9)，用于排序。"""
    m = re.search(r"jdk-(\d+)(?:\.(\d+))?(?:\.(\d+))?", name)
    if not m:
        return (0,)
    return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0))


def _resolve_oracle_jar_path(jar_path: str) -> str:
    """解析 Oracle JDBC jar 路径，兼容 PyInstaller 单文件模式。

    若 jar_path 为空，自动搜索内置 libs 目录下的 ojdbc*.jar。
    """
    if not jar_path:
        jar_path = "ojdbc8.jar"  # 默认使用 ojdbc8（兼容 Oracle 11g+）
    p = Path(jar_path)
    if p.is_absolute() and p.is_file():
        return str(p)
    meipass = getattr(sys, "_MEIPASS", None)
    candidates = []
    if meipass:
        candidates.append(Path(meipass) / jar_path)
        candidates.append(Path(meipass) / "libs" / jar_path)
        # 模糊搜索内置 libs 下的 ojdbc*.jar
        libs_dir = Path(meipass) / "libs"
        if libs_dir.is_dir():
            candidates.extend(sorted(libs_dir.glob("ojdbc*.jar")))
    candidates.append(Path.cwd() / jar_path)
    candidates.append(Path.cwd() / "libs" / jar_path)
    candidates.append(Path(__file__).resolve().parents[2] / "libs" / Path(jar_path).name)
    for cand in candidates:
        if cand.is_file():
            return str(cand.resolve())
    raise FileNotFoundError(
        f"Oracle JDBC 驱动未找到: {jar_path}，"
        f"请下载 ojdbc8.jar 并放入 libs/ 目录或设置 ORACLE_JDBC_JAR_PATH 环境变量"
    )


class OracleJdbcDriver(DriverAdapter):
    """Oracle JDBC 驱动（JayDeBeApi 实现）。

    使用 Oracle 官方 JDBC 驱动（ojdbc6.jar / ojdbc8.jar），
    完全兼容 Oracle 11g，无需 Oracle Client 库。
    """

    name = "oracle_jdbc"
    dbapi = jaydebeapi

    def __init__(self, jar_path: str = "") -> None:
        # 优先使用环境变量 ORACLE_JDBC_JAR_PATH
        self._jar_input = jar_path or os.environ.get("ORACLE_JDBC_JAR_PATH", "")

    @property
    def jar_path(self) -> str:
        return _resolve_oracle_jar_path(self._jar_input)

    def parse_url(self, url: str) -> dict[str, Any]:
        # 优先匹配 Service Name 格式（带 //），再匹配 SID 格式，最后匹配无 // 的 Service Name
        m = SVC_RE.match(url)
        if m:
            host, port, ident = m.groups()
            return {"host": host, "port": int(port), "service_name": ident, "url_style": "service"}
        m = SID_RE.match(url)
        if m:
            host, port, ident = m.groups()
            return {"host": host, "port": int(port), "service_name": ident, "url_style": "sid"}
        m = SVC_NO_SLASH_RE.match(url)
        if m:
            host, port, ident = m.groups()
            return {"host": host, "port": int(port), "service_name": ident, "url_style": "service"}
        raise ValueError(f"Oracle JDBC URL 不被支持: {url}")

    def connect(self, conf):
        jar = self.jar_path
        _start_jvm_for_oracle(jar)
        # JayDeBeApi 需要完整 JDBC URL
        return jaydebeapi.connect(
            "oracle.jdbc.OracleDriver",
            conf.url,
            [conf.user, conf.password],
            jars=[jar],
        )

    def quote_ident(self, name: str) -> str:
        return f'"{name.upper()}"'

    def is_select(self, sql: str) -> bool:
        head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        return head in ("SELECT", "WITH")

    def ping_sql(self) -> str:
        return "SELECT 1 FROM DUAL"

    # ------------------------------------------------------------------
    # 元数据查询（Oracle 数据字典视图）
    # ------------------------------------------------------------------

    def describe_table(
        self, conn: Any, table: str, schema: str | None = None
    ) -> list[ColumnInfo]:
        table_name = table.upper()
        owner = schema.upper() if schema else self._current_user(conn)
        pk_cols = self._primary_key_columns(conn, table_name, owner)
        sql = """
            SELECT
                c.COLUMN_NAME,
                c.DATA_TYPE,
                c.NULLABLE,
                c.DATA_DEFAULT,
                c.CHAR_LENGTH,
                c.DATA_PRECISION,
                c.DATA_SCALE
            FROM ALL_TAB_COLUMNS c
            WHERE c.OWNER = ?
              AND c.TABLE_NAME = ?
            ORDER BY c.COLUMN_ID
        """
        cols: list[ColumnInfo] = []
        with conn.cursor() as cur:
            cur.execute(sql, (owner, table_name))
            for row in cur.fetchall():
                col_name, data_type, nullable, data_default, char_len, data_prec, data_scale = row
                cols.append(ColumnInfo(
                    name=str(col_name),
                    data_type=str(data_type),
                    nullable=(str(nullable) == "Y"),
                    default=str(data_default) if data_default is not None else None,
                    is_primary_key=str(col_name) in pk_cols,
                    character_maximum_length=int(char_len) if char_len is not None else None,
                    numeric_precision=int(data_prec) if data_prec is not None else None,
                    numeric_scale=int(data_scale) if data_scale is not None else None,
                ))
        return cols

    def list_tables(
        self,
        conn: Any,
        schema: str | None = None,
        table_type: str | None = None,
    ) -> list[TableInfo]:
        owner = (schema or self._current_user(conn)).upper()
        params: list[Any] = [owner]
        if table_type == "VIEW":
            sql = """
                SELECT VIEW_NAME AS TABLE_NAME, 'VIEW' AS TABLE_TYPE
                FROM ALL_VIEWS
                WHERE OWNER = ?
                ORDER BY VIEW_NAME
            """
        elif table_type == "BASE TABLE":
            sql = """
                SELECT TABLE_NAME, 'BASE TABLE' AS TABLE_TYPE
                FROM ALL_TABLES
                WHERE OWNER = ?
                  AND TABLE_NAME NOT LIKE 'BIN$%'
                ORDER BY TABLE_NAME
            """
        else:
            sql = """
                SELECT TABLE_NAME, 'BASE TABLE' AS TABLE_TYPE
                FROM ALL_TABLES
                WHERE OWNER = ?
                  AND TABLE_NAME NOT LIKE 'BIN$%'
                UNION ALL
                SELECT VIEW_NAME AS TABLE_NAME, 'VIEW' AS TABLE_TYPE
                FROM ALL_VIEWS
                WHERE OWNER = ?
                ORDER BY TABLE_NAME
            """
            params.append(owner)
        tables: list[TableInfo] = []
        with conn.cursor() as cur:
            cur.execute(sql, params)
            for row in cur.fetchall():
                name, ttype = row
                tables.append(TableInfo(
                    name=str(name),
                    type=str(ttype),
                    schema=owner,
                ))
        return tables

    def list_schemas(self, conn: Any) -> list[SchemaInfo]:
        current_user = self._current_user(conn)
        schemas: list[SchemaInfo] = []
        with conn.cursor() as cur:
            cur.execute("SELECT USERNAME FROM ALL_USERS ORDER BY USERNAME")
            for row in cur.fetchall():
                name = str(row[0])
                schemas.append(SchemaInfo(
                    name=name,
                    is_default=(name == current_user),
                ))
        return schemas

    def list_indexes(
        self, conn: Any, table: str, schema: str | None = None
    ) -> list[IndexInfo]:
        table_name = table.upper()
        owner = (schema or self._current_user(conn)).upper()
        sql = """
            SELECT
                i.INDEX_NAME,
                ic.COLUMN_NAME,
                i.UNIQUENESS,
                ic.COLUMN_POSITION
            FROM ALL_INDEXES i
            JOIN ALL_IND_COLUMNS ic
              ON i.OWNER = ic.INDEX_OWNER
             AND i.INDEX_NAME = ic.INDEX_NAME
             AND i.TABLE_OWNER = ic.TABLE_OWNER
             AND i.TABLE_NAME = ic.TABLE_NAME
            WHERE i.TABLE_OWNER = ?
              AND i.TABLE_NAME = ?
            ORDER BY i.INDEX_NAME, ic.COLUMN_POSITION
        """
        index_map: dict[str, IndexInfo] = {}
        pk_names = self._pk_index_names(conn, table_name, owner)
        with conn.cursor() as cur:
            cur.execute(sql, (owner, table_name))
            for row in cur.fetchall():
                idx_name, col_name, uniqueness, _pos = row
                idx_name_str = str(idx_name)
                col_name_str = str(col_name)
                if idx_name_str not in index_map:
                    index_map[idx_name_str] = IndexInfo(
                        name=idx_name_str,
                        is_unique=(str(uniqueness) == "UNIQUE"),
                        is_primary=(idx_name_str in pk_names),
                    )
                index_map[idx_name_str].columns.append(col_name_str)
        return list(index_map.values())

    def explain_query(
        self, conn: Any, sql: str, params: list | tuple | None = None
    ) -> ExplainPlan:
        plan = ExplainPlan()
        statement_id = f"open_db_mcp_{abs(hash(sql))}"[:28]
        with conn.cursor() as cur:
            try:
                explain_sql = f"EXPLAIN PLAN SET STATEMENT_ID = '{statement_id}' FOR {sql}"
                cur.execute(explain_sql, params or [])
            except Exception:
                return plan
            try:
                cur.execute(
                    "SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY('PLAN_TABLE', ?, 'ALL'))",
                    [statement_id],
                )
                raw_rows = []
                for row in cur.fetchall():
                    raw_rows.append({"plan_line": str(row[0]) if row else ""})
                plan.raw_rows = raw_rows
            except Exception:
                pass
        return plan

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _current_user(conn: Any) -> str:
        with conn.cursor() as cur:
            cur.execute("SELECT USER FROM DUAL")
            row = cur.fetchone()
            return str(row[0]) if row else ""

    @staticmethod
    def _primary_key_columns(
        conn: Any, table: str, owner: str
    ) -> set[str]:
        sql = """
            SELECT cc.COLUMN_NAME
            FROM ALL_CONSTRAINTS c
            JOIN ALL_CONS_COLUMNS cc
              ON c.CONSTRAINT_NAME = cc.CONSTRAINT_NAME
             AND c.OWNER = cc.OWNER
             AND c.TABLE_NAME = cc.TABLE_NAME
            WHERE c.OWNER = ?
              AND c.TABLE_NAME = ?
              AND c.CONSTRAINT_TYPE = 'P'
            ORDER BY cc.POSITION
        """
        with conn.cursor() as cur:
            cur.execute(sql, (owner, table))
            return {str(row[0]) for row in cur.fetchall()}

    @staticmethod
    def _pk_index_names(
        conn: Any, table: str, owner: str
    ) -> set[str]:
        sql = """
            SELECT INDEX_NAME
            FROM ALL_CONSTRAINTS
            WHERE OWNER = ?
              AND TABLE_NAME = ?
              AND CONSTRAINT_TYPE = 'P'
        """
        with conn.cursor() as cur:
            cur.execute(sql, (owner, table))
            return {str(row[0]) for row in cur.fetchall()}
