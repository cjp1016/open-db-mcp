"""DBA 服务层：锁管理 + 表空间管理。

提供数据库管理员常用功能：
1. 锁查询：查看当前阻塞/死锁信息
2. 会话终止：解锁被阻塞的表
3. 表空间分析：查看数据文件使用情况
4. 表空间扩容：扩展数据文件大小
"""

from __future__ import annotations

import time
from typing import Any

from ..config import Settings
from ..registry import DataSourceRegistry
from ..safety import auditor


class DbaService:
    """DBA 管理服务。"""

    def __init__(
        self,
        registry: DataSourceRegistry,
        settings: Settings,
    ) -> None:
        self._registry = registry
        self._settings = settings

    def list_locks(self, data_source: str | None = None) -> dict[str, Any]:
        """查询当前锁/阻塞信息。"""
        data_source = self._registry.resolve(data_source)
        pool = self._registry.get(data_source)
        conn = pool.connection()
        try:
            driver = pool.driver
            locks = driver.list_locks(conn)
            # 检测死锁：如果存在循环阻塞
            deadlock_detected = self._detect_deadlock(locks)
            return {
                "locks": locks,
                "count": len(locks),
                "deadlock_detected": deadlock_detected,
            }
        finally:
            conn.close()

    def kill_session(
        self,
        session_id: str,
        serial: str | None = None,
        data_source: str | None = None,
        dry_run: bool = True,
        purpose: str | None = None,
    ) -> dict[str, Any]:
        """终止指定会话（解锁）。

        Args:
            session_id: 会话 ID。
            serial: Oracle 专用 serial#。
            data_source: 数据源名称。
            dry_run: 默认 True，仅预检不执行。
            purpose: 操作目的（用于审计）。
        """
        data_source = self._registry.resolve(data_source)
        pool = self._registry.get(data_source)
        conn = pool.connection()
        try:
            driver = pool.driver

            # dry_run 模式：仅查询会话信息
            if dry_run:
                session_info = self._get_session_info(conn, driver, session_id)
                return {
                    "dry_run": True,
                    "session_id": session_id,
                    "serial": serial,
                    "session_info": session_info,
                    "message": "预检完成，设置 dry_run=False 执行终止操作",
                }

            # 实际执行
            started = time.perf_counter()
            result = driver.kill_session(conn, session_id, serial)
            duration = int((time.perf_counter() - started) * 1000)

            # 审计记录
            auditor.audit(
                jndi=data_source,
                sql=result.get("sql", f"KILL SESSION {session_id}"),
                params={"session_id": session_id, "serial": serial},
                affected_rows=1 if result.get("success") else 0,
                duration_ms=duration,
                status="ok" if result.get("success") else "error",
                error=None if result.get("success") else result.get("message"),
                dry_run=False,
                purpose=purpose,
            )

            return {
                "dry_run": False,
                "success": result.get("success", False),
                "message": result.get("message", ""),
                "sql": result.get("sql", ""),
                "duration_ms": duration,
            }
        finally:
            conn.close()

    def list_tablespaces(self, data_source: str | None = None) -> dict[str, Any]:
        """查询表空间/数据文件使用情况。"""
        data_source = self._registry.resolve(data_source)
        pool = self._registry.get(data_source)
        conn = pool.connection()
        try:
            driver = pool.driver
            tablespaces = driver.list_tablespaces(conn)
            # 计算汇总信息
            total_mb = sum(t.get("total_mb", 0) for t in tablespaces)
            used_mb = sum(t.get("used_mb", 0) for t in tablespaces)
            avg_pct = (used_mb / total_mb * 100) if total_mb > 0 else 0
            return {
                "tablespaces": tablespaces,
                "count": len(tablespaces),
                "summary": {
                    "total_mb": round(total_mb, 2),
                    "used_mb": round(used_mb, 2),
                    "free_mb": round(total_mb - used_mb, 2),
                    "avg_used_pct": round(avg_pct, 1),
                },
            }
        finally:
            conn.close()

    def resize_tablespace(
        self,
        file_path: str,
        new_size_mb: int,
        data_source: str | None = None,
        dry_run: bool = True,
        purpose: str | None = None,
    ) -> dict[str, Any]:
        """扩容数据文件。

        Args:
            file_path: 数据文件路径。
            new_size_mb: 新大小（MB）。
            data_source: 数据源名称。
            dry_run: 默认 True，仅预检不执行。
            purpose: 操作目的（用于审计）。
        """
        data_source = self._registry.resolve(data_source)
        pool = self._registry.get(data_source)
        conn = pool.connection()
        try:
            driver = pool.driver

            # dry_run 模式：查询当前大小
            if dry_run:
                current_info = self._get_file_info(conn, driver, file_path)
                return {
                    "dry_run": True,
                    "file_path": file_path,
                    "current_size_mb": current_info.get("total_mb"),
                    "target_size_mb": new_size_mb,
                    "increase_mb": round(
                        new_size_mb - (current_info.get("total_mb") or 0), 2
                    ),
                    "message": "预检完成，设置 dry_run=False 执行扩容操作",
                }

            # 实际执行
            started = time.perf_counter()
            result = driver.resize_tablespace(conn, file_path, new_size_mb)
            duration = int((time.perf_counter() - started) * 1000)

            # 审计记录
            auditor.audit(
                jndi=data_source,
                sql=result.get("sql", f"RESIZE {file_path} TO {new_size_mb}M"),
                params={"file_path": file_path, "new_size_mb": new_size_mb},
                affected_rows=0,
                duration_ms=duration,
                status="ok" if result.get("success") else "error",
                error=None if result.get("success") else result.get("message"),
                dry_run=False,
                purpose=purpose,
            )

            return {
                "dry_run": False,
                "success": result.get("success", False),
                "message": result.get("message", ""),
                "sql": result.get("sql", ""),
                "duration_ms": duration,
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_deadlock(locks: list[dict[str, Any]]) -> bool:
        """检测是否存在循环阻塞（死锁）。"""
        # 构建阻塞图
        blocking_map: dict[str, str | None] = {}
        for lock in locks:
            sess = lock.get("session_id")
            blocking = lock.get("blocking_session")
            if sess and blocking:
                blocking_map[sess] = blocking

        # 检测环
        for start in blocking_map:
            visited = set()
            current = start
            while current and current not in visited:
                visited.add(current)
                current = blocking_map.get(current)
            if current and current in visited:
                return True
        return False

    @staticmethod
    def _get_session_info(
        conn: Any, driver: Any, session_id: str
    ) -> dict[str, Any]:
        """获取会话详细信息（用于 dry_run 预检）。"""
        try:
            locks = driver.list_locks(conn)
            for lock in locks:
                if lock.get("session_id") == session_id:
                    return lock
        except Exception:
            pass
        return {"session_id": session_id, "message": "未找到会话信息"}

    @staticmethod
    def _get_file_info(
        conn: Any, driver: Any, file_path: str
    ) -> dict[str, Any]:
        """获取数据文件当前信息（用于 dry_run 预检）。"""
        try:
            tablespaces = driver.list_tablespaces(conn)
            for ts in tablespaces:
                if ts.get("file_path") == file_path:
                    return ts
        except Exception:
            pass
        return {"file_path": file_path, "total_mb": None}
