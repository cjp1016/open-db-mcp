<div align="center">

# ⚡ open-db-mcp

**让 LLM Agent 安全地访问你的数据库**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-Protocol-FF6E40.svg?style=flat-square)](https://modelcontextprotocol.io)

**简体中文** | [English](README_EN.md)

</div>

---

基于 [MCP 协议](https://modelcontextprotocol.io) 的多数据源数据库工具，通过 stdio 本地进程与 Claude Desktop / Cursor / Trae 等 AI 客户端即插即用。

**支持数据库**：MySQL · Oracle · 达梦 · PostgreSQL · Vastbase · openGauss · SQLite

**核心能力**：多数据源一键切换 · 三层白名单安全护栏 · 影响行数预检 · 事务支持 · 慢 SQL 分析 · CSV/JSON 导入导出 · 跨库 Schema Diff · 插件化驱动扩展

---

## 🚀 快速开始

```bash
git clone https://github.com/cjp1016/open-db-mcp.git
cd open-db-mcp
uv sync                     # 安装依赖
uv run open-db-mcp init     # 生成 ~/.open-db-mcp/ 配置
uv run open-db-mcp doctor   # 健康检查
```

### 配置数据源

编辑 `~/.open-db-mcp/datasources.json`：

```json
{
  "MY_MYSQL": {
    "driver": "mysql",
    "url": "jdbc:mysql://127.0.0.1:3306/test_db",
    "user": "root",
    "password": "env:DB_PASSWORD"
  }
}
```

- `driver`：简写（`mysql` / `oracle` / `dm` / `postgres` / `sqlite`）或完整 JDBC 类名
- `password`：支持明文或安全引用（`env:变量名` / `keyring:` / `cmd:`）
- 可选参数：`pool_min`、`pool_max`、`max_affected_rows`

### 注册到 MCP 客户端

**最简配置**（使用默认路径 `config/datasources.json`）：

```json
{
  "mcpServers": {
    "open-db-mcp": {
      "command": "open-db-mcp",
      "args": ["run"]
    }
  }
}
```

**完整配置**（通过 `env` 注入运行时参数）：

```json
{
  "mcpServers": {
    "open-db-mcp": {
      "command": "open-db-mcp",
      "args": ["run"],
      "env": {
        "MCP_DATASOURCES_CFG_PATH": "/path/to/datasources.json",
        "MCP_WHITELIST_PATH": "/path/to/whitelist.json",
        "MCP_POOL_MAX": "8",
        "MCP_MAX_AFFECTED_ROWS": "1000",
        "MCP_QUERY_TIMEOUT_SEC": "30",
        "MCP_AUDIT_LOG_PATH": "/path/to/audit.jsonl",
        "DB_PASSWORD": "your_password"
      }
    }
  }
}
```

**环境变量说明**：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MCP_DATASOURCES_CFG_PATH` | `config/datasources.json` | 数据源配置文件路径 |
| `MCP_WHITELIST_PATH` | `config/whitelist.json` | 白名单配置文件路径 |
| `MCP_POOL_MAX` | `8` | 每个数据源的连接池上限 |
| `MCP_MAX_AFFECTED_ROWS` | `1000` | 单次 DML 允许影响的最大行数 |
| `MCP_DEFAULT_QUERY_MAX_ROWS` | `1000` | 查询默认最大返回行数 |
| `MCP_QUERY_TIMEOUT_SEC` | `30` | 查询超时（秒） |
| `MCP_AUDIT_LOG_PATH` | `~/.open-db-mcp/audit.jsonl` | 审计日志路径 |
| `MCP_AUDIT_ENABLED` | `true` | 是否启用审计日志 |
| `MCP_DM_JDBC_JAR_PATH` | `libs/DmJdbcDriver18.jar` | 达梦 JDBC 驱动 jar 路径 |

> 所有 `MCP_*` 变量也可写入 `~/.open-db-mcp/.env` 文件，效果相同。

**各客户端配置文件位置**：

| 客户端 | 配置文件 |
|--------|----------|
| Claude Desktop (macOS) | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Claude Desktop (Windows) | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cursor | `~/.cursor/mcp.json` |
| Trae / 其他 | 参考对应客户端的 MCP 配置文档，格式相同 |

---

## 💬 联系 & 赞助

<div align="center">

<img src="image/wechat.png" alt="微信联系方式" width="220" />
<img src="image/wechat-pay.png" alt="微信打赏二维码" width="220" />

</div>

---

## 📄 License

[MIT](LICENSE) © cjp1016
