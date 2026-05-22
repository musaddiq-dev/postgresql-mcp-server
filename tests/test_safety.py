import pytest

from postgresql_mcp_server.server import execute_write_query, explain_query


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM users",
        "VACUUM",
        "GRANT SELECT ON users TO someone",
    ],
)
def test_execute_write_query_rejects_unexpected_statement_types_before_connection(query):
    with pytest.raises(ValueError):
        execute_write_query(query)


def test_execute_write_query_rejects_multiple_statements_before_connection():
    with pytest.raises(ValueError):
        execute_write_query("UPDATE users SET name = 'x'; DROP TABLE users")


def test_explain_query_rejects_multiple_statements_before_connection():
    with pytest.raises(ValueError):
        explain_query("SELECT * FROM users; SELECT * FROM secrets")
