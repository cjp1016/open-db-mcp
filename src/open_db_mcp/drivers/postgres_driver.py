"""PostgreSQL 驱动适配：psycopg2 纯 Python 驱动。

兼容 PostgreSQL 9.6+、海量数据库（Vastbase）、openGauss 等国产 PG 系数据库。
"""

from __future__ import annotations

import re
from typing import Any

try:
    import psycopg2
except ImportError:  # pragma: no cover - psycopg2 未装时由插件加载层处理
    psycopg2 = None  # type: ignore[assignment]

from .base import (
    ColumnInfo,
    DriverAdapter,
    ExplainPlan,
    IndexInfo,
    SchemaInfo,
    TableInfo,
)

# 支持 jdbc:postgresql:// / jdbc:vastbase:// / jdbc:opengauss://
PG_URL_RE = re.compile(
    r"^jdbc:(?:postgresql|vastbase|opengauss)://([^:/]+):(\d+)(?:/([^?]+))?(?:\?(.+))?$"
)


class PostgresDriver(DriverAdapter):
    """PostgreSQL 驱动（psycopg2 实现）。

    兼容 PG 9.6+、海量数据库（Vastbase）、openGauss，
    元数据走 ``information_schema`` 与 ``pg_catalog``。
    """

    name = "postgres"
    dbapi = psycopg2

    def parse_url(self, url: str) -> dict[str, Any]:
        m = PG_URL_RE.match(url)
        if not m:
            raise ValueError(f"PostgreSQL/Vastbase/openGauss JDBC URL 不被支持: {url}")
        host, port, database, query_str = m.groups()
        result: dict[str, Any] = {
            "host": host,
            "port": int(port),
            "database": database or "",
        }
        if query_str:
            from urllib.parse import parse_qs
            params = parse_qs(query_str, keep_blank_values=True)
            if "user" in params:
                result["user"] = params["user"][0]
            if "password" in params:
                result["password"] = params["password"][0]
            if "sslmode" in params:
                result["sslmode"] = params["sslmode"][0]
            if "applicationName" in params:
                result["application_name"] = params["applicationName"][0]
            # currentSchema → search_path（Vastbase / PG 通用）
            if "currentSchema" in params:
                result["options"] = f"-c search_path={params['currentSchema'][0]}"
        return result

    def connect(self, conf):
        u = self.parse_url(conf.url)
        kwargs: dict[str, Any] = {
            "host": u["host"],
            "port": u["port"],
            "dbname": u["database"],
            "user": conf.user,
            "password": conf.password,
        }
        if "sslmode" in u:
            kwargs["sslmode"] = u["sslmode"]
        if "application_name" in u:
            kwargs["application_name"] = u["application_name"]
        if "options" in u:
            kwargs["options"] = u["options"]
        conn = psycopg2.connect(**kwargs)
        # 与其他驱动一致：默认 autocommit=False，由 open-db-mcp 显式管理事务
        conn.autocommit = False
        return conn

    def quote_ident(self, name: str) -> str:
        return f'"{name}"'

    def is_select(self, sql: str) -> bool:
        head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        return head in ("SELECT", "WITH", "VALUES", "TABLE")

    def ping_sql(self) -> str:
        return "SELECT 1"

    # ------------------------------------------------------------------
    # 元数据查询
    # ------------------------------------------------------------------

    def describe_table(
        self, conn: Any, table: str, schema: str | None = None
    ) -> list[ColumnInfo]:
        table_schema = schema or self._current_schema(conn)
        pk_cols = self._primary_key_columns(conn, table, table_schema)
        sql = """
            SELECT
                c.column_name,
                c.data_type,
                c.is_nullable,
                c.column_default,
                c.character_maximum_length,
                c.numeric_precision,
                c.numeric_scale,
                pgd.description AS column_comment
            FROM information_schema.columns c
            LEFT JOIN pg_catalog.pg_statio_all_tables st
              ON st.schemaname = c.table_schema
             AND st.relname = c.table_name
            LEFT JOIN pg_catalog.pg_description pgd
              ON pgd.objoid = st.relid
             AND pgd.objsubid = c.ordinal_position
            WHERE c.table_schema = %s
              AND c.table_name = %s
            ORDER BY c.ordinal_position
        """
        cols: list[ColumnInfo] = []
        with conn.cursor() as cur:
            cur.execute(sql, (table_schema, table))
            for row in cur.fetchall():
                (
                    name, data_type, is_nullable, col_default,
                    char_max_len, num_prec, num_scale, comment,
                ) = row
                cols.append(ColumnInfo(
                    name=name,
                    data_type=data_type,
                    nullable=(is_nullable == "YES"),
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
        conditions = ["table_schema = %s"]
        params: list[Any] = [table_schema]
        if table_type == "BASE TABLE":
            conditions.append("table_type = 'BASE TABLE'")
        elif table_type == "VIEW":
            conditions.append("table_type = 'VIEW'")
        sql = f"""
            SELECT table_name, table_type
            FROM information_schema.tables
            WHERE {' AND '.join(conditions)}
            ORDER BY table_name
        """
        # 注释与行数估算走 pg_catalog（information_schema.tables 没这俩）
        rel_stats = self._relation_stats(conn, table_schema)
        tables: list[TableInfo] = []
        with conn.cursor() as cur:
            cur.execute(sql, params)
            for name, ttype in cur.fetchall():
                stat = rel_stats.get(name, {})
                tables.append(TableInfo(
                    name=name,
                    type=ttype,
                    schema=table_schema,
                    rows=stat.get("rows"),
                    comment=stat.get("comment"),
                ))
        return tables

    def list_schemas(self, conn: Any) -> list[SchemaInfo]:
        default = self._current_schema(conn)
        schemas: list[SchemaInfo] = []
        with conn.cursor() as cur:
            cur.execute(
                "SELECT schema_name "
                "FROM information_schema.schemata "
                "WHERE schema_name NOT LIKE 'pg_%' "
                "  AND schema_name NOT IN ('information_schema') "
                "ORDER BY schema_name"
            )
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
        # 使用 generate_subscripts 替代 array_position，兼容 openGauss 旧版本
        sql = """
            SELECT
                i.relname AS index_name,
                a.attname AS column_name,
                idx.indisunique AS is_unique,
                idx.indisprimary AS is_primary,
                s.ord AS column_position
            FROM pg_index idx
            JOIN pg_class t ON t.oid = idx.indrelid
            JOIN pg_class i ON i.oid = idx.indexrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN LATERAL generate_subscripts(idx.indkey, 1) AS s(ord) ON TRUE
            JOIN pg_attribute a
              ON a.attrelid = t.oid
             AND a.attnum = idx.indkey[s.ord]
            WHERE t.relname = %s
              AND n.nspname = %s
            ORDER BY i.relname, s.ord
        """
        index_map: dict[str, IndexInfo] = {}
        with conn.cursor() as cur:
            cur.execute(sql, (table, table_schema))
            for idx_name, col_name, is_unique, is_primary, _pos in cur.fetchall():
                if idx_name not in index_map:
                    index_map[idx_name] = IndexInfo(
                        name=idx_name,
                        is_unique=bool(is_unique),
                        is_primary=bool(is_primary),
                    )
                index_map[idx_name].columns.append(col_name)
        return list(index_map.values())

    def explain_query(
        self, conn: Any, sql: str, params: list | tuple | None = None
    ) -> ExplainPlan:
        plan = ExplainPlan()
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN {sql}", params or ())
            for row in cur.fetchall():
                plan.raw_rows.append({"plan": row[0]})
        return plan

    # ------------------------------------------------------------------
    # 慢查询日志
    # ------------------------------------------------------------------

    def fetch_slow_queries(
        self, conn: Any, limit: int = 50, threshold_sec: float = 1.0
    ) -> list[dict[str, Any]]:
        """从 pg_stat_statements 获取慢查询（需安装扩展）。"""
        sql = """
            SELECT
                query,
                calls,
                ROUND(mean_exec_time::numeric, 1) AS avg_ms,
                ROUND(max_exec_time::numeric, 1) AS max_ms,
                ROUND(total_exec_time::numeric, 1) AS total_ms,
                ROUND(rows::numeric / NULLIF(calls, 0), 0) AS avg_rows
            FROM pg_stat_statements
            WHERE mean_exec_time > %s * 1000
            ORDER BY mean_exec_time DESC
            LIMIT %s
        """
        results: list[dict[str, Any]] = []
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (threshold_sec, limit))
                for row in cur.fetchall():
                    query, calls, avg_ms, max_ms, total_ms, avg_rows = row
                    results.append({
                        "sql": (query or "")[:2000],
                        "duration_ms": int(avg_ms or 0),
                        "max_ms": int(max_ms or 0),
                        "exec_count": int(calls or 0),
                        "total_ms": int(total_ms or 0),
                        "avg_rows": int(avg_rows or 0),
                        "source": "database",
                    })
        except Exception:
            pass
        return results

    # ------------------------------------------------------------------
    # DBA 功能：锁管理 + 表空间管理
    # ------------------------------------------------------------------

    def list_locks(self, conn: Any) -> list[dict[str, Any]]:
        """查询当前锁/阻塞信息（pg_locks + pg_stat_activity）。"""
        sql = """
            SELECT
                blocked.pid AS session_id,
                blocked.usename AS username,
                blocked.state AS status,
                blocking.pid AS blocking_session,
                EXTRACT(EPOCH FROM (now() - blocked.query_start))::int AS wait_time_sec,
                blocked.query AS sql_text,
                blocked_lock.locktype AS lock_type,
                blocked_lock.relation::regclass::text AS table_name
            FROM pg_stat_activity blocked
            JOIN pg_locks blocked_lock ON blocked.pid = blocked_lock.pid
            LEFT JOIN pg_locks blocking_lock
              ON blocked_lock.locktype = blocking_lock.locktype
             AND blocked_lock.relation = blocking_lock.relation
             AND blocked_lock.pid != blocking_lock.pid
             AND blocking_lock.granted
            LEFT JOIN pg_stat_activity blocking ON blocking_lock.pid = blocking.pid
            WHERE NOT blocked_lock.granted
              AND blocked.pid != pg_backend_pid()
            ORDER BY wait_time_sec DESC NULLS LAST
        """
        results: list[dict[str, Any]] = []
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                for row in cur.fetchall():
                    pid, username, status, blocking_pid, wait_sec, sql_text, lock_type, table_name = row
                    results.append({
                        "session_id": str(pid),
                        "serial": None,
                        "username": username or "",
                        "status": status or "",
                        "blocking_session": str(blocking_pid) if blocking_pid else None,
                        "wait_time_sec": int(wait_sec) if wait_sec else 0,
                        "lock_type": lock_type or "",
                        "table_name": table_name or "",
                        "sql_text": (sql_text or "")[:500],
                    })
        except Exception:
            # 回退到简单查询
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT pid, usename, state, query_start, query
                        FROM pg_stat_activity
                        WHERE state != 'idle'
                          AND pid != pg_backend_pid()
                        ORDER BY query_start
                    """)
                    for row in cur.fetchall():
                        pid, username, status, _start, sql_text = row
                        results.append({
                            "session_id": str(pid),
                            "serial": None,
                            "username": username or "",
                            "status": status or "",
                            "blocking_session": None,
                            "wait_time_sec": 0,
                            "lock_type": "",
                            "sql_text": (sql_text or "")[:500],
                        })
            except Exception:
                pass
        return results

    def kill_session(
        self, conn: Any, session_id: str, serial: str | None = None
    ) -> dict[str, Any]:
        """终止 PostgreSQL 会话：pg_terminate_backend(pid)。"""
        sql = f"SELECT pg_terminate_backend({session_id})"
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
                success = row[0] if row else False
            return {
                "success": bool(success),
                "message": f"已终止会话 {session_id}" if success else f"无法终止会话 {session_id}",
                "sql": sql,
            }
        except Exception as exc:
            return {"success": False, "message": str(exc), "sql": sql}

    def list_tablespaces(self, conn: Any) -> list[dict[str, Any]]:
        """查询表空间使用情况（pg_tablespace + pg_database_size）。"""
        sql = """
            SELECT
                spcname AS name,
                COALESCE(pg_tablespace_location(oid), 'default') AS file_path,
                ROUND(pg_tablespace_size(oid) / 1024.0 / 1024.0, 2) AS total_mb
            FROM pg_tablespace
            ORDER BY pg_tablespace_size(oid) DESC
        """
        results: list[dict[str, Any]] = []
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                for row in cur.fetchall():
                    name, file_path, total_mb = row
                    results.append({
                        "name": name,
                        "file_path": file_path or "",
                        "total_mb": float(total_mb) if total_mb else 0,
                        "used_mb": float(total_mb) if total_mb else 0,
                        "free_mb": 0,
                        "used_pct": 100.0,
                        "autoextend": True,
                        "max_size_mb": 0,
                    })
        except Exception:
            pass
        # 添加数据库级别统计
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        datname,
                        ROUND(pg_database_size(datname) / 1024.0 / 1024.0, 2) AS size_mb
                    FROM pg_database
                    WHERE datistemplate = false
                    ORDER BY pg_database_size(datname) DESC
                """)
                for row in cur.fetchall():
                    db_name, size_mb = row
                    results.append({
                        "name": f"db:{db_name}",
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
        """PostgreSQL 表空间自动管理，不支持手动扩容。"""
        return {
            "success": False,
            "message": (
                "PostgreSQL 表空间由文件系统自动管理，不支持手动 RESIZE。"
                "建议：1) 清理无用数据 2) 执行 VACUUM FULL 3) 扩展磁盘空间。"
            ),
            "sql": "",
        }

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _current_schema(conn: Any) -> str:
        with conn.cursor() as cur:
            cur.execute("SELECT current_schema()")
            row = cur.fetchone()
            return row[0] if row and row[0] else "public"

    @staticmethod
    def _primary_key_columns(
        conn: Any, table: str, schema: str
    ) -> set[str]:
        sql = """
            SELECT a.attname
            FROM pg_index idx
            JOIN pg_class t ON t.oid = idx.indrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(idx.indkey)
            WHERE t.relname = %s
              AND n.nspname = %s
              AND idx.indisprimary
        """
        with conn.cursor() as cur:
            cur.execute(sql, (table, schema))
            return {row[0] for row in cur.fetchall()}

    @staticmethod
    def _relation_stats(conn: Any, schema: str) -> dict[str, dict[str, Any]]:
        """一次性拉取 schema 内所有表的行数估算 + 注释。"""
        sql = """
            SELECT
                c.relname,
                c.reltuples::bigint AS rows,
                d.description
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_description d
              ON d.objoid = c.oid AND d.objsubid = 0
            WHERE n.nspname = %s
              AND c.relkind IN ('r', 'p', 'v', 'm')
        """
        stats: dict[str, dict[str, Any]] = {}
        with conn.cursor() as cur:
            cur.execute(sql, (schema,))
            for name, rows, comment in cur.fetchall():
                stats[name] = {
                    "rows": int(rows) if rows is not None and rows >= 0 else None,
                    "comment": comment or None,
                }
        return stats
