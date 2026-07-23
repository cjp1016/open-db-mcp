"""慢查询分析服务层。

提供三层能力：
1. 本地慢查询记录与检索（执行计时超阈值自动记录）
2. 数据库原生慢日志查询（通过 DriverAdapter.fetch_slow_queries）
3. 基于 EXPLAIN 的优化建议分析
"""

from __future__ import annotations

import gzip
import json
import logging
import shutil
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Settings
from ..registry import DataSourceRegistry
from ..safety.sql_analyzer import analyze

log = logging.getLogger("open-db-mcp.services.slow_query")

_lock = threading.Lock()

# 日志文件达到此大小时触发压缩归档（默认 10MB）
_MAX_LOG_SIZE_BYTES = 10 * 1024 * 1024


class SlowQueryService:
    """慢查询分析服务。"""

    def __init__(
        self,
        registry: DataSourceRegistry,
        settings: Settings,
        log_path: str = "",
    ) -> None:
        self._registry = registry
        self._settings = settings
        self._log_path = log_path
        # 内存环形缓冲（避免频繁磁盘 IO）
        self._records: list[dict[str, Any]] = []
        self._max_records = settings.slow_query_max_records

    # ------------------------------------------------------------------
    # L1: 本地慢查询记录
    # ------------------------------------------------------------------

    def record(
        self,
        *,
        jndi: str,
        sql: str,
        duration_ms: int,
        params: Any = None,
        rowcount: int | None = None,
        purpose: str | None = None,
    ) -> None:
        """记录一条慢查询（由 QueryService/DmlService 在超阈值时调用）。"""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "jndi": jndi,
            "sql": sql[:2000],  # 截断超长 SQL
            "params": _safe_params(params),
            "duration_ms": duration_ms,
            "rowcount": rowcount,
            "purpose": purpose or "",
        }
        with _lock:
            self._records.append(entry)
            if len(self._records) > self._max_records:
                self._records = self._records[-self._max_records:]
        # 异步写磁盘（不阻塞主流程）
        self._persist(entry)

    def list_slow_queries(
        self,
        *,
        data_source: str | None = None,
        threshold_ms: int | None = None,
        limit: int = 50,
        source: str = "local",
    ) -> dict[str, Any]:
        """列出慢查询记录。

        Args:
            data_source: 过滤指定数据源，None 表示全部。
            threshold_ms: 过滤阈值（覆盖全局配置）。
            limit: 最大返回条数。
            source: 'local'（本地记录）/ 'database'（数据库原生日志）/ 'all'。
        """
        results: list[dict[str, Any]] = []

        if source in ("local", "all"):
            results.extend(self._query_local(data_source, threshold_ms, limit))

        if source in ("database", "all"):
            db_records = self._query_database(data_source, threshold_ms, limit)
            results.extend(db_records)

        # 按耗时降序
        results.sort(key=lambda r: r.get("duration_ms", 0), reverse=True)
        return {
            "records": results[:limit],
            "count": len(results[:limit]),
            "threshold_ms": threshold_ms or self._settings.slow_query_threshold_ms,
            "source": source,
        }

    def get_query_stats(
        self,
        *,
        data_source: str | None = None,
        top_n: int = 10,
    ) -> dict[str, Any]:
        """按 SQL 模板聚合统计，返回 Top-N 慢查询。

        聚合维度：SQL 模板（去除具体参数值后的骨架）。
        """
        with _lock:
            records = list(self._records)

        if data_source:
            records = [r for r in records if r["jndi"] == data_source]

        # 按 SQL 模板聚合
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in records:
            template = _normalize_sql(r["sql"])
            groups[template].append(r)

        stats: list[dict[str, Any]] = []
        for template, group in groups.items():
            durations = [g["duration_ms"] for g in group]
            stats.append({
                "sql_template": template[:500],
                "count": len(group),
                "avg_ms": round(sum(durations) / len(durations), 1),
                "max_ms": max(durations),
                "min_ms": min(durations),
                "total_ms": sum(durations),
                "data_sources": sorted(set(g["jndi"] for g in group)),
                "last_seen": max(g["ts"] for g in group),
            })

        # 按总耗时降序
        stats.sort(key=lambda s: s["total_ms"], reverse=True)
        return {
            "top_queries": stats[:top_n],
            "total_records": len(records),
            "unique_templates": len(groups),
        }

    # ------------------------------------------------------------------
    # L3: 优化建议分析
    # ------------------------------------------------------------------

    def analyze_slow_query(
        self,
        sql: str,
        data_source: str | None = None,
    ) -> dict[str, Any]:
        """对指定 SQL 做深度分析：EXPLAIN + 索引命中 + 优化建议。"""
        data_source = self._registry.resolve(data_source)
        driver = self._registry.driver(data_source)
        pool = self._registry.get(data_source)

        intent = analyze(sql)
        if not intent.is_readonly:
            raise ValueError("analyze_slow_query 仅支持只读语句 (SELECT/WITH)")

        conn = pool.connection()
        try:
            # 1. 获取执行计划
            plan = driver.explain_query(conn, sql=sql, params={})

            # 2. 分析执行计划中的问题
            issues = _analyze_plan(plan.raw_rows, driver.name)

            # 3. 检查涉及表的索引覆盖
            index_info = self._check_indexes(conn, driver, intent.tables)

            # 4. 生成优化建议
            suggestions = _generate_suggestions(issues, intent, index_info)

            return {
                "data_source": data_source,
                "sql": sql[:1000],
                "tables": intent.tables,
                "execution_plan": {
                    "estimated_rows": plan.estimated_rows,
                    "estimated_cost": plan.estimated_cost,
                    "plan_rows": plan.raw_rows,
                },
                "issues": issues,
                "index_coverage": index_info,
                "suggestions": suggestions,
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _query_local(
        self,
        data_source: str | None,
        threshold_ms: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """从本地内存/磁盘记录中查询。"""
        threshold = threshold_ms or self._settings.slow_query_threshold_ms
        with _lock:
            records = list(self._records)

        # 如果内存为空，尝试从磁盘加载
        if not records and self._log_path:
            records = self._load_from_disk()

        filtered = [
            r for r in records
            if r["duration_ms"] >= threshold
            and (data_source is None or r["jndi"] == data_source)
        ]
        filtered.sort(key=lambda r: r["duration_ms"], reverse=True)
        return filtered[:limit]

    def _query_database(
        self,
        data_source: str | None,
        threshold_ms: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """从数据库原生慢日志查询。"""
        threshold_sec = (threshold_ms or self._settings.slow_query_threshold_ms) / 1000.0
        results: list[dict[str, Any]] = []

        jndi_list = (
            [data_source] if data_source
            else [item["jndi"] for item in self._registry.list()]
        )

        for jndi in jndi_list:
            try:
                driver = self._registry.driver(jndi)
                fetch_fn = getattr(driver, "fetch_slow_queries", None)
                if fetch_fn is None:
                    continue
                pool = self._registry.get(jndi)
                conn = pool.connection()
                try:
                    rows = fetch_fn(conn, limit=limit, threshold_sec=threshold_sec)
                    for row in rows:
                        row["jndi"] = jndi
                        row["source"] = "database"
                    results.extend(rows)
                finally:
                    conn.close()
            except Exception as exc:
                log.warning("查询 %s 数据库慢日志失败: %s", jndi, exc)

        return results

    def _check_indexes(
        self, conn: Any, driver: Any, tables: list[str]
    ) -> list[dict[str, Any]]:
        """检查涉及表的索引情况。"""
        info: list[dict[str, Any]] = []
        for table in tables:
            try:
                indexes = driver.list_indexes(conn, table=table, schema=None)
                info.append({
                    "table": table,
                    "index_count": len(indexes),
                    "indexes": [
                        {"name": idx.name, "columns": idx.columns, "is_primary": idx.is_primary}
                        for idx in indexes
                    ],
                })
            except Exception as exc:
                info.append({"table": table, "error": str(exc)})
        return info

    def _persist(self, entry: dict[str, Any]) -> None:
        """写入磁盘日志（JSONL 格式），超过 10MB 自动压缩归档。"""
        if not self._log_path:
            return
        try:
            p = Path(self._log_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with _lock:
                # 写入前检查大小，超限则先归档
                if p.is_file() and p.stat().st_size >= _MAX_LOG_SIZE_BYTES:
                    self._rotate_log(p)
                with p.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            log.warning("写入慢查询日志失败: %s", exc)

    def _rotate_log(self, log_file: Path) -> None:
        """将当前日志压缩归档到 bak/ 目录，带时间戳。"""
        try:
            bak_dir = log_file.parent / "bak"
            bak_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            bak_name = f"{log_file.stem}_{ts}.jsonl.gz"
            bak_path = bak_dir / bak_name
            # 压缩当前文件
            with log_file.open("rb") as f_in:
                with gzip.open(bak_path, "wb", compresslevel=6) as f_out:
                    shutil.copyfileobj(f_in, f_out)
            # 清空当前文件
            log_file.write_text("", encoding="utf-8")
            log.info("慢查询日志已归档: %s", bak_path)
        except Exception as exc:
            log.warning("慢查询日志归档失败: %s", exc)

    def _load_from_disk(self) -> list[dict[str, Any]]:
        """从磁盘 JSONL 加载历史记录。"""
        if not self._log_path:
            return []
        p = Path(self._log_path)
        if not p.is_file():
            return []
        records: list[dict[str, Any]] = []
        try:
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        except Exception:
            pass
        # 只保留最近 N 条
        return records[-self._max_records:]


# ------------------------------------------------------------------
# 执行计划分析引擎
# ------------------------------------------------------------------

# 各数据库全表扫描标识
_FULL_SCAN_MARKERS = {
    "mysql": ["ALL", "index"],
    "oracle": ["TABLE ACCESS FULL", "TABLE ACCESS"],
    "dm": ["TABLE ACCESS FULL", "TABLE ACCESS"],
    "postgres": ["Seq Scan"],
    "sqlite": ["SCAN"],
}

_TEMP_TABLE_MARKERS = ["Using temporary", "TEMPORARY", "TempTable"]
_FILESORT_MARKERS = ["Using filesort", "SORT", "filesort"]


def _analyze_plan(plan_rows: list[dict[str, Any]], dialect: str) -> list[dict[str, Any]]:
    """分析执行计划，识别性能问题。"""
    issues: list[dict[str, Any]] = []
    full_scan_markers = _FULL_SCAN_MARKERS.get(dialect, ["ALL", "FULL", "SCAN"])

    for row in plan_rows:
        row_str = json.dumps(row, ensure_ascii=False, default=str).upper()

        # 全表扫描
        for marker in full_scan_markers:
            if marker.upper() in row_str:
                issues.append({
                    "type": "full_table_scan",
                    "severity": "high",
                    "detail": f"检测到全表扫描标识: {marker}",
                    "plan_row": row,
                })
                break

        # 临时表
        for marker in _TEMP_TABLE_MARKERS:
            if marker.upper() in row_str:
                issues.append({
                    "type": "temporary_table",
                    "severity": "medium",
                    "detail": f"使用临时表: {marker}",
                    "plan_row": row,
                })
                break

        # 文件排序
        for marker in _FILESORT_MARKERS:
            if marker.upper() in row_str:
                issues.append({
                    "type": "filesort",
                    "severity": "medium",
                    "detail": f"文件排序: {marker}",
                    "plan_row": row,
                })
                break

    return issues


def _generate_suggestions(
    issues: list[dict[str, Any]],
    intent: Any,
    index_info: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """基于问题列表和索引信息生成优化建议。"""
    suggestions: list[dict[str, Any]] = []

    has_full_scan = any(i["type"] == "full_table_scan" for i in issues)
    has_temp = any(i["type"] == "temporary_table" for i in issues)
    has_filesort = any(i["type"] == "filesort" for i in issues)

    if has_full_scan:
        # 检查是否有 WHERE 条件但无索引
        tables_with_idx = {
            t["table"] for t in index_info if t.get("index_count", 0) > 0
        }
        no_idx_tables = [t for t in intent.tables if t not in tables_with_idx]
        if no_idx_tables and intent.has_where:
            suggestions.append({
                "priority": "high",
                "action": "add_index",
                "detail": f"表 {no_idx_tables} 存在 WHERE 条件但缺少索引，"
                          f"建议为 WHERE 涉及的列创建索引",
            })
        elif intent.has_where:
            suggestions.append({
                "priority": "high",
                "action": "check_index_usage",
                "detail": "存在全表扫描，请检查 WHERE 条件列是否命中已有索引"
                          "（注意隐式类型转换、函数包裹列等导致索引失效的情况）",
            })
        else:
            suggestions.append({
                "priority": "medium",
                "action": "add_where",
                "detail": "查询无 WHERE 条件导致全表扫描，建议添加过滤条件减少扫描行数",
            })

    if has_temp:
        suggestions.append({
            "priority": "medium",
            "action": "optimize_group_by",
            "detail": "使用了临时表，常见于 GROUP BY / DISTINCT / UNION，"
                      "建议：1) 为 GROUP BY 列建索引 2) 减少 DISTINCT 使用 3) 拆分子查询",
        })

    if has_filesort:
        suggestions.append({
            "priority": "medium",
            "action": "optimize_order_by",
            "detail": "使用了文件排序，建议：1) 为 ORDER BY 列建索引 "
                      "2) 确保 ORDER BY 方向一致 3) 减少排序数据量",
        })

    if not suggestions and not issues:
        suggestions.append({
            "priority": "low",
            "action": "no_issue",
            "detail": "执行计划未发现明显性能问题，若仍慢可关注：网络延迟、锁等待、数据量增长",
        })

    return suggestions


# ------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------

def _normalize_sql(sql: str) -> str:
    """将 SQL 归一化为模板（去除具体字面值，保留结构）。"""
    import re
    s = sql.strip().rstrip(";")
    # 替换字符串字面值
    s = re.sub(r"'[^']*'", "'?'", s)
    # 替换数字字面值
    s = re.sub(r"\b\d+(\.\d+)?\b", "?", s)
    # 压缩空白
    s = re.sub(r"\s+", " ", s)
    return s


def _safe_params(params: Any) -> Any:
    """参数脱敏。"""
    if params is None:
        return None
    if isinstance(params, dict):
        return {k: _truncate(v) for k, v in params.items()}
    if isinstance(params, (list, tuple)):
        return [_truncate(v) for v in params]
    return _truncate(params)


def _truncate(v: Any) -> Any:
    if isinstance(v, str) and len(v) > 200:
        return v[:200] + "..."
    return v


# ------------------------------------------------------------------
# 模块级快捷接口（类似 auditor，避免修改各 Service 构造函数）
# ------------------------------------------------------------------

_global_service: SlowQueryService | None = None
_threshold_ms: int = 1000


def configure(service: SlowQueryService, threshold_ms: int) -> None:
    """启动时配置全局慢查询记录器。"""
    global _global_service, _threshold_ms
    _global_service = service
    _threshold_ms = threshold_ms


def record_if_slow(
    *,
    jndi: str,
    sql: str,
    duration_ms: int,
    params: Any = None,
    rowcount: int | None = None,
    purpose: str | None = None,
) -> None:
    """若耗时超过阈值则记录慢查询（由 QueryService/DmlService 调用）。"""
    if _global_service is None or duration_ms < _threshold_ms:
        return
    _global_service.record(
        jndi=jndi, sql=sql, duration_ms=duration_ms,
        params=params, rowcount=rowcount, purpose=purpose,
    )
