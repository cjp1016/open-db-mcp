"""open-db-mcp CLI 入口。"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import typer
import yaml

from . import __version__
from .config import get_package_root, get_settings

app = typer.Typer(
    name="open-db-mcp",
    help="Oracle / 达梦 / MySQL 数据库 MCP 工具（stdio 本地版）",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def init(
    force: bool = typer.Option(False, "--force", help="覆盖已有 config.yaml"),
) -> None:
    """首次运行：引导生成 ~/.open-db-mcp/config.yaml + whitelist.json。

    数据源配置请直接编辑项目目录下的 config/datasources.json。
    """
    settings = get_settings()
    cfg_dir = Path(settings.user_config_dir)
    cfg_dir.mkdir(parents=True, exist_ok=True)

    cfg_path = cfg_dir / "config.yaml"
    if cfg_path.exists() and not force:
        typer.echo(f"已存在 {cfg_path}，跳过生成（用 --force 覆盖）")
    else:
        cfg_data = {
            "dmJdbcJarPath": "libs/DmJdbcDriver18.jar",
            "poolMax": settings.pool_max,
            "maxAffectedRows": settings.max_affected_rows,
            "auditLogPath": str(cfg_dir / "audit.jsonl"),
        }
        cfg_path.write_text(
            yaml.safe_dump(cfg_data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        typer.echo(f"已生成: {cfg_path}")

    wl_path = cfg_dir / "whitelist.json"
    if not wl_path.exists():
        default_wl = {
            "LOCAL_MYSQL": {"allowed_tables": [], "max_affected_rows": 100},
            "ORACLE_SAMPLE":  {"allowed_tables": [], "max_affected_rows": 100},
        }
        wl_path.write_text(
            json.dumps(default_wl, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        typer.echo(f"已生成: {wl_path}（请按需编辑表/列白名单）")

    pkg_root = get_package_root()
    ds_src = pkg_root / "config" / "datasources.json"
    ds_dst = cfg_dir / "datasources.json"
    if ds_src.is_file() and not ds_dst.exists():
        shutil.copy(ds_src, ds_dst)
        typer.echo(f"已复制数据源配置: {ds_dst}（请编辑填入实际密码）")

    typer.echo("下一步：")
    typer.echo(f"  1. 编辑 {ds_dst} 配置数据源连接信息（driver / url / user / password）")
    typer.echo(f"  2. 编辑 {wl_path} 配置表/列白名单")
    typer.echo(f"  3. 运行 `open-db-mcp doctor` 健康检查")
    typer.echo(f"  4. 在 MCP 客户端配置 command = {sys.argv[0]}")


@app.command()
def run() -> None:
    """启动 stdio MCP server（被 MCP 客户端调用）。"""
    from .server import main

    main()


@app.command()
def doctor() -> None:
    """健康检查：解析 datasources.json、ping 每个数据源、检查 JVM 状态。"""
    from .registry import DataSourceRegistry

    settings = get_settings()
    pkg_root = get_package_root()
    paths = settings.resolved_paths(pkg_root)

    ds_cfg = paths["datasources_cfg"]
    if not ds_cfg or not Path(ds_cfg).is_file():
        typer.echo(f"[ERR] datasources.json 不存在: {ds_cfg!r}")
        typer.echo("请运行 `open-db-mcp init` 或设置 MCP_DATASOURCES_CFG_PATH")
        raise typer.Exit(code=1)

    typer.echo(f"[OK] datasources.json: {ds_cfg}")

    registry = DataSourceRegistry(settings.pool_min, settings.pool_max)
    try:
        loaded = registry.load_from_json(
            ds_cfg,
            dm_jar_path=paths["dm_jdbc_jar"],
            oracle_jdbc_jar_path=paths.get("oracle_jdbc_jar", ""),
        )
    except Exception as exc:
        typer.echo(f"[ERR] datasources.json 加载失败: {exc}")
        raise typer.Exit(code=1) from exc

    typer.echo(f"[OK] 加载完成: {loaded}")

    for jndi in loaded:
        try:
            h = registry.health(jndi)
            typer.echo(f"  - {jndi}: {h}")
        except Exception as exc:
            typer.echo(f"  - {jndi}: [ERR] {exc}")


@app.command()
def version() -> None:
    """打印版本号。"""
    typer.echo(f"open-db-mcp {__version__}")


@app.command()
def package(
    target: str = typer.Option(
        "current", help="current / windows / macos / linux"
    ),
) -> None:
    """PyInstaller 打包（需要 `pip install '.[packaging]'`）。"""
    pkg_root = get_package_root()
    spec_name = {
        "current": "build_current.spec",
        "windows": "build_windows.spec",
        "macos": "build_macos.spec",
        "linux": "build_linux.spec",
    }.get(target)
    if not spec_name:
        typer.echo(f"[ERR] 不支持的 target: {target}")
        raise typer.Exit(code=1)

    spec_path = pkg_root / "packaging" / spec_name
    if not spec_path.is_file():
        typer.echo(f"[ERR] spec 不存在: {spec_path}")
        raise typer.Exit(code=1)

    import subprocess

    cmd = ["pyinstaller", str(spec_path), "--clean", "--noconfirm"]
    typer.echo(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=pkg_root)
    typer.echo("[OK] 打包完成，产物在 dist/")


if __name__ == "__main__":
    app()
