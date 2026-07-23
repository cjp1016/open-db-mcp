"""DML 服务层：写入操作业务逻辑。

包含 DML 执行、事务管理、DDL 执行，从 MCP 工具层抽离。
"""

from __future__ import annotations

import time
from typing import Any

from ..config import Settings
from ..registry import DataSourceRegistry
from ..safety import SafetyError, auditor
from ..safety.impact_estimator import estimate
from ..safety.sql_analyzer import analyze
from ..safety.sql_validator import validate_sql
from ..safety.whitelist import check
from ..safety.whitelist import max_affected_rows as rule_max_affected_rows
from ..tx import transaction as tx
from . import slow_query_service as sqs


class DmlService:
    """DML 写入 + 事务服务。"""

    def __init__(
        self,
        registry: DataSourceRegistry,
        settings: Settings,
    ) -> None:
        self._registry = registry
        self._settings = settings

    def execute_dml(
        self,
        sql: str,
        data_source: str | None = None,
        params: dict | None = None,
        dry_run: bool = True,
        max_affected_rows_override: int | None = None,
        purpose: str | None = None,
    ) -> dict[str, Any]:
        """执行受限的 UPDATE/INSERT/DELETE。"""
        data_source = self._registry.resolve(data_source)
        cfg = self._registry.get_whitelist()
        intent = analyze(sql)
        check(intent, data_source, cfg)

        # 深度 SQL 校验
        conf = self._registry.conf(data_source)
        validation = validate_sql(sql, dialect=conf.kind)
        if not validation.is_safe:
            raise SafetyError(
                f"SQL 安全校验失败: {'; '.join(validation.violations)}"
            )

        p_tuple = _params_tuple(params)
        cap = max_affected_rows_override or rule_max_affected_rows(
            data_source, cfg, self._settings.max_affected_rows
        )

        current_conn = tx.get_current_connection()
        own_conn = current_conn is None
        conn = current_conn or self._registry.get(data_source).connection()
        try:
            started = time.perf_counter()
            est = estimate(conn, sql, p_tuple) if intent.dml in (
                "UPDATE", "DELETE"
            ) else 0
            if est > cap:
                auditor.audit(
                    jndi=data_source,
                    sql=sql,
                    params=params,
                    affected_rows=0,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    status="rejected",
                    error=f"预计影响 {est} 行 > 上限 {cap}",
                    dry_run=True,
                    purpose=purpose,
                )
                raise SafetyError(
                    f"预计影响 {est} 行 > 上限 {cap}，已拒绝执行"
                )
            if dry_run:
                return {
                    "dry_run": True,
                    "estimated_affected_rows": est,
                    "cap": cap,
                    "dml": intent.dml,
                    "tables": intent.tables,
                }
            with conn.cursor() as cur:
                cur.execute(sql, p_tuple)
                affected = cur.rowcount or 0
            if own_conn:
                conn.commit()
            else:
                tx.touch()
            duration = int((time.perf_counter() - started) * 1000)
            auditor.audit(
                jndi=data_source,
                sql=sql,
                params=params,
                affected_rows=affected,
                duration_ms=duration,
                status="ok",
                dry_run=False,
                purpose=purpose,
            )
            sqs.record_if_slow(
                jndi=data_source, sql=sql, duration_ms=duration,
                params=params, rowcount=affected, purpose=purpose,
            )
            return {
                "dry_run": False,
                "affected_rows": affected,
                "duration_ms": duration,
                "dml": intent.dml,
                "tables": intent.tables,
            }
        except Exception as exc:
            if own_conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            auditor.audit(
                jndi=data_source,
                sql=sql,
                params=params,
                status="error",
                error=str(exc),
                dry_run=dry_run,
                purpose=purpose,
            )
            raise
        finally:
            if own_conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def begin_transaction(
        self,
        data_source: str | None = None,
        timeout_sec: int | None = None,
    ) -> dict[str, Any]:
        """开启跨语句事务。"""
        data_source = self._registry.resolve(data_source)
        return tx.begin(data_source, self._registry, timeout_sec=timeout_sec)

    def commit_transaction(self) -> dict[str, Any]:
        """提交当前事务。"""
        return tx.commit()

    def rollback_transaction(self) -> dict[str, Any]:
        """回滚当前事务。"""
        return tx.rollback()

    def get_transaction_status(self) -> dict[str, Any]:
        """查询当前事务状态。"""
        return tx.status()

    def execute_ddl(
        self,
        sql: str,
        data_source: str | None = None,
        dry_run: bool = True,
        purpose: str | None = None,
    ) -> dict[str, Any]:
        """执行 DDL（CREATE/ALTER/DROP）或 PL/SQL 匿名块。"""
        data_source = self._registry.resolve(data_source)
        cfg = self._registry.get_whitelist()
        intent = analyze(sql)
        if intent.dml not in ("DDL", "PLSQL"):
            raise ValueError(
                f"execute_ddl 仅允许 DDL 或 PL/SQL，实际类型为 {intent.dml}"
            )
        check(intent, data_source, cfg)

        current_conn = tx.get_current_connection()
        own_conn = current_conn is None
        conn = current_conn or self._registry.get(data_source).connection()
        try:
            started = time.perf_counter()

            if dry_run:
                try:
                    with conn.cursor() as cur:
                        cur.execute(sql)
                    conn.rollback()
                except Exception as exc:
                    return {
                        "dry_run": True,
                        "dml": intent.dml,
                        "tables": intent.tables,
                        "syntax_ok": False,
                        "error": str(exc),
                    }
                return {
                    "dry_run": True,
                    "dml": intent.dml,
                    "tables": intent.tables,
                    "syntax_ok": True,
                }

            with conn.cursor() as cur:
                cur.execute(sql)
            if own_conn:
                conn.commit()
            else:
                tx.touch()
            duration = int((time.perf_counter() - started) * 1000)
            auditor.audit(
                jndi=data_source,
                sql=sql,
                params=None,
                affected_rows=0,
                duration_ms=duration,
                status="ok",
                dry_run=False,
                purpose=purpose,
            )
            return {
                "dry_run": False,
                "status": "ok",
                "duration_ms": duration,
                "dml": intent.dml,
                "tables": intent.tables,
            }
        except Exception as exc:
            if own_conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            auditor.audit(
                jndi=data_source,
                sql=sql,
                params=None,
                status="error",
                error=str(exc),
                dry_run=dry_run,
                purpose=purpose,
            )
            raise
        finally:
            if own_conn:
                try:
                    conn.close()
                except Exception:
                    pass


def _params_tuple(params: Any) -> Any:
    """统一参数格式：dict → tuple（按 key 排序），tuple/list → 原样。"""
    if params is None:
        return ()
    if isinstance(params, dict):
        return tuple(params[k] for k in sorted(params.keys()))
    return params
