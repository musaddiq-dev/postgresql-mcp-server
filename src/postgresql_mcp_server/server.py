#!/usr/bin/env python3
"""
PostgreSQL Model Context Protocol (MCP) Server
A fully functional MCP server for interacting with PostgreSQL databases.

Version 2 - Enhanced with connection pooling and startup validation.
"""

import os
import re
import json
import logging
import sys
from typing import List, Dict, Any
from contextlib import contextmanager
import psycopg2
from psycopg2 import Error
from psycopg2 import sql
from psycopg2.pool import ThreadedConnectionPool
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field
from typing_extensions import Annotated

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("postgresql-mcp-server")

# Load environment variables
load_dotenv()

# =============================================================================
# CONNECTION POOL SETUP
# =============================================================================

_connection_pool: ThreadedConnectionPool | None = None
READ_QUERY_LIMIT = int(os.getenv("POSTGRES_READ_QUERY_LIMIT", "1000"))


def validate_environment() -> None:
    """
    Validate that all required environment variables are present.
    
    Raises:
        ValueError: If any required environment variable is missing or empty
    """
    required_vars = ['POSTGRES_HOST', 'POSTGRES_PORT', 'POSTGRES_USER', 'POSTGRES_DB']
    missing_vars = []
    
    for var in required_vars:
        value = os.getenv(var)
        if not value or value.strip() == '':
            missing_vars.append(var)
    
    if missing_vars:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing_vars)}. "
            f"Please set these in your .env file or environment."
        )


def test_connection() -> None:
    """
    Test the database connection to ensure it works.
    
    Raises:
        Exception: If the database connection fails
    """
    try:
        conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST"),
            port=os.getenv("POSTGRES_PORT"),
            user=os.getenv("POSTGRES_USER"),
            password=os.getenv("POSTGRES_PASSWORD"),
            dbname=os.getenv("POSTGRES_DB")
        )
        conn.close()
    except Exception as e:
        raise Exception(f"Database connection test failed: {str(e)}")


def initialize_connection_pool() -> ThreadedConnectionPool:
    """
    Initialize the global connection pool with validated environment variables.
    
    Creates a ThreadedConnectionPool for efficient connection reuse across
    multiple tool calls. This is a massive performance improvement over
    creating new connections for every query.
    
    Returns:
        ThreadedConnectionPool: The initialized connection pool
        
    Raises:
        Exception: If pool initialization fails
    """
    global _connection_pool
    
    if _connection_pool is not None:
        return _connection_pool
    
    try:
        _connection_pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            host=os.getenv("POSTGRES_HOST"),
            port=os.getenv("POSTGRES_PORT"),
            user=os.getenv("POSTGRES_USER"),
            password=os.getenv("POSTGRES_PASSWORD"),
            dbname=os.getenv("POSTGRES_DB")
        )
        return _connection_pool
    except Exception as e:
        raise Exception(f"Failed to initialize connection pool: {str(e)}")


@contextmanager
def get_db_connection():
    """
    Context manager that yields a database connection from the pool.
    
    This ensures connections are properly returned to the pool after use,
    even if an error occurs during query execution.
    
    Yields:
        psycopg2.extensions.connection: A database connection from the pool
        
    Raises:
        Exception: If getting a connection from the pool fails
    """
    conn = None
    try:
        if _connection_pool is None:
            initialize_connection_pool()
        if _connection_pool is None:
            raise RuntimeError("Connection pool is not initialized")
        conn = _connection_pool.getconn()
        yield conn
    except Exception as e:
        if conn:
            conn.rollback()
        raise Exception(f"Database operation failed: {str(e)}")
    finally:
        if conn:
            _connection_pool.putconn(conn)


# =============================================================================
# MCP SERVER INITIALIZATION
# =============================================================================

# Initialize FastMCP server
mcp = FastMCP("postgresql_mcp")


# =============================================================================
# MCP TOOLS
# =============================================================================

