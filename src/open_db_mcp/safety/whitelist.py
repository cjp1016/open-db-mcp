"""白名单配置加载与匹配。

白名单按操作类型分三层：
    read    只读查询（SELECT / WITH）
    write   数据操作（INSERT / UPDATE / DELETE / MERGE）
    ddl     数据定义（CREATE / ALTER / DROP / TRUNCATE 等）

结构：
    {
      "<JNDI>": {
        "read": {
          "allowed_tables": ["SCHEMA.TABLE", "SCHEMA.*"],
          "allowed_columns": ["ID", "NAME"],     # 可选，空/缺省=所有列可读
          "forbidden_columns": ["PASSWORD"],     # 黑名单模式（与 allowed_columns 二选一）
          "max_rows": 1000
        },
        "write": {
          "allowed_tables": ["SCHEMA.TABLE"],
          "allowed_columns": [...],              # 可选，空/缺省=所有列可写
          "forbidden_columns": ["ID"],
          "max_affected_rows": 100,
          "require_where": true
        },
        "ddl": {
          "allowed": false,
          "allowed_tables": [...]                # 仅当 allowed=true 时生效
        }
      }
    }

向后兼容：旧式扁平结构（直接写 allowed_tables / forbidden_columns / max_affected_rows）
会被自动升级为 read+write 共享配置。
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import SafetyError
from .sql_analyzer import SqlIntent


# ------------------------------------------------------------------
# 数据模型
# ------------------------------------------------------------------

@dataclass
class ReadRule:
    """读操作白名单规则。"""

    allowed_tables: list[str] = field(default_factory=list)
    allowed_columns: list[str] | None = None
    forbidden_columns: list[str] = field(default_factory=list)
    max_rows: int = 1000


@dataclass
class WriteRule:
    """写操作白名单规则。"""

    allowed_tables: list[str] = field(default_factory=list)
    allowed_columns: list[str] | None = None
    forbidden_columns: list[str] = field(default_factory=list)
    max_affected_rows: int = 100
    require_where: bool = True


@dataclass
class DdlRule:
    """DDL 白名单规则。"""

    allowed: bool = False
    allowed_tables: list[str] = field(default_factory=list)


@dataclass
class WhitelistRule:
    """单个数据源的完整白名单规则。"""

    read: ReadRule = field(default_factory=ReadRule)
    write: WriteRule = field(default_factory=WriteRule)
    ddl: DdlRule = field(default_factory=DdlRule)


# ------------------------------------------------------------------
# 加载与格式升级
# ------------------------------------------------------------------

def load_whitelist(path: str | Path) -> dict[str, WhitelistRule]:
    """加载 whitelist.json，自动升级旧式扁平格式。"""
    p = Path(path)
    if not p.is_file():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {jndi: _upgrade_rule(cfg) for jndi, cfg in raw.items()}


def find_rule(jndi: str, cfg: dict[str, WhitelistRule]) -> WhitelistRule | None:
    return cfg.get(jndi)


def whitelist_from_dict(raw: dict[str, dict]) -> dict[str, WhitelistRule]:
    """将 dict 格式的白名单转为 WhitelistRule 对象格式。"""
    return {jndi: _upgrade_rule(cfg) for jndi, cfg in raw.items()}


def whitelist_to_dict(cfg: dict[str, WhitelistRule]) -> dict[str, dict]:
    """将 WhitelistRule 对象格式转回 dict（用于持久化）。"""
    out: dict[str, dict] = {}
    for jndi, rule in cfg.items():
        out[jndi] = {
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
    return out


def _upgrade_rule(raw: dict) -> WhitelistRule:
    """将旧式扁平格式升级为分层格式。

    旧式：{"allowed_tables": [...], "forbidden_columns": [...], "max_affected_rows": 100}
    新式：{"read": {...}, "write": {...}, "ddl": {...}}
    """
    if any(k in raw for k in ("read", "write", "ddl")):
        return _parse_new_format(raw)
    return _parse_legacy_format(raw)


def _parse_new_format(raw: dict) -> WhitelistRule:
    read_cfg = raw.get("read", {}) or {}
    write_cfg = raw.get("write", {}) or {}
    ddl_cfg = raw.get("ddl", {}) or {}

    read = ReadRule(
        allowed_tables=list(read_cfg.get("allowed_tables", []) or []),
        allowed_columns=read_cfg.get("allowed_columns"),
        forbidden_columns=list(read_cfg.get("forbidden_columns", []) or []),
        max_rows=int(read_cfg.get("max_rows", 1000)),
    )
    write = WriteRule(
        allowed_tables=list(write_cfg.get("allowed_tables", []) or []),
        allowed_columns=write_cfg.get("allowed_columns"),
        forbidden_columns=list(write_cfg.get("forbidden_columns", []) or []),
        max_affected_rows=int(write_cfg.get("max_affected_rows", 100)),
        require_where=bool(write_cfg.get("require_where", True)),
    )
    ddl = DdlRule(
        allowed=bool(ddl_cfg.get("allowed", False)),
        allowed_tables=list(ddl_cfg.get("allowed_tables", []) or []),
    )
    return WhitelistRule(read=read, write=write, ddl=ddl)


def _parse_legacy_format(raw: dict) -> WhitelistRule:
    tables = list(raw.get("allowed_tables", []) or [])
    forbidden = list(raw.get("forbidden_columns", []) or [])
    max_affected = int(raw.get("max_affected_rows", 100))

    # 旧格式只有 max_affected_rows 一个上限，读/写共用同一值以保留原行为
    read = ReadRule(
        allowed_tables=tables,
        forbidden_columns=forbidden,
        max_rows=max_affected,
    )
    write = WriteRule(
        allowed_tables=tables,
        forbidden_columns=forbidden,
        max_affected_rows=max_affected,
        require_where=True,
    )
    ddl = DdlRule(allowed=False)
    return WhitelistRule(read=read, write=write, ddl=ddl)


# ------------------------------------------------------------------
# 校验入口
# ------------------------------------------------------------------

def check(intent: SqlIntent, jndi: str, cfg: dict[str, WhitelistRule]) -> None:
    """根据操作类型执行对应白名单校验。命中拒绝策略时抛 SafetyError。"""
    rule = cfg.get(jndi)
    if not rule:
        raise SafetyError(f"数据源 {jndi!r} 无白名单配置")

    if intent.dml in ("SELECT", "WITH"):
        _check_read(intent, rule)
    elif intent.dml in ("INSERT", "UPDATE", "DELETE", "MERGE"):
        _check_write(intent, rule)
    elif intent.dml == "DDL":
        _check_ddl(intent, rule)
    elif intent.dml == "PLSQL":
        _check_plsql(intent, rule)
    else:
        raise SafetyError(f"未知的 DML 类型: {intent.dml}")


def _check_read(intent: SqlIntent, rule: WhitelistRule) -> None:
    read = rule.read
    if not intent.tables:
        return
    matched = any(_match_table(t, read.allowed_tables) for t in intent.tables)
    if not matched:
        raise SafetyError(
            f"读操作：表不在白名单: {intent.tables}（允许: {read.allowed_tables}）"
        )
    if read.forbidden_columns and intent.columns:
        forbidden_upper = {c.upper() for c in read.forbidden_columns}
        for col in intent.columns:
            if col.upper() in forbidden_upper:
                raise SafetyError(f"读操作：禁止读取列: {col}")
    if read.allowed_columns is not None and intent.columns:
        allowed_upper = {c.upper() for c in read.allowed_columns}
        for col in intent.columns:
            if col.upper() not in allowed_upper:
                raise SafetyError(f"读操作：列不在白名单: {col}")


def _check_write(intent: SqlIntent, rule: WhitelistRule) -> None:
    write = rule.write
    if not intent.tables:
        raise SafetyError("无法从 SQL 中识别到表名")

    matched = any(_match_table(t, write.allowed_tables) for t in intent.tables)
    if not matched:
        raise SafetyError(
            f"写操作：表不在白名单: {intent.tables}（允许: {write.allowed_tables}）"
        )

    if write.forbidden_columns and intent.columns:
        forbidden_upper = {c.upper() for c in write.forbidden_columns}
        for col in intent.columns:
            if col.upper() in forbidden_upper:
                raise SafetyError(f"写操作：禁止写入列: {col}")

    if write.allowed_columns is not None and intent.columns:
        allowed_upper = {c.upper() for c in write.allowed_columns}
        for col in intent.columns:
            if col.upper() not in allowed_upper:
                raise SafetyError(f"写操作：列不在白名单: {col}")

    if intent.dml in ("UPDATE", "DELETE") and write.require_where and not intent.has_where:
        raise SafetyError(f"{intent.dml} 必须带 WHERE 子句")

    if intent.dml == "INSERT" and not intent.columns:
        raise SafetyError("INSERT 必须显式列出列名（不允许 INSERT INTO t VALUES (...)）")

    if intent.dml == "MERGE":
        raise SafetyError("MERGE 暂未支持，请拆分 UPDATE/INSERT")


def _check_ddl(intent: SqlIntent, rule: WhitelistRule) -> None:
    ddl = rule.ddl
    if not ddl.allowed:
        raise SafetyError("DDL 操作未授权（白名单中 ddl.allowed = false）")
    if intent.tables and ddl.allowed_tables:
        matched = any(_match_table(t, ddl.allowed_tables) for t in intent.tables)
        if not matched:
            raise SafetyError(
                f"DDL：表不在白名单: {intent.tables}（允许: {ddl.allowed_tables}）"
            )


def _check_plsql(intent: SqlIntent, rule: WhitelistRule) -> None:
    if not rule.ddl.allowed:
        raise SafetyError("PL/SQL 未授权（白名单中 ddl.allowed = false）")


# ------------------------------------------------------------------
# 辅助查询
# ------------------------------------------------------------------

def max_rows(jndi: str, cfg: dict[str, WhitelistRule], default: int) -> int:
    """获取读操作最大返回行数。"""
    rule = cfg.get(jndi)
    if not rule:
        return default
    return rule.read.max_rows


def max_affected_rows(jndi: str, cfg: dict[str, WhitelistRule], default: int) -> int:
    """获取写操作最大影响行数。"""
    rule = cfg.get(jndi)
    if not rule:
        return default
    return rule.write.max_affected_rows


def is_ddl_allowed(jndi: str, cfg: dict[str, WhitelistRule]) -> bool:
    """DDL 是否被允许。"""
    rule = cfg.get(jndi)
    if not rule:
        return False
    return rule.ddl.allowed


# ------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------

def _match_table(table: str, patterns: list[str]) -> bool:
    """支持 SCHEMA.TABLE 精确匹配、SCHEMA.* 通配、* 全通配。"""
    if not patterns:
        return False
    t = table.upper()
    for p in patterns:
        u = p.upper()
        if u == "*":
            return True
        if "*" in u:
            if fnmatch.fnmatch(t, u):
                return True
        elif t == u:
            return True
    return False

