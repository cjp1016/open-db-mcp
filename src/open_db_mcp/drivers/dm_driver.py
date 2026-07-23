"""达梦驱动适配：JayDeBeApi + JPype1 桥接 JDBC。"""

from __future__ import annotations

import os
import re
import sys
from functools import lru_cache
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

DM_URL_RE = re.compile(r"^jdbc:dm://([^:/]+):(\d+)(?:/([^?]+))?$")


@lru_cache(maxsize=1)
def _start_jvm(jar_abs_path: str) -> None:
    """惰性启动 JVM（每进程只能启动一次，用 lru_cache 保护）。"""
    if jpype.isJVMStarted():
        return
    if not Path(jar_abs_path).is_file():
        raise FileNotFoundError(f"达梦 JDBC 驱动不存在: {jar_abs_path}")
    # JPype1 1.5+ 不再支持 convertStringArguments；保留其他参数向后兼容
    import inspect

    kwargs = {
        "jvmpath": _resolve_jvm_path(),
        "classpath": [jar_abs_path],
    }
    sig = inspect.signature(jpype.startJVM)
    if "convertStrings" in sig.parameters:
        kwargs["convertStrings"] = True
    jpype.startJVM(**kwargs)


def _resolve_jvm_path() -> str:
    """解析 JVM 共享库路径，兼容 JDK 8～21+。

    优先级：
    1. 环境变量 JPYPE_JVM_PATH / JAVA_HOME / JRE_HOME
    2. macOS 常见路径（/Library/Java/JavaVirtualMachines/jdk-*.jdk）
    3. JPype1 默认
    """
    # 1. 环境变量
    for env in ("JPYPE_JVM_PATH", "JAVA_HOME", "JRE_HOME"):
        p = os.environ.get(env)
        if p:
            for candidate in (
                Path(p) / "jre" / "bin" / "server" / "jvm.dll",    # JDK 8 (Windows)
                Path(p) / "bin" / "server" / "jvm.dll",            # JDK 11+ (Windows)
                Path(p) / "lib" / "server" / "libjvm.so",          # Linux
                Path(p) / "jre" / "lib" / "server" / "libjvm.so", # JDK 8 (Linux)
                Path(p) / "lib" / "server" / "libjvm.dylib",       # macOS
                Path(p) / "jre" / "lib" / "server" / "libjvm.dylib",  # JDK 8 (macOS)
            ):
                if candidate.is_file():
                    return str(candidate)

    # 2. macOS 常见路径
    jvm_root = Path("/Library/Java/JavaVirtualMachines")
    if jvm_root.is_dir():
        # 选最高版本
        candidates = sorted(
            jvm_root.glob("jdk-*.jdk"),
            key=lambda p: _jdk_version_key(p.name),
            reverse=True,
        )
        for c in candidates:
            lib = c / "Contents" / "Home" / "lib" / "server" / "libjvm.dylib"
            if lib.is_file():
                return str(lib)
            # 旧格式
            lib2 = c / "Contents" / "MacOS" / "libjvm.dylib"
            if lib2.is_file():
                return str(lib2)

    # 3. 兜底
    return jpype.getDefaultJVMPath()


def _jdk_version_key(name: str) -> tuple:
    """从 'jdk-17.0.9.jdk' 提取 (17, 0, 9)，用于排序。"""
    import re
    m = re.search(r"jdk-(\d+)(?:\.(\d+))?(?:\.(\d+))?", name)
    if not m:
        return (0,)
    return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0))


def _resolve_jar_path(jar_path: str) -> str:
    """解析 jar 路径，兼容 PyInstaller 单文件模式。

    若 jar_path 为空，自动在内置 libs 目录搜索 DmJdbcDriver*.jar。
    """
    if not jar_path:
        jar_path = "DmJdbcDriver18.jar"
    p = Path(jar_path)
    if p.is_absolute() and p.is_file():
        return str(p)
    meipass = getattr(sys, "_MEIPASS", None)
    candidates = []
    if meipass:
        candidates.append(Path(meipass) / jar_path)
        candidates.append(Path(meipass) / "libs" / jar_path)
        # 模糊搜索内置 libs 下的 DmJdbcDriver*.jar
        libs_dir = Path(meipass) / "libs"
        if libs_dir.is_dir():
            candidates.extend(sorted(libs_dir.glob("DmJdbcDriver*.jar")))
    candidates.append(Path.cwd() / jar_path)
    candidates.append(Path.cwd() / "libs" / jar_path)
    candidates.append(Path(__file__).resolve().parents[2] / "libs" / Path(jar_path).name)
    for cand in candidates:
        if cand.is_file():
            return str(cand.resolve())
    raise FileNotFoundError(f"达梦 JDBC 驱动未找到: {jar_path}")


