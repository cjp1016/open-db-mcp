"""数据源注册器：管理多数据源连接池（DBUtils.PooledDB）。

支持：
1. 预设置：从 datasources.json 批量加载（load_from_json）
2. 动态调整：运行时 add / update / remove 单个数据源
3. 白名单叠加：文件白名单 + 内存覆盖层，动态数据源也能跑 DML
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from dbutils.pooled_db import PooledDB

from .drivers.base import DriverAdapter
from .drivers.factory import build_driver
from .parser.jdbc_properties import (
    DataSourceConf,
    build_data_source_conf,
    normalize_driver,
)
from .safety.whitelist import WhitelistRule, whitelist_from_dict

log = logging.getLogger("open-db-mcp.registry")


class DataSourceRegistry:
    """管理多数据源的连接池、驱动与白名单。"""

    def __init__(self, pool_min: int, pool_max: int) -> None:
        self._pool_min = pool_min
        self._pool_max = pool_max
        self._confs: dict[str, DataSourceConf] = {}
        self._pools: dict[str, PooledDB] = {}
        self._drivers: dict[str, DriverAdapter] = {}
        self._active: str | None = None
        # 动态注册的 dm 数据源需要 jar 路径，由 server 启动时设置
        self._dm_jar_path: str = ""
        self._oracle_jdbc_jar_path: str = ""
        # 白名单：base 来自 whitelist.json，overrides 来自动态注册
        self._whitelist_base: dict[str, dict] = {}
        self._whitelist_overrides: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # 配置注入
    # ------------------------------------------------------------------

    def set_dm_jar_path(self, path: str) -> None:
        """设置达梦 JDBC jar 路径，供后续动态注册 dm 数据源使用。"""
        self._dm_jar_path = path

    def set_oracle_jdbc_jar_path(self, path: str) -> None:
        """设置 Oracle JDBC jar 路径，供后续动态注册 oracle_jdbc 数据源使用。"""
        self._oracle_jdbc_jar_path = path

    def set_whitelist_base(
        self, cfg: dict[str, dict] | dict[str, WhitelistRule] | None
    ) -> None:
        """设置来自 whitelist.json 的基础白名单。

        接受 dict 格式或 WhitelistRule 格式，内部统一存为 dict 以便合并。
        """
        if cfg is None:
            self._whitelist_base = {}
            return
        converted: dict[str, dict] = {}
        for jndi, rule in cfg.items():
            if isinstance(rule, WhitelistRule):
                converted[jndi] = {
                    "read": {
                        "allowed_tables": list(rule.read.allowed_tables),
                        "allowed_columns": list(rule.read.allowed_columns) if rule.read.allowed_columns is not None else None,
                        "forbidden_columns": list(rule.read.forbidden_columns),
                        "max_rows": rule.read.max_rows,
                    },
                    "write": {
                        "allowed_tables": list(rule.write.allowed_tables),
                        "allowed_columns": list(rule.write.allowed_columns) if rule.write.allowed_columns is not None else None,
                        "forbidden_columns": list(rule.write.forbidden_columns),
                        "max_affected_rows": rule.write.max_affected_rows,
                        "require_where": rule.write.require_where,
                    },
                    "ddl": {
                        "allowed": rule.ddl.allowed,
                        "allowed_tables": list(rule.ddl.allowed_tables),
                    },
                }
            else:
                converted[jndi] = dict(rule)
        self._whitelist_base = converted

    def set_whitelist_for(self, jndi: str, rule: dict) -> None:
        """为指定数据源设置白名单规则（覆盖式）。"""
        self._whitelist_overrides[jndi] = rule

    def remove_whitelist_for(self, jndi: str) -> None:
        """移除指定数据源的白名单覆盖（不影响 base）。"""
        self._whitelist_overrides.pop(jndi, None)

    def get_whitelist(self) -> dict[str, WhitelistRule]:
        """返回合并后的白名单：base ∪ overrides（overrides 优先）。"""
        merged: dict[str, dict] = dict(self._whitelist_base)
        for jndi, rule in self._whitelist_overrides.items():
            if jndi in merged:
                combined = dict(merged[jndi])
                combined.update(rule)
                merged[jndi] = combined
            else:
                merged[jndi] = rule
        return whitelist_from_dict(merged)

    # ------------------------------------------------------------------
    # 批量加载（预设置）
    # ------------------------------------------------------------------

    def load_from_json(
        self, path: str | Path, dm_jar_path: str = "", oracle_jdbc_jar_path: str = ""
    ) -> list[str]:
        """从 datasources.json 加载所有数据源。

        单个数据源注册失败不影响其他数据源。失败原因会写日志，
        调用方可用 registry.list() 检查实际注册了哪些。
        """
        if dm_jar_path:
            self._dm_jar_path = dm_jar_path
        if oracle_jdbc_jar_path:
            self._oracle_jdbc_jar_path = oracle_jdbc_jar_path

        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"datasources.json 不存在: {p}")

        with p.open("r", encoding="utf-8") as f:
            raw: dict[str, dict[str, Any]] = json.load(f)

        loaded: list[str] = []
        for jndi, cfg in raw.items():
            try:
                self._register_one(jndi, cfg)
                loaded.append(jndi)
            except Exception as exc:
                log.warning("数据源 %s 注册失败: %s", jndi, exc)
        return loaded

    # ------------------------------------------------------------------
    # 动态注册
    # ------------------------------------------------------------------

    def add(
        self,
        *,
        jndi: str,
        driver: str,
        url: str,
        user: str,
        password: str,
        pool_max: int | None = None,
        max_affected_rows: int | None = None,
        allowed_tables: list[str] | None = None,
        forbidden_columns: list[str] | None = None,
    ) -> DataSourceConf:
        """运行时注册新数据源。

        Args:
            jndi: 唯一标识，不能与已存在的重名。
            driver: kind 简写（'mysql' / 'oracle' / 'dm'）或完整 JDBC 类名。
            url: JDBC URL。
            user: 用户名。
            password: 密码。
            pool_max: 连接池上限，None 则使用全局 pool_max。
            max_affected_rows: DML 行数上限，写入白名单覆盖层。
            allowed_tables: 允许操作的表（白名单），None 表示无白名单（DML 会被拒绝）。
            forbidden_columns: 禁止写入的列。

        Raises:
            ValueError: jndi 已存在或 driver 不支持。
        """
        if jndi in self._confs:
            raise ValueError(
                f"数据源 {jndi!r} 已存在，请改用 update_datasource"
            )
        driver_cls, kind = normalize_driver(driver)
        cfg: dict[str, Any] = {
            "driver": driver_cls,
            "url": url,
            "user": user,
            "password": password,
            "kind": kind,
        }
        if pool_max is not None:
            cfg["pool_max"] = pool_max
        conf = self._register_one(jndi, cfg)

        # 注入白名单覆盖层（保证 DML 可用）
        rule: dict[str, Any] = {}
        if allowed_tables is not None:
            rule["allowed_tables"] = list(allowed_tables)
        if forbidden_columns is not None:
            rule["forbidden_columns"] = list(forbidden_columns)
        if max_affected_rows is not None:
            rule["max_affected_rows"] = int(max_affected_rows)
        if rule:
            self._whitelist_overrides[jndi] = rule
        return conf

    def update(
        self,
        jndi: str,
        *,
        url: str | None = None,
        user: str | None = None,
        password: str | None = None,
        pool_max: int | None = None,
    ) -> DataSourceConf:
        """更新已存在数据源的连接参数（重建连接池，保留白名单）。

        未传入的字段保留原值。
        """
        if jndi not in self._confs:
            raise KeyError(f"未注册的数据源: {jndi}")
        old = self._confs[jndi]
        new_url = url or old.url
        new_user = user or old.user
        new_password = password or old.password
        # 先移除连接池，但保留白名单覆盖
        self._close_pool(jndi)
        self._confs.pop(jndi, None)
        self._drivers.pop(jndi, None)
        self._pools.pop(jndi, None)

        cfg: dict[str, Any] = {
            "driver": old.driver,
            "url": new_url,
            "user": new_user,
            "password": new_password,
            "kind": old.kind,
        }
        if pool_max is not None:
            cfg["pool_max"] = pool_max
        return self._register_one(jndi, cfg)

    def remove(self, jndi: str, drop_whitelist: bool = True) -> None:
        """注销数据源：关闭连接池并清理内部状态。

        Args:
            jndi: 数据源名称。
            drop_whitelist: 是否同时移除白名单覆盖层。
        """
        if jndi not in self._confs:
            raise KeyError(f"未注册的数据源: {jndi}")
        if self._active == jndi:
            self._active = None
        self._close_pool(jndi)
        self._confs.pop(jndi, None)
        self._drivers.pop(jndi, None)
        self._pools.pop(jndi, None)
        if drop_whitelist:
            self._whitelist_overrides.pop(jndi, None)

    def exists(self, jndi: str) -> bool:
        return jndi in self._confs

    # ------------------------------------------------------------------
    # 单数据源注册核心逻辑（公共给 load_from_json / add / update 复用）
    # ------------------------------------------------------------------

    def _register_one(
        self, jndi: str, cfg: dict[str, Any]
    ) -> DataSourceConf:
        """根据配置字典构建 driver、连接池并登记到内部映射。"""
        driver_cls = cfg.get("driver", "")
        # 通过插件注册表校验驱动（支持第三方扩展），kind 由 build_data_source_conf 内部解析
        try:
            normalize_driver(driver_cls)
        except ValueError as exc:
            raise ValueError(f"驱动不支持或为空: {driver_cls!r}") from exc
        try:
            conf = build_data_source_conf(
                jndi=jndi,
                driver=driver_cls,
                url=cfg["url"],
                user=cfg["user"],
                password=cfg["password"],
                resolve=True,
            )
        except KeyError as exc:
            raise ValueError(f"缺少必填字段 {exc}") from exc

        driver = build_driver(
            conf,
            dm_jar_path=self._dm_jar_path,
            oracle_jdbc_jar_path=self._oracle_jdbc_jar_path,
        )
        creator = _creator(driver, conf)
        dbapi = getattr(driver, "dbapi", None)
        if dbapi is not None and not hasattr(creator, "dbapi"):
            creator.dbapi = dbapi
        failures = None
        if dbapi is not None:
            failures = (
                getattr(dbapi, "OperationalError", Exception),
                getattr(dbapi, "InterfaceError", Exception),
                getattr(dbapi, "InternalError", Exception),
            )
        ds_pool_max = cfg.get("pool_max", self._pool_max)
        pool = PooledDB(
            creator=creator,
            mincached=0,
            maxcached=ds_pool_max,
            maxconnections=ds_pool_max,
            blocking=True,
            ping=1,
            failures=failures,
        )
        self._confs[jndi] = conf
        self._drivers[jndi] = driver
        self._pools[jndi] = pool
        return conf

    def _close_pool(self, jndi: str) -> None:
        pool = self._pools.get(jndi)
        if pool is None:
            return
        try:
            close = getattr(pool, "close", None)
            if callable(close):
                close()
        except Exception as exc:
            log.warning("关闭数据源 %s 连接池失败: %s", jndi, exc)

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    def get(self, jndi: str) -> PooledDB:
        pool = self._pools.get(jndi)
        if not pool:
            raise KeyError(f"未注册的数据源: {jndi}")
        return pool

    def conf(self, jndi: str) -> DataSourceConf:
        if jndi not in self._confs:
            raise KeyError(f"未注册的数据源: {jndi}")
        return self._confs[jndi]

    def driver(self, jndi: str) -> DriverAdapter:
        if jndi not in self._drivers:
            raise KeyError(f"未注册的数据源: {jndi}")
        return self._drivers[jndi]

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "jndi": j,
                "kind": self._confs[j].kind,
                "url": self._confs[j].url,
                "user": self._confs[j].user,
                "pool": self.pool_stats(j),
                "dynamic": j in self._whitelist_overrides,
            }
            for j in self._confs
        ]

    def pool_stats(self, jndi: str) -> dict[str, int]:
        pool = self.get(jndi)
        idle = len(getattr(pool, "_idle_cache", []) or [])
        return {
            "idle": idle,
            "max": self._pool_max,
            "min": self._pool_min,
        }

    def set_active(self, jndi: str) -> str:
        """设置当前活跃数据源，校验 jndi 必须已注册。"""
        if jndi not in self._confs:
            raise KeyError(
                f"未注册的数据源: {jndi}，可用: {list(self._confs.keys())}"
            )
        self._active = jndi
        return jndi

    def get_active(self) -> str | None:
        """返回当前活跃数据源 JNDI，未设置时返回 None。"""
        return self._active

    def resolve(self, jndi: str | None) -> str:
        """解析数据源：传入 None/空串则回退到活跃数据源，否则校验存在性。"""
        if not jndi:
            if not self._active:
                raise KeyError(
                    "未指定 data_source 且未设置当前活跃数据源，"
                    "请先调用 use_datasource 切换"
                )
            return self._active
        if jndi not in self._confs:
            raise KeyError(
                f"未注册的数据源: {jndi}，可用: {list(self._confs.keys())}"
            )
        return jndi

    def health(self, jndi: str) -> dict[str, Any]:
        """探测连通性：拿一条连接跑 driver 指定的 ping SQL。"""
        pool = self.get(jndi)
        driver = self.driver(jndi)
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(driver.ping_sql())
                row = cur.fetchone()
            ok = row is not None and (row[0] == 1 or row[0] == "1")
            return {
                "ok": ok,
                "kind": driver.name,
                "pool": self.pool_stats(jndi),
            }
        finally:
            conn.close()


def _creator(driver: DriverAdapter, conf: DataSourceConf):
    """PooledDB creator 闭包。"""
    def _create():
        return driver.connect(conf)
    return _create
