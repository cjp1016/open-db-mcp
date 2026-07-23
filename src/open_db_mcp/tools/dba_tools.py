"""DBA 管理类 MCP 工具（薄适配层）。

提供锁管理、会话终止、表空间分析、表空间扩容等 DBA 功能。
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..registry import DataSourceRegistry
from ..services.dba_service import DbaService


def register(mcp, registry: DataSourceRegistry, settings: Settings) -> None:
    """把工具注册到 FastMCP 实例。"""
    service = DbaService(registry, settings)

    @mcp.tool()
    def list_locks(data_source: str | None = None) -> dict[str, Any]:
        """查询当前数据库锁/阻塞信息。

        返回当前被阻塞的会话列表，包含会话 ID、用户、状态、阻塞源、
        等待时间、SQL 语句等信息。可用于诊断死锁和长时间阻塞问题。

        Args:
            data_source: 数据源名称，不传则使用当前活跃数据源。

        Returns:
            {
                locks: [{session_id, username, status, blocking_session,
                         wait_time_sec, lock_type, sql_text}],
                count: N,
                deadlock_detected: true/false
            }
        """
        return service.list_locks(data_source=data_source)

    @mcp.tool()
    def kill_session(
        session_id: str,
        serial: str | None = None,
        data_source: str | None = None,
        dry_run: bool = True,
        purpose: str | None = None,
    ) -> dict[str, Any]:
        """终止指定数据库会话（解锁被阻塞的表）。

        高危操作！默认 dry_run=True 仅预检，确认无误后设 dry_run=False 执行。
        Oracle 需要提供 serial# 参数（可从 list_locks 获取）。

        Args:
            session_id: 会话 ID（Oracle SID / MySQL process_id / PG pid）。
            serial: Oracle 专用 serial#，其他数据库忽略。
            data_source: 数据源名称，不传则使用当前活跃数据源。
            dry_run: 默认 True（仅预检，不实际终止）。
            purpose: 执行此操作的目的说明（用于审计日志）。

        Returns:
            dry_run=True  → {dry_run: True, session_info: {...}}
            dry_run=False → {dry_run: False, success: true/false, message: "..."}
        """
        return service.kill_session(
            session_id=session_id,
            serial=serial,
            data_source=data_source,
            dry_run=dry_run,
            purpose=purpose,
        )

    @mcp.tool()
    def list_tablespaces(data_source: str | None = None) -> dict[str, Any]:
        """查询表空间/数据文件使用情况。

        返回所有表空间的容量、已用、空闲空间及使用率。
        Oracle/达梦 返回 DBF 文件级别信息，MySQL/PG 返回逻辑表空间信息。

        Args:
            data_source: 数据源名称，不传则使用当前活跃数据源。

        Returns:
            {
                tablespaces: [{name, file_path, total_mb, used_mb,
                               free_mb, used_pct, autoextend, max_size_mb}],
                count: N,
                summary: {total_mb, used_mb, free_mb, avg_used_pct}
            }
        """
        return service.list_tablespaces(data_source=data_source)

    @mcp.tool()
    def resize_tablespace(
        file_path: str,
        new_size_mb: int,
        data_source: str | None = None,
        dry_run: bool = True,
        purpose: str | None = None,
    ) -> dict[str, Any]:
        """扩容数据文件/表空间。

        高危操作！默认 dry_run=True 仅预检，确认无误后设 dry_run=False 执行。
        仅 Oracle/达梦 支持手动扩容，MySQL/PG 表空间自动管理。

        Args:
            file_path: 数据文件路径（如 /u01/oradata/users01.dbf）。
            new_size_mb: 新大小（MB），必须大于当前大小。
            data_source: 数据源名称，不传则使用当前活跃数据源。
            dry_run: 默认 True（仅预检，不实际扩容）。
            purpose: 执行此操作的目的说明（用于审计日志）。

        Returns:
            dry_run=True  → {dry_run: True, current_size_mb, target_size_mb}
            dry_run=False → {dry_run: False, success: true/false, message: "..."}
        """
        return service.resize_tablespace(
            file_path=file_path,
            new_size_mb=new_size_mb,
            data_source=data_source,
            dry_run=dry_run,
            purpose=purpose,
        )
