"""Read-only safety layer for generated SQL.

Every statement the agent wants to run passes validate_readonly():
  - parses as exactly ONE statement (sqlglot, postgres dialect)
  - top-level is SELECT (or a WITH ... SELECT)
  - no write/DDL/command nodes anywhere in the tree
  - no dangerous server-side functions
  - a LIMIT is appended when missing (row cap)
"""
import sqlglot
from sqlglot import exp

DENIED_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter,
    exp.Create, exp.TruncateTable, exp.Merge, exp.Command,
    exp.Grant, exp.Copy, exp.LoadData,
)

DENIED_FUNCTIONS = {
    "pg_sleep", "dblink", "lo_export", "lo_import", "set_config",
    "pg_terminate_backend", "pg_cancel_backend", "pg_read_file",
    "pg_write_file", "pg_ls_dir", "current_setting", "query_to_xml",
}

MAX_ROWS = 1000


class UnsafeSQL(ValueError):
    pass


def validate_readonly(sql: str) -> str:
    """Returns the SQL unchanged (plus a LIMIT if missing) or raises UnsafeSQL."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s]
    except Exception as exc:
        raise UnsafeSQL(f"unparseable SQL: {exc}") from exc
    if len(statements) != 1:
        raise UnsafeSQL(f"exactly one statement required, got {len(statements)}")

    stmt = statements[0]
    top = stmt.find(exp.Select) if isinstance(stmt, exp.Union) else stmt
    if not isinstance(stmt, (exp.Select, exp.Union)) and not (
        isinstance(stmt, exp.With) and stmt.find(exp.Select)
    ):
        raise UnsafeSQL("only SELECT statements are allowed")

    for node in stmt.walk():
        if isinstance(node, DENIED_NODES):
            raise UnsafeSQL(f"forbidden statement type: {type(node).__name__}")
        if isinstance(node, exp.Anonymous) and node.name.lower() in DENIED_FUNCTIONS:
            raise UnsafeSQL(f"forbidden function: {node.name}")
        if isinstance(node, exp.Func) and getattr(node, "sql_name", lambda: "")().lower() in DENIED_FUNCTIONS:
            raise UnsafeSQL(f"forbidden function: {node.sql_name()}")

    if not stmt.args.get("limit") and not (isinstance(stmt, exp.Union) and stmt.args.get("limit")):
        stmt = stmt.copy()
        stmt.set("limit", exp.Limit(expression=exp.Literal.number(MAX_ROWS)))
    return stmt.sql(dialect="postgres")
