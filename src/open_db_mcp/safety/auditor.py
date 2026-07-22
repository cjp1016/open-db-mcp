"""审计：每次 DML 写一行 JSON Lines。"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_path: str = ""
_enabled: bool = True


def configure(path: str, enabled: bool) -> None:
    global _path, _enabled
    _path = path
    _enabled = enabled


def audit(
    *,
    jndi: str,
    sql: str,
    params: Any = None,
    affected_rows: int | None = None,
    duration_ms: int = 0,
    status: str = "ok",
    error: str | None = None,
    dry_run: bool = False,
    user: str | None = None,
) -> None:
    if not _enabled or not _path:
        return
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "user": user or os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
        "jndi": jndi,
        "sql": sql,
        "params": _safe_params(params),
        "affected_rows": affected_rows,
        "duration_ms": duration_ms,
        "status": status,
        "error": error,
        "dry_run": dry_run,
    }
    line = json.dumps(record, ensure_ascii=False, default=str)
    with _lock:
        p = Path(_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def _safe_params(params: Any) -> Any:
    """参数脱敏：截断超长字符串，避免把大字段写进审计。"""
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
