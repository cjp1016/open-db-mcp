"""驱动适配层。

公共类型由 :mod:`open_db_mcp.drivers.base` 提供，
具体驱动实现位于 :mod:`open_db_mcp.drivers.mysql_driver` /
:mod:`open_db_mcp.drivers.oracle_driver` / :mod:`open_db_mcp.drivers.dm_driver`，
第三方驱动通过 entry_points 注册（见 :mod:`open_db_mcp.drivers.registry`）。
"""

from __future__ import annotations

from .base import (
    ColumnInfo,
    DriverAdapter,
    ExplainPlan,
    IndexInfo,
    SchemaInfo,
    TableInfo,
)
from .factory import build_driver
from .registry import (
    DriverPlugin,
    DriverPluginRegistry,
    ENTRY_POINT_GROUP,
    get_driver_registry,
    reset_driver_registry,
)

__all__ = [
    "ColumnInfo",
    "DriverAdapter",
    "DriverPlugin",
    "DriverPluginRegistry",
    "ENTRY_POINT_GROUP",
    "ExplainPlan",
    "IndexInfo",
    "SchemaInfo",
    "TableInfo",
    "build_driver",
    "get_driver_registry",
    "reset_driver_registry",
]
