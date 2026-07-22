"""驱动插件注册中心。

基于 ``importlib.metadata.entry_points`` 实现可扩展的驱动发现机制：

- 内置驱动（mysql / oracle / dm）通过 ``pyproject.toml`` 中的
  ``[project.entry-points."open_db_mcp.drivers"]`` 声明，启动时自动加载。
- 第三方包只需在自己的 ``pyproject.toml`` 中加入同名 entry_points 组，
  即可被 ``open-db-mcp`` 自动发现并使用，无需修改本仓库代码。

每个 entry point 指向一个返回 :class:`DriverPlugin` 描述符的可调用对象
（通常是工厂函数）。

调用流程：
    load_plugins() -> dict[kind, DriverPlugin]
    build_driver(conf) -> DriverAdapter   # 内部查 plugin registry
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any, Callable

from .base import DriverAdapter

log = logging.getLogger("open-db-mcp.drivers.registry")

#: entry_points 组名（第三方包用此组注册驱动插件）
ENTRY_POINT_GROUP = "open_db_mcp.drivers"


@dataclass(frozen=True)
class DriverPlugin:
    """驱动插件描述符。

    Attributes:
        kind: 驱动短名（如 'mysql' / 'oracle' / 'postgres'），全小写。
        driver_classes: 该驱动可接受的 JDBC 驱动类全名列表。
            用于把 jdbc.properties 里的 driver 字段映射到 kind。
        url_sample: URL 示例，供 LLM 在对话中参考。
        factory: 构造驱动实例的可调用对象。
            签名: ``factory(**kwargs) -> DriverAdapter``。
            内置驱动接受 ``dm_jar_path`` 等可选参数。
        package_name: 来源包名（用于诊断与日志）。
    """

    kind: str
    driver_classes: tuple[str, ...]
    url_sample: str
    factory: Callable[..., DriverAdapter]
    package_name: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class DriverPluginRegistry:
    """驱动插件注册表。

    职责：
    1. 通过 entry_points 自动发现已安装的驱动插件
    2. 维护 kind -> DriverPlugin 与 driver_class -> kind 双向映射
    3. 提供查询接口给 factory / jdbc_properties 使用

    线程安全：启动期一次性加载，运行期只读。
    """

    def __init__(self) -> None:
        self._by_kind: dict[str, DriverPlugin] = {}
        self._by_driver_class: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------

    def load_plugins(self) -> None:
        """扫描 entry_points 并加载所有驱动插件。

        重复 kind 会被记录为警告，后注册者覆盖先注册者（便于本地覆盖第三方实现）。
        """
        eps = entry_points()
        # Python 3.10+: entry_points() 返回 EntryPoints 或带 group 的字典
        group_eps = (
            eps.select(group=ENTRY_POINT_GROUP)
            if hasattr(eps, "select")
            else eps.get(ENTRY_POINT_GROUP, [])
        )
        for ep in group_eps:
            try:
                plugin_factory = ep.load()
                plugin = plugin_factory()
                if not isinstance(plugin, DriverPlugin):
                    log.warning(
                        "entry_point %s 返回的不是 DriverPlugin: %r，跳过",
                        ep.name,
                        type(plugin).__name__,
                    )
                    continue
                self.register(plugin, package_name=ep.value.split(":")[0])
            except Exception as exc:
                log.warning("加载驱动插件 %s 失败: %s", ep.name, exc)

    def register(
        self,
        plugin: DriverPlugin,
        package_name: str = "",
    ) -> None:
        """手动注册一个驱动插件（用于测试或代码内显式注册）。"""
        kind = plugin.kind.lower()
        if kind in self._by_kind:
            old = self._by_kind[kind]
            log.warning(
                "驱动 kind %r 已被 %s 注册，现被 %s 覆盖",
                kind,
                old.package_name or "<builtin>",
                package_name or plugin.package_name or "<manual>",
            )
        # 补全 package_name
        if package_name and not plugin.package_name:
            plugin = DriverPlugin(
                kind=plugin.kind,
                driver_classes=plugin.driver_classes,
                url_sample=plugin.url_sample,
                factory=plugin.factory,
                package_name=package_name,
                extra=plugin.extra,
            )
        self._by_kind[kind] = plugin
        for cls in plugin.driver_classes:
            existing = self._by_driver_class.get(cls)
            if existing and existing != kind:
                log.warning(
                    "驱动类 %s 已映射到 kind=%s，现被 kind=%s 覆盖",
                    cls,
                    existing,
                    kind,
                )
            self._by_driver_class[cls] = kind
        log.info(
            "已注册驱动插件 kind=%s classes=%s from=%s",
            kind,
            list(plugin.driver_classes),
            plugin.package_name or package_name or "<manual>",
        )

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def list_kinds(self) -> list[str]:
        """所有已注册 kind（排序后返回）。"""
        return sorted(self._by_kind.keys())

    def get_plugin(self, kind: str) -> DriverPlugin | None:
        """按 kind 查找插件，不存在返回 None。"""
        return self._by_kind.get(kind.lower())

    def find_kind_by_driver_class(self, driver_class: str) -> str | None:
        """根据 JDBC 驱动类全名反查 kind。"""
        return self._by_driver_class.get(driver_class)

    def list_supported_drivers(self) -> list[dict[str, str]]:
        """枚举所有支持的驱动，供 LLM 在对话中查阅。"""
        out: list[dict[str, str]] = []
        for kind in self.list_kinds():
            plugin = self._by_kind[kind]
            out.append({
                "kind": kind,
                "driver_class": plugin.driver_classes[0] if plugin.driver_classes else "",
                "url_sample": plugin.url_sample,
                "package": plugin.package_name,
            })
        return out

    def normalize_driver(self, driver: str) -> tuple[str, str]:
        """接受 kind 简写或完整 JDBC 驱动类名，统一返回 (类名, kind)。

        Args:
            driver: 'oracle' / 'mysql' / 'dm' 等简写，或完整类名。

        Raises:
            ValueError: 不支持的驱动。
        """
        lower = driver.lower()
        # 1. kind 简写匹配
        plugin = self._by_kind.get(lower)
        if plugin and plugin.driver_classes:
            return plugin.driver_classes[0], plugin.kind
        # 2. 类名匹配
        kind = self._by_driver_class.get(driver)
        if kind:
            return driver, kind
        # 3. 兼容旧式 _DRIVER_MAP 行为：kind 直接给出但无 driver_classes
        if plugin:
            warnings.warn(
                f"驱动 kind={driver!r} 未声明 driver_classes，无法保证类名一致性",
                UserWarning,
                stacklevel=2,
            )
            return driver, plugin.kind
        available = self.list_kinds()
        raise ValueError(
            f"不支持的驱动: {driver!r}，可选 kind: {available} "
            f"或类名: {list(self._by_driver_class)}"
        )

    def build(self, kind: str, **kwargs: Any) -> DriverAdapter:
        """根据 kind 构造驱动实例。"""
        plugin = self._by_kind.get(kind.lower())
        if not plugin:
            raise ValueError(
                f"不支持的数据源类型: {kind!r}，已注册: {self.list_kinds()}"
            )
        return plugin.factory(**kwargs)


# ----------------------------------------------------------------------
# 内置驱动插件工厂
# ----------------------------------------------------------------------

def _mysql_plugin() -> DriverPlugin:
    from .mysql_driver import MysqlDriver

    return DriverPlugin(
        kind="mysql",
        driver_classes=(
            "com.mysql.cj.jdbc.Driver",
            "com.mysql.jdbc.Driver",
        ),
        url_sample=(
            "jdbc:mysql://host:port/database?"
            "characterEncoding=utf8mb4&serverTimezone=Asia/Shanghai"
        ),
        factory=lambda **_kw: MysqlDriver(),
        package_name="open-db-mcp",
    )


def _oracle_plugin() -> DriverPlugin:
    from .oracle_driver import OracleDriver

    return DriverPlugin(
        kind="oracle",
        driver_classes=("oracle.jdbc.OracleDriver",),
        url_sample=(
            "jdbc:oracle:thin:@//host:port/service_name "
            "或 jdbc:oracle:thin:@host:port:SID"
        ),
        factory=lambda **_kw: OracleDriver(),
        package_name="open-db-mcp",
    )


def _oracle_jdbc_plugin() -> DriverPlugin:
    """Oracle JDBC 驱动插件（JayDeBeApi，Oracle 11g 兼容）。"""
    from .oracle_jdbc_driver import OracleJdbcDriver

    def _factory(**kwargs: Any) -> OracleJdbcDriver:
        return OracleJdbcDriver(jar_path=kwargs.get("oracle_jdbc_jar_path", ""))

    return DriverPlugin(
        kind="oracle_jdbc",
        driver_classes=("oracle.jdbc.OracleDriver",),
        url_sample=(
            "jdbc:oracle:thin:@//host:port/service_name "
            "或 jdbc:oracle:thin:@host:port:SID"
        ),
        factory=_factory,
        package_name="open-db-mcp",
    )


def _dm_plugin() -> DriverPlugin:
    from .dm_driver import DmDriver

    def _factory(**kwargs: Any) -> DmDriver:
        return DmDriver(jar_path=kwargs.get("dm_jar_path", ""))

    return DriverPlugin(
        kind="dm",
        driver_classes=("dm.jdbc.driver.DmDriver",),
        url_sample="jdbc:dm://host:port/database",
        factory=_factory,
        package_name="open-db-mcp",
    )


def _postgres_plugin() -> DriverPlugin:
    from .postgres_driver import PostgresDriver

    return DriverPlugin(
        kind="postgres",
        driver_classes=(
            "org.postgresql.Driver",
            "com.vastbase.jdbc.Driver",   # 海量数据库 Vastbase
            "org.opengauss.Driver",      # openGauss
        ),
        url_sample="jdbc:postgresql://host:port/database?sslmode=disable",
        factory=lambda **_kw: PostgresDriver(),
        package_name="open-db-mcp",
    )


def _vastbase_plugin() -> DriverPlugin:
    """Vastbase（海量数据库）别名插件，复用 PostgresDriver。"""
    from .postgres_driver import PostgresDriver

    return DriverPlugin(
        kind="vastbase",
        driver_classes=("com.vastbase.jdbc.Driver",),
        url_sample="jdbc:vastbase://host:port/database",
        factory=lambda **_kw: PostgresDriver(),
        package_name="open-db-mcp",
    )


def _opengauss_plugin() -> DriverPlugin:
    """openGauss 别名插件，复用 PostgresDriver。"""
    from .postgres_driver import PostgresDriver

    return DriverPlugin(
        kind="opengauss",
        driver_classes=("org.opengauss.Driver",),
        url_sample="jdbc:opengauss://host:port/database",
        factory=lambda **_kw: PostgresDriver(),
        package_name="open-db-mcp",
    )


def _sqlite_plugin() -> DriverPlugin:
    from .sqlite_driver import SqliteDriver

    return DriverPlugin(
        kind="sqlite",
        driver_classes=("org.sqlite.JDBC",),
        url_sample="jdbc:sqlite:/path/to/database.db",
        factory=lambda **_kw: SqliteDriver(),
        package_name="open-db-mcp",
    )


# ----------------------------------------------------------------------
# 单例
# ----------------------------------------------------------------------

_global_registry: DriverPluginRegistry | None = None


def get_driver_registry() -> DriverPluginRegistry:
    """获取全局驱动插件注册表（懒加载 + entry_points 扫描）。"""
    global _global_registry
    if _global_registry is None:
        reg = DriverPluginRegistry()
        # 先注册内置驱动（保证即使 entry_points 失败也有最小可用集合）
        reg.register(_mysql_plugin())
        reg.register(_oracle_plugin())
        reg.register(_oracle_jdbc_plugin())  # Oracle 11g JDBC 驱动
        reg.register(_dm_plugin())
        # PostgreSQL / Vastbase / openGauss 是可选依赖：psycopg2 未安装则跳过
        try:
            import psycopg2  # noqa: F401
            reg.register(_postgres_plugin())
            reg.register(_vastbase_plugin())
            reg.register(_opengauss_plugin())
        except ImportError:
            log.debug("psycopg2 未安装，跳过 postgres/vastbase/opengauss 驱动注册")
        # SQLite 是 stdlib，默认注册
        reg.register(_sqlite_plugin())
        # 再扫描第三方 entry_points（可覆盖内置实现）
        try:
            reg.load_plugins()
        except Exception as exc:
            log.warning("扫描驱动 entry_points 失败（仅使用内置驱动）: %s", exc)
        _global_registry = reg
    return _global_registry


def reset_driver_registry() -> None:
    """测试用：清空全局注册表。"""
    global _global_registry
    _global_registry = None
