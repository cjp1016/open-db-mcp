"""open-db-mcp 作为模块运行的入口（`python -m open_db_mcp` 与 PyInstaller EXE）。

注意：使用绝对导入而非相对导入——PyInstaller onefile 模式下
__main__.py 被打包为顶层脚本，相对导入会失败。
"""

from __future__ import annotations

import sys

try:
    from open_db_mcp.cli import app
except ImportError:
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
    from open_db_mcp.cli import app

if __name__ == "__main__":
    # Typer app() 默认 no_args_is_help=True，无参时显示帮助并退出
    app()
