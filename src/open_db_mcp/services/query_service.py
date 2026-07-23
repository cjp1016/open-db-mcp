"""查询服务层：只读查询业务逻辑。

从 MCP 工具层抽离，便于：
1. 单元测试（无需构造 FastMCP 实例）
2. 复用到其他入口（HTTP API / CLI）
3. 保持工具层薄、业务层独立演进
"""

from __future__ import annotations

import time
from typing import Any

from ..config import Settings
from ..registry import DataSourceRegistry
from ..safety.sql_analyzer import analyze
from ..safety.sql_validator import validate_sql
from . import slow_query_service as sqs


class QueryService:
    """只读查询服务。"""

    def __init__(
        self,
        registry: DataSourceRegistry,
        settings: Settings,
    ) -> None:
        self._registry = registry
        self._settings = settings

    def execute(
        self,
        sql: str,
        data_source: str | None = None,
        params: dict | None = None,
        max_rows: int = 1000,
        purpose: str | None = None,
    ) -> dict[str, Any]:
        """执行只读 SELECT/WITH 查询。"""
        data_source = self._registry.resolve(data_source)

        # 第一层校验：DML 类型
        intent = analyze(sql)
        if not intent.is_readonly:
            raise ValueError(
                f"execute_query 仅允许只读语句，实际 DML={intent.dml}"
            )

        # 第二层校验：SQL 深度安全（多语句 / 危险函数）
        conf = self._registry.conf(data_source)
        validation = validate_sql(sql, dialect=conf.kind)
        if not validation.is_safe:
            raise ValueError(
                f"SQL 安全校验失败: {'; '.join(validation.violations)}"
            )

        cap = min(max_rows, self._settings.default_query_max_rows)
        pool = self._registry.get(data_source)
        conn = pool.connection()
        try:
            started = time.perf_counter()
            with conn.cursor() as cur:
                cur.execute(sql, _params_tuple(params))
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchmany(cap)
                duration_ms = int((time.perf_counter() - started) * 1000)
                sqs.record_if_slow(
                    jndi=data_source, sql=sql, duration_ms=duration_ms,
                    params=params, rowcount=len(rows), purpose=purpose,
                )
                return {
                    "columns": cols,
                    "rows": [_serialize(r) for r in rows],
                    "rowcount": len(rows),
                    "truncated": len(rows) == cap,
                    "duration_ms": duration_ms,
                }
        finally:
            conn.close()


# ------------------------------------------------------------------
# 内部工具函数（service 层与 tools 层共享）
# ------------------------------------------------------------------

def _params_tuple(params: Any) -> Any:
    """统一参数格式：dict → tuple（按 key 排序），tuple/list → 原样。"""
    if params is None:
        return ()
    if isinstance(params, dict):
        return tuple(params[k] for k in sorted(params.keys()))
    return params


def _serialize(row) -> list:
    """将 DB 返回的行序列化为可 JSON 化的列表。"""
    out: list = []
    for v in row:
        if isinstance(v, (bytes, bytearray)):
            out.append(v.decode("utf-8", errors="replace"))
        else:
            out.append(v)
    return out