@mcp.tool(
    name="postgresql_list_tables",
    annotations=ToolAnnotations(title="List PostgreSQL Tables", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def list_tables() -> List[str]:
    """
    Returns a list of all table names in the public schema.
    
    Filters out system tables and returns only user-created tables.
    
    Returns:
        List[str]: List of table names in the public schema
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_type = 'BASE TABLE'
                ORDER BY table_name;
            """)
            tables = [row[0] for row in cursor.fetchall()]
            return tables


@mcp.tool(
    name="postgresql_describe_table",
    annotations=ToolAnnotations(title="Describe PostgreSQL Table", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def describe_table(
    table_name: Annotated[str, Field(description="Public table name to describe", min_length=1, max_length=63)],
) -> List[Dict[str, Any]]:
    """
    Queries information_schema.columns to return column details for a table.
    
    Args:
        table_name (str): The name of the table to describe
        
    Returns:
        List[Dict[str, Any]]: List of column dictionaries with name, type, and nullability
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Use parameterized query to prevent SQL injection
            cursor.execute("""
                SELECT 
                    column_name,
                    data_type,
                    is_nullable,
                    column_default,
                    character_maximum_length,
                    numeric_precision,
                    numeric_scale
                FROM information_schema.columns
                WHERE table_schema = 'public' 
                AND table_name = %s
                ORDER BY ordinal_position;
            """, (table_name,))
            
            columns = []
            for row in cursor.fetchall():
                columns.append({
                    "column_name": row[0],
                    "data_type": row[1],
                    "is_nullable": row[2],
                    "column_default": row[3],
                    "max_length": row[4],
                    "precision": row[5],
                    "scale": row[6]
                })
            return columns


@mcp.tool(
    name="postgresql_execute_read_query",
    annotations=ToolAnnotations(title="Execute PostgreSQL Read Query", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def execute_read_query(
    query: Annotated[str, Field(description="Single read-only PostgreSQL query", min_length=1, max_length=100000)],
) -> List[Dict[str, Any]]:
    """
    Executes read-only SQL queries (SELECT, etc). Safe to auto-approve.
    
    Uses READ ONLY transaction mode to ensure no data modification can occur.
    Automatically rolls back any transaction to prevent accidental commits.
    
    Args:
        query (str): The SQL query to execute
        
    Returns:
        List[Dict[str, Any]]: Query results as a list of dictionaries, or a string with error hints
        
    Raises:
        ValueError: If query attempts to modify data
    """
    with get_db_connection() as conn:
        try:
            # Set session to read-only mode to prevent any modifications
            conn.set_session(readonly=True)
            
            with conn.cursor() as cursor:
                cursor.execute(query)
                
                # Get column names
                columns = [desc[0] for desc in cursor.description]
                
                # Fetch bounded rows and convert to list of dictionaries
                rows = cursor.fetchmany(READ_QUERY_LIMIT + 1)
                if len(rows) > READ_QUERY_LIMIT:
                    rows = rows[:READ_QUERY_LIMIT]
                    logger.warning("Read query results truncated at %s rows", READ_QUERY_LIMIT)
                results = [dict(zip(columns, row)) for row in rows]
                
                return results
        except Error as e:
            # Rollback the failed transaction to allow schema lookup
            try:
                conn.rollback()
            except Exception as rollback_error:
                logger.debug("Rollback after query error failed: %s", rollback_error)
            
            error_msg = str(e)
            
            # Helper function to get all tables from the database
            def get_all_tables():
                tables = []
                try:
                    with conn.cursor() as cursor:
                        cursor.execute("""
                            SELECT table_name 
                            FROM information_schema.tables 
                            WHERE table_schema = 'public' 
                            AND table_type = 'BASE TABLE'
                        """)
                        tables = [row[0].lower() for row in cursor.fetchall()]
                except Exception as schema_error:
                    logger.debug("Could not load table list for schema hint: %s", schema_error)
                return tables
            
            # Helper function to get schema for a table
            def get_table_schema(table_name):
                columns = []
                try:
                    with conn.cursor() as cursor:
                        cursor.execute("""
                            SELECT column_name, data_type
                            FROM information_schema.columns
                            WHERE table_schema = 'public' 
                            AND table_name = %s
                            ORDER BY ordinal_position
                        """, (table_name,))
                        columns = [(row[0], row[1]) for row in cursor.fetchall()]
                except Exception as schema_error:
                    logger.debug("Could not load table schema for hint: %s", schema_error)
                return columns
            
            # Detect tables in the query
            all_tables = get_all_tables()
            query_lower = query.lower()
            found_tables = set()
            
            # Find tables mentioned in FROM, JOIN, or INTO clauses
            for table in all_tables:
                # Look for table name in the query (as a whole word)
                pattern = r'\b' + re.escape(table) + r'\b'
                if re.search(pattern, query_lower):
                    found_tables.add(table)
            
            # Build the schema hint
            hint_lines = [f"ERROR: {error_msg}", "", "--- SCHEMA HINT ---", "I found these tables in your query. Here are their valid columns:", ""]
            
            if found_tables:
                for table in sorted(found_tables):
                    # Get the actual table name from the query (preserve original case)
                    actual_table_name = table
                    columns = get_table_schema(actual_table_name)
                    
                    hint_lines.append(f"Table '{actual_table_name}':")
                    for col_name, col_type in columns:
                        hint_lines.append(f"  - {col_name} ({col_type})")
                    hint_lines.append("")
            else:
                hint_lines.append("No recognized tables found in your query.")
                hint_lines.append("")
                # List all available tables
                hint_lines.append("Available tables in database:")
                for table in sorted(all_tables):
                    hint_lines.append(f"  - {table}")
                hint_lines.append("")
            
            hint_lines.append("Please correct your SQL query using these exact column names.")
            
            raise RuntimeError("\n".join(hint_lines))
        finally:
            # Always rollback to ensure no accidental commits
            try:
                conn.rollback()
            except Exception as rollback_error:
                logger.debug("Final rollback after read query failed: %s", rollback_error)


@mcp.tool(
    name="postgresql_execute_write_query",
    annotations=ToolAnnotations(title="Execute PostgreSQL Write Query", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
)
def execute_write_query(
    query: Annotated[str, Field(description="Single INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, or TRUNCATE statement", min_length=1, max_length=100000)],
) -> str:
    """
    Executes data modification queries (INSERT, UPDATE, DELETE, DROP). Requires manual approval.
    
    Executes the query and explicitly commits the transaction. This tool should be used
    for any SQL statements that modify data or database structure.
    
    Args:
        query (str): The SQL query to execute
        
    Returns:
        str: A success message with the number of rows affected (if applicable)
    """
    query_upper = query.strip().upper()
    if ";" in query.strip().rstrip(";"):
        raise ValueError("Multiple SQL statements are not allowed.")
    allowed_starts = ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "TRUNCATE")
    if not query_upper.startswith(allowed_starts):
        raise ValueError(
            "Only INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, and TRUNCATE statements are allowed. "
            "Use postgresql_execute_read_query for read-only queries."
        )

    with get_db_connection() as conn:
        try:
            # Ensure session is in read-write mode (may be read-only from previous pool use)
            conn.set_session(readonly=False)
            with conn.cursor() as cursor:
                cursor.execute(query)
                
                # Try to get row count for INSERT/UPDATE/DELETE
                try:
                    row_count = cursor.rowcount
                    conn.commit()
                    return f"Query executed successfully. {row_count} row(s) affected."
                except Exception:
                    # For queries that don't have rowcount (like CREATE TABLE)
                    conn.commit()
                    return "Query executed successfully."
        except Exception as e:
            conn.rollback()
            raise Exception(f"Query execution failed: {str(e)}")


@mcp.tool(
    name="postgresql_explain_query",
    annotations=ToolAnnotations(title="Explain PostgreSQL Query", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def explain_query(
    query: Annotated[str, Field(description="Single PostgreSQL query to explain", min_length=1, max_length=100000)],
) -> List[Dict[str, Any]]:
    """
    Prepends EXPLAIN to the query to return the execution plan without running it.
    
    Args:
        query (str): The SQL query to explain
        
    Returns:
        List[Dict[str, Any]]: The EXPLAIN plan results
    """
    if ";" in query.strip().rstrip(";"):
        raise ValueError("Multiple SQL statements are not allowed.")

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Prepend EXPLAIN to the query
            explain_query = f"EXPLAIN {query}"
            cursor.execute(explain_query)
            
            # Get column names
            columns = [desc[0] for desc in cursor.description]
            
            # Fetch all rows and convert to list of dictionaries
            rows = cursor.fetchall()
            results = [dict(zip(columns, row)) for row in rows]
            
            return results


@mcp.tool(
    name="postgresql_get_database_summary",
    annotations=ToolAnnotations(title="Get PostgreSQL Database Summary", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def get_database_summary() -> Dict[str, Any]:
    """
    Returns a comprehensive summary of the database.
    
    Returns:
        Dict[str, Any]: Dictionary containing database name, PostgreSQL version, and table count
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Get PostgreSQL version
            cursor.execute("SELECT version();")
            version_result = cursor.fetchone()
            pg_version = version_result[0] if version_result else "Unknown"
            
            # Get database name
            cursor.execute("SELECT current_database();")
            db_result = cursor.fetchone()
            db_name = db_result[0] if db_result else "Unknown"
            
            # Get total count of tables in public schema
            cursor.execute("""
                SELECT COUNT(*) 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_type = 'BASE TABLE';
            """)
            table_count_result = cursor.fetchone()
            table_count = table_count_result[0] if table_count_result else 0
            
            return {
                "database_name": db_name,
                "postgresql_version": pg_version,
                "total_tables": table_count
            }


# =============================================================================
# ADVANCED TOOLS (Phase 2 & 3)
# =============================================================================

@mcp.tool(
    name="postgresql_get_relationships",
    annotations=ToolAnnotations(title="Get PostgreSQL Relationships", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def get_relationships(
    table_name: Annotated[str, Field(description="Public table name to inspect", min_length=1, max_length=63)],
) -> List[Dict[str, Any]]:
    """
    Queries information_schema to find Foreign Keys pointing TO and FROM this table.
    
    Args:
        table_name (str): The name of the table to analyze for relationships
        
    Returns:
        List[Dict[str, Any]]: List of foreign key relationships including:
            - relationship_type: 'incoming' or 'outgoing'
            - foreign_table_name: The other table involved in the relationship
            - constraint_name: Name of the foreign key constraint
            - column_name: The column in this table
            - foreign_column_name: The column in the referenced table
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            relationships = []
            
            # Get incoming FKs (other tables reference this table)
            cursor.execute("""
                SELECT
                    tc.table_name AS foreign_table_name,
                    tc.constraint_name,
                    kcu.column_name,
                    ccu.column_name AS foreign_column_name
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                    ON ccu.constraint_name = tc.constraint_name
                    AND ccu.table_schema = tc.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                    AND ccu.table_name = %s
                    AND tc.table_schema = 'public'
                    AND ccu.table_schema = 'public'
                ORDER BY tc.table_name, tc.constraint_name;
            """, (table_name,))
            
            for row in cursor.fetchall():
                relationships.append({
                    "relationship_type": "incoming",
                    "foreign_table_name": row[0],
                    "constraint_name": row[1],
                    "column_name": row[3],  # Referenced column in this table
                    "foreign_column_name": row[2]  # Column in the other table
                })
            
            # Get outgoing FKs (this table references other tables)
            cursor.execute("""
                SELECT
                    ccu.table_name AS foreign_table_name,
                    tc.constraint_name,
                    kcu.column_name,
                    ccu.column_name AS foreign_column_name
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                    ON ccu.constraint_name = tc.constraint_name
                    AND ccu.table_schema = tc.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                    AND tc.table_name = %s
                    AND tc.table_schema = 'public'
                    AND ccu.table_schema = 'public'
                ORDER BY ccu.table_name, tc.constraint_name;
            """, (table_name,))
            
            for row in cursor.fetchall():
                relationships.append({
                    "relationship_type": "outgoing",
                    "foreign_table_name": row[0],
                    "constraint_name": row[1],
                    "column_name": row[2],  # Column in this table
                    "foreign_column_name": row[3]  # Referenced column in the other table
                })
            
            return relationships


@mcp.tool(
    name="postgresql_analyze_indexes",
    annotations=ToolAnnotations(title="Analyze PostgreSQL Indexes", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def analyze_indexes(
    table_name: Annotated[str, Field(description="Public table name to inspect", min_length=1, max_length=63)],
) -> List[Dict[str, Any]]:
    """
    Returns all indexes, their columns, and sizes for the specified table.
    
    Uses pg_size_pretty to display human-readable sizes.
    
    Args:
        table_name (str): The name of the table to analyze indexes for
        
    Returns:
        List[Dict[str, Any]]: List of indexes with:
            - index_name: Name of the index
            - columns: List of column names in the index
            - is_unique: Whether the index enforces uniqueness
            - is_primary: Whether this is a primary key index
            - index_type: Type of index (btree, hash, etc.)
            - size: Human-readable size of the index
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT
                    i.relname AS index_name,
                    array_agg(a.attname ORDER BY array_position(ix.indkey, a.attnum)) AS columns,
                    ix.indisunique AS is_unique,
                    ix.indisprimary AS is_primary,
                    am.amname AS index_type,
                    pg_size_pretty(pg_relation_size(i.oid)) AS size
                FROM pg_index ix
                JOIN pg_class t ON t.oid = ix.indrelid
                JOIN pg_class i ON i.oid = ix.indexrelid
                JOIN pg_am am ON am.oid = i.relam
                JOIN pg_namespace n ON n.oid = t.relnamespace
                CROSS JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS c(attnum, ord)
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = c.attnum
                WHERE t.relname = %s
                    AND n.nspname = 'public'
                GROUP BY i.relname, ix.indisunique, ix.indisprimary, am.amname, i.oid
                ORDER BY i.relname;
            """, (table_name,))
            
            indexes = []
            for row in cursor.fetchall():
                indexes.append({
                    "index_name": row[0],
                    "columns": row[1],
                    "is_unique": row[2],
                    "is_primary": row[3],
                    "index_type": row[4],
                    "size": row[5]
                })
            
            return indexes


@mcp.tool(
    name="postgresql_preview_table",
    annotations=ToolAnnotations(title="Preview PostgreSQL Table", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def preview_table(
    table_name: Annotated[str, Field(description="Public table name to preview", min_length=1, max_length=63)],
) -> List[Dict[str, Any]]:
    """
    Returns the first 10 rows of a table (LIMIT 10).
    
    Useful for quickly inspecting table data without running full queries.
    
    Args:
        table_name (str): The name of the table to preview
        
    Returns:
        List[Dict[str, Any]]: List of up to 10 rows as dictionaries
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Use parameterized query with identifier
            query = sql.SQL("SELECT * FROM public.{} LIMIT 10").format(
                sql.Identifier(table_name)
            )
            cursor.execute(query)
            
            # Get column names
            columns = [desc[0] for desc in cursor.description]
            
            # Fetch all rows and convert to list of dictionaries
            rows = cursor.fetchall()
            results = [dict(zip(columns, row)) for row in rows]
            
            return results


@mcp.tool(
    name="postgresql_search_sql_definitions",
    annotations=ToolAnnotations(title="Search PostgreSQL SQL Definitions", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def search_sql_definitions(
    search_term: Annotated[str, Field(description="Term to search in public SQL definitions", min_length=1, max_length=200)],
) -> List[Dict[str, Any]]:
    """
    Searches information_schema.routines or pg_proc for code containing the search_term.
    
    Finds functions, procedures, and other database objects that contain the search term
    in their definitions or bodies.
    
    Args:
        search_term (str): The term to search for in SQL definitions
        
    Returns:
        List[Dict[str, Any]]: List of matching routines with:
            - routine_name: Name of the function/procedure
            - routine_type: Type of routine (FUNCTION, PROCEDURE, etc.)
            - schema_name: Schema containing the routine
            - definition: The routine definition (truncated if too long)
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Search in information_schema for functions and procedures
            cursor.execute("""
                SELECT
                    routine_name,
                    routine_type,
                    routine_schema AS schema_name,
                    LEFT(routine_definition, 500) AS definition
                FROM information_schema.routines
                WHERE routine_schema = 'public'
                    AND (
                        routine_definition ILIKE %s
                        OR routine_name ILIKE %s
                    )
                ORDER BY routine_name;
            """, (f'%{search_term}%', f'%{search_term}%'))
            
            matches = []
            for row in cursor.fetchall():
                matches.append({
                    "routine_name": row[0],
                    "routine_type": row[1],
                    "schema_name": row[2],
                    "definition": row[3]
                })
            
            # Also search pg_proc for views and other objects
            cursor.execute("""
                SELECT
                    p.proname AS routine_name,
                    'function' AS routine_type,
                    n.nspname AS schema_name,
                    LEFT(pg_get_functiondef(p.oid), 500) AS definition
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public'
                    AND (
                        pg_get_functiondef(p.oid) ILIKE %s
                        OR p.proname ILIKE %s
                    )
                ORDER BY p.proname;
            """, (f'%{search_term}%', f'%{search_term}%'))
            
            for row in cursor.fetchall():
                # Avoid duplicates by checking if we already have this routine
                if not any(m['routine_name'] == row[0] for m in matches):
                    matches.append({
                        "routine_name": row[0],
                        "routine_type": row[1],
                        "schema_name": row[2],
                        "definition": row[3]
                    })
            
            return matches


# =============================================================================
# MCP RESOURCES (Passive Data)
# =============================================================================

@mcp.resource("postgres://list_tables")
def list_tables_resource() -> str:
    """
    Returns a plain text list of all public tables.
    
    This is a passive resource that can be accessed via the postgres://list_tables URI.
    
    Returns:
        str: Plain text list of tables, one per line
    """
    tables = list_tables()
    return "\n".join(tables)


@mcp.resource("postgres://schema/{table_name}")
def table_schema_resource(table_name: str) -> str:
    """
    Returns the full schema definition (CREATE TABLE statement) for a specific table.
    
    This is a passive resource that can be accessed via the postgres://schema/{table_name} URI.
    
    Args:
        table_name (str): The name of the table
        
    Returns:
        str: The CREATE TABLE statement for the table
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Get the CREATE TABLE statement using pg_dump function
            cursor.execute("""
                SELECT 'CREATE TABLE ' || quote_ident(table_schema) || '.' || quote_ident(table_name) || ' (' ||
                       string_agg(
                           quote_ident(column_name) || ' ' ||
                           data_type ||
                           CASE WHEN character_maximum_length IS NOT NULL
                                THEN '(' || character_maximum_length || ')'
                                ELSE '' END ||
                           CASE WHEN is_nullable = 'NO' THEN ' NOT NULL' ELSE '' END ||
                           CASE WHEN column_default IS NOT NULL
                                THEN ' DEFAULT ' || column_default
                                ELSE '' END,
                           ', '
                       ) ||
                       ');' AS create_statement
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                GROUP BY table_schema, table_name;
            """, (table_name,))
            
            result = cursor.fetchone()
            if result and result[0]:
                return result[0]
            else:
                return f"-- Table '{table_name}' not found in public schema"


# =============================================================================
# SERVER ENTRY POINT
# =============================================================================

def main() -> None:
    """Run the PostgreSQL MCP server."""
    try:
        validate_environment()
        test_connection()
        initialize_connection_pool()
    except Exception as e:
        logger.error("PostgreSQL MCP server startup failed: %s", e)
        raise SystemExit(f"Error: Could not initialize PostgreSQL connection - {e}") from e

    logger.info("Starting PostgreSQL MCP Server")
    mcp.run()


if __name__ == "__main__":
    main()
