<div align="center">

# ⚡ open-db-mcp

### 🔌 多数据源数据库 MCP 工具 · stdio 本地版

**让 LLM Agent 安全、高效地访问你的数据库**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-Protocol-FF6E40.svg?style=flat-square)](https://modelcontextprotocol.io)
[![Databases](https://img.shields.io/badge/DB-MySQL%20%7C%20Oracle%20%7C%20DM%20%7C%20PG%20%7C%20SQLite-336791.svg?style=flat-square&logo=database)](#-支持的数据库)

**简体中文** | [English](README_EN.md)

</div>

---

## 📖 简介

**open-db-mcp** 是一个基于 [Model Context Protocol (MCP)](https://modelcontextprotocol.io) 的多数据源数据库工具，通过 **stdio 本地进程**与 Claude Desktop / Cursor / Trae 等 AI 客户端即插即用，向 LLM Agent 暴露**受限且安全**的查询与写入能力。

> 💡 零网络依赖 · 三层安全护栏 · 一键切换数据源 · 开箱即用

---

## ✨ 核心特性

| | 特性 | 说明 |
|---|------|------|
| 🗄️ | **多驱动支持** | MySQL、Oracle、达梦（DM）、PostgreSQL、Vastbase、openGauss、SQLite，统一 `DriverAdapter` 抽象 |
| 🚀 | **零网络依赖** | stdio 本地进程，与 Claude Desktop / Cursor / Trae 即插即用 |
| 🔄 | **多数据源管理** | 支持 `datasources.json` 或 Kettle 风格 `jdbc.properties`，一键切换数据源 |
| 🛡️ | **安全护栏** | 三层白名单（read / write / ddl）+ WHERE 强制 + 影响行数预检 + JSONL 审计 |
| 🔒 | **SQL 注入防御** | 基于 sqlparse 的 SQL 语义分析 + 白名单校验，双保险 |
| 📦 | **可打包分发** | PyInstaller 三平台单文件可执行，团队成员无需装 Python |
| 🔁 | **事务支持** | `begin_transaction` / `commit` / `rollback` 跨语句事务 |
| 📤 | **数据导入导出** | CSV / JSON 格式，支持批量导入 |
| 🔍 | **Schema Diff** | 跨数据源表结构对比，列 / 索引级差异检测 |
| 🧩 | **插件化驱动** | 基于 `entry_points` 的驱动插件体系，易于扩展 |

---

## 🗄️ 支持的数据库

| 数据库 | Driver | 依赖 |
|--------|--------|------|
| 🐬 MySQL | `mysql` | PyMySQL + DBUtils |
| 🔴 Oracle | `oracle` | oracledb thin 模式 |
| 🏮 达梦 DM | `dm` | JayDeBeApi + JPype1 + DmJdbcDriver jar（内置打包，自动搜索） |
| 🐘 PostgreSQL | `postgres` | psycopg2-binary（可选） |
| 🌊 海量数据库 Vastbase | `vastbase` | psycopg2-binary（复用 postgres 驱动） |
| 🌿 openGauss | `opengauss` | psycopg2-binary（复用 postgres 驱动） |
| 📦 SQLite | `sqlite` | Python stdlib |

---

## 🚀 快速开始

### 📥 安装

```bash
git clone https://github.com/cjp1016/open-db-mcp.git
cd open-db-mcp
uv sync                  # 或 pip install -e '.[dev]'
uv run open-db-mcp init # 生成 ~/.open-db-mcp/ 配置目录
uv run open-db-mcp doctor  # 健康检查
```

### ⚙️ 配置数据源

编辑 `~/.open-db-mcp/datasources.json`：

```json
{
  "LOCAL_MYSQL": {
    "driver": "com.mysql.cj.jdbc.Driver",
    "url": "jdbc:mysql://127.0.0.1:3306/test_db?characterEncoding=utf8mb4",
    "user": "root",
    "password": "env:DB_PASSWORD",
    "pool_min": 2,
    "pool_max": 4
  }
}
```

> 🔐 密码支持 `env:` / `keyring:` / `cmd:` 四种安全引用方式。

### 🔗 注册到 MCP 客户端

**Claude Desktop**（`~/Library/Application Support/Claude/claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "smart-db": {
      "command": "open-db-mcp",
      "args": ["run"],
      "env": {
        "MCP_DATASOURCES_CFG_PATH": "/path/to/your/datasources.json"
      }
    }
  }
}
```

**Cursor**（`~/.cursor/mcp.json`）：同上结构。

---

## 🛠️ CLI 命令

| 命令 | 说明 |
|------|------|
| `open-db-mcp init` | 🎬 首次运行：引导生成配置文件 |
| `open-db-mcp doctor` | 🩺 健康检查：解析配置、ping 每个数据源 |
| `open-db-mcp run` | ▶️ 启动 stdio MCP server（被 MCP 客户端调用） |
| `open-db-mcp list` | 📋 列出所有数据源与连接池状态 |
| `open-db-mcp package <target>` | 📦 PyInstaller 打包 |

---

## 🧰 MCP 工具清单（25 个）

| 分类 | 工具 |
|------|------|
| 🗂️ 数据源管理 | `list_datasources`, `use_datasource`, `get_active_datasource`, `add_datasource`, `update_datasource`, `remove_datasource`, `ping_datasource`, `get_pool_stats`, `list_drivers` |
| 🔎 元数据浏览 | `list_schemas`, `list_tables`, `list_indexes`, `describe_table`, `explain_query`, `sample_table`, `diff_schema` |
| ⚡ 查询执行 | `execute_query` |
| ✏️ DML + 事务 | `execute_dml`, `execute_ddl`, `begin_transaction`, `commit_transaction`, `rollback_transaction`, `get_transaction_status` |
| 📤 数据导入导出 | `export_table`, `import_csv` |

---

## 🏗️ 架构

```
MCP 工具层（tools/）  →  服务层（services/）  →  驱动层（drivers/）
    薄包装 + 参数解析        业务逻辑 + 校验         DB 方言适配
```

- ✅ 遵循 SOLID 原则，高内聚低耦合
- ✅ 驱动层基于 Protocol 抽象，易于扩展新数据库
- ✅ 服务层纯业务逻辑，可独立单元测试

---

## 👨‍💻 开发

```bash
# 代码检查
ruff check src/
mypy src/
```

---

## 💬 联系我们

<div align="center">

扫码添加微信，备注 **open-db-mcp** 加入交流群：

<img src="image/wechat.png" alt="微信联系方式" width="260" />

</div>

---

## ❤️ 支持赞助

如果这个项目对你有帮助，欢迎扫码请作者喝杯咖啡 ☕

<div align="center">

<img src="image/wechat-pay.png" alt="微信打赏二维码" width="260" />

</div>

---

## 📄 License

[MIT](LICENSE) © open-db-mcp contributors

---

<div align="center">

**⚡ open-db-mcp** — 让 AI 与数据库安全对话

[![GitHub](https://img.shields.io/badge/GitHub-cjp1016%2Fopen--db--mcp-181717.svg?style=for-the-badge&logo=github)](https://github.com/cjp1016/open-db-mcp)

</div>
