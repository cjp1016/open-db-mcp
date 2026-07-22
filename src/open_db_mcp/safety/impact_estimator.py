"""影响行数预检：把 UPDATE/DELETE 转成 SELECT COUNT(*) 预跑。"""

from __future__ import annotations

import re

from .sql_analyzer import analyze


def estimate(conn, sql: str, params: tuple) -> int:
    """估算 UPDATE/DELETE 将影响的行数。

    Args:
        conn: PEP 249 连接。
        sql: 原始 SQL。
        params: 绑定参数。

    Returns:
        估算行数；非 UPDATE/DELETE 返回 0。
    """
    intent = analyze(sql)
    if intent.dml not in ("UPDATE", "DELETE"):
        return 0
    count_sql = _rewrite_to_count(sql, intent)
    cur = conn.cursor()
    try:
        cur.execute(count_sql, params)
        row = cur.fetchone()
        if not row:
            return 0
        return int(row[0])
    finally:
        cur.close()


def _rewrite_to_count(sql: str, intent) -> str:
    """把 UPDATE/DELETE 转成 SELECT COUNT(*) FROM ... WHERE ...。

    实现策略：
    - UPDATE t SET col=:1 WHERE cond → SELECT COUNT(*) FROM t WHERE cond
    - DELETE FROM t WHERE cond       → SELECT COUNT(*) FROM t WHERE cond
    - UPDATE t SET col=:1           → SELECT COUNT(*) FROM t  （会触发 WHERE 校验失败）
    """
    s = sql.strip().rstrip(";")
    if intent.dml == "DELETE":
        # DELETE [FROM] <tables> [WHERE ...]
        m = re.match(r"^DELETE(?:\s+FROM)?\s+", s, flags=re.IGNORECASE)
        if not m:
            raise ValueError(f"无法解析 DELETE 语句: {s[:80]!r}")
        body = s[m.end():]
        where_idx = _find_keyword_index(body, "WHERE")
        target = body if where_idx < 0 else body[:where_idx]
        where_clause = "" if where_idx < 0 else body[where_idx:].strip()
        return f"SELECT COUNT(*) FROM {target.strip()}{(' ' + where_clause) if where_clause else ''}"
    if intent.dml == "UPDATE":
        # UPDATE <target> SET ...
        m = re.match(r"^UPDATE\s+(.+?)\s+SET\s+", s, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            raise ValueError(f"无法解析 UPDATE 语句: {s[:80]!r}")
        target = m.group(1).strip()
        # 找 SET 之后的 WHERE
        after_set = s[m.end():]
        where_idx = _find_keyword_index(after_set, "WHERE")
        if where_idx < 0:
            return f"SELECT COUNT(*) FROM {target}"
        return f"SELECT COUNT(*) FROM {target} {after_set[where_idx:].strip()}"
    return ""


def _find_keyword_index(body: str, keyword: str) -> int:
    """忽略括号/字符串内的子串，定位独立 keyword 起始位置。"""
    depth = 0
    in_str: str | None = None
    i = 0
    kw = keyword.upper()
    while i < len(body):
        ch = body[i]
        if in_str:
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ("'", '"'):
            in_str = ch
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and body[i:i + len(kw)].upper() == kw:
            prev = body[i - 1] if i > 0 else " "
            nxt = body[i + len(kw)] if i + len(kw) < len(body) else " "
            if not prev.isalnum() and prev != "_" and not nxt.isalnum() and nxt != "_":
                return i
        i += 1
    return -1
