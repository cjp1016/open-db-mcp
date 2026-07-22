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
