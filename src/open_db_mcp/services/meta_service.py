"""元数据浏览服务层。

通过 DriverAdapter 的元数据方法实现，不直接写 SQL。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..config import Settings
from ..drivers.base import ColumnInfo, IndexInfo, TableInfo
from ..registry import DataSourceRegistry
from ..safety.sql_analyzer import analyze
from ..safety.sql_validator import validate_sql


class MetaService:
    """元数据浏览服务。"""

    def __init__(
        self,
        registry: DataSourceRegistry,
        settings: Settings,
    ) -> None:
        self._registry = registry
        self._settings = settings

    def list_schemas(self, data_source: str | None = None) -> dict[str, Any]:
        """列出所有 schema / database。"""
        data_source = self._registry.resolve(data_source)
        driver = self._registry.driver(data_source)
        pool = self._registry.get(data_source)
        conn = pool.connection()
        try:
            schemas = driver.list_schemas(conn)
            return {
                "schemas": [asdict(s) for s in schemas],
                "count": len(schemas),
                "data_source": data_source,
            }
        finally:
            conn.close()

    def list_tables(
        self,
        data_source: str | None = None,
        schema: str | None = None,
        table_type: str | None = None,
    ) -> dict[str, Any]:
        """列出 schema 中的表/视图。"""
        data_source = self._registry.resolve(data_source)
        driver = self._registry.driver(data_source)
        pool = self._registry.get(data_source)
        conn = pool.connection()
        try:
            tables = driver.list_tables(conn, schema=schema, table_type=table_type)
            return {
                "tables": [asdict(t) for t in tables],
                "count": len(tables),
                "schema": schema,
                "data_source": data_source,
            }
        finally:
            conn.close()

    def list_views(
        self,
        data_source: str | None = None,
        schema: str | None = None,
    ) -> dict[str, Any]:
        """列出视图。"""
        return self.list_tables(data_source=data_source, schema=schema, table_type="VIEW")

    def list_indexes(
        self,
        table: str,
        data_source: str | None = None,
        schema: str | None = None,
    ) -> dict[str, Any]:
        """列出指定表的索引。"""
        data_source = self._registry.resolve(data_source)
        driver = self._registry.driver(data_source)
        pool = self._registry.get(data_source)
        conn = pool.connection()
        try:
            indexes = driver.list_indexes(conn, table=table, schema=schema)
            return {
                "table": table,
                "indexes": [asdict(idx) for idx in indexes],
                "count": len(indexes),
                "schema": schema,
                "data_source": data_source,
            }
        finally:
            conn.close()

    def describe_table(
        self,
        table: str,
        data_source: str | None = None,
        schema: str | None = None,
    ) -> dict[str, Any]:
        """返回表的完整结构（列+索引+主键）。"""
        data_source = self._registry.resolve(data_source)
        driver = self._registry.driver(data_source)
        pool = self._registry.get(data_source)
        conn = pool.connection()
        try:
            columns = driver.describe_table(conn, table=table, schema=schema)
            indexes = driver.list_indexes(conn, table=table, schema=schema)
            pk_cols = [
                col for idx in indexes if idx.is_primary
                for col in idx.columns
            ]
            return {
                "table": table,
                "schema": schema,
                "data_source": data_source,
                "columns": [asdict(c) for c in columns],
                "column_count": len(columns),
                "primary_keys": pk_cols,
                "indexes": [asdict(idx) for idx in indexes],
                "index_count": len(indexes),
            }
        finally:
            conn.close()

    def explain_query(
        self,
        sql: str,
        data_source: str | None = None,
        params: dict | None = None,
    ) -> dict[str, Any]:
        """分析 SQL 的执行计划。"""
        data_source = self._registry.resolve(data_source)
        intent = analyze(sql)
        if not intent.is_readonly:
            raise ValueError("explain_query 仅支持只读语句 (SELECT/WITH)")

        driver = self._registry.driver(data_source)
        pool = self._registry.get(data_source)
        conn = pool.connection()
        try:
            plan = driver.explain_query(conn, sql=sql, params=params or {})
            return {
                "data_source": data_source,
                "estimated_rows": plan.estimated_rows,
                "estimated_cost": plan.estimated_cost,
                "plan_rows": plan.raw_rows,
                "plan_step_count": len(plan.raw_rows),
            }
        finally:
            conn.close()

    def table_sample(
        self,
        table: str,
        data_source: str | None = None,
        schema: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """采样表的前 N 行数据。"""
        from .query_service import _params_tuple, _serialize

        data_source = self._registry.resolve(data_source)
        driver = self._registry.driver(data_source)
        cap = min(limit, min(100, self._settings.default_query_max_rows))
        pool = self._registry.get(data_source)
        conn = pool.connection()
        try:
            quoted_table = driver.quote_ident(table)
            if schema:
                quoted_table = f"{driver.quote_ident(schema)}.{quoted_table}"
            if driver.name in ("oracle", "dm"):
                sql = f"SELECT * FROM {quoted_table} WHERE ROWNUM <= :1"
            elif driver.name == "sqlite":
                sql = f"SELECT * FROM {quoted_table} LIMIT ?"
            else:
                sql = f"SELECT * FROM {quoted_table} LIMIT %s"
            with conn.cursor() as cur:
                cur.execute(sql, _params_tuple([cap]))
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchall()
                return {
                    "table": table,
                    "schema": schema,
                    "data_source": data_source,
                    "columns": cols,
                    "rows": [_serialize(r) for r in rows],
                    "rowcount": len(rows),
                    "limit": cap,
                }
        finally:
            conn.close()

    def schema_diff(
        self,
        source_data_source: str | None,
        target_data_source: str | None,
        source_schema: str | None = None,
        target_schema: str | None = None,
        table_type: str | None = "BASE TABLE",
    ) -> dict[str, Any]:
        """比较两个数据源/schema 之间的表结构差异。

        比较维度：
        - 表存在性（仅源端 / 仅目标端）
        - 同名表的列差异（新增列 / 删除列 / 类型变更）
        - 同名表的索引差异（新增索引 / 删除索引 / 列变化）

        Args:
            source_data_source: 源数据源 JNDI 名称。
            target_data_source: 目标数据源 JNDI 名称。
            source_schema: 源 schema 名。
            target_schema: 目标 schema 名。
            table_type: 比较的表类型，默认 BASE TABLE。

        Returns:
            结构化的差异报告 dict。
        """
        src_ds = self._registry.resolve(source_data_source)
        tgt_ds = self._registry.resolve(target_data_source)

        src_driver = self._registry.driver(src_ds)
        tgt_driver = self._registry.driver(tgt_ds)
        src_pool = self._registry.get(src_ds)
        tgt_pool = self._registry.get(tgt_ds)

        src_conn = src_pool.connection()
        tgt_conn = tgt_pool.connection()
        try:
            src_tables = src_driver.list_tables(
                src_conn, schema=source_schema, table_type=table_type,
            )
            tgt_tables = tgt_driver.list_tables(
                tgt_conn, schema=target_schema, table_type=table_type,
            )

            src_map = {t.name: t for t in src_tables}
            tgt_map = {t.name: t for t in tgt_tables}

            only_in_source = sorted(set(src_map) - set(tgt_map))
            only_in_target = sorted(set(tgt_map) - set(src_map))
            common_tables = sorted(set(src_map) & set(tgt_map))

            table_diffs: list[dict[str, Any]] = []
            for table_name in common_tables:
                diff = self._diff_table(
                    src_driver, src_conn, table_name, source_schema,
                    tgt_driver, tgt_conn, table_name, target_schema,
                )
                if diff["has_changes"]:
                    table_diffs.append(diff)

            return {
                "source": {"data_source": src_ds, "schema": source_schema},
                "target": {"data_source": tgt_ds, "schema": target_schema},
                "summary": {
                    "source_table_count": len(src_tables),
                    "target_table_count": len(tgt_tables),
                    "only_in_source": len(only_in_source),
                    "only_in_target": len(only_in_target),
                    "common_tables": len(common_tables),
                    "tables_with_changes": len(table_diffs),
                },
                "only_in_source": only_in_source,
                "only_in_target": only_in_target,
                "table_diffs": table_diffs,
            }
        finally:
            src_conn.close()
            tgt_conn.close()

    # ------------------------------------------------------------------
    # schema_diff 内部方法
    # ------------------------------------------------------------------

    def _diff_table(
        self,
        src_driver, src_conn, src_table: str, src_schema: str | None,
        tgt_driver, tgt_conn, tgt_table: str, tgt_schema: str | None,
    ) -> dict[str, Any]:
        """比较单张表的列和索引差异。"""
        src_cols = src_driver.describe_table(
            src_conn, table=src_table, schema=src_schema,
        )
        tgt_cols = tgt_driver.describe_table(
            tgt_conn, table=tgt_table, schema=tgt_schema,
        )
        src_idx = src_driver.list_indexes(
            src_conn, table=src_table, schema=src_schema,
        )
        tgt_idx = tgt_driver.list_indexes(
            tgt_conn, table=tgt_table, schema=tgt_schema,
        )

        col_diff = self._diff_columns(src_cols, tgt_cols)
        idx_diff = self._diff_indexes(src_idx, tgt_idx)

        has_changes = (
            col_diff["added"] or col_diff["removed"]
            or col_diff["modified"] or idx_diff["added"]
            or idx_diff["removed"] or idx_diff["modified"]
        )

        return {
            "table": src_table,
            "has_changes": has_changes,
            "columns": col_diff,
            "indexes": idx_diff,
        }

    @staticmethod
    def _diff_columns(
        src: list[ColumnInfo], tgt: list[ColumnInfo],
    ) -> dict[str, Any]:
        """比较列差异。"""
        src_map = {c.name: c for c in src}
        tgt_map = {c.name: c for c in tgt}

        added = sorted(set(tgt_map) - set(src_map))
        removed = sorted(set(src_map) - set(tgt_map))
        common = set(src_map) & set(tgt_map)

        modified: list[dict[str, Any]] = []
        for name in sorted(common):
            s = src_map[name]
            t = tgt_map[name]
            changes = _column_changes(s, t)
            if changes:
                modified.append({
                    "column": name,
                    "changes": changes,
                    "source": _col_summary(s),
                    "target": _col_summary(t),
                })

        return {
            "added": added,
            "removed": removed,
            "modified": modified,
        }

    @staticmethod
    def _diff_indexes(
        src: list[IndexInfo], tgt: list[IndexInfo],
    ) -> dict[str, Any]:
        """比较索引差异。"""
        src_map = {i.name: i for i in src}
        tgt_map = {i.name: i for i in tgt}

        added = sorted(set(tgt_map) - set(src_map))
        removed = sorted(set(src_map) - set(tgt_map))
        common = set(src_map) & set(tgt_map)

        modified: list[dict[str, Any]] = []
        for name in sorted(common):
            s = src_map[name]
            t = tgt_map[name]
            changes = _index_changes(s, t)
            if changes:
                modified.append({
                    "index": name,
                    "changes": changes,
                    "source": asdict(s),
                    "target": asdict(t),
                })

        return {
            "added": added,
            "removed": removed,
            "modified": modified,
        }


# ------------------------------------------------------------------
# schema_diff 内部工具函数
# ------------------------------------------------------------------

def _col_summary(col: ColumnInfo) -> dict[str, Any]:
    """列摘要（用于差异展示）。"""
    return {
        "name": col.name,
        "data_type": col.data_type,
        "nullable": col.nullable,
        "default": col.default,
        "is_primary_key": col.is_primary_key,
        "character_maximum_length": col.character_maximum_length,
        "numeric_precision": col.numeric_precision,
        "numeric_scale": col.numeric_scale,
    }


def _column_changes(s: ColumnInfo, t: ColumnInfo) -> list[str]:
    """比较两列，返回变化描述列表。"""
    changes: list[str] = []
    if s.data_type != t.data_type:
        changes.append(f"type: {s.data_type} → {t.data_type}")
    if s.nullable != t.nullable:
        changes.append(f"nullable: {s.nullable} → {t.nullable}")
    if s.default != t.default:
        changes.append(f"default: {s.default} → {t.default}")
    if s.is_primary_key != t.is_primary_key:
        changes.append(f"primary_key: {s.is_primary_key} → {t.is_primary_key}")
    if s.character_maximum_length != t.character_maximum_length:
        changes.append(
            f"char_length: {s.character_maximum_length} → {t.character_maximum_length}"
        )
    if s.numeric_precision != t.numeric_precision:
        changes.append(
            f"numeric_precision: {s.numeric_precision} → {t.numeric_precision}"
        )
    if s.numeric_scale != t.numeric_scale:
        changes.append(f"numeric_scale: {s.numeric_scale} → {t.numeric_scale}")
    return changes


def _index_changes(s: IndexInfo, t: IndexInfo) -> list[str]:
    """比较两个索引，返回变化描述列表。"""
    changes: list[str] = []
    if s.is_unique != t.is_unique:
        changes.append(f"unique: {s.is_unique} → {t.is_unique}")
    if s.is_primary != t.is_primary:
        changes.append(f"primary: {s.is_primary} → {t.is_primary}")
    if s.columns != t.columns:
        changes.append(f"columns: {s.columns} → {t.columns}")
    return changes
