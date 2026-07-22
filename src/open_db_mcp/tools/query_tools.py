"""只读查询类 MCP 工具（薄适配层）。"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..registry import DataSourceRegistry
from ..services.query_service import QueryService


def register(mcp, registry: DataSourceRegistry, settings: Settings) -> None:
    """把工具注册到 FastMCP 实例。"""
    service = QueryService(registry, settings)

    @mcp.tool()
    def execute_query(
        sql: str,
        data_source: str | None = None,
        params: dict | None = None,
        max_rows: int = 1000,
    ) -> dict[str, Any]:
        """执行只读 SELECT/WITH 查询，返回 {columns, rows, rowcount}。

        Args:
            sql: SQL 字符串，必须以 SELECT 或 WITH 开头。
            data_source: 数据源名称，不传则使用当前活跃数据源。
            params: 命名参数（如 {"id": 123}）或位置参数。
            max_rows: 最大返回行数（默认 1000）。
        """
        return service.execute(
            sql=sql,
            data_source=data_source,
            params=params,
            max_rows=max_rows,
        )
