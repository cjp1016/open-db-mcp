"""统一异常层级。

所有业务异常都继承自 DbMcpError，便于 MCP 工具层统一捕获与格式化。

异常层级：
    DbMcpError
    ├── DataSourceError          数据源相关
    │   ├── DataSourceNotFoundError    数据源不存在
    │   ├── DataSourceAlreadyExistsError 数据源已存在
    │   └── ConnectionError            连接失败
    ├── SecurityError              安全相关
    │   ├── SqlInjectionError          SQL 注入检测
    │   ├── WhitelistViolationError    白名单违规
    │   └── ForbiddenOperationError    禁止的操作类型
    ├── QueryError                 查询相关
    │   ├── QueryTimeoutError          查询超时
    │   ├── QuerySyntaxError           SQL 语法错误
    │   └── ResultTooLargeError        结果集过大
    ├── DmlError                   DML 相关
    │   ├── TooManyRowsAffectedError   影响行数超限
    │   └── DryRunRequiredError        需要 dry_run
    ├── DdlError                   DDL 相关
    │   └── DdlNotAllowedError          DDL 未授权
    ├── TransactionError           事务相关
    │   ├── TransactionNotFoundError   事务不存在
    │   ├── TransactionTimeoutError    事务超时
    │   └── NestedTransactionError     嵌套事务不支持
    └── ConfigurationError         配置相关
        └── InvalidConfigError         配置非法
"""

from __future__ import annotations


class DbMcpError(Exception):
    """所有业务异常的基类。"""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


# ------------------------------------------------------------------
# DataSourceError
# ------------------------------------------------------------------

class DataSourceError(DbMcpError):
    """数据源相关异常基类。"""

    code = "DATA_SOURCE_ERROR"
    status_code = 400


class DataSourceNotFoundError(DataSourceError):
    """数据源不存在。"""

    code = "DATA_SOURCE_NOT_FOUND"
    status_code = 404


class DataSourceAlreadyExistsError(DataSourceError):
    """数据源已存在。"""

    code = "DATA_SOURCE_ALREADY_EXISTS"
    status_code = 409


class ConnectionError(DataSourceError):
    """数据库连接失败。"""

    code = "CONNECTION_FAILED"
    status_code = 502


# ------------------------------------------------------------------
# SecurityError
# ------------------------------------------------------------------

class SecurityError(DbMcpError):
    """安全相关异常基类。"""

    code = "SECURITY_ERROR"
    status_code = 403


class SqlInjectionError(SecurityError):
    """SQL 注入检测。"""

    code = "SQL_INJECTION_DETECTED"
    status_code = 403


class WhitelistViolationError(SecurityError):
    """白名单违规。"""

    code = "WHITELIST_VIOLATION"
    status_code = 403


class ForbiddenOperationError(SecurityError):
    """禁止的操作类型（如 DDL 未授权）。"""

    code = "FORBIDDEN_OPERATION"
    status_code = 403


# ------------------------------------------------------------------
# QueryError
# ------------------------------------------------------------------

class QueryError(DbMcpError):
    """查询相关异常基类。"""

    code = "QUERY_ERROR"
    status_code = 400


class QueryTimeoutError(QueryError):
    """查询超时。"""

    code = "QUERY_TIMEOUT"
    status_code = 408


class QuerySyntaxError(QueryError):
    """SQL 语法错误。"""

    code = "QUERY_SYNTAX_ERROR"
    status_code = 400


class ResultTooLargeError(QueryError):
    """结果集过大。"""

    code = "RESULT_TOO_LARGE"
    status_code = 413


# ------------------------------------------------------------------
# DmlError
# ------------------------------------------------------------------

class DmlError(DbMcpError):
    """DML 相关异常基类。"""

    code = "DML_ERROR"
    status_code = 400


class DataImportError(DmlError):
    """数据导入失败。"""

    code = "DATA_IMPORT_ERROR"
    status_code = 400


class TooManyRowsAffectedError(DmlError):
    """影响行数超限。"""

    code = "TOO_MANY_ROWS_AFFECTED"
    status_code = 400


class DryRunRequiredError(DmlError):
    """DML 需要先 dry_run。"""

    code = "DRY_RUN_REQUIRED"
    status_code = 400


# ------------------------------------------------------------------
# DdlError
# ------------------------------------------------------------------

class DdlError(DbMcpError):
    """DDL 相关异常基类。"""

    code = "DDL_ERROR"
    status_code = 400


class DdlNotAllowedError(DdlError):
    """DDL 未授权。"""

    code = "DDL_NOT_ALLOWED"
    status_code = 403


# ------------------------------------------------------------------
# TransactionError
# ------------------------------------------------------------------

class TransactionError(DbMcpError):
    """事务相关异常基类。"""

    code = "TRANSACTION_ERROR"
    status_code = 400


class TransactionNotFoundError(TransactionError):
    """事务不存在。"""

    code = "TRANSACTION_NOT_FOUND"
    status_code = 404


class TransactionTimeoutError(TransactionError):
    """事务超时。"""

    code = "TRANSACTION_TIMEOUT"
    status_code = 408


class NestedTransactionError(TransactionError):
    """嵌套事务不支持。"""

    code = "NESTED_TRANSACTION_NOT_SUPPORTED"
    status_code = 400


# ------------------------------------------------------------------
# ConfigurationError
# ------------------------------------------------------------------

class ConfigurationError(DbMcpError):
    """配置相关异常基类。"""

    code = "CONFIGURATION_ERROR"
    status_code = 400


class InvalidConfigError(ConfigurationError):
    """配置非法。"""

    code = "INVALID_CONFIG"
    status_code = 400
