"""驱动适配层公共类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ColumnInfo:
    """表列元数据。"""

    name: str
    data_type: str
    nullable: bool = True
    default: str | None = None
    is_primary_key: bool = False
    comment: str | None = None
    character_maximum_length: int | None = None
    numeric_precision: int | None = None
    numeric_scale: int | None = None


@dataclass
class TableInfo:
    """表元数据。"""

    name: str
    type: str
    schema: str | None = None
    rows: int | None = None
    comment: str | None = None


@dataclass
class IndexInfo:
    """索引元数据。"""

    name: str
    columns: list[str] = field(default_factory=list)
    is_unique: bool = False
    is_primary: bool = False


@dataclass
class SchemaInfo:
    """Schema / Database 元数据。"""

    name: str
    is_default: bool = False


@dataclass
class ExplainPlan:
    """执行计划。"""

    raw_rows: list[dict[str, Any]] = field(default_factory=list)
    estimated_rows: int | None = None
    estimated_cost: float | None = None


class DriverAdapter(Protocol):
    """驱动适配器统一协议（PEP 249 connection）。"""

    name: str
    dbapi: Any  # DBUtils 需要此属性识别异常类型

    def connect(self, conf):  # noqa: D401
        """建立一个新的 PEP 249 兼容连接。"""
        ...

    def parse_url(self, url: str) -> dict[str, Any]:
        """解析 JDBC URL，提取 host/port/db 等字段。"""
        ...

    def quote_ident(self, name: str) -> str:
        """按方言加引号（Oracle 大写，达梦大写）。"""
        ...

    def is_select(self, sql: str) -> bool:
        """判断 SQL 是否为只读（防御性默认 false）。"""
        ...

    def ping_sql(self) -> str:
        """返回该数据库的健康检查 SQL。"""
        ...

    def describe_table(
        self, conn: Any, table: str, schema: str | None = None
    ) -> list[ColumnInfo]:
        """查询指定表的列元数据。"""
        ...

    def list_tables(
        self,
        conn: Any,
        schema: str | None = None,
        table_type: str | None = None,
    ) -> list[TableInfo]:
        """列出 schema 中的表/视图。

        Args:
            conn: 数据库连接。
            schema: schema 名，None 使用当前默认 schema。
            table_type: 'BASE TABLE' / 'VIEW' / None(全部)。
        """
        ...

    def list_schemas(self, conn: Any) -> list[SchemaInfo]:
        """列出所有 schema / database。"""
        ...

    def list_indexes(
        self, conn: Any, table: str, schema: str | None = None
    ) -> list[IndexInfo]:
        """列出指定表的索引。"""
        ...

    def explain_query(
        self, conn: Any, sql: str, params: list | tuple | None = None
    ) -> ExplainPlan:
        """EXPLAIN 查询执行计划。"""
        ...

    def fetch_slow_queries(
        self, conn: Any, limit: int = 50, threshold_sec: float = 1.0
    ) -> list[dict[str, Any]]:
        """从数据库原生慢日志获取慢查询记录（可选实现）。

        返回字段约定：
            sql, duration_ms, exec_time, user, source='database'
        未实现的驱动应返回空列表。
        """
        return []
