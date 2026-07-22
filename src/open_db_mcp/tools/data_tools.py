"""数据导入导出类 MCP 工具（薄适配层）。

通过 DataService 提供 CSV/JSON 格式的表数据导出与 CSV 导入。
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..registry import DataSourceRegistry
from ..services.data_service import DataService


def register(mcp, registry: DataSourceRegistry, settings: Settings) -> None:
    """把工具注册到 FastMCP 实例。"""
    service = DataService(registry, settings)

    @mcp.tool()
    def export_table(
        table: str,
        format: str = "csv",
        data_source: str | None = None,
        schema: str | None = None,
        where: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """导出表数据为 CSV 或 JSON 格式。

        Args:
            table: 表名。
            format: 输出格式，'csv'（默认）或 'json'。
            data_source: 数据源名称，不传则使用当前活跃数据源。
            schema: schema 名，不传使用默认 schema。
            where: 可选 WHERE 条件（不含 WHERE 关键字）。
            limit: 可选导出行数限制。
        """
        return service.export_table(
            table=table,
            format=format,
            data_source=data_source,
            schema=schema,
            where=where,
            limit=limit,
        )

    @mcp.tool()
    def import_csv(
        table: str,
        csv_content: str,
        data_source: str | None = None,
        schema: str | None = None,
        batch_size: int = 500,
    ) -> dict[str, Any]:
        """从 CSV 字符串批量导入数据到指定表（首行必须是列名）。

        Args:
            table: 目标表名。
            csv_content: CSV 格式字符串，首行是列名。
            data_source: 数据源名称，不传则使用当前活跃数据源。
            schema: schema 名，不传使用默认 schema。
            batch_size: 批量插入大小，默认 500。
        """
        return service.import_csv(
            table=table,
            csv_content=csv_content,
            data_source=data_source,
            schema=schema,
            batch_size=batch_size,
        )
