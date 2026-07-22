<div align="center">

# ⚡ open-db-mcp

**Let LLM Agents access your databases safely**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-Protocol-FF6E40.svg?style=flat-square)](https://modelcontextprotocol.io)

[简体中文](README.md) | **English**

</div>

---

A multi-datasource database tool built on the [MCP protocol](https://modelcontextprotocol.io). Runs as a stdio local process, plug-and-play with Claude Desktop / Cursor / Trae.

**Supported databases**: MySQL · Oracle · DM (Dameng) · PostgreSQL · Vastbase · openGauss · SQLite

**Key capabilities**: One-click datasource switching · 3-layer whitelist guardrails · Impact-row pre-check · Transactions · CSV/JSON import/export · Cross-database Schema Diff · Plugin-based driver extension

---

## 🚀 Quick Start

```bash
git clone https://github.com/cjp1016/open-db-mcp.git
cd open-db-mcp
uv sync                     # install dependencies
uv run open-db-mcp init     # generate ~/.open-db-mcp/ config
uv run open-db-mcp doctor   # health check
```

### Configure Datasources

Edit `~/.open-db-mcp/datasources.json`:

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

- `driver`: shorthand (`mysql` / `oracle` / `dm` / `postgres` / `sqlite`) or full JDBC class name
- `password`: plaintext or secure reference (`env:VAR` / `keyring:` / `cmd:`)
- Optional: `pool_min`, `pool_max`, `max_affected_rows`

### Register with MCP Clients

**Minimal config** (uses default path `config/datasources.json`):

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

**Full config** (inject runtime parameters via `env`):

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

**Environment variables**:

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_DATASOURCES_CFG_PATH` | `config/datasources.json` | Datasource config file path |
| `MCP_WHITELIST_PATH` | `config/whitelist.json` | Whitelist config file path |
| `MCP_POOL_MAX` | `8` | Connection pool cap per datasource |
| `MCP_MAX_AFFECTED_ROWS` | `1000` | Max affected rows per DML statement |
| `MCP_DEFAULT_QUERY_MAX_ROWS` | `1000` | Default max rows returned by queries |
| `MCP_QUERY_TIMEOUT_SEC` | `30` | Query timeout (seconds) |
| `MCP_AUDIT_LOG_PATH` | `~/.open-db-mcp/audit.jsonl` | Audit log path |
| `MCP_AUDIT_ENABLED` | `true` | Enable/disable audit logging |
| `MCP_DM_JDBC_JAR_PATH` | `libs/DmJdbcDriver18.jar` | DM JDBC driver jar path |

> All `MCP_*` variables can also be placed in a `~/.open-db-mcp/.env` file with the same effect.

**Client config file locations**:

| Client | Config file |
|--------|-------------|
| Claude Desktop (macOS) | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Claude Desktop (Windows) | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cursor | `~/.cursor/mcp.json` |
| Trae / others | Same format — refer to the client's MCP documentation |

---

## 🧰 MCP Tools (25)

| Category | Tools |
|----------|-------|
| Datasource | `list_datasources`, `use_datasource`, `get_active_datasource`, `add_datasource`, `update_datasource`, `remove_datasource`, `ping_datasource`, `get_pool_stats`, `list_drivers` |
| Metadata | `list_schemas`, `list_tables`, `list_indexes`, `describe_table`, `explain_query`, `sample_table`, `diff_schema` |
| Query | `execute_query` |
| DML + TX | `execute_dml`, `execute_ddl`, `begin_transaction`, `commit_transaction`, `rollback_transaction`, `get_transaction_status` |
| Import/Export | `export_table`, `import_csv` |

---

## 🛠️ CLI

| Command | Description |
|---------|-------------|
| `open-db-mcp init` | Guided config generation |
| `open-db-mcp doctor` | Health check |
| `open-db-mcp run` | Start stdio MCP server |
| `open-db-mcp list` | List datasources & pool status |

---

## 💬 Contact & Support

<div align="center">

<img src="image/wechat.png" alt="WeChat Contact" width="220" />
<img src="image/wechat-pay.png" alt="WeChat Pay QR Code" width="220" />

</div>

---

## 📄 License

[MIT](LICENSE) © cjp1016
