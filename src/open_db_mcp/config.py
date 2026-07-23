"""open-db-mcp 配置层。

加载优先级（高 → 低）：
    1. 显式环境变量 MCP_*
    2. 用户私有配置 ~/.open-db-mcp/config.yaml（init 时生成）
    3. 包内置默认配置
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_user_config_dir() -> str:
    return str(Path.home() / ".open-db-mcp")


class Settings(BaseSettings):
    """open-db-mcp 运行时配置。"""

    model_config = SettingsConfigDict(
        env_prefix="MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 路径 ----
    user_config_dir: str = Field(default_factory=_default_user_config_dir)
    dm_jdbc_jar_path: str = "DmJdbcDriver18.jar"
    oracle_jdbc_jar_path: str = "ojdbc8.jar"  # Oracle 11g JDBC 驱动
    whitelist_path: str = ""
    datasources_cfg_path: str = ""

    # ---- 池与限流 ----
    pool_min: int = 1
    pool_max: int = 8
    max_affected_rows: int = 1000
    default_query_max_rows: int = 1000
    query_timeout_sec: int = 30

    # ---- 慢查询分析 ----
    slow_query_threshold_ms: int = 1000
    slow_query_log_path: str = ""
    slow_query_max_records: int = 500

    # ---- 审计 ----
    audit_log_path: str = ""
    audit_enabled: bool = True

    def __init__(self, **kwargs: Any) -> None:
        """加载优先级：显式 kwargs > 环境变量 > yaml > 字段默认值。"""
        yaml_data = self._load_yaml_defaults()
        for camel_key, value in yaml_data.items():
            snake_key = _CAMEL_TO_SNAKE.get(camel_key, camel_key)
            env_var = f"MCP_{snake_key.upper()}"
            if env_var not in os.environ and snake_key not in kwargs:
                kwargs[snake_key] = value
        super().__init__(**kwargs)

    @staticmethod
    def _load_yaml_defaults() -> dict[str, Any]:
        cfg_dir = Path(_default_user_config_dir())
        yaml_path = cfg_dir / "config.yaml"
        if not yaml_path.is_file():
            return {}
        try:
            with yaml_path.open("r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return {}

    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        # 已通过 __init__ 完成 yaml 注入，此钩子保留为空。
        return None

    def resolved_paths(self, package_root: Path) -> dict[str, str]:
        """解析所有相对路径，返回绝对路径字典。"""
        user_dir = Path(self.user_config_dir)
        return {
            "dm_jdbc_jar": self._abs(
                self.dm_jdbc_jar_path,
                [user_dir, package_root, package_root / "libs"],
            ),
            "oracle_jdbc_jar": self._abs(
                self.oracle_jdbc_jar_path,
                [user_dir, package_root, package_root / "libs"],
            ),
            "whitelist": self._abs(
                self.whitelist_path or "config/whitelist.json",
                [user_dir, package_root],
            ),
            "datasources_cfg": self._abs(
                self.datasources_cfg_path or "datasources.json",
                [Path.cwd(), user_dir],
            ),
            "audit_log": self.audit_log_path or str(
                user_dir / "audit.jsonl"
            ),
            "slow_query_log": self.slow_query_log_path or str(
                Path.cwd() / "slow_queries.jsonl"
            ),
        }

    @staticmethod
    def _abs(path_str: str, candidates: list[Path]) -> str:
        if not path_str:
            return ""
        p = Path(path_str)
        if p.is_absolute():
            return str(p)
        for base in candidates:
            cand = (base / p).resolve()
            if cand.exists():
                return str(cand)
        return str((candidates[0] / p).resolve()) if candidates else str(p)


_CAMEL_TO_SNAKE = {
    "dmJdbcJarPath": "dm_jdbc_jar_path",
    "oracleJdbcJarPath": "oracle_jdbc_jar_path",
    "whitelistPath": "whitelist_path",
    "datasourcesCfgPath": "datasources_cfg_path",
    "userConfigDir": "user_config_dir",
    "poolMin": "pool_min",
    "poolMax": "pool_max",
    "maxAffectedRows": "max_affected_rows",
    "defaultQueryMaxRows": "default_query_max_rows",
    "queryTimeoutSec": "query_timeout_sec",
    "auditLogPath": "audit_log_path",
    "auditEnabled": "audit_enabled",
    "slowQueryThresholdMs": "slow_query_threshold_ms",
    "slowQueryLogPath": "slow_query_log_path",
    "slowQueryMaxRecords": "slow_query_max_records",
}


def get_package_root() -> Path:
    """获取包内资源根目录（兼容 PyInstaller 单文件模式）。"""
    import sys

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parents[2]


# 延迟构造：先 import 模块，再 get_settings()
_cached: Settings | None = None


def get_settings() -> Settings:
    global _cached
    if _cached is None:
        _cached = Settings()
    return _cached


def reset_settings_cache() -> None:
    """测试用：清空 settings 单例。"""
    global _cached
    _cached = None
