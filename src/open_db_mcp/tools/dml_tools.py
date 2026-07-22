"""DML 写入 + 事务 MCP 工具（薄适配层）。"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..registry import DataSourceRegistry
from ..services.dml_service import DmlService


def register(mcp, registry: DataSourceRegistry, settings: Settings) -> None:
    """把工具注册到 FastMCP 实例。"""
    service = DmlService(registry, settings)

    @mcp.tool()
    def execute_dml(
        sql: str,
        data_source: str | None = None,
        params: dict | None = None,
        dry_run: bool = True,
        max_affected_rows_override: int | None = None,
    ) -> dict[str, Any]:
        """执行受限的 UPDATE/INSERT/DELETE。

        Args:
            sql: SQL 字符串。
            data_source: 数据源名称，不传则使用当前活跃数据源。
            params: 命名/位置参数。
            dry_run: 默认 True（仅做影响行数预检，不实际执行）。
            max_affected_rows_override: 覆盖白名单中的 max_affected_rows。

        Returns:
            dry_run=True  → {dry_run: True, estimated_affected_rows: N}
            dry_run=False → {dry_run: False, affected_rows: N, duration_ms}
        """
        return service.execute_dml(
            sql=sql,
            data_source=data_source,
            params=params,
            dry_run=dry_run,
            max_affected_rows_override=max_affected_rows_override,
        )

    @mcp.tool()
    def begin_transaction(
        data_source: str | None = None,
        timeout_sec: int | None = None,
    ) -> dict[str, Any]:
        """开启跨语句事务（在当前会话内）。

        Args:
            data_source: 数据源名称，不传则使用当前活跃数据源。
            timeout_sec: 事务超时时间（秒），默认 300 秒。
                超时后事务会被自动回滚，防止连接泄漏。
        """
        return service.begin_transaction(
            data_source=data_source,
            timeout_sec=timeout_sec,
        )

    @mcp.tool()
    def commit_transaction() -> dict[str, Any]:
        """提交当前事务。"""
        return service.commit_transaction()

    @mcp.tool()
    def rollback_transaction() -> dict[str, Any]:
        """回滚当前事务。"""
        return service.rollback_transaction()

    @mcp.tool()
    def get_transaction_status() -> dict[str, Any]:
        """查询当前事务状态（不修改事务）。

        返回字段：
            active: 是否有进行中的事务
            jndi: 数据源名称
            elapsed_sec: 已耗时（秒）
            idle_sec: 空闲时长（秒）
            timeout_sec: 超时阈值（秒）
            remaining_sec: 剩余超时时间（秒）
            is_expired: 是否已超时
        """
        return service.get_transaction_status()

    @mcp.tool()
    def execute_ddl(
        sql: str,
        data_source: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """执行 DDL（CREATE/ALTER/DROP）或 PL/SQL 匿名块。

        用于建表、加索引、执行存储过程等结构性变更。
        默认 dry_run=True 仅做语法预检，确认无误后设 dry_run=False 实际执行。

        Args:
            sql: DDL 或 PL/SQL 匿名块。
            data_source: 数据源名称，不传则使用当前活跃数据源。
            dry_run: 默认 True（仅预检语法，不实际执行）。

        Returns:
            dry_run=True  → {dry_run: True, dml, tables, syntax_ok}
            dry_run=False → {dry_run: False, status, duration_ms}
        """
        return service.execute_ddl(
            sql=sql,
            data_source=data_source,
            dry_run=dry_run,
        )
