<!-- mcp-name: io.github.musaddiq-dev/postgresql-mcp-server -->
# PostgreSQL MCP Server

A Python Model Context Protocol (MCP) server for inspecting and querying PostgreSQL databases from MCP-compatible clients. It provides schema discovery, safe read-only query execution, query explanation, table previews, index analysis, relationship inspection, and PostgreSQL resources for table metadata.

## Features

- List public tables and inspect table schemas
- Execute read-only SQL in a PostgreSQL read-only transaction
- Explain query plans without executing the target query directly
- Preview table rows with a fixed limit
- Inspect foreign-key relationships and indexes
- Expose passive MCP resources for table lists and schema details

## Safety Model

`postgresql_execute_read_query` runs with PostgreSQL read-only transaction mode, caps returned rows by `POSTGRES_READ_QUERY_LIMIT`, and rolls back after execution. The server also includes `postgresql_execute_write_query`, which only accepts a single INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, or TRUNCATE statement and can modify data/schema if the connected database user has permission. Do not auto-approve write-capable tools in your MCP client. For public or shared use, run the server with a dedicated read-only PostgreSQL user.

## Requirements

- Python 3.11+
- PostgreSQL database
- MCP-compatible client such as Claude Desktop, Cursor, VS Code, or another MCP host

## Installation

When published to PyPI, install or run the server like a standard Python MCP package:

```bash
uvx mdev-postgresql-mcp-server
```

For local development from source:

```bash
git clone https://github.com/musaddiq-dev/postgresql-mcp-server.git
cd postgresql-mcp-server
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Configuration

Copy the example environment file and update it with your database connection details.

```bash
cp .env.example .env
```

| Variable | Description | Required | Default |
| --- | --- | --- | --- |
| `POSTGRES_HOST` | PostgreSQL host | Yes | `localhost` |
| `POSTGRES_PORT` | PostgreSQL port | Yes | `5432` |
| `POSTGRES_USER` | PostgreSQL username | Yes | None |
| `POSTGRES_PASSWORD` | PostgreSQL password | No | None |
| `POSTGRES_DB` | PostgreSQL database name | Yes | None |
| `LOG_LEVEL` | Python logging level written to stderr | No | `INFO` |
| `POSTGRES_READ_QUERY_LIMIT` | Maximum rows returned by read queries | No | `1000` |

Example read-only user:

```sql
CREATE USER mcp_readonly WITH PASSWORD 'change-me';
GRANT CONNECT ON DATABASE your_database TO mcp_readonly;
GRANT USAGE ON SCHEMA public TO mcp_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mcp_readonly;
```

## Running

```bash
mdev-postgresql-mcp-server
```

From a local checkout before PyPI publication, run:

```bash
python -m postgresql_mcp_server.server
```

## MCP Client Configuration

For published installs, prefer `uvx`. MCP servers using stdio must write protocol messages only to stdout; this server writes logs to stderr through Python logging.

### Claude Desktop / Cursor / Windsurf / Cline

Most MCP clients accept this `mcpServers` JSON shape:

```json
{
  "mcpServers": {
    "postgresql": {
      "command": "uvx",
      "args": ["mdev-postgresql-mcp-server"],
      "env": {
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_USER": "mcp_readonly",
        "POSTGRES_PASSWORD": "change-me",
        "POSTGRES_DB": "your_database"
      }
    }
  }
}
```

For local development from this repository, use the installed console script path instead:

```json
{
  "mcpServers": {
    "postgresql": {
      "command": "/absolute/path/to/postgresql-mcp-server/.venv/bin/mdev-postgresql-mcp-server",
      "args": [],
      "env": {
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_USER": "mcp_readonly",
        "POSTGRES_PASSWORD": "change-me",
        "POSTGRES_DB": "your_database"
      }
    }
  }
}
```

### Claude Code CLI

```bash
claude mcp add postgresql \
  --env POSTGRES_HOST=localhost \
  --env POSTGRES_PORT=5432 \
  --env POSTGRES_USER=mcp_readonly \
  --env POSTGRES_PASSWORD=change-me \
  --env POSTGRES_DB=your_database \
  -- uvx mdev-postgresql-mcp-server
```

### VS Code MCP

VS Code uses the same command/args/env model in its MCP configuration:

```json
{
  "servers": {
    "postgresql": {
      "type": "stdio",
      "command": "uvx",
      "args": ["mdev-postgresql-mcp-server"],
      "env": {
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_USER": "mcp_readonly",
        "POSTGRES_PASSWORD": "change-me",
        "POSTGRES_DB": "your_database"
      }
    }
  }
}
```

## Tools

| Tool | Purpose | Safety |
| --- | --- | --- |
| `postgresql_list_tables` | List public base tables | Read-only |
| `postgresql_describe_table` | Show columns and metadata for a table | Read-only |
| `postgresql_execute_read_query` | Run bounded SQL under read-only transaction mode | Read-only |
| `postgresql_execute_write_query` | Run a single approved modifying SQL statement and commit | Destructive |
| `postgresql_explain_query` | Return PostgreSQL `EXPLAIN` output for a single query | Read-only |
| `postgresql_get_database_summary` | Return database version and table count | Read-only |
| `postgresql_get_relationships` | Inspect foreign-key relationships | Read-only |
| `postgresql_analyze_indexes` | Inspect indexes and sizes | Read-only |
| `postgresql_preview_table` | Return up to 10 rows from a table | Read-only |
| `postgresql_search_sql_definitions` | Search public SQL routines/functions | Read-only |

## Resources

- `postgres://list_tables` returns public table names.
- `postgres://schema/{table_name}` returns a generated schema statement for a table.

## Smoke Check

Without a database, verify syntax with:

```bash
python -m py_compile src/postgresql_mcp_server/server.py
```

With a configured database, start the server and use your MCP client to call `list_tables`.

## Distribution

This server is published through the standard Python MCP distribution path:

- PyPI package: [`mdev-postgresql-mcp-server`](https://pypi.org/project/mdev-postgresql-mcp-server/)
- MCP Registry name: `io.github.musaddiq-dev/postgresql-mcp-server`
- Runtime hint: `uvx`
- Transport: `stdio`

The `mcp-name` marker at the top of this README is required for MCP Registry ownership verification. Users should prefer `uvx mdev-postgresql-mcp-server` in local MCP client configurations.

## Security Notes

- Do not commit `.env` or MCP client configs containing credentials.
- Use least-privilege database users.
- Treat `execute_write_query` as destructive and require explicit user approval in your MCP client.
- Review generated SQL before running write-capable tools.

## License

MIT
