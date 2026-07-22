"""数据源管理类 MCP 工具。

包含两类工具：
1. 预设置数据源的查询/切换：list_datasources / ping_datasource / get_pool_stats /
   use_datasource / get_active_datasource
2. 运行时动态注册：add_datasource / update_datasource / remove_datasource /
   list_drivers

持久化通过 DataSourceRepository / WhitelistRepository 完成，
工具层负责在持久化后同步 registry 的内存白名单。
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import Settings, get_package_root
from ..parser.jdbc_properties import list_supported_drivers, normalize_driver
from ..registry import DataSourceRegistry
from ..repositories import DataSourceRepository, WhitelistRepository
from ..tx import transaction as tx

log = logging.getLogger("open-db-mcp.tools.ds")


def register(mcp, registry: DataSourceRegistry, settings: Settings) -> None:
    """把工具注册到 FastMCP 实例。"""
    package_root = get_package_root()
    ds_repo = DataSourceRepository(settings, package_root)
    wl_repo = WhitelistRepository(settings, package_root)

    # ------------------------------------------------------------------
    # 预设置数据源：查询/切换
    # ------------------------------------------------------------------

    @mcp.tool()
    def list_datasources() -> list[dict[str, Any]]:
        """列出所有已注册数据源（名称/类型/URL/连接池/是否活跃）。"""
        return registry.list()

    @mcp.tool()
    def ping_datasource(data_source: str) -> dict[str, Any]:
        """探测数据源连通性，返回 {ok, kind, pool}。"""
        return registry.health(data_source)

    @mcp.tool()
    def get_pool_stats(data_source: str) -> dict[str, int]:
        """查看数据源连接池统计（空闲/最大/最小）。"""
        return registry.pool_stats(data_source)

    @mcp.tool()
    def use_datasource(data_source: str) -> dict[str, Any]:
        """切换当前会话的默认数据源，后续工具调用默认使用它。

        Args:
            data_source: 数据源名称（如 LOCAL_MYSQL / ORACLE_SAMPLE）。
        """
        active = registry.set_active(data_source)
        return {
            "active": active,
            "available": [j["jndi"] for j in registry.list()],
        }

    @mcp.tool()
    def get_active_datasource() -> dict[str, Any]:
        """获取当前默认数据源及其基本信息。"""
        active = registry.get_active()
        if not active:
            return {
                "active": None,
                "available": [j["jndi"] for j in registry.list()],
                "message": "未设置当前活跃数据源，请先调用 use_datasource",
            }
        conf = registry.conf(active)
        return {
            "active": active,
            "kind": conf.kind,
            "url": conf.url,
            "user": conf.user,
            "available": [j["jndi"] for j in registry.list()],
        }

    # ------------------------------------------------------------------
    # 运行时动态注册
    # ------------------------------------------------------------------

    @mcp.tool()
    def list_drivers() -> list[dict[str, str]]:
        """列出支持的数据库驱动（kind/类名/URL 示例），供 add_datasource 参考。"""
        return list_supported_drivers()

    @mcp.tool()
    def add_datasource(
        jndi: str,
        driver: str,
        url: str,
        user: str,
        password: str,
        pool_max: int | None = None,
        max_affected_rows: int | None = None,
        allowed_tables: list[str] | None = None,
        forbidden_columns: list[str] | None = None,
        persist: bool = False,
        set_active: bool = False,
    ) -> dict[str, Any]:
        """动态注册新数据源（MySQL/Oracle/达梦/PostgreSQL/SQLite）。

        Args:
            jndi: 数据源唯一名称，不能与已有重名。
            driver: 驱动简写（'mysql'/'oracle'/'dm'/'postgres'/'sqlite'）或 JDBC 类名。
            url: JDBC URL，如 jdbc:mysql://host:3306/db。
            user: 用户名。
            password: 密码。
            pool_max: 连接池上限（默认使用全局配置）。
            max_affected_rows: DML 单次影响行数上限。
            allowed_tables: 允许 DML 的表白名单（SCHEMA.TABLE 或 SCHEMA.*）。
            forbidden_columns: 禁止写入的列名列表。
            persist: True 时写入 datasources.json 持久化。
            set_active: True 时立即切换为当前数据源。
        """
        driver_cls, _kind = normalize_driver(driver)
        conf = registry.add(
            jndi=jndi,
            driver=driver_cls,
            url=url,
            user=user,
            password=password,
            pool_max=pool_max,
            max_affected_rows=max_affected_rows,
            allowed_tables=allowed_tables,
            forbidden_columns=forbidden_columns,
        )
        persisted = False
        if persist:
            persisted = _persist_datasource(
                ds_repo=ds_repo,
                wl_repo=wl_repo,
                registry=registry,
                jndi=jndi,
                driver=driver_cls,
                url=url,
                user=user,
                password=password,
                pool_max=pool_max,
                max_affected_rows=max_affected_rows,
                allowed_tables=allowed_tables,
                forbidden_columns=forbidden_columns,
            )
        active = registry.get_active()
        if set_active:
            active = registry.set_active(jndi)
        return {
            "jndi": conf.jndi,
            "kind": conf.kind,
            "url": conf.url,
            "user": conf.user,
            "active": active,
            "persisted": persisted,
            "available": [j["jndi"] for j in registry.list()],
        }

    @mcp.tool()
    def update_datasource(
        jndi: str,
        url: str | None = None,
        user: str | None = None,
        password: str | None = None,
        pool_max: int | None = None,
        persist: bool = False,
    ) -> dict[str, Any]:
        """更新数据源连接参数（重建连接池，保留白名单），未传字段保留原值。

        Args:
            jndi: 数据源名称。
            url: 新 JDBC URL，None 表示不变。
            user: 新用户名，None 表示不变。
            password: 新密码，None 表示不变。
            pool_max: 新连接池上限，None 表示不变。
            persist: True 时同步更新 datasources.json。
        """
        _refuse_if_in_transaction(jndi)
        conf = registry.update(
            jndi=jndi,
            url=url,
            user=user,
            password=password,
            pool_max=pool_max,
        )
        persisted = False
        if persist:
            persisted = ds_repo.save(
                jndi=jndi,
                driver=conf.driver,
                url=conf.url,
                user=conf.user,
                password=conf.password,
                pool_max=pool_max,
            )
        return {
            "jndi": conf.jndi,
            "kind": conf.kind,
            "url": conf.url,
            "user": conf.user,
            "persisted": persisted,
        }

    @mcp.tool()
    def remove_datasource(
        jndi: str,
        drop_whitelist: bool = True,
        persist: bool = False,
    ) -> dict[str, Any]:
        """注销数据源：关闭连接池并清理状态。

        Args:
            jndi: 数据源名称。
            drop_whitelist: True 同时移除白名单覆盖。
            persist: True 时从 datasources.json 中删除。
        """
        _refuse_if_in_transaction(jndi)
        registry.remove(jndi, drop_whitelist=drop_whitelist)
        if persist:
            ds_repo.delete(jndi)
            if drop_whitelist:
                updated_wl = wl_repo.delete(jndi)
                registry.set_whitelist_base(updated_wl)
        active = registry.get_active()
        return {
            "removed": jndi,
            "active": active,
            "available": [j["jndi"] for j in registry.list()],
        }


def _persist_datasource(
    *,
    ds_repo: DataSourceRepository,
    wl_repo: WhitelistRepository,
    registry: DataSourceRegistry,
    jndi: str,
    driver: str,
    url: str,
    user: str,
    password: str,
    pool_max: int | None,
    max_affected_rows: int | None,
    allowed_tables: list[str] | None,
    forbidden_columns: list[str] | None,
) -> bool:
    """持久化数据源 + 同步白名单到 registry。

    将"持久化"与"内存同步"两个关注点组合在此处，避免仓储层耦合 registry。
    """
    ds_repo.save(
        jndi=jndi,
        driver=driver,
        url=url,
        user=user,
        password=password,
        pool_max=pool_max,
        max_affected_rows=max_affected_rows,
    )
    if allowed_tables is not None or forbidden_columns is not None:
        updated_wl = wl_repo.save(
            jndi=jndi,
            allowed_tables=allowed_tables,
            forbidden_columns=forbidden_columns,
            max_affected_rows=max_affected_rows,
        )
        # 同步刷新 registry 的 base，确保后续 get_whitelist 返回最新
        registry.set_whitelist_base(updated_wl)
    return True


def _refuse_if_in_transaction(jndi: str) -> None:
    """若该数据源上有进行中事务则拒绝更新/移除。"""
    state = tx.get_current()
    if state is not None and state.jndi == jndi:
        raise RuntimeError(
            f"数据源 {jndi!r} 上有进行中的事务，请先 commit/rollback"
        )
