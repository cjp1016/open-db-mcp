"""白名单配置仓储：负责 whitelist.json 的读写。

只关心持久化，不感知 registry 的内存状态。
调用方在持久化后自行调用 registry.set_whitelist_base() 同步。
"""

from __future__ import annotations

import logging
from typing import Any

from .base import BaseJsonRepository

log = logging.getLogger("open-db-mcp.repositories.whitelist")


class WhitelistRepository(BaseJsonRepository):
    """whitelist.json 持久化仓储。"""

    def save(
        self,
        *,
        jndi: str,
        allowed_tables: list[str] | None = None,
        forbidden_columns: list[str] | None = None,
        max_affected_rows: int | None = None,
    ) -> dict[str, Any]:
        """写入/更新单个数据源的白名单条目，返回更新后的完整白名单 dict。

        采用合并策略：保留原有字段，仅更新传入的字段。
        """
        path = self._resolve_path("whitelist")
        wl = self._load(path)
        rule: dict[str, Any] = dict(wl.get(jndi) or {})
        if allowed_tables is not None:
            rule["allowed_tables"] = list(allowed_tables)
        if forbidden_columns is not None:
            rule["forbidden_columns"] = list(forbidden_columns)
        if max_affected_rows is not None:
            rule["max_affected_rows"] = int(max_affected_rows)
        wl[jndi] = rule
        self._write(path, wl)
        log.info("持久化白名单 %s 到 %s", jndi, path)
        return wl

    def delete(self, jndi: str) -> dict[str, Any]:
        """移除指定数据源的白名单条目，返回更新后的完整白名单 dict。"""
        path = self._resolve_path("whitelist")
        wl = self._load(path)
        if jndi in wl:
            wl.pop(jndi)
            self._write(path, wl)
            log.info("从 %s 移除白名单 %s", path, jndi)
        return wl

    def load_all(self) -> dict[str, dict[str, Any]]:
        """加载全部白名单配置。"""
        path = self._resolve_path("whitelist")
        return self._load(path)

    def load_one(self, jndi: str) -> dict[str, Any] | None:
        """加载单个数据源白名单，不存在返回 None。"""
        return self.load_all().get(jndi)
