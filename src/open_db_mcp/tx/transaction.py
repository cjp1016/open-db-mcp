"""跨语句事务：begin/commit/rollback + 超时检测 + 状态查询。

基于 ContextVar 存当前会话连接，避免污染全局。
事务自动超时机制：开启事务时记录时间戳，后续操作时检查是否超时，
超时自动回滚并释放连接，防止 LLM 忘记 commit 导致连接泄漏。
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from ..registry import DataSourceRegistry

_current: ContextVar["_TxState | None"] = ContextVar("open_db_mcp_tx", default=None)

# 默认事务超时时间（秒），可在 Settings 中覆盖
DEFAULT_TX_TIMEOUT_SEC: int = 300  # 5 分钟


@dataclass
class _TxState:
    """事务运行时状态。"""

    jndi: str
    conn: Any
    started_at: float
    timeout_sec: int
    last_activity_at: float

    def is_expired(self, now: float | None = None) -> bool:
        """检查事务是否已超时。"""
        current = now if now is not None else time.time()
        return (current - self.last_activity_at) > self.timeout_sec

    def touch(self) -> None:
        """更新最近活动时间。"""
        self.last_activity_at = time.time()


def get_current() -> _TxState | None:
    """获取当前事务状态（同时检查是否已超时）。"""
    state = _current.get()
    if state is None:
        return None
    if state.is_expired():
        _auto_rollback_expired(state)
        return None
    return state


def get_current_connection() -> Any | None:
    """获取当前事务连接（不更新活动时间）。"""
    state = get_current()
    return state.conn if state else None


def begin(
    jndi: str,
    registry: DataSourceRegistry,
    timeout_sec: int | None = None,
) -> dict:
    """开启跨语句事务。

    Args:
        jndi: 数据源名称。
        registry: 数据源注册器。
        timeout_sec: 事务超时时间（秒），None 使用默认值。
    """
    state = _current.get()
    if state is not None:
        raise RuntimeError(f"事务已在数据源 {state.jndi!r} 上开启")

    pool = registry.get(jndi)
    conn = pool.connection()
    try:
        conn.autocommit = False
    except AttributeError:
        # 某些驱动（如 jaydebeapi）使用 conn.jconn.setAutoCommit(False)
        if hasattr(conn, "jconn"):
            conn.jconn.setAutoCommit(False)

    now = time.time()
    timeout = timeout_sec if timeout_sec is not None else DEFAULT_TX_TIMEOUT_SEC
    new_state = _TxState(
        jndi=jndi,
        conn=conn,
        started_at=now,
        timeout_sec=timeout,
        last_activity_at=now,
    )
    _current.set(new_state)
    return {
        "jndi": jndi,
        "status": "begun",
        "timeout_sec": timeout,
        "started_at": now,
    }


def commit() -> dict:
    """提交当前事务。"""
    state = _current.get()
    if state is None:
        raise RuntimeError("没有进行中的事务")
    if state.is_expired():
        _auto_rollback_expired(state)
        raise RuntimeError("事务已超时，已自动回滚")
    try:
        state.conn.commit()
        return {
            "jndi": state.jndi,
            "status": "committed",
            "duration_sec": round(time.time() - state.started_at, 3),
        }
    finally:
        _close(state)


def rollback() -> dict:
    """回滚当前事务。"""
    state = _current.get()
    if state is None:
        raise RuntimeError("没有进行中的事务")
    try:
        state.conn.rollback()
        return {
            "jndi": state.jndi,
            "status": "rolled_back",
            "duration_sec": round(time.time() - state.started_at, 3),
        }
    finally:
        _close(state)


def status() -> dict:
    """查询当前事务状态（不修改事务）。"""
    state = _current.get()
    if state is None:
        return {"active": False, "status": "no_transaction"}
    now = time.time()
    elapsed = now - state.started_at
    idle = now - state.last_activity_at
    remaining = max(0, state.timeout_sec - idle)
    return {
        "active": True,
        "status": "active",
        "jndi": state.jndi,
        "started_at": state.started_at,
        "elapsed_sec": round(elapsed, 3),
        "idle_sec": round(idle, 3),
        "timeout_sec": state.timeout_sec,
        "remaining_sec": round(remaining, 3),
        "is_expired": state.is_expired(now),
    }


def touch() -> None:
    """更新事务最近活动时间（在执行 DML 后调用）。"""
    state = _current.get()
    if state is not None:
        state.touch()


def _auto_rollback_expired(state: _TxState) -> None:
    """自动回滚已超时的事务。"""
    try:
        state.conn.rollback()
    except Exception:
        pass
    _close(state)


def _close(state: _TxState) -> None:
    """关闭事务连接（归还连接池）。"""
    try:
        state.conn.close()  # PooledDB 连接 close 是归还
    except Exception:
        pass
    _current.set(None)
