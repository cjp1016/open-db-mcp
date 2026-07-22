"""驱动工厂：根据 DataSourceConf.kind 选择对应驱动。

支持两种调用方式：
1. ``build_driver(conf, **kwargs)`` - 兼容旧 API，自动查全局插件注册表
2. ``DriverPluginRegistry.build(kind, **kwargs)`` - 显式使用插件注册表

第三方驱动通过 ``[project.entry-points."open_db_mcp.drivers"]`` 注册后，
会自动被本工厂识别，无需修改本文件。
"""

from __future__ import annotations

from .base import DriverAdapter
from .registry import get_driver_registry


def build_driver(conf, **kwargs) -> DriverAdapter:
    """根据 DataSourceConf.kind 构建驱动实例。

    Args:
        conf: DataSourceConf 实例。
        **kwargs: 透传给驱动的额外参数（如 dm_jar_path）。
    """
    return get_driver_registry().build(conf.kind, **kwargs)
