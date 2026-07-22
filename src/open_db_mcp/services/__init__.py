"""服务层：业务逻辑封装。

将 MCP 工具层与底层数据访问分离：
    tools 层（薄）→ services 层 → registry / safety / tx

好处：
1. 工具层只做参数解析与结果格式化
2. 服务层可独立单元测试（无需 FastMCP）
3. 服务层可被其他入口复用（HTTP API / CLI）
"""

from .data_service import DataService
from .dml_service import DmlService
from .meta_service import MetaService
from .query_service import QueryService

__all__ = ["DataService", "DmlService", "MetaService", "QueryService"]
