#!/usr/bin/env python3
"""open-db-mcp stdio 启动器。

MCP 客户端（Claude Desktop / Cursor / Qoder）调用本脚本。
脚本负责：
  1. 注入环境变量（如 MCP_JDBC_PROPERTIES_PATH）
  2. 转发 stdin/stdout 给真实的 open-db-mcp
  3. 隔离 Java JVM / Python 虚拟环境

也可作为 PyInstaller 单文件产物的入口。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def resolve_executable() -> list[str]:
    """找到可执行的 open-db-mcp。优先级：
       1. 与本 wrapper 同目录的 open-db-mcp（二进制发布）
       2. PATH 中的 open-db-mcp（pip 安装）
       3. python -m open_db_mcp.server（开发模式）
    """
    here = Path(__file__).resolve().parent
    candidates = [
        here / ("open-db-mcp.exe" if os.name == "nt" else "open-db-mcp"),
        Path(shutil.which("open-db-mcp") or ""),
    ]
    for c in candidates:
        if c and c.is_file():
            return [str(c)]
    # 开发模式
    return [sys.executable, "-m", "open_db_mcp.server"]


def main() -> int:
    exe = resolve_executable()
    # stdio 转发：stdin/stdout/stderr 透传，子进程即 MCP server
    try:
        return subprocess.call(exe, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
    except FileNotFoundError as exc:
        print(f"[ERR] 找不到可执行文件: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
