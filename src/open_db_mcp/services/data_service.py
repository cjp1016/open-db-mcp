"""数据导入/导出服务层。

提供表数据的 CSV / JSON 格式导入导出功能：
- 导出：从数据库读取数据并格式化为 CSV 或 JSON
- 导入：从 CSV 数据批量插入到目标表

安全约束：
- 导出受白名单 read 层控制（通过 registry）
- 导入受白名单 write 层控制（通过 sql_validator / whitelist）
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict
from typing import Any

from ..config import Settings
from ..errors import DataImportError
from ..registry import DataSourceRegistry
from ..safety.sql_analyzer import analyze
from ..safety.sql_validator import validate_sql
from .query_service import _params_tuple, _serialize


class DataService:
    """数据导入导出服务。"""

    def __init__(
        self,
        registry: DataSourceRegistry,
        settings: Settings,
    ) -> None:
        self._registry = registry
        self._settings = settings

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------

    def export_table(
        self,
        table: str,
        format: str = "csv",
        data_source: str | None = None,
        schema: str | None = None,
        where: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """导出表数据为指定格式（csv / json）。

        Args:
            table: 表名。
            format: 输出格式，'csv' 或 'json'。
            data_source: 数据源名称。
            schema: schema 名。
            where: 可选的 WHERE 条件（不含 WHERE 关键字）。
            limit: 可选的导出行数限制。
        """
        fmt = format.lower()
        if fmt == "csv":
            return self.export_csv(table, data_source, schema, where, limit)
        if fmt == "json":
            return self.export_json(table, data_source, schema, where, limit)
        raise ValueError(f"不支持的导出格式: {format!r}，仅支持 'csv' / 'json'")

    def export_csv(
        self,
        table: str,
        data_source: str | None = None,
        schema: str | None = None,
        where: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """导出表数据为 CSV 格式字符串。

        Args:
            table: 表名。
            data_source: JNDI 名称，不传则使用当前活跃数据源。
            schema: schema 名。
            where: 可选的 WHERE 条件（不含 WHERE 关键字）。
            limit: 可选的导出行数限制。

        Returns:
            包含 csv_content, columns, row_count 的 dict。
        """
        data_source = self._registry.resolve(data_source)
        driver = self._registry.driver(data_source)

        select_sql = self._build_select_sql(table, schema, where, driver)
        intent = analyze(select_sql)
        if not intent.is_readonly:
            raise ValueError("export_csv 仅支持只读查询")

        conf = self._registry.conf(data_source)
        validation = validate_sql(select_sql, dialect=conf.kind)
        if not validation.is_safe:
            raise ValueError(
                f"SQL 安全校验失败: {'; '.join(validation.violations)}"
            )

        pool = self._registry.get(data_source)
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                params: tuple = ()
                sql = select_sql
                if limit is not None:
                    if driver.name in ("oracle", "dm"):
                        sql = f"SELECT * FROM ({sql}) WHERE ROWNUM <= :1"
                    elif driver.name == "sqlite":
                        sql = f"{sql} LIMIT ?"
                    else:
                        sql = f"{sql} LIMIT %s"
                    params = (limit,)
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchall()

                csv_buf = io.StringIO()
                writer = csv.writer(csv_buf)
                writer.writerow(cols)
                for row in rows:
                    writer.writerow(_serialize(row))

                return {
                    "table": table,
                    "schema": schema,
                    "data_source": data_source,
                    "columns": cols,
                    "row_count": len(rows),
                    "csv_content": csv_buf.getvalue(),
                }
        finally:
            conn.close()

    def export_json(
        self,
        table: str,
        data_source: str | None = None,
        schema: str | None = None,
        where: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """导出表数据为 JSON 格式字符串。

        参数含义同 export_csv。
        """
        data_source = self._registry.resolve(data_source)
        driver = self._registry.driver(data_source)

        select_sql = self._build_select_sql(table, schema, where, driver)
        intent = analyze(select_sql)
        if not intent.is_readonly:
            raise ValueError("export_json 仅支持只读查询")

        conf = self._registry.conf(data_source)
        validation = validate_sql(select_sql, dialect=conf.kind)
        if not validation.is_safe:
            raise ValueError(
                f"SQL 安全校验失败: {'; '.join(validation.violations)}"
            )

        pool = self._registry.get(data_source)
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                params: tuple = ()
                sql = select_sql
                if limit is not None:
                    if driver.name in ("oracle", "dm"):
                        sql = f"SELECT * FROM ({sql}) WHERE ROWNUM <= :1"
                    elif driver.name == "sqlite":
                        sql = f"{sql} LIMIT ?"
                    else:
                        sql = f"{sql} LIMIT %s"
                    params = (limit,)
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchall()

                records = [
                    dict(zip(cols, _serialize(row)))
                    for row in rows
                ]
                return {
                    "table": table,
                    "schema": schema,
                    "data_source": data_source,
                    "columns": cols,
                    "row_count": len(rows),
                    "json_content": json.dumps(
                        records, ensure_ascii=False, default=str
                    ),
                }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 导入
    # ------------------------------------------------------------------

    def import_csv(
        self,
        table: str,
        csv_content: str,
        data_source: str | None = None,
        schema: str | None = None,
        batch_size: int = 500,
    ) -> dict[str, Any]:
        """从 CSV 字符串导入数据到指定表。

        CSV 第一行必须是表头，列名需与表列名匹配（允许子集）。

        Args:
            table: 目标表名。
            csv_content: CSV 格式字符串，首行是列名。
            data_source: JNDI 名称，不传则使用当前活跃数据源。
            schema: schema 名。
            batch_size: 批量插入大小，默认 500。

        Returns:
            包含 inserted_count, columns, batch_count 的 dict。
        """
        data_source = self._registry.resolve(data_source)
        driver = self._registry.driver(data_source)

        # 解析 CSV
        rows, columns = self._parse_csv(csv_content)
        if not columns:
            raise DataImportError("CSV 内容为空或缺少表头")
        if not rows:
            return {
                "table": table,
                "schema": schema,
                "data_source": data_source,
                "columns": columns,
                "inserted_count": 0,
                "batch_count": 0,
            }

        # 校验表存在性并取列信息
        pool = self._registry.get(data_source)
        conn = pool.connection()
        try:
            col_infos = driver.describe_table(conn, table=table, schema=schema)
            db_cols = {c.name for c in col_infos}
            for col in columns:
                if col not in db_cols:
                    raise DataImportError(
                        f"CSV 列 '{col}' 在表 '{table}' 中不存在"
                    )

            # 构建 INSERT SQL
            quoted_cols = [driver.quote_ident(c) for c in columns]
            quoted_table = driver.quote_ident(table)
            if schema:
                quoted_table = f"{driver.quote_ident(schema)}.{quoted_table}"
            placeholders = self._placeholders(driver.name, len(columns))
            insert_sql = (
                f"INSERT INTO {quoted_table} "
                f"({', '.join(quoted_cols)}) VALUES ({placeholders})"
            )

            # 校验 INSERT 语句（白名单 write 层）
            conf = self._registry.conf(data_source)
            validation = validate_sql(insert_sql, dialect=conf.kind)
            if not validation.is_safe:
                raise ValueError(
                    f"SQL 安全校验失败: {'; '.join(validation.violations)}"
                )

            # 批量插入
            total = 0
            batch_count = 0
            for batch in _chunked(rows, batch_size):
                with conn.cursor() as cur:
                    cur.executemany(insert_sql, batch)
                    total += len(batch)
                    batch_count += 1
            conn.commit()
            return {
                "table": table,
                "schema": schema,
                "data_source": data_source,
                "columns": columns,
                "inserted_count": total,
                "batch_count": batch_count,
            }
        except DataImportError:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            raise DataImportError(f"导入失败: {e}") from e
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _build_select_sql(
        table: str,
        schema: str | None,
        where: str | None,
        driver: Any,
    ) -> str:
        """构造 SELECT 查询。"""
        quoted_table = driver.quote_ident(table)
        if schema:
            quoted_table = f"{driver.quote_ident(schema)}.{quoted_table}"
        sql = f"SELECT * FROM {quoted_table}"
        if where:
            sql += f" WHERE {where}"
        return sql

    @staticmethod
    def _parse_csv(csv_content: str) -> tuple[list[list[Any]], list[str]]:
        """解析 CSV，返回 (数据行, 列名列表)。"""
        buf = io.StringIO(csv_content)
        reader = csv.reader(buf)
        try:
            columns = next(reader)
        except StopIteration:
            return [], []
        rows = [row for row in reader if any(cell.strip() for cell in row)]
        return rows, columns

    @staticmethod
    def _placeholders(driver_name: str, count: int) -> str:
        """根据驱动生成占位符字符串。"""
        if driver_name in ("oracle", "dm"):
            return ", ".join(f":{i+1}" for i in range(count))
        if driver_name == "sqlite":
            return ", ".join(["?"] * count)
        return ", ".join(["%s"] * count)


def _chunked(seq: list, size: int):
    """按 size 分块。"""
    for i in range(0, len(seq), size):
        yield seq[i:i + size]
