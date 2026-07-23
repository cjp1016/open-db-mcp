"""MySQL 驱动适配：pymysql 纯 Python 驱动。"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs

import pymysql

from .base import (
    ColumnInfo,
    DriverAdapter,
    ExplainPlan,
    IndexInfo,
    SchemaInfo,
    TableInfo,
)

# jdbc:mysql://host:port/database?param1=val1&param2=val2
MYSQL_URL_RE = re.compile(
    r"^jdbc:mysql://([^:/]+):(\d+)(?:/([^?]+))?(?:\?(.+))?$"
)


class MysqlDriver(DriverAdapter):
    name = "mysql"
    dbapi = pymysql

    def parse_url(self, url: str) -> dict[str, Any]:
        m = MYSQL_URL_RE.match(url)
        if not m:
            raise ValueError(f"MySQL JDBC URL 不被支持: {url}")
        host, port, database, query_str = m.groups()
        result: dict[str, Any] = {
            "host": host,
            "port": int(port),
            "database": database or "",
        }
        if query_str:
            params = parse_qs(query_str, keep_blank_values=True)
            # 提取常用参数
            if "useSSL" in params:
                result["use_ssl"] = params["useSSL"][0].lower() == "true"
            if "characterEncoding" in params:
                result["charset"] = params["characterEncoding"][0]
            if "serverTimezone" in params:
                result["server_timezone"] = params["serverTimezone"][0]
        return result

    def connect(self, conf):
        u = self.parse_url(conf.url)
        kwargs: dict[str, Any] = {
            "host": u["host"],
            "port": u["port"],
            "user": conf.user,
            "password": conf.password,
            "database": u["database"],
            "charset": u.get("charset", "utf8mb4"),
            "cursorclass": pymysql.cursors.Cursor,
        }
        # pymysql 默认 autocommit=True，与 Oracle/DM 行为对齐
        return pymysql.connect(**kwargs)

    def quote_ident(self, name: str) -> str:
        return f"`{name}`"

    def is_select(self, sql: str) -> bool:
        head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        return head in ("SELECT", "WITH")

    def ping_sql(self) -> str:
        return "SELECT 1"

    # ------------------------------------------------------------------
    # 元数据查询
    # ------------------------------------------------------------------

    def describe_table(
        self, conn: Any, table: str, schema: str | None = None
    ) -> list[ColumnInfo]:
        cols: list[ColumnInfo] = []
        table_schema = schema or self._current_schema(conn)
        sql = """
            SELECT
                c.COLUMN_NAME,
                c.DATA_TYPE,
                c.IS_NULLABLE,
                c.COLUMN_DEFAULT,
                c.CHARACTER_MAXIMUM_LENGTH,
                c.NUMERIC_PRECISION,
                c.NUMERIC_SCALE,
                c.COLUMN_COMMENT
            FROM information_schema.COLUMNS c
            WHERE c.TABLE_SCHEMA = %s
              AND c.TABLE_NAME = %s
            ORDER BY c.ORDINAL_POSITION
        """
        pk_cols = self._primary_key_columns(conn, table, table_schema)
        with conn.cursor() as cur:
            cur.execute(sql, (table_schema, table))
            for row in cur.fetchall():
                name, data_type, is_nullable, col_default, char_max_len, num_prec, num_scale, comment = row
                cols.append(ColumnInfo(
                    name=name,
                    data_type=data_type,
                    nullable=is_nullable == "YES",
                    default=str(col_default) if col_default is not None else None,
                    is_primary_key=name in pk_cols,
                    comment=comment or None,
                    character_maximum_length=int(char_max_len) if char_max_len is not None else None,
                    numeric_precision=int(num_prec) if num_prec is not None else None,
                    numeric_scale=int(num_scale) if num_scale is not None else None,
                ))
        return cols

    def list_tables(
        self,
        conn: Any,
        schema: str | None = None,
        table_type: str | None = None,
    ) -> list[TableInfo]:
        table_schema = schema or self._current_schema(conn)
        sql = """
            SELECT TABLE_NAME, TABLE_TYPE, TABLE_ROWS, TABLE_COMMENT
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s
        """
        params: list[Any] = [table_schema]
        if table_type:
            sql += " AND TABLE_TYPE = %s"
            params.append(table_type)
        sql += " ORDER BY TABLE_NAME"
        tables: list[TableInfo] = []
        with conn.cursor() as cur:
            cur.execute(sql, params)
            for row in cur.fetchall():
                name, ttype, rows_count, comment = row
                tables.append(TableInfo(
                    name=name,
                    type=ttype,
                    schema=table_schema,
                    rows=int(rows_count) if rows_count is not None else None,
                    comment=comment or None,
                ))
        return tables

    def list_schemas(self, conn: Any) -> list[SchemaInfo]:
        default = self._current_schema(conn)
        schemas: list[SchemaInfo] = []
        with conn.cursor() as cur:
            cur.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA ORDER BY SCHEMA_NAME")
            for row in cur.fetchall():
                name = row[0]
                schemas.append(SchemaInfo(
                    name=name,
                    is_default=(name == default),
                ))
        return schemas

    def list_indexes(
        self, conn: Any, table: str, schema: str | None = None
    ) -> list[IndexInfo]:
        table_schema = schema or self._current_schema(conn)
        sql = """
            SELECT
                INDEX_NAME,
                COLUMN_NAME,
                NON_UNIQUE,
                SEQ_IN_INDEX
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = %s
            ORDER BY INDEX_NAME, SEQ_IN_INDEX
        """
        index_map: dict[str, IndexInfo] = {}
        with conn.cursor() as cur:
            cur.execute(sql, (table_schema, table))
            for row in cur.fetchall():
                idx_name, col_name, non_unique, _seq = row
                if idx_name not in index_map:
                    index_map[idx_name] = IndexInfo(
                        name=idx_name,
                        is_unique=(non_unique == 0),
                        is_primary=(idx_name == "PRIMARY"),
                    )
                index_map[idx_name].columns.append(col_name)
        return list(index_map.values())

    def explain_query(
        self, conn: Any, sql: str, params: list | tuple | None = None
    ) -> ExplainPlan:
        explain_sql = f"EXPLAIN {sql}"
        plan = ExplainPlan()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(explain_sql, params or ())
            rows = cur.fetchall()
            total_rows = 0
            for row in rows:
                row_dict = dict(row)
                plan.raw_rows.append(row_dict)
                rows_est = row_dict.get("rows")
                if rows_est is not None:
                    total_rows += int(rows_est)
            plan.estimated_rows = total_rows if rows else None
        return plan

    # ------------------------------------------------------------------
    # 慢查询日志
    # ------------------------------------------------------------------

    def fetch_slow_queries(
        self, conn: Any, limit: int = 50, threshold_sec: float = 1.0
    ) -> list[dict[str, Any]]:
        """从 performance_schema 获取慢查询摘要。"""
        sql = """
            SELECT
                DIGEST_TEXT AS sql_text,
                COUNT_STAR AS exec_count,
                ROUND(AVG_TIMER_WAIT / 1000000000, 1) AS avg_ms,
                ROUND(MAX_TIMER_WAIT / 1000000000, 1) AS max_ms,
                ROUND(SUM_TIMER_WAIT / 1000000000, 1) AS total_ms,
                FIRST_SEEN,
                LAST_SEEN,
                SCHEMA_NAME
            FROM performance_schema.events_statements_summary_by_digest
            WHERE AVG_TIMER_WAIT > %s * 1000000000000
              AND DIGEST_TEXT IS NOT NULL
              AND SCHEMA_NAME IS NOT NULL
            ORDER BY AVG_TIMER_WAIT DESC
            LIMIT %s
        """
        results: list[dict[str, Any]] = []
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (threshold_sec, limit))
                cols = [d[0] for d in cur.description] if cur.description else []
                for row in cur.fetchall():
                    record = dict(zip(cols, row))
                    results.append({
                        "sql": record.get("sql_text", "")[:2000],
                        "duration_ms": int(record.get("avg_ms", 0)),
                        "max_ms": int(record.get("max_ms", 0)),
                        "exec_count": int(record.get("exec_count", 0)),
                        "schema": record.get("SCHEMA_NAME"),
                        "first_seen": str(record.get("FIRST_SEEN", "")),
                        "last_seen": str(record.get("LAST_SEEN", "")),
                        "source": "database",
                    })
        except Exception:
            pass
        return results

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _current_schema(conn: Any) -> str:
        with conn.cursor() as cur:
            cur.execute("SELECT DATABASE()")
            row = cur.fetchone()
            return row[0] if row and row[0] else ""

    @staticmethod
    def _primary_key_columns(
        conn: Any, table: str, schema: str
    ) -> set[str]:
        sql = """
            SELECT COLUMN_NAME
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = %s
              AND CONSTRAINT_NAME = 'PRIMARY'
            ORDER BY ORDINAL_POSITION
        """
        with conn.cursor() as cur:
            cur.execute(sql, (schema, table))
            return {row[0] for row in cur.fetchall()}
