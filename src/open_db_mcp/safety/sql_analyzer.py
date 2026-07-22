"""SQL 解析：从 SQL 字符串中提取 DML 类型、表名、列名、WHERE 状态。

基于 sqlparse 的 token 流转，针对 Oracle/达梦常见写法做归一化。
sqlparse 的关键特性：
- `SCHEMA.TABLE` 拆成 3 个 token（Name, Punctuation '.', Name）
- `T1 a` 别名是两个独立 Name token
- UPDATE SET col1 = 1, col2 = 2 是 6 个独立 token
- INSERT INTO t (col1, col2) 的列括号是 Punctuation '(' 和 ')'
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlparse
from sqlparse.sql import TokenList
from sqlparse.tokens import DDL, DML, Keyword, Name, Punctuation

DML_TYPES = {"SELECT", "INSERT", "UPDATE", "DELETE", "MERGE"}
MUTATION_TYPES = {"INSERT", "UPDATE", "DELETE", "MERGE", "DDL", "PLSQL"}
_TABLE_KEYWORDS = {"FROM", "INTO", "UPDATE", "JOIN", "MERGE", "TABLE"}
_END_KEYWORDS = {"WHERE", "SET", "ON", "GROUP", "ORDER", "HAVING", "VALUES", "RETURNING"}


@dataclass
class SqlIntent:
    dml: str
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    has_where: bool = False
    raw: str = ""

    @property
    def is_readonly(self) -> bool:
        return self.dml in ("SELECT", "WITH")


def analyze(sql: str) -> SqlIntent:
    if not sql or not sql.strip():
        raise ValueError("SQL 不能为空")
    parsed = sqlparse.parse(sql)
    if not parsed:
        raise ValueError("无法解析 SQL")
    stmt = parsed[0]
    dml = _detect_dml(stmt)
    tables = _extract_tables(stmt)
    columns: list[str] = []
    has_where = bool(_find_keyword(stmt, ["WHERE"]))
    if dml == "INSERT":
        columns = _extract_insert_columns(stmt)
    elif dml == "UPDATE":
        columns = _extract_update_set_columns(stmt)
    elif dml == "SELECT":
        columns = _extract_select_columns(stmt)
    return SqlIntent(
        dml=dml, tables=tables, columns=columns, has_where=has_where, raw=sql
    )


def _detect_dml(stmt: TokenList) -> str:
    for tok in stmt.flatten():
        if tok.ttype is DML and tok.value.upper() in DML_TYPES:
            return tok.value.upper()
        if tok.ttype is Keyword and tok.value.upper() == "WITH":
            return "SELECT"
        if tok.ttype is DDL:
            return "DDL"
    head = stmt.token_first(skip_cm=True)
    if head and head.value.upper() in DML_TYPES:
        return head.value.upper()
    # PL/SQL 匿名块：DECLARE / BEGIN 开头 → 视为 PLSQL 类型（非只读）
    first_flat = next(stmt.flatten(), None)
    if first_flat and first_flat.value.upper() in ("DECLARE", "BEGIN"):
        return "PLSQL"
    raise ValueError(f"无法识别 DML 类型: {stmt.value[:50]!r}")


def _is_name(tok) -> bool:
    """判断 token 是否为列名/表名/标识符（Name 类型）。"""
    return tok.ttype is Name and bool(tok.value.strip())


def _extract_tables(stmt: TokenList) -> list[str]:
    """提取 FROM/INTO/UPDATE/JOIN 后的表名，自动处理 schema 限定与别名。"""
    out: list[str] = []
    tokens = [t for t in stmt.flatten() if not t.is_whitespace]
    state: str | None = None
    pending = ""  # 累积的表名前缀（不含别名）
    last_was_name = False  # 上一个 token 是否为 Name（用于区分 schema 与表名）
    paren_depth = 0  # 子查询括号深度

    def flush():
        nonlocal pending
        if pending:
            out.append(pending)
            pending = ""

    for tok in tokens:
        u = tok.value.upper().strip()

        # 括号深度追踪
        if tok.ttype is Punctuation:
            if u == "(":
                paren_depth += 1
                if paren_depth > 0 and state and not pending:
                    # 进入子查询，结束当前表名收集
                    state = None
                continue
            elif u == ")":
                paren_depth = max(0, paren_depth - 1)
                continue
            elif u == ",":
                if state and paren_depth == 0:
                    flush()
                continue
            elif u == ".":
                # schema 限定词
                if pending:
                    pending += "."
                last_was_name = False
                continue
            else:
                continue

        if paren_depth > 0:
            continue

        if u in _TABLE_KEYWORDS:
            state = u
            last_was_name = False
            pending = ""
            continue
        if u in _END_KEYWORDS:
            if state:
                flush()
            state = None
            last_was_name = False
            continue
        if u == "AS":
            # AS 别名：跳过下一个 Name
            continue
        if state is None:
            continue
        if not _is_name(tok):
            continue

        # 当前是 Name
        if pending and not last_was_name:
            # 上一个 token 是 schema（pending 是 SCHEMA.），现在追加表名
            pending += tok.value.strip('"').strip("`")
            last_was_name = True
        elif last_was_name and pending:
            # 上一个 Name 是表名，当前 Name 是别名
            flush()
            last_was_name = False
        else:
            # 第一个 Name：可能是 schema 或表名
            pending = tok.value.strip('"').strip("`")
            last_was_name = True

    if state:
        flush()

    # 去重保序
    seen = set()
    dedup: list[str] = []
    for t in out:
        if t and t not in seen:
            seen.add(t)
            dedup.append(t)
    return dedup


def _extract_insert_columns(stmt: TokenList) -> list[str]:
    """提取 INSERT INTO t (col1, col2) 中的列名。"""
    tokens = list(stmt.flatten())
    # 找 INTO 之后第一个 ( ... ) 之间所有的 Name
    seen_into = False
    depth = 0
    cols: list[str] = []
    in_target_paren = False
    for tok in tokens:
        if not seen_into and tok.value.upper() == "INTO":
            seen_into = True
            continue
        if not seen_into:
            continue
        if tok.ttype is Punctuation:
            u = tok.value
            if u == "(":
                depth += 1
                if depth == 1:
                    in_target_paren = True
                continue
            elif u == ")":
                depth -= 1
                if depth == 0:
                    break
                continue
            elif u == ",":
                continue
        if in_target_paren and _is_name(tok):
            cols.append(tok.value.strip('"').strip("`"))
    return cols


def _extract_update_set_columns(stmt: TokenList) -> list[str]:
    """提取 UPDATE t SET col1=1, col2=2 中的列名。"""
    tokens = list(stmt.flatten())
    seen_set = False
    cols: list[str] = []
    for tok in tokens:
        u = tok.value.upper()
        if not seen_set:
            if u == "SET":
                seen_set = True
            continue
        # 结束条件
        if u in _END_KEYWORDS:
            break
        if tok.ttype is Punctuation:
            continue
        if _is_name(tok):
            # 值或列名交替出现：Name -> "=" -> 值 -> "," -> Name ...
            # 通过"=" 后的下一个 Name 是值 来过滤，但简单做法是：取 SET 之后所有 Name。
            # 但这样会误把等号右边的值也当作列名。
            # 改进：维护一个"下一个 Name 是列名"标志。
            pass
    # 重新实现：扫描 SET 之后，识别 Name 紧跟 = 的模式
    cols.clear()
    seen_set = False
    i = 0
    flat = [t for t in stmt.flatten() if not t.is_whitespace]
    for idx, tok in enumerate(flat):
        u = tok.value.upper()
        if not seen_set:
            if u == "SET":
                seen_set = True
            continue
        if u in _END_KEYWORDS:
            break
        if _is_name(tok):
            # 下一个非空白 token 是 = 吗？
            nxt = flat[idx + 1] if idx + 1 < len(flat) else None
            if nxt and nxt.value == "=":
                cols.append(tok.value.strip('"').strip("`"))
    return cols


def _find_keyword(stmt: TokenList, names: list[str]):
    for tok in stmt.flatten():
        if tok.ttype is Keyword and tok.value.upper() in names:
            return tok
    return None


def _extract_select_columns(stmt: TokenList) -> list[str]:
    """提取 SELECT 与 FROM 之间的列名。

    支持：
        SELECT a, b, c FROM t
        SELECT t.a, t.b FROM t
        SELECT a AS x FROM t
        SELECT * FROM t（返回空列表，表示不限定列）
    """
    flat = [t for t in stmt.flatten() if not t.is_whitespace]
    cols: list[str] = []
    in_select_list = False
    paren_depth = 0
    for idx, tok in enumerate(flat):
        u = tok.value.upper()
        if tok.ttype is Punctuation:
            if u == "(":
                paren_depth += 1
                continue
            elif u == ")":
                paren_depth = max(0, paren_depth - 1)
                continue
            elif u == ",":
                continue
            elif u == ".":
                continue
        if u == "SELECT":
            in_select_list = True
            continue
        if u == "FROM":
            break
        if not in_select_list:
            continue
        if paren_depth > 0:
            # 函数调用内的 Name 跳过（如 COUNT(*) / MAX(x) 中的 x 也跳过以避免误判）
            continue
        if u == "AS":
            continue
        if u == "*":
            continue
        if _is_name(tok):
            cols.append(tok.value.strip('"').strip("`"))
    return cols
