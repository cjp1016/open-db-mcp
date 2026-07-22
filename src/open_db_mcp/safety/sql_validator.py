"""基于 SQLGlot 的 SQL 安全校验器。

提供比 sql_analyzer 更深的语义校验：
- 多语句检测（默认禁用）
- 危险函数检测（SLEEP / BENCHMARK / LOAD_FILE 等）
- 注释剥离（防止通过注释绕过校验）
- 方言感知的 AST 解析
- 表/列提取（更精确）

设计原则：防御性深度，宁可误报不可漏报。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import ParseError as SqlGlotParseError

    _SQLGLOT_AVAILABLE = True
except ImportError:
    _SQLGLOT_AVAILABLE = False
    sqlglot = None  # type: ignore[assignment]
    exp = None  # type: ignore[assignment]

    class SqlGlotParseError(Exception):
        """sqlglot 未安装时的占位异常。"""


# 危险函数黑名单（SQL 注入 / 拒绝服务常用）
DANGEROUS_FUNCTIONS: set[str] = {
    "SLEEP",
    "BENCHMARK",
    "LOAD_FILE",
    "OUTFILE",
    "DUMPFILE",
    "GET_LOCK",
    "RELEASE_LOCK",
    "SYS_EVAL",
    "SYS_EXEC",
    "UTL_FILE",
    "UTL_HTTP",
    "UTL_TCP",
    "DBMS_JAVA",
    "DBMS_PIPE",
    "DBMS_LOCK",
    "DBMS_SQL",
}

# 危险语句关键字（在 AST 中不应出现的）
DANGEROUS_STATEMENTS: set[str] = {
    "DESC",
    "SHOW",
    "USE",
    "GRANT",
    "REVOKE",
    "SHUTDOWN",
    "KILL",
}

# kind → sqlglot dialect 映射
_DIALECT_MAP: dict[str, str] = {
    "mysql": "mysql",
    "oracle": "oracle",
    "dm": "oracle",  # 达梦兼容 Oracle 语法
    "postgres": "postgres",
    "vastbase": "postgres",   # 海量数据库兼容 PG 语法
    "opengauss": "postgres",  # openGauss 兼容 PG 语法
    "sqlite": "sqlite",
}


@dataclass
class SqlValidationResult:
    """SQL 校验结果。"""

    is_safe: bool
    violations: list[str] = field(default_factory=list)
    parsed_statements: list[Any] = field(default_factory=list)
    statement_count: int = 0
    parse_error: str | None = None

    @property
    def has_multiple_statements(self) -> bool:
        return self.statement_count > 1


def validate_sql(
    sql: str,
    dialect: str = "mysql",
    allow_multiple_statements: bool = False,
) -> SqlValidationResult:
    """对 SQL 做深度安全校验。

    Args:
        sql: SQL 字符串。
        dialect: 数据库方言（mysql / oracle / dm）。
        allow_multiple_statements: 是否允许多语句。

    Returns:
        SqlValidationResult: 校验结果。
    """
    result = SqlValidationResult(is_safe=True)

    if not sql or not sql.strip():
        result.violations.append("空 SQL")
        result.is_safe = False
        return result

    if not _SQLGLOT_AVAILABLE:
        # sqlglot 不可用时降级为简单检查
        return _validate_without_sqlglot(sql, allow_multiple_statements)

    sqlglot_dialect = _DIALECT_MAP.get(dialect, "mysql")

    # 1. 解析 SQL（自动分号分句）
    try:
        statements = sqlglot.parse(sql, read=sqlglot_dialect)
    except SqlGlotParseError as e:
        result.parse_error = str(e)
        result.is_safe = False
        result.violations.append(f"SQL 语法错误: {e}")
        return result
    except Exception as e:
        result.parse_error = str(e)
        result.is_safe = False
        result.violations.append(f"SQL 解析失败: {e}")
        return result

    statements = [s for s in statements if s is not None]
    result.parsed_statements = statements
    result.statement_count = len(statements)

    # 2. 多语句检测
    if not allow_multiple_statements and len(statements) > 1:
        result.is_safe = False
        result.violations.append(
            f"禁止多语句执行（检测到 {len(statements)} 条语句）"
        )

    # 3. 逐条语句检查 AST
    for stmt in statements:
        _check_statement_ast(stmt, result)

    return result


def _check_statement_ast(stmt: Any, result: SqlValidationResult) -> None:
    """遍历 AST 节点，检测危险构造。"""
    if exp is None:
        return

    for node in stmt.walk():
        node_obj = node[0] if isinstance(node, tuple) else node

        # 检测危险函数调用
        if isinstance(node_obj, exp.Anonymous):
            func_name = str(node_obj.name or "").upper()
            if func_name in DANGEROUS_FUNCTIONS:
                result.is_safe = False
                result.violations.append(f"禁止使用函数: {func_name}")

        # 检测危险语句类型
        stmt_type = type(node_obj).__name__.upper()
        if stmt_type in DANGEROUS_STATEMENTS:
            result.is_safe = False
            result.violations.append(f"禁止使用语句: {stmt_type}")

        # 检测系统表访问（仅 MySQL）
        if isinstance(node_obj, exp.Table):
            table_name = str(node_obj.name or "").upper()
            if table_name in ("MYSQL", "INFORMATION_SCHEMA") and _is_write_statement(stmt):
                result.is_safe = False
                result.violations.append(
                    f"禁止对系统表 {table_name} 执行写操作"
                )


def _is_write_statement(stmt: Any) -> bool:
    """判断是否为写语句（INSERT / UPDATE / DELETE / DDL）。"""
    if exp is None:
        return False
    return isinstance(stmt, (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Alter,
        exp.Drop,
        exp.AlterTable,
    ))


def _validate_without_sqlglot(
    sql: str, allow_multiple_statements: bool
) -> SqlValidationResult:
    """sqlglot 不可用时的降级校验。"""
    result = SqlValidationResult(is_safe=True)
    sql_upper = sql.upper()

    # 多语句检测（基于分号）
    semicolon_count = sql.strip().rstrip(";").count(";")
    if not allow_multiple_statements and semicolon_count > 0:
        result.is_safe = False
        result.violations.append(
            f"禁止多语句执行（检测到 {semicolon_count + 1} 条语句）"
        )

    # 危险函数检测（基于字符串匹配）
    for func in DANGEROUS_FUNCTIONS:
        if func in sql_upper:
            result.is_safe = False
            result.violations.append(f"禁止使用函数: {func}")

    return result


def extract_tables(sql: str, dialect: str = "mysql") -> list[str]:
    """从 SQL 中提取所有表名（基于 AST，比正则更准确）。"""
    if not _SQLGLOT_AVAILABLE or not sql.strip():
        return []

    sqlglot_dialect = _DIALECT_MAP.get(dialect, "mysql")
    try:
        statements = sqlglot.parse(sql, read=sqlglot_dialect)
    except Exception:
        return []

    tables: list[str] = []
    for stmt in statements:
        if stmt is None:
            continue
        if exp is None:
            continue
        for node in stmt.walk():
            node_obj = node[0] if isinstance(node, tuple) else node
            if isinstance(node_obj, exp.Table):
                name = node_obj.name or ""
                db = node_obj.db or ""
                if db and name:
                    tables.append(f"{db}.{name}")
                elif name:
                    tables.append(name)
    return tables


def is_readonly_statement(sql: str, dialect: str = "mysql") -> bool:
    """判断 SQL 是否为只读语句（SELECT / WITH）。"""
    if not _SQLGLOT_AVAILABLE or not sql.strip():
        head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        return head in ("SELECT", "WITH")

    sqlglot_dialect = _DIALECT_MAP.get(dialect, "mysql")
    try:
        statements = sqlglot.parse(sql, read=sqlglot_dialect)
    except Exception:
        return False

    if not statements or statements[0] is None:
        return False

    if exp is None:
        return False

    stmt = statements[0]
    return isinstance(stmt, (exp.Select, exp.Subquery))
