"""JSON 持久化基类与工具函数。

提供统一的 JSON 文件读写能力，所有具体仓储继承 BaseJsonRepository
并实现自己的路径解析逻辑。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..config import Settings

log = logging.getLogger("open-db-mcp.repositories")


def load_json_object(path: Path) -> dict[str, Any]:
    """加载 JSON 文件为 dict，文件不存在或格式错误时返回空 dict。"""
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log.warning("加载 %s 失败: %s，将覆盖为空对象", path, exc)
        return {}


def write_json_object(path: Path, data: dict[str, Any]) -> None:
    """将 dict 以 UTF-8 + 缩进 2 写入 JSON 文件，自动创建父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class BaseJsonRepository:
    """JSON 文件持久化基类。

    子类需实现 `_resolve_path()` 返回具体的 JSON 文件路径。
    """

    def __init__(self, settings: Settings, package_root: Path) -> None:
        self._settings = settings
        self._package_root = package_root

    def _resolve_path(self, key: str) -> Path:
        """根据 settings.resolved_paths 的 key 解析绝对路径。"""
        paths = self._settings.resolved_paths(self._package_root)
        return Path(paths[key])

    def _load(self, path: Path) -> dict[str, Any]:
        return load_json_object(path)

    def _write(self, path: Path, data: dict[str, Any]) -> None:
        write_json_object(path, data)