class DmDriver(DriverAdapter):
    name = "dm"
    dbapi = jaydebeapi

    def __init__(self, jar_path: str = "") -> None:
        self._jar_input = jar_path

    @property
    def jar_path(self) -> str:
        return _resolve_jar_path(self._jar_input)

    def parse_url(self, url: str) -> dict[str, Any]:
        m = DM_URL_RE.match(url)
        if not m:
            raise ValueError(f"达梦 JDBC URL 非法: {url}")
        host, port, db = m.groups()
        return {"host": host, "port": int(port), "database": db or ""}

    def connect(self, conf):
        _start_jvm(self.jar_path)
        # JayDeBeApi 需要完整 JDBC URL（含 jdbc: 前缀）
        # JayDeBeApi 1.2+ 使用 jars= 参数；旧版 jclasspath= 已弃用
        return jaydebeapi.connect(
            "dm.jdbc.driver.DmDriver",
            conf.url,
            [conf.user, conf.password],
            jars=[self.jar_path],
        )

    def quote_ident(self, name: str) -> str:
        return f'"{name.upper()}"'

    def is_select(self, sql: str) -> bool:
        head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        return head in ("SELECT", "WITH")

    def ping_sql(self) -> str:
        return "SELECT 1 FROM DUAL"

    # ------------------------------------------------------------------
    # 元数据查询（达梦兼容 Oracle 数据字典视图）
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
                c.DATA_LENGTH,
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
                col_name, data_type, nullable, data_default, data_len, data_prec, data_scale = row
                cols.append(ColumnInfo(
                    name=str(col_name),
                    data_type=str(data_type),
                    nullable=(str(nullable) == "Y"),
                    default=str(data_default) if data_default is not None else None,
                    is_primary_key=str(col_name) in pk_cols,
                    character_maximum_length=int(data_len) if data_len is not None else None,
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
    # 慢查询日志
    # ------------------------------------------------------------------

    def fetch_slow_queries(
        self, conn: Any, limit: int = 50, threshold_sec: float = 1.0
    ) -> list[dict[str, Any]]:
        """从 V$SQL 获取慢查询（达梦兼容 Oracle 视图）。"""
        sql = """
            SELECT
                SQL_TEXT,
                EXECUTIONS,
                ROUND(ELAPSED_TIME / NULLIF(EXECUTIONS, 0) / 1000, 1) AS avg_ms,
                ROUND(ELAPSED_TIME / 1000, 1) AS total_ms,
                PARSING_SCHEMA_NAME,
                LAST_ACTIVE_TIME
            FROM V$SQL
            WHERE ELAPSED_TIME / NULLIF(EXECUTIONS, 0) > ?
              AND PARSING_SCHEMA_NAME NOT IN ('SYS', 'SYSTEM', 'SYSAUDITOR')
            ORDER BY ELAPSED_TIME / NULLIF(EXECUTIONS, 0) DESC
            LIMIT ?
        """
        results: list[dict[str, Any]] = []
        try:
            with conn.cursor() as cur:
                cur.execute(sql, [int(threshold_sec * 1_000_000), limit])
                for row in cur.fetchall():
                    sql_text, execs, avg_ms, total_ms, schema, last_active = row
                    results.append({
                        "sql": (sql_text or "")[:2000],
                        "duration_ms": int(avg_ms or 0),
                        "total_ms": int(total_ms or 0),
                        "exec_count": int(execs or 0),
                        "schema": schema,
                        "last_seen": str(last_active or ""),
                        "source": "database",
                    })
        except Exception:
            pass
        return results

    # ------------------------------------------------------------------
    # DBA 功能：锁管理 + 表空间管理
    # ------------------------------------------------------------------

    def list_locks(self, conn: Any) -> list[dict[str, Any]]:
        """查询当前锁/阻塞信息（V$SESSIONS + V$LOCK，达梦兼容 Oracle）。"""
        sql = """
            SELECT
                s.SESS_ID,
                s.SESS_ID AS SERIAL,
                s.USER_NAME,
                s.STATE,
                b.SESS_ID AS BLOCKING_SESS,
                s.CREATE_TIME,
                l.LOCK_TYPE,
                s.SQL_TEXT
            FROM V$SESSIONS s
            LEFT JOIN V$LOCK l ON s.SESS_ID = l.SESS_ID AND l.LOCK_MODE = 0
            LEFT JOIN V$SESSIONS b ON s.BLOCKED_BY = b.SESS_ID
            WHERE s.SESS_ID != SESSID()
              AND (s.BLOCKED_BY IS NOT NULL OR l.LOCK_MODE = 0)
            ORDER BY s.CREATE_TIME
        """
        results: list[dict[str, Any]] = []
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                for row in cur.fetchall():
                    sess_id, _serial, username, state, blocking, _ctime, lock_type, sql_text = row
                    results.append({
                        "session_id": str(sess_id),
                        "serial": str(sess_id),
                        "username": str(username or ""),
                        "status": str(state or ""),
                        "blocking_session": str(blocking) if blocking else None,
                        "wait_time_sec": 0,
                        "lock_type": str(lock_type or ""),
                        "sql_text": str(sql_text or "")[:500],
                    })
        except Exception:
            pass
        return results

    def kill_session(
        self, conn: Any, session_id: str, serial: str | None = None
    ) -> dict[str, Any]:
        """终止达梦会话：SP_CLOSE_SESSION(sess_id)。"""
        sql = f"CALL SP_CLOSE_SESSION({session_id})"
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
            return {"success": True, "message": f"已终止会话 {session_id}", "sql": sql}
        except Exception as exc:
            return {"success": False, "message": str(exc), "sql": sql}

    def list_tablespaces(self, conn: Any) -> list[dict[str, Any]]:
        """查询表空间使用情况（DBA_DATA_FILES + DBA_FREE_SPACE）。"""
        sql = """
            SELECT
                d.TABLESPACE_NAME,
                d.FILE_NAME,
                ROUND(d.BYTES / 1024 / 1024, 2) AS TOTAL_MB,
                ROUND((d.BYTES - NVL(f.FREE_BYTES, 0)) / 1024 / 1024, 2) AS USED_MB,
                ROUND(NVL(f.FREE_BYTES, 0) / 1024 / 1024, 2) AS FREE_MB,
                ROUND((d.BYTES - NVL(f.FREE_BYTES, 0)) / d.BYTES * 100, 1) AS USED_PCT,
                d.AUTOEXTENSIBLE,
                ROUND(d.MAXBYTES / 1024 / 1024, 2) AS MAX_SIZE_MB
            FROM DBA_DATA_FILES d
            LEFT JOIN (
                SELECT FILE_ID, SUM(BYTES) AS FREE_BYTES
                FROM DBA_FREE_SPACE
                GROUP BY FILE_ID
            ) f ON d.FILE_ID = f.FILE_ID
            ORDER BY USED_PCT DESC
        """
        results: list[dict[str, Any]] = []
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                for row in cur.fetchall():
                    name, file_path, total, used, free, pct, autoext, max_sz = row
                    results.append({
                        "name": str(name),
                        "file_path": str(file_path),
                        "total_mb": float(total) if total else 0,
                        "used_mb": float(used) if used else 0,
                        "free_mb": float(free) if free else 0,
                        "used_pct": float(pct) if pct else 0,
                        "autoextend": str(autoext) == "YES",
                        "max_size_mb": float(max_sz) if max_sz else 0,
                    })
        except Exception:
            pass
        return results

    def resize_tablespace(
        self, conn: Any, file_path: str, new_size_mb: int
    ) -> dict[str, Any]:
        """扩容数据文件：ALTER TABLESPACE ... RESIZE DATAFILE。"""
        # 达梦语法：ALTER TABLESPACE ts_name RESIZE DATAFILE 'path' TO nM
        # 但更通用的是直接指定文件
        sql = f"ALTER DATABASE DATAFILE '{file_path}' RESIZE {new_size_mb}M"
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
            return {
                "success": True,
                "message": f"已将 {file_path} 扩容至 {new_size_mb}MB",
                "sql": sql,
            }
        except Exception as exc:
            return {"success": False, "message": str(exc), "sql": sql}

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
