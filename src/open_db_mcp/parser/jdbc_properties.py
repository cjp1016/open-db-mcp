"""jdbc.properties 解析器。

格式约定（Kettle simple-jndi）：
    MY_DS/type=javax.sql.DataSource
    MY_DS/driver=oracle.jdbc.OracleDriver
    MY_DS/url=jdbc:oracle:thin:@host:port:service
    MY_DS/user=...
    MY_DS/password=...
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..drivers.registry import get_driver_registry
from .secret_resolver import ResolvedSecret, is_secret_reference, resolve_secret

Kind = Literal["oracle", "oracle_jdbc", "dm", "mysql", "postgres", "vastbase", "opengauss", "sqlite"]

# 保留 _DRIVER_MAP / _KIND_TO_DEFAULT_DRIVER 作为向后兼容引用，
# 实际校验逻辑委托给 DriverPluginRegistry（支持第三方插件扩展）。
_DRIVER_MAP: dict[str, Kind] = {
    "oracle.jdbc.OracleDriver": "oracle",
    "dm.jdbc.driver.DmDriver": "dm",
    "com.mysql.cj.jdbc.Driver": "mysql",
    "com.mysql.jdbc.Driver": "mysql",
    "org.postgresql.Driver": "postgres",
    "com.vastbase.jdbc.Driver": "vastbase",
    "org.opengauss.Driver": "opengauss",
    "org.sqlite.JDBC": "sqlite",
}

_KIND_TO_DEFAULT_DRIVER: dict[Kind, str] = {
    "oracle": "oracle.jdbc.OracleDriver",
    "oracle_jdbc": "oracle.jdbc.OracleDriver",
    "dm": "dm.jdbc.driver.DmDriver",
    "mysql": "com.mysql.cj.jdbc.Driver",
    "postgres": "org.postgresql.Driver",
    "vastbase": "com.vastbase.jdbc.Driver",
    "opengauss": "org.opengauss.Driver",
    "sqlite": "org.sqlite.JDBC",
}


def normalize_driver(driver: str) -> tuple[str, str]:
    """接受 kind 简写或完整 JDBC 驱动类名，统一返回 (类名, kind)。

    Args:
        driver: 'oracle' / 'mysql' / 'dm' 等简写，或完整类名（如 com.mysql.cj.jdbc.Driver）。

    Raises:
        ValueError: 不支持的驱动。
    """
    return get_driver_registry().normalize_driver(driver)


def list_supported_drivers() -> list[dict[str, str]]:
    """枚举所有支持的驱动（含第三方插件），用于 LLM 在对话中查阅。"""
    return get_driver_registry().list_supported_drivers()


@dataclass(frozen=True)
class DataSourceConf:
    jndi: str
    driver: str
    url: str
    user: str
    password: str
    kind: Kind
    password_source: str = "plaintext"

    @property
    def is_password_reference(self) -> bool:
        """密码是否为引用形式（非明文存储）。"""
        return self.password_source != "plaintext"

    def mask(self) -> dict:
        """脱敏快照（用于日志/审计）。"""
        return {
            "jndi": self.jndi,
            "driver": self.driver,
            "url": self.url,
            "user": self.user,
            "password": "***",
            "password_source": self.password_source,
            "kind": self.kind,
        }


def build_data_source_conf(
    jndi: str,
    driver: str,
    url: str,
    user: str,
    password: str,
    resolve: bool = True,
) -> DataSourceConf:
    """构建 DataSourceConf，可选解析密码引用。

    Args:
        jndi: 数据源名称。
        driver: 驱动类名或 kind 简写。
        url: JDBC URL。
        user: 用户名。
        password: 密码（支持 env:/keyring:/cmd:/${VAR} 引用）。
        resolve: 是否立即解析密码引用。默认为 True。
            设为 False 时，password 字段保留原始引用字符串，
            password_source 标记为引用类型。
    """
    driver_cls, kind = normalize_driver(driver)

    if resolve:
        resolved: ResolvedSecret = resolve_secret(password)
        return DataSourceConf(
            jndi=jndi,
            driver=driver_cls,
            url=url,
            user=user,
            password=resolved.value,
            kind=kind,
            password_source=resolved.source,
        )

    source = "plaintext"
    if is_secret_reference(password):
        source = "reference"
    return DataSourceConf(
        jndi=jndi,
        driver=driver_cls,
        url=url,
        user=user,
        password=password,
        kind=kind,
        password_source=source,
    )


class JdbcPropertiesParser:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def parse(self, resolve_passwords: bool = True) -> dict[str, DataSourceConf]:
        """解析 jdbc.properties。

        Args:
            resolve_passwords: 是否解析密码引用（env:/keyring:/cmd:）。
                默认为 True。设置为 False 时保留引用字符串，
                供 Registry 在连接时再解析。
        """
        if not self.path.is_file():
            raise FileNotFoundError(f"jdbc.properties 不存在: {self.path}")
        groups: dict[str, dict[str, str]] = {}
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", "!")):
                continue
            if "=" not in line:
                raise ValueError(f"非法 jdbc.properties 行: {raw!r}")
            k, v = line.split("=", 1)
            m = re.match(r"^([A-Za-z0-9_]+)/([A-Za-z0-9_]+)$", k)
            if not m:
                raise ValueError(f"非法键格式（期望 JNDI/key）: {k!r}")
            jndi, key = m.group(1), m.group(2)
            groups.setdefault(jndi, {})[key] = v.strip()
        out: dict[str, DataSourceConf] = {}
        for jndi, kv in groups.items():
            missing = {"type", "driver", "url", "user", "password"} - kv.keys()
            if missing:
                raise ValueError(f"{jndi} 缺少字段: {sorted(missing)}")
            driver = kv["driver"]
            # 委托给 DriverPluginRegistry 校验（支持第三方插件扩展）
            try:
                normalize_driver(driver)
            except ValueError as exc:
                raise ValueError(f"{jndi} 不支持的驱动: {driver}") from exc
            out[jndi] = build_data_source_conf(
                jndi=jndi,
                driver=driver,
                url=kv["url"],
                user=kv["user"],
                password=kv["password"],
                resolve=resolve_passwords,
            )
        return out
