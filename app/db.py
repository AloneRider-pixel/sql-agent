"""Postgres access: schema introspection + tightly sandboxed read-only execution."""
import os

import psycopg2

def connect(url=None):
    return psycopg2.connect(url or os.getenv("DATABASE_URL", ""))


def introspect_schema(conn, schema="public"):
    """Returns {table: [(column, type), ...]} for the given schema."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = %s
        ORDER BY table_name, ordinal_position
        """,
        (schema,))
    out = {}
    for table, column, dtype in cur.fetchall():
        out.setdefault(table, []).append((column, dtype))
    return out


def run_readonly(conn, sql, row_cap=1000, statement_timeout_ms=5000):
    """Executes inside a READ ONLY transaction with a statement timeout.
    Never returns more than row_cap rows."""
    cur = conn.cursor()
    cur.execute("BEGIN TRANSACTION READ ONLY")
    cur.execute(f"SET LOCAL statement_timeout = {int(statement_timeout_ms)}")
    try:
        cur.execute(sql)
        rows = cur.fetchmany(row_cap)
        columns = [d[0] for d in cur.description] if cur.description else []
    finally:
        conn.rollback()  # read-only txn: rollback, never commit
    return columns, rows
