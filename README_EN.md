<div align="center">

# ⚡ open-db-mcp

### 🔌 Multi-DataSource Database MCP Tool · stdio Local Edition

**Let LLM Agents access your databases safely and efficiently**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-Protocol-FF6E40.svg?style=flat-square)](https://modelcontextprotocol.io)
[![Databases](https://img.shields.io/badge/DB-MySQL%20%7C%20Oracle%20%7C%20DM%20%7C%20PG%20%7C%20SQLite-336791.svg?style=flat-square&logo=database)](#-supported-databases)

[简体中文](README.md) | **English**

</div>

---

## 📖 Introduction

**open-db-mcp** is a multi-datasource database tool built on the [Model Context Protocol (MCP)](https://modelcontextprotocol.io). It runs as a **stdio local process** and works plug-and-play with AI clients like Claude Desktop / Cursor / Trae, exposing **restricted and secure** query & write capabilities to LLM Agents.

> 💡 Zero network dependency · 3-layer safety guardrails · One-click datasource switching · Works out of the box

---

## ✨ Key Features

| | Feature | Description |
|---|---------|-------------|
| 🗄️ | **Multi-driver support** | MySQL, Oracle, DM (Dameng), PostgreSQL, Vastbase, openGauss, SQLite — unified `DriverAdapter` abstraction |
| 🚀 | **Zero network dependency** | stdio local process, plug-and-play with Claude Desktop / Cursor / Trae |
| 🔄 | **Multi-datasource management** | Supports `datasources.json` or Kettle-style `jdbc.properties`, one-click datasource switching |
| 🛡️ | **Safety guardrails** | 3-layer whitelist (read / write / ddl) + mandatory WHERE + impact-row pre-check + JSONL audit |
| 🔒 | **SQL injection defense** | sqlparse-based SQL semantic analysis + whitelist validation, double insurance |
| 📦 | **Packagable distribution** | PyInstaller single-file executables for 3 platforms — teammates don't need Python |
| 🔁 | **Transaction support** | `begin_transaction` / `commit` / `rollback` cross-statement transactions |
| 📤 | **Data import/export** | CSV / JSON formats, batch import supported |
| 🔍 | **Schema Diff** | Cross-datasource table structure comparison, column/index-level diff detection |
| 🧩 | **Plugin drivers** | `entry_points`-based driver plugin system, easy to extend |

---

## 🗄️ Supported Databases

| Database | Driver | Dependency |
|----------|--------|------------|
| 🐬 MySQL | `mysql` | PyMySQL + DBUtils |
| 🔴 Oracle | `oracle` | oracledb thin mode |
| 🏮 Dameng DM | `dm` | JayDeBeApi + JPype1 + DmJdbcDriver jar (bundled, auto-discovered) |
| 🐘 PostgreSQL | `postgres` | psycopg2-binary (optional) |
| 🌊 Vastbase | `vastbase` | psycopg2-binary (reuses postgres driver) |
| 🌿 openGauss | `opengauss` | psycopg2-binary (reuses postgres driver) |
| 📦 SQLite | `sqlite` | Python stdlib |

---

## 🚀 Quick Start

### 📥 Installation

```bash
git clone https://github.com/cjp1016/open-db-mcp.git
cd open-db-mcp
uv sync                  # or pip install -e '.[dev]'
uv run open-db-mcp init # generate ~/.open-db-mcp/ config directory
uv run open-db-mcp doctor  # health check
```

### ⚙️ Configure Datasources

Edit `~/.open-db-mcp/datasources.json`:

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

> 🔐 Passwords support `env:` / `keyring:` / `cmd:` secure reference modes.

### 🔗 Register with MCP Clients

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

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

**Cursor** (`~/.cursor/mcp.json`): same structure.

---

## 🛠️ CLI Commands

| Command | Description |
|---------|-------------|
| `open-db-mcp init` | 🎬 First run: guided config file generation |
| `open-db-mcp doctor` | 🩺 Health check: parse config, ping every datasource |
| `open-db-mcp run` | ▶️ Start stdio MCP server (invoked by MCP clients) |
| `open-db-mcp list` | 📋 List all datasources and pool status |
| `open-db-mcp package <target>` | 📦 PyInstaller packaging |

---

## 🧰 MCP Tools (25)

| Category | Tools |
|----------|-------|
| 🗂️ Datasource management | `list_datasources`, `use_datasource`, `get_active_datasource`, `add_datasource`, `update_datasource`, `remove_datasource`, `ping_datasource`, `get_pool_stats`, `list_drivers` |
| 🔎 Metadata browsing | `list_schemas`, `list_tables`, `list_indexes`, `describe_table`, `explain_query`, `sample_table`, `diff_schema` |
| ⚡ Query execution | `execute_query` |
| ✏️ DML + transactions | `execute_dml`, `execute_ddl`, `begin_transaction`, `commit_transaction`, `rollback_transaction`, `get_transaction_status` |
| 📤 Import / export | `export_table`, `import_csv` |

---

## 🏗️ Architecture

```
MCP Tools Layer (tools/)  →  Services Layer (services/)  →  Drivers Layer (drivers/)
  thin wrapper + parsing      business logic + validation    DB dialect adapters
```

- ✅ SOLID principles, high cohesion & low coupling
- ✅ Driver layer based on Protocol abstraction — easy to add new databases
- ✅ Services layer is pure business logic, independently unit-testable

---

## 👨‍💻 Development

```bash
# Lint & type check
ruff check src/
mypy src/
```

---

## 💬 Contact Us

<div align="center">

Scan the QR code to add us on WeChat, note **open-db-mcp** to join the group:

<img src="image/wechat.png" alt="WeChat Contact" width="260" />

</div>

---

## ❤️ Support

If this project helps you, feel free to buy the author a coffee ☕

<div align="center">

<img src="image/wechat-pay.png" alt="WeChat Pay QR Code" width="260" />

</div>

---

## 📄 License

[MIT](LICENSE) © open-db-mcp contributors

---

<div align="center">

**⚡ open-db-mcp** — Let AI talk to your databases safely

[![GitHub](https://img.shields.io/badge/GitHub-cjp1016%2Fopen--db--mcp-181717.svg?style=for-the-badge&logo=github)](https://github.com/cjp1016/open-db-mcp)

</div>
