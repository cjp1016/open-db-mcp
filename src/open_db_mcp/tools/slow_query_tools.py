"""慢查询分析类 MCP 工具（薄适配层）。"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..registry import DataSourceRegistry
from ..services.slow_query_service import SlowQueryService


def register(
    mcp,
    registry: DataSourceRegistry,
    settings: Settings,
    service: SlowQueryService,
) -> None:
    """把工具注册到 FastMCP 实例。"""

    @mcp.tool()
    def list_slow_queries(
        data_source: str | None = None,
        threshold_ms: int | None = None,
        limit: int = 50,
        source: str = "local",
    ) -> dict[str, Any]:
        """列出慢查询记录（本地计时记录 + 数据库原生日志）。

        Args:
            data_source: 数据源名称，不传则查全部。
            threshold_ms: 慢查询阈值（毫秒），不传使用全局配置。
            limit: 最大返回条数（默认 50）。
            source: 数据来源 'local'（本地记录）/ 'database'（DB原生日志）/ 'all'（合并）。
        """
        return service.list_slow_queries(
            data_source=data_source,
            threshold_ms=threshold_ms,
            limit=limit,
            source=source,
        )

    @mcp.tool()
    def analyze_slow_query(
        sql: str,
        data_source: str | None = None,
    ) -> dict[str, Any]:
        """深度分析慢 SQL：执行计划 + 索引命中 + 优化建议。

        Args:
            sql: SELECT 语句。
            data_source: 数据源名称，不传则使用当前活跃数据源。
        """
        return service.analyze_slow_query(
            sql=sql,
            data_source=data_source,
        )

    @mcp.tool()
    def get_query_stats(
        data_source: str | None = None,
        top_n: int = 10,
    ) -> dict[str, Any]:
        """查询统计：按 SQL 模板聚合，返回 Top-N 慢查询（avg/max/count）。

        Args:
            data_source: 数据源名称，不传则统计全部。
            top_n: 返回前 N 条（默认 10）。
        """
        return service.get_query_stats(
            data_source=data_source,
            top_n=top_n,
        )
