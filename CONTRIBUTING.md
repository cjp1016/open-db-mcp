# Contributing to open-db-mcp

Thanks for your interest in contributing! This document provides guidelines for contributing to the open-db-mcp project.

## Getting Started

### Prerequisites

- Python 3.10+
- uv (package manager)
- git

### Development Setup

```bash
# Clone the repository
git clone https://github.com/cjp1016/open-db-mcp.git
cd open-db-mcp

# Install dependencies
uv sync --all-extras

# Run type checking
uv run mypy src/

# Run linting
uv run ruff check src/
```

## Project Structure

```
src/open_db_mcp/
├── __init__.py           # Package metadata
├── __main__.py           # CLI entry point
├── server.py             # MCP server bootstrap
├── config.py             # Global settings
├── registry.py           # Data source registry
├── parser/               # Configuration parsers (JDCB URL, etc.)
├── drivers/              # Database driver adapters
│   ├── base.py           # DriverAdapter protocol
│   ├── factory.py        # Driver factory
│   ├── mysql_driver.py   # MySQL driver
│   └── oracle_driver.py  # Oracle driver
├── safety/               # Security layer (SQL analyzer, whitelist, audit)
├── tools/                # MCP tool definitions (thin adapter layer)
│   ├── ds_tools.py       # Data source management tools
│   ├── query_tools.py    # Query tools
│   ├── dml_tools.py      # DML tools
│   ├── ddl_tools.py      # DDL tools
│   ├── meta_tools.py     # Metadata browsing tools
│   └── txn_tools.py      # Transaction tools
├── services/             # Business logic layer
│   ├── query_service.py
│   ├── dml_service.py
│   └── ds_service.py
├── repositories/         # Persistence layer
│   ├── datasource_repo.py
│   └── whitelist_repo.py
└── errors.py             # Exception hierarchy
```

## Architecture Principles

We follow these design principles:

1. **Single Responsibility**: Each module/class should have one clear purpose
2. **Dependency Inversion**: Depend on abstractions (protocols), not concretions
3. **Open/Closed**: Open for extension, closed for modification (driver plugin system)
4. **Security First**: All user input is untrusted; defense in depth
5. **Testability**: Business logic should be testable without MCP server or real DB

## Code Style

- **Indentation**: 4 spaces
- **Line length**: 120 characters max
- **Naming**:
  - Classes: `PascalCase` (noun phrases, e.g. `DataSourceService`)
  - Functions/Methods: `snake_case` (verb phrases, e.g. `execute_query`)
  - Variables: `snake_case` (descriptive, no single letters)
  - Booleans: `is_` / `has_` / `can_` prefix
- **Type hints**: All public functions must have type annotations
- **Docstrings**: Only for public APIs; code should be self-documenting
- **Imports**: Sorted by ruff (`uv run ruff check --select I --fix`)

We use `ruff` for linting and formatting, and `mypy` for type checking.
Both must pass before a PR can be merged.

## How to Contribute

### Reporting Bugs

Use the GitHub issue tracker and include:

1. Python version (`python --version`)
2. open-db-mcp version (`pdm list open-db-mcp`)
3. Database type and version
4. Steps to reproduce
5. Expected behavior
6. Actual behavior / error traceback

### Suggesting Features

Open an issue with:

1. Use case description
2. Proposed API / behavior
3. Alternatives considered

### Submitting Changes

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes
4. Ensure linting passes (`uv run ruff check src/`)
5. Ensure type checking passes (`uv run mypy src/`)
6. Commit your changes
7. Push to the branch
8. Open a Pull Request

### Pull Request Checklist

- [ ] Linting passes (ruff)
- [ ] Type checking passes (mypy)
- [ ] No commented-out code
- [ ] No TODO/FIXME comments without an associated issue

## Adding a New Database Driver

Quick overview:

1. Create a new file in `src/open_db_mcp/drivers/` (e.g. `postgres_driver.py`)
2. Implement the `DriverAdapter` protocol
3. Register via entry points in `pyproject.toml`:
   ```toml
   [project.entry-points."open_db_mcp.drivers"]
   postgres = "open_db_mcp.drivers.postgres_driver:PostgresDriver"
   ```

## Security

If you discover a security vulnerability, please DO NOT open a public issue.
Instead, report it privately to the maintainers.

## License

By contributing to open-db-mcp, you agree that your contributions will be licensed
under the MIT license as described in the LICENSE file.
