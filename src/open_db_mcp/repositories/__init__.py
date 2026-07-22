"""持久化仓储层。

将数据源 / 白名单的 JSON 持久化逻辑从工具层抽离，遵循单一职责原则：
- DataSourceRepository：负责 datasources.json 的读写
- WhitelistRepository：负责 whitelist.json 的读写

工具层 / 服务层通过这两个仓储实现持久化，并自行处理与 registry 的内存同步。
"""

from __future__ import annotations

from .base import BaseJsonRepository, load_json_object, write_json_object
from .datasource_repository import DataSourceRepository
from .whitelist_repository import WhitelistRepository

__all__ = [
    "BaseJsonRepository",
    "DataSourceRepository",
    "WhitelistRepository",
    "load_json_object",
    "write_json_object",
]
