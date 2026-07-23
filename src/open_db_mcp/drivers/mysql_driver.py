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
    # DBA 功能：锁管理 + 表空间管理
    # ------------------------------------------------------------------

    def list_locks(self, conn: Any) -> list[dict[str, Any]]:
        """查询当前锁/阻塞信息（information_schema + PROCESSLIST）。"""
        # MySQL 8.0+ 使用 performance_schema.data_locks
        # 兼容 MySQL 5.7 使用 information_schema.INNODB_LOCKS
        sql = """
            SELECT
                p.ID AS session_id,
                p.USER AS username,
                p.STATE AS status,
                p.TIME AS wait_time_sec,
                p.INFO AS sql_text,
                COALESCE(r.trx_mysql_thread_id, 0) AS blocking_session
            FROM information_schema.PROCESSLIST p
            LEFT JOIN information_schema.INNODB_TRX t ON p.ID = t.trx_mysql_thread_id
            LEFT JOIN information_schema.INNODB_LOCK_WAITS w ON t.trx_id = w.requesting_trx_id
            LEFT JOIN information_schema.INNODB_TRX r ON w.blocking_trx_id = r.trx_id
            WHERE p.COMMAND != 'Sleep'
              AND p.INFO IS NOT NULL
            ORDER BY p.TIME DESC
        """
        results: list[dict[str, Any]] = []
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                for row in cur.fetchall():
                    sess_id, username, status, wait_sec, sql_text, blocking = row
                    results.append({
                        "session_id": str(sess_id),
                        "serial": None,
                        "username": username or "",
                        "status": status or "",
                        "blocking_session": str(blocking) if blocking else None,
                        "wait_time_sec": int(wait_sec) if wait_sec else 0,
                        "lock_type": "InnoDB",
                        "sql_text": (sql_text or "")[:500],
                    })
        except Exception:
            # 回退到简单查询
            try:
                with conn.cursor() as cur:
                    cur.execute("SHOW PROCESSLIST")
                    for row in cur.fetchall():
                        sess_id, username, _host, _db, command, wait_sec, status, sql_text = row[:8]
                        if command != "Sleep" and sql_text:
                            results.append({
                                "session_id": str(sess_id),
                                "serial": None,
                                "username": username or "",
                                "status": status or "",
                                "blocking_session": None,
                                "wait_time_sec": int(wait_sec) if wait_sec else 0,
                                "lock_type": "",
                                "sql_text": (sql_text or "")[:500],
                            })
            except Exception:
                pass
        return results

    def kill_session(
        self, conn: Any, session_id: str, serial: str | None = None
    ) -> dict[str, Any]:
        """终止 MySQL 会话：KILL CONNECTION id。"""
        sql = f"KILL CONNECTION {session_id}"
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
            return {"success": True, "message": f"已终止会话 {session_id}", "sql": sql}
        except Exception as exc:
            return {"success": False, "message": str(exc), "sql": sql}

    def list_tablespaces(self, conn: Any) -> list[dict[str, Any]]:
        """查询表空间使用情况（information_schema.FILES，MySQL 8.0+）。"""
        sql = """
            SELECT
                TABLESPACE_NAME,
                FILE_NAME,
                ROUND(TOTAL_EXTENTS * EXTENT_SIZE / 1024 / 1024, 2) AS TOTAL_MB,
                ROUND((TOTAL_EXTENTS - FREE_EXTENTS) * EXTENT_SIZE / 1024 / 1024, 2) AS USED_MB,
                ROUND(FREE_EXTENTS * EXTENT_SIZE / 1024 / 1024, 2) AS FREE_MB,
                ROUND((TOTAL_EXTENTS - FREE_EXTENTS) / NULLIF(TOTAL_EXTENTS, 0) * 100, 1) AS USED_PCT
            FROM information_schema.FILES
            WHERE FILE_TYPE = 'TABLESPACE'
            ORDER BY USED_PCT DESC
        """
        results: list[dict[str, Any]] = []
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                for row in cur.fetchall():
                    name, file_path, total, used, free, pct = row
                    results.append({
                        "name": name or "",
                        "file_path": file_path or "",
                        "total_mb": float(total) if total else 0,
                        "used_mb": float(used) if used else 0,
                        "free_mb": float(free) if free else 0,
                        "used_pct": float(pct) if pct else 0,
                        "autoextend": True,
                        "max_size_mb": 0,
                    })
        except Exception:
            # 回退到数据库级别统计
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT
                            TABLE_SCHEMA,
                            ROUND(SUM(DATA_LENGTH + INDEX_LENGTH) / 1024 / 1024, 2) AS SIZE_MB
                        FROM information_schema.TABLES
                        GROUP BY TABLE_SCHEMA
                        ORDER BY SIZE_MB DESC
                    """)
                    for row in cur.fetchall():
                        schema, size_mb = row
                        results.append({
                            "name": schema,
                            "file_path": "",
                            "total_mb": float(size_mb) if size_mb else 0,
                            "used_mb": float(size_mb) if size_mb else 0,
                            "free_mb": 0,
                            "used_pct": 100.0,
                            "autoextend": True,
                            "max_size_mb": 0,
                        })
            except Exception:
                pass
        return results

    def resize_tablespace(
        self, conn: Any, file_path: str, new_size_mb: int
    ) -> dict[str, Any]:
        """MySQL 表空间自动管理，不支持手动扩容。"""
        return {
            "success": False,
            "message": (
                "MySQL InnoDB 表空间自动管理，不支持手动 RESIZE。"
                "建议使用 ALTER TABLE ... ENGINE=InnoDB 重建表"
                "或调整 innodb_data_file_path 配置。"
            ),
            "sql": "",
        }

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
