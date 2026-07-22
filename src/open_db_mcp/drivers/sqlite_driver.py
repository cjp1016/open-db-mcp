"""SQLite 驱动适配：stdlib sqlite3。"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from .base import (
    ColumnInfo,
    DriverAdapter,
    ExplainPlan,
    IndexInfo,
    SchemaInfo,
    TableInfo,
)

# jdbc:sqlite:/path/to/file.db
# jdbc:sqlite:file.db
SQLITE_URL_RE = re.compile(r"^jdbc:sqlite:(.+)")


class SqliteDriver(DriverAdapter):
    """SQLite 驱动（stdlib sqlite3 实现）。

    SQLite 没有 schema / user 概念，所有表默认在 ``main``（数据库）下。
    驱动把 ``main`` 虚拟为默认 schema。
    """

    name = "sqlite"
    dbapi = sqlite3

    def parse_url(self, url: str) -> dict[str, Any]:
        m = SQLITE_URL_RE.match(url)
        if not m:
            raise ValueError(f"SQLite JDBC URL 不被支持: {url}")
        path = m.group(1)
        return {
            "path": path,
            "database": self._extract_db_name(path),
        }

    @staticmethod
    def _extract_db_name(path: str) -> str:
        import os
        base = os.path.basename(path)
        if "." in base:
            return base.rsplit(".", 1)[0]
        return base or "main"

    def connect(self, conf):
        path = self.parse_url(conf.url)["path"]
        # sqlite3 不通过 user/password 认证，但 conf.user / conf.password
        # 可携带在 URL 里作为附加参数（如 PRAGMA key）
        conn = sqlite3.connect(
            path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
        )
        # 与其他驱动一致：默认 autocommit=False
        conn.isolation_level = "DEFERRED"
        # 外键约束默认开启（SQLite 默认关闭）
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def quote_ident(self, name: str) -> str:
        return f'"{name}"'

    def is_select(self, sql: str) -> bool:
        head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        return head in (
            "SELECT", "WITH", "EXPLAIN", "PRAGMA", "VALUES", "TABLE",
        )

    def ping_sql(self) -> str:
        return "SELECT 1"

    # ------------------------------------------------------------------
    # 元数据查询
    # ------------------------------------------------------------------

    def describe_table(
        self, conn: Any, table: str, schema: str | None = None
    ) -> list[ColumnInfo]:
        table_name = table
        pk_cols = self._primary_key_columns(conn, table_name)
        cols: list[ColumnInfo] = []
        cur = conn.cursor()
        try:
            cur.execute(f"PRAGMA table_info(\"{table_name}\")")
            for row in cur.fetchall():
                cid, name, col_type, not_null, dflt_value, pk = row
                cols.append(ColumnInfo(
                    name=name,
                    data_type=str(col_type) if col_type else "TEXT",
                    nullable=(not not_null),
                    default=str(dflt_value) if dflt_value is not None else None,
                    is_primary_key=bool(pk),
                    character_maximum_length=self._extract_char_len(col_type),
                    numeric_precision=None,
                    numeric_scale=None,
                ))
        finally:
            cur.close()
        return cols

    def list_tables(
        self,
        conn: Any,
        schema: str | None = None,
        table_type: str | None = None,
    ) -> list[TableInfo]:
        sql = "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view')"
        params: list[Any] = []
        if table_type == "BASE TABLE":
            sql += " AND type = 'table'"
        elif table_type == "VIEW":
            sql += " AND type = 'view'"
        sql += " ORDER BY name"
        tables: list[TableInfo] = []
        cur = conn.cursor()
        try:
            cur.execute(sql, params)
            for name, ttype in cur.fetchall():
                type_label = "BASE TABLE" if ttype == "table" else "VIEW"
                row_count = self._estimate_rows(conn, name)
                tables.append(TableInfo(
                    name=name,
                    type=type_label,
                    schema="main",
                    rows=row_count,
                    comment=None,
                ))
        finally:
            cur.close()
        return tables

    def list_schemas(self, conn: Any) -> list[SchemaInfo]:
        schemas: list[SchemaInfo] = []
        cur = conn.cursor()
        try:
            cur.execute("PRAGMA database_list")
            for row in cur.fetchall():
                seq, name, file = row
                schemas.append(SchemaInfo(
                    name=name,
                    is_default=(name == "main"),
                ))
        finally:
            cur.close()
        return schemas

    def list_indexes(
        self, conn: Any, table: str, schema: str | None = None
    ) -> list[IndexInfo]:
        index_map: dict[str, IndexInfo] = {}
        pk_cols = self._primary_key_columns(conn, table)
        cur = conn.cursor()
        try:
            cur.execute(f"PRAGMA index_list(\"{table}\")")
            for row in cur.fetchall():
                seq, idx_name, is_unique, origin, partial = row
                cur2 = conn.cursor()
                try:
                    cur2.execute(f"PRAGMA index_info(\"{idx_name}\")")
                    columns = []
                    for col_row in cur2.fetchall():
                        seqno, cid, col_name = col_row
                        columns.append(col_name)
                finally:
                    cur2.close()
                is_primary = (origin == "pk")
                index_map[idx_name] = IndexInfo(
                    name=idx_name,
                    columns=columns,
                    is_unique=bool(is_unique),
                    is_primary=is_primary,
                )
        finally:
            cur.close()
        if pk_cols and not any(idx.is_primary for idx in index_map.values()):
            index_map["pk_" + table] = IndexInfo(
                name="pk_" + table,
                columns=list(pk_cols),
                is_unique=True,
                is_primary=True,
            )
        return list(index_map.values())

    def explain_query(
        self, conn: Any, sql: str, params: list | tuple | None = None
    ) -> ExplainPlan:
        plan = ExplainPlan()
        cur = conn.cursor()
        try:
            cur.execute(f"EXPLAIN QUERY PLAN {sql}", params or ())
            for row in cur.fetchall():
                detail = row[3] if len(row) >= 4 else str(row)
                plan.raw_rows.append({"detail": detail})
        except Exception:
            return plan
        finally:
            cur.close()
        return plan

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _primary_key_columns(conn: Any, table: str) -> set[str]:
        cur = conn.cursor()
        try:
            cur.execute(f"PRAGMA table_info(\"{table}\")")
            return {row[1] for row in cur.fetchall() if row[5]}
        finally:
            cur.close()

    @staticmethod
    def _estimate_rows(conn: Any, table: str) -> int | None:
        """估算表行数（用 sqlite_stat1 或 COUNT(*)）。"""
        cur = conn.cursor()
        try:
            try:
                cur.execute(
                    "SELECT stat FROM sqlite_stat1 WHERE tbl = ?",
                    (table,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    parts = row[0].split()
                    if parts and parts[0].isdigit():
                        return int(parts[0])
            except Exception:
                pass
            try:
                cur.execute(f"SELECT COUNT(*) FROM \"{table}\"")
                return cur.fetchone()[0]
            except Exception:
                return None
        finally:
            cur.close()

    @staticmethod
    def _extract_char_len(data_type: str) -> int | None:
        if not data_type:
            return None
        m = re.search(r"\((\d+)\)", data_type)
        if m:
            return int(m.group(1))
        return None
