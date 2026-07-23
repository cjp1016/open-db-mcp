"""Oracle 驱动适配：支持 thin 模式（默认）和 thick 模式（Oracle 11g 兼容）。"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import oracledb

from .base import (
    ColumnInfo,
    DriverAdapter,
    ExplainPlan,
    IndexInfo,
    SchemaInfo,
    TableInfo,
)

log = logging.getLogger(__name__)

# thick 模式初始化标志（全局只初始化一次）
_thick_mode_initialized = False


def _ensure_thick_mode() -> None:
    """根据环境变量 ORACLE_CLIENT_LIB_DIR 初始化 thick 模式（Oracle 11g 兼容）。"""
    global _thick_mode_initialized
    if _thick_mode_initialized:
        return
    lib_dir = os.environ.get("ORACLE_CLIENT_LIB_DIR", "").strip()
    if lib_dir:
        try:
            oracledb.init_oracle_client(lib_dir=lib_dir)
            _thick_mode_initialized = True
            log.info("Oracle thick 模式已启用，客户端库路径: %s", lib_dir)
        except Exception as exc:
            log.warning("Oracle thick 模式初始化失败（将使用 thin 模式）: %s", exc)
    else:
        _thick_mode_initialized = True  # 未配置则跳过，避免重复检查

# Oracle JDBC URL 格式（兼容 Oracle 11g+）:
# 1. SID 格式:      jdbc:oracle:thin:@host:port:SID
# 2. Service Name:  jdbc:oracle:thin:@//host:port/service_name
# 3. Service Name:  jdbc:oracle:thin:@host:port/service_name  (无 //)
SID_RE = re.compile(r"^jdbc:oracle:thin:@([^:/]+):(\d+):([^:/]+)$")
SVC_RE = re.compile(r"^jdbc:oracle:thin:@//([^:/]+):(\d+)/([^?#/]+)$")
SVC_NO_SLASH_RE = re.compile(r"^jdbc:oracle:thin:@([^:/]+):(\d+)/([^?#/]+)$")


class OracleDriver(DriverAdapter):
    name = "oracle"
    dbapi = oracledb

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
        # Oracle 11g 兼容：检查是否需要启用 thick 模式
        _ensure_thick_mode()
        u = self.parse_url(conf.url)
        # SID 格式用 sid 参数，Service Name 格式用 service_name
        if u["url_style"] == "sid":
            dsn = oracledb.makedsn(
                u["host"], u["port"], sid=u["service_name"]
            )
        else:
            dsn = oracledb.makedsn(
                u["host"], u["port"], service_name=u["service_name"]
            )
        return oracledb.connect(
            user=conf.user, password=conf.password, dsn=dsn
        )

    def quote_ident(self, name: str) -> str:
        return f'"{name.upper()}"'

    def is_select(self, sql: str) -> bool:
        head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        return head in ("SELECT", "WITH")

    def ping_sql(self) -> str:
        return "SELECT 1 FROM DUAL"

    # ------------------------------------------------------------------
    # 元数据查询
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
            WHERE c.OWNER = :owner
              AND c.TABLE_NAME = :table_name
            ORDER BY c.COLUMN_ID
        """
        cols: list[ColumnInfo] = []
        with conn.cursor() as cur:
            cur.execute(sql, owner=owner, table_name=table_name)
            for row in cur.fetchall():
                col_name, data_type, nullable, data_default, char_len, data_prec, data_scale = row
                cols.append(ColumnInfo(
                    name=col_name,
                    data_type=data_type,
                    nullable=(nullable == "Y"),
                    default=str(data_default) if data_default is not None else None,
                    is_primary_key=col_name in pk_cols,
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
        sql = """
            SELECT TABLE_NAME, 'BASE TABLE' AS TABLE_TYPE
            FROM ALL_TABLES
            WHERE OWNER = :owner
              AND TABLE_NAME NOT LIKE 'BIN$%'
        """
        params: dict[str, Any] = {"owner": owner}
        if table_type == "VIEW":
            sql = """
                SELECT VIEW_NAME AS TABLE_NAME, 'VIEW' AS TABLE_TYPE
                FROM ALL_VIEWS
                WHERE OWNER = :owner
            """
        elif table_type is None:
            sql = """
                SELECT TABLE_NAME, 'BASE TABLE' AS TABLE_TYPE
                FROM ALL_TABLES
                WHERE OWNER = :owner
                  AND TABLE_NAME NOT LIKE 'BIN$%'
                UNION ALL
                SELECT VIEW_NAME AS TABLE_NAME, 'VIEW' AS TABLE_TYPE
                FROM ALL_VIEWS
                WHERE OWNER = :owner
            """
        sql += " ORDER BY TABLE_NAME"
        tables: list[TableInfo] = []
        with conn.cursor() as cur:
            cur.execute(sql, **params)
            for row in cur.fetchall():
                name, ttype = row
                tables.append(TableInfo(
                    name=name,
                    type=ttype,
                    schema=owner,
                ))
        return tables

    def list_schemas(self, conn: Any) -> list[SchemaInfo]:
        current_user = self._current_user(conn)
        schemas: list[SchemaInfo] = []
        with conn.cursor() as cur:
            cur.execute("SELECT USERNAME FROM ALL_USERS ORDER BY USERNAME")
            for row in cur.fetchall():
                name = row[0]
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
                ic.COLUMN_POSITION,
                i.INDEX_TYPE
            FROM ALL_INDEXES i
            JOIN ALL_IND_COLUMNS ic
              ON i.OWNER = ic.INDEX_OWNER
             AND i.INDEX_NAME = ic.INDEX_NAME
             AND i.TABLE_OWNER = ic.TABLE_OWNER
             AND i.TABLE_NAME = ic.TABLE_NAME
            WHERE i.TABLE_OWNER = :owner
              AND i.TABLE_NAME = :table_name
            ORDER BY i.INDEX_NAME, ic.COLUMN_POSITION
        """
        index_map: dict[str, IndexInfo] = {}
        pk_names = self._pk_index_names(conn, table_name, owner)
        with conn.cursor() as cur:
            cur.execute(sql, owner=owner, table_name=table_name)
            for row in cur.fetchall():
                idx_name, col_name, uniqueness, _pos, _idx_type = row
                if idx_name not in index_map:
                    index_map[idx_name] = IndexInfo(
                        name=idx_name,
                        is_unique=(uniqueness == "UNIQUE"),
                        is_primary=(idx_name in pk_names),
                    )
                index_map[idx_name].columns.append(col_name)
        return list(index_map.values())

    def explain_query(
        self, conn: Any, sql: str, params: list | tuple | None = None
    ) -> ExplainPlan:
        plan = ExplainPlan()
        statement_id = f"open_db_mcp_{id(sql)}"[:30]
        with conn.cursor() as cur:
            try:
                cur.execute(f"EXPLAIN PLAN SET STATEMENT_ID = '{statement_id}' FOR {sql}", params or [])
            except Exception:
                return plan
            cur.execute("""
                SELECT *
                FROM TABLE(DBMS_XPLAN.DISPLAY('PLAN_TABLE', :stmt_id, 'ALL'))
            """, stmt_id=statement_id)
            raw_rows = []
            for row in cur.fetchall():
                raw_rows.append({"plan_line": str(row[0]) if row else ""})
            plan.raw_rows = raw_rows
        return plan

    # ------------------------------------------------------------------
    # 慢查询日志
    # ------------------------------------------------------------------

    def fetch_slow_queries(
        self, conn: Any, limit: int = 50, threshold_sec: float = 1.0
    ) -> list[dict[str, Any]]:
        """从 V$SQL 获取慢查询（elapsed_time 单位为微秒）。"""
        sql = """
            SELECT
                SQL_TEXT,
                EXECUTIONS,
                ROUND(ELAPSED_TIME / NULLIF(EXECUTIONS, 0) / 1000, 1) AS avg_ms,
                ROUND(ELAPSED_TIME / 1000, 1) AS total_ms,
                PARSING_SCHEMA_NAME,
                LAST_ACTIVE_TIME
            FROM V$SQL
            WHERE ELAPSED_TIME / NULLIF(EXECUTIONS, 0) > :threshold_us
              AND PARSING_SCHEMA_NAME NOT IN ('SYS', 'SYSTEM')
            ORDER BY ELAPSED_TIME / NULLIF(EXECUTIONS, 0) DESC
            FETCH FIRST :lim ROWS ONLY
        """
        results: list[dict[str, Any]] = []
        try:
            with conn.cursor() as cur:
                cur.execute(sql, threshold_us=int(threshold_sec * 1_000_000), lim=limit)
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
        """查询当前锁/阻塞信息（V$SESSION + V$LOCK）。"""
        sql = """
            SELECT
                s.SID,
                s.SERIAL#,
                s.USERNAME,
                s.STATUS,
                s.BLOCKING_SESSION,
                s.SECONDS_IN_WAIT,
                l.TYPE AS LOCK_TYPE,
                q.SQL_TEXT
            FROM V$SESSION s
            LEFT JOIN V$LOCK l ON s.SID = l.SID AND l.REQUEST > 0
            LEFT JOIN V$SQL q ON s.SQL_ID = q.SQL_ID AND q.CHILD_NUMBER = 0
            WHERE s.TYPE = 'USER'
              AND (s.BLOCKING_SESSION IS NOT NULL OR l.REQUEST > 0)
            ORDER BY s.SECONDS_IN_WAIT DESC NULLS LAST
        """
        results: list[dict[str, Any]] = []
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                for row in cur.fetchall():
                    sid, serial, username, status, blocking, wait_sec, lock_type, sql_text = row
                    results.append({
                        "session_id": str(sid),
                        "serial": str(serial),
                        "username": username or "",
                        "status": status or "",
                        "blocking_session": str(blocking) if blocking else None,
                        "wait_time_sec": int(wait_sec) if wait_sec else 0,
                        "lock_type": lock_type or "",
                        "sql_text": (sql_text or "")[:500],
                    })
        except Exception:
            pass
        return results

    def kill_session(
        self, conn: Any, session_id: str, serial: str | None = None
    ) -> dict[str, Any]:
        """终止 Oracle 会话：ALTER SYSTEM KILL SESSION 'sid,serial#' IMMEDIATE。"""
        if not serial:
            # 尝试自动获取 serial#
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT SERIAL# FROM V$SESSION WHERE SID = :sid",
                        sid=int(session_id),
                    )
                    row = cur.fetchone()
                    if row:
                        serial = str(row[0])
            except Exception as exc:
                return {"success": False, "message": f"无法获取 serial#: {exc}"}
        if not serial:
            return {"success": False, "message": "缺少 serial# 参数"}
        sql = f"ALTER SYSTEM KILL SESSION '{session_id},{serial}' IMMEDIATE"
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
            return {"success": True, "message": f"已终止会话 {session_id},{serial}", "sql": sql}
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
                        "name": name,
                        "file_path": file_path,
                        "total_mb": float(total) if total else 0,
                        "used_mb": float(used) if used else 0,
                        "free_mb": float(free) if free else 0,
                        "used_pct": float(pct) if pct else 0,
                        "autoextend": autoext == "YES",
                        "max_size_mb": float(max_sz) if max_sz else 0,
                    })
        except Exception:
            pass
        return results

    def resize_tablespace(
        self, conn: Any, file_path: str, new_size_mb: int
    ) -> dict[str, Any]:
        """扩容数据文件：ALTER DATABASE DATAFILE 'path' RESIZE nM。"""
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
            return row[0] if row else ""

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
            WHERE c.OWNER = :owner
              AND c.TABLE_NAME = :table_name
              AND c.CONSTRAINT_TYPE = 'P'
            ORDER BY cc.POSITION
        """
        with conn.cursor() as cur:
            cur.execute(sql, owner=owner, table_name=table)
            return {row[0] for row in cur.fetchall()}

    @staticmethod
    def _pk_index_names(
        conn: Any, table: str, owner: str
    ) -> set[str]:
        sql = """
            SELECT INDEX_NAME
            FROM ALL_CONSTRAINTS
            WHERE OWNER = :owner
              AND TABLE_NAME = :table_name
              AND CONSTRAINT_TYPE = 'P'
        """
        with conn.cursor() as cur:
            cur.execute(sql, owner=owner, table_name=table)
            return {row[0] for row in cur.fetchall()}
