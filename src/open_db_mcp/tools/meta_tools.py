"""元数据浏览类 MCP 工具（薄适配层）。

通过 MetaService 调用 DriverAdapter 的元数据方法实现。
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..registry import DataSourceRegistry
from ..services.meta_service import MetaService


def register(mcp, registry: DataSourceRegistry, settings: Settings) -> None:
    """把工具注册到 FastMCP 实例。"""
    service = MetaService(registry, settings)

    @mcp.tool()
    def list_schemas(data_source: str | None = None) -> dict[str, Any]:
        """列出所有 schema / database。

        Args:
            data_source: 数据源名称，不传则使用当前活跃数据源。
        """
        return service.list_schemas(data_source=data_source)

    @mcp.tool()
    def list_tables(
        data_source: str | None = None,
        schema: str | None = None,
        table_type: str | None = None,
    ) -> dict[str, Any]:
        """列出指定 schema 中的表和/或视图。

        Args:
            data_source: 数据源名称，不传则使用当前活跃数据源。
            schema: schema 名，不传使用默认 schema。
            table_type: 'BASE TABLE' / 'VIEW'，不传则全部列出。
        """
        return service.list_tables(
            data_source=data_source,
            schema=schema,
            table_type=table_type,
        )

    @mcp.tool()
    def list_indexes(
        table: str,
        data_source: str | None = None,
        schema: str | None = None,
    ) -> dict[str, Any]:
        """列出指定表的索引。

        Args:
            table: 表名。
            data_source: 数据源名称，不传则使用当前活跃数据源。
            schema: schema 名，不传使用默认 schema。
        """
        return service.list_indexes(
            table=table,
            data_source=data_source,
            schema=schema,
        )

    @mcp.tool()
    def describe_table(
        table: str,
        data_source: str | None = None,
        schema: str | None = None,
    ) -> dict[str, Any]:
        """查看表结构（列/类型/主键/索引/注释）。

        Args:
            table: 表名。
            data_source: 数据源名称，不传则使用当前活跃数据源。
            schema: schema 名，不传使用默认 schema。
        """
        return service.describe_table(
            table=table,
            data_source=data_source,
            schema=schema,
        )

    @mcp.tool()
    def explain_query(
        sql: str,
        data_source: str | None = None,
        params: dict | None = None,
    ) -> dict[str, Any]:
        """获取 SQL 执行计划（EXPLAIN），不实际执行。

        Args:
            sql: SELECT 语句。
            data_source: 数据源名称，不传则使用当前活跃数据源。
            params: 查询参数。
        """
        return service.explain_query(
            sql=sql,
            data_source=data_source,
            params=params,
        )

    @mcp.tool()
    def sample_table(
        table: str,
        data_source: str | None = None,
        schema: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """采样表前 N 行数据，用于快速了解数据内容。

        Args:
            table: 表名。
            data_source: 数据源名称，不传则使用当前活跃数据源。
            schema: schema 名，不传使用默认 schema。
            limit: 采样行数（默认 10，最大 100）。
        """
        return service.table_sample(
            table=table,
            data_source=data_source,
            schema=schema,
            limit=limit,
        )

    @mcp.tool()
    def diff_schema(
        source_data_source: str | None,
        target_data_source: str | None,
        source_schema: str | None = None,
        target_schema: str | None = None,
        table_type: str | None = "BASE TABLE",
    ) -> dict[str, Any]:
        """比较两个数据源/schema 的表结构差异（表/列/索引）。

        Args:
            source_data_source: 源数据源名称。
            target_data_source: 目标数据源名称。
            source_schema: 源 schema 名。
            target_schema: 目标 schema 名。
            table_type: 比较的表类型，默认 BASE TABLE。
        """
        return service.schema_diff(
            source_data_source=source_data_source,
            target_data_source=target_data_source,
            source_schema=source_schema,
            target_schema=target_schema,
            table_type=table_type,
        )
