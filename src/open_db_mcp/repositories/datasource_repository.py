"""数据源配置仓储：负责 datasources.json 的读写。

只关心持久化，不感知 registry 的内存状态。
调用方在持久化后自行决定是否同步到 registry。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..config import Settings
from .base import BaseJsonRepository

log = logging.getLogger("open-db-mcp.repositories.datasource")


class DataSourceRepository(BaseJsonRepository):
    """datasources.json 持久化仓储。"""

    def save(
        self,
        *,
        jndi: str,
        driver: str,
        url: str,
        user: str,
        password: str,
        pool_max: int | None = None,
        max_affected_rows: int | None = None,
    ) -> bool:
        """写入/更新单个数据源条目。

        Args:
            jndi: 数据源唯一名称。
            driver: 驱动类全名或 kind 简写。
            url: JDBC URL。
            user: 用户名。
            password: 密码。
            pool_max: 连接池上限，None 则不写入该字段。
            max_affected_rows: DML 行数上限，None 则不写入该字段。

        Returns:
            True 表示已写入磁盘。
        """
        path = self._resolve_path("datasources_cfg")
        data = self._load(path)
        entry: dict[str, Any] = {
            "driver": driver,
            "url": url,
            "user": user,
            "password": password,
        }
        if pool_max is not None:
            entry["pool_max"] = int(pool_max)
        if max_affected_rows is not None:
            entry["max_affected_rows"] = int(max_affected_rows)
        data[jndi] = entry
        self._write(path, data)
        log.info("持久化数据源 %s 到 %s", jndi, path)
        return True

    def delete(self, jndi: str) -> bool:
        """从 datasources.json 移除指定条目（不存在则视为已删除）。"""
        path = self._resolve_path("datasources_cfg")
        data = self._load(path)
        if jndi in data:
            data.pop(jndi)
            self._write(path, data)
            log.info("从 %s 移除数据源 %s", path, jndi)
        return True

    def load_all(self) -> dict[str, dict[str, Any]]:
        """加载全部数据源配置。"""
        path = self._resolve_path("datasources_cfg")
        return self._load(path)

    def load_one(self, jndi: str) -> dict[str, Any] | None:
        """加载单个数据源配置，不存在返回 None。"""
        return self.load_all().get(jndi)
