"""SQL 安全层公共异常。

SafetyError 继承自 errors.SecurityError，使安全层异常纳入统一异常层级，
便于 MCP 工具层与未来 HTTP/CLI 入口统一捕获与序列化。
"""

from __future__ import annotations

from ..errors import SecurityError


class SafetyError(SecurityError):
    """白名单/影响行数/WHERE 等校验失败时抛出。

    继承 SecurityError 以融入统一异常层级（errors.py）：
        SecurityError
        └── SafetyError（安全层细粒度校验）
    """

    code = "SAFETY_VIOLATION"
    status_code = 403

    def __init__(self, message: str, code: str = "SAFETY_VIOLATION") -> None:
        super().__init__(message)
        self.code = code
