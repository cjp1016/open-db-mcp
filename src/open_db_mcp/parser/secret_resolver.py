"""密码解析器：支持多种安全引用方式。

支持的密码格式：
- 明文密码：直接写密码字符串
- 环境变量引用：`env:VAR_NAME` 或 `${VAR_NAME}`
- 系统密钥环引用：`keyring:SERVICE:ACCOUNT`（需安装 keyring 包）
- 命令执行引用：`cmd:shell_command`（输出作为密码）
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass

_ENV_BRACE_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")
_ENV_PREFIX_RE = re.compile(r"^env:([A-Z_][A-Z0-9_]*)$")
_KEYRING_RE = re.compile(r"^keyring:([^:]+):(.+)$")
_CMD_PREFIX_RE = re.compile(r"^cmd:(.+)$")


@dataclass(frozen=True)
class ResolvedSecret:
    value: str
    source: str

    def describe(self) -> str:
        return self.source


def resolve_secret(raw: str) -> ResolvedSecret:
    """解析密码/密钥字符串，返回实际值和来源描述。

    Args:
        raw: 原始密码字符串，可能是明文或引用格式。

    Returns:
        ResolvedSecret: 解析后的值和来源描述。

    Raises:
        ValueError: 引用的环境变量不存在或命令执行失败。
    """
    if not raw:
        return ResolvedSecret(value=raw, source="empty")

    m = _ENV_PREFIX_RE.match(raw)
    if m:
        var_name = m.group(1)
        val = os.environ.get(var_name)
        if val is None:
            raise ValueError(f"环境变量 {var_name!r} 未设置（密码引用 env:{var_name} 失败）")
        return ResolvedSecret(value=val, source=f"env:{var_name}")

    m = _KEYRING_RE.match(raw)
    if m:
        service, account = m.group(1), m.group(2)
        val = _resolve_keyring(service, account)
        return ResolvedSecret(value=val, source=f"keyring:{service}:{account}")

    m = _CMD_PREFIX_RE.match(raw)
    if m:
        cmd = m.group(1)
        val = _resolve_cmd(cmd)
        return ResolvedSecret(value=val, source=f"cmd:{cmd}")

    if _ENV_BRACE_RE.search(raw):
        val = _expand_env_braces(raw)
        return ResolvedSecret(value=val, source="env:interpolated")

    return ResolvedSecret(value=raw, source="plaintext")


def is_secret_reference(raw: str) -> bool:
    """判断字符串是否为密钥引用（非明文）。"""
    if not raw:
        return False
    return bool(
        _ENV_PREFIX_RE.match(raw)
        or _KEYRING_RE.match(raw)
        or _CMD_PREFIX_RE.match(raw)
        or _ENV_BRACE_RE.search(raw)
    )


def _expand_env_braces(text: str) -> str:
    def _replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        val = os.environ.get(var_name)
        if val is None:
            raise ValueError(f"环境变量 {var_name!r} 未设置（密码引用 ${{{var_name}}} 失败）")
        return val
    return _ENV_BRACE_RE.sub(_replacer, text)


def _resolve_keyring(service: str, account: str) -> str:
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError:
        raise ValueError(
            "keyring 包未安装，无法使用 keyring: 引用。"
            " 请运行: pip install keyring"
        ) from None
    pwd = keyring.get_password(service, account)
    if pwd is None:
        raise ValueError(
            f"keyring 中未找到密码：service={service!r}, account={account!r}"
        )
    return pwd


def _resolve_cmd(cmd: str) -> str:
    try:
        args = shlex.split(cmd)
    except ValueError as e:
        raise ValueError(f"cmd: 引用命令解析失败: {e}") from e
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except subprocess.CalledProcessError as e:
        raise ValueError(
            f"cmd: 引用命令执行失败 (exit={e.returncode}): {e.stderr.strip()}"
        ) from e
    except FileNotFoundError:
        raise ValueError(f"cmd: 引用命令不存在: {args[0]!r}") from None
    except subprocess.TimeoutExpired:
        raise ValueError(f"cmd: 引用命令执行超时 (10s): {cmd!r}") from None
    return result.stdout.strip()
