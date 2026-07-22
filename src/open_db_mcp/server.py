"""open-db-mcp stdio MCP server 入口。

关键约束：所有日志必须走 stderr，否则会污染 stdout 上的 MCP 协议 JSON 流。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .config import get_package_root, get_settings
from .registry import DataSourceRegistry
from .safety import auditor
from .safety.whitelist import load_whitelist
from .tools import data_tools, dml_tools, ds_tools, meta_tools, query_tools


def _setup_logging() -> logging.Logger:
    """stdio 模式下日志只能走 stderr。"""
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    return logging.getLogger("open-db-mcp")


log = _setup_logging()


def build_server() -> FastMCP:
    """构造并装备 FastMCP server。"""
    settings = get_settings()
    pkg_root = get_package_root()
    paths = settings.resolved_paths(pkg_root)

    ds_cfg = paths["datasources_cfg"]
    if not ds_cfg or not Path(ds_cfg).is_file():
        log.error(
            "datasources.json 不存在或未配置: %r。请先运行 `open-db-mcp init`"
            " 或设置环境变量 MCP_DATASOURCES_CFG_PATH",
            ds_cfg,
        )
        sys.exit(2)

    log.info("加载 datasources.json: %s", ds_cfg)
    registry = DataSourceRegistry(settings.pool_min, settings.pool_max)
    registry.set_dm_jar_path(paths["dm_jdbc_jar"])
    registry.set_oracle_jdbc_jar_path(paths.get("oracle_jdbc_jar", ""))
    loaded = registry.load_from_json(
        ds_cfg,
        dm_jar_path=paths["dm_jdbc_jar"],
        oracle_jdbc_jar_path=paths.get("oracle_jdbc_jar", ""),
    )
    log.info("已注册数据源: %s", loaded)

    # 白名单：作为 base 注入 registry，动态注册的数据源会叠加到 overrides
    wl_cfg = load_whitelist(paths["whitelist"])
    registry.set_whitelist_base(wl_cfg)
    log.info("白名单已加载: %s (%d 个数据源)", paths["whitelist"], len(wl_cfg))

    # 审计
    auditor.configure(paths["audit_log"], settings.audit_enabled)
    log.info("审计日志: %s (enabled=%s)", paths["audit_log"], settings.audit_enabled)

    mcp = FastMCP("open-db-mcp")
    ds_tools.register(mcp, registry, settings)
    query_tools.register(mcp, registry, settings)
    dml_tools.register(mcp, registry, settings)
    meta_tools.register(mcp, registry, settings)
    data_tools.register(mcp, registry, settings)

    return mcp


def main() -> None:
    """stdio 入口点。"""
    mcp = build_server()
    log.info("open-db-mcp 启动（transport=stdio）")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
