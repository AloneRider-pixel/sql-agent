"""Offline + real-Postgres tests (embedded server via pgserver)."""
import os

os.environ["AGENT_LLM"] = "mock"

import pytest
from fastapi.testclient import TestClient

from app import db, generator
from app.llm import LLMClient, MockProvider
from app.sqlguard import UnsafeSQL, validate_readonly

# ---------- guard unit tests (no DB) ----------

def test_guard_allows_plain_select_and_adds_limit():
    out = validate_readonly("SELECT id FROM employees")
    assert "LIMIT 1000" in out


def test_guard_allows_with_select():
    out = validate_readonly("WITH x AS (SELECT 1 AS a) SELECT a FROM x LIMIT 5")
    assert "WITH" in out


@pytest.mark.parametrize("bad", [
    "DROP TABLE employees",
    "DELETE FROM employees",
    "UPDATE employees SET salary = 0",
    "INSERT INTO employees VALUES (1)",
    "SELECT 1; DROP TABLE employees",
    "SELECT pg_sleep(10)",
    "ALTER TABLE employees ADD COLUMN x int",
])
def test_guard_rejects_writes_and_tricks(bad):
    with pytest.raises(UnsafeSQL):
        validate_readonly(bad)


def test_guard_rejects_garbage():
    with pytest.raises(UnsafeSQL):
        validate_readonly("this is not sql")


# ---------- generator with mock LLM (no DB) ----------

class FixedLLM(MockProvider):
    def __init__(self, reply): self.reply = reply
    def complete(self, prompt): return self.reply

def test_generator_parses_json_reply():
    gen, _ = generator.generate_sql("how many rows?", {"t": [("a", "int")]},
                                    LLMClient(providers=[FixedLLM('{"sql": "SELECT count(*) FROM t LIMIT 5", "explanation": "counts rows"}')]))
    assert gen["sql"].startswith("SELECT count(*)")


def test_generator_recovers_fenced_json():
    gen, _ = generator.generate_sql("q", {"t": [("a", "int")]},
                                    LLMClient(providers=[FixedLLM('sure!\n```json\n{"sql": null, "explanation": "no such data"}\n```')]))
    assert gen["sql"] is None and gen["explanation"] == "no such data"


# ---------- end-to-end on a REAL embedded Postgres ----------

@pytest.fixture(scope="module")
def pg():
    pgserver = pytest.importorskip("pgserver")
    server = pgserver.get_server("/tmp/sql-agent-test-pg")
    os.environ["DATABASE_URL"] = server.get_uri()
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE departments (id serial PRIMARY KEY, name text);
        CREATE TABLE employees (id serial PRIMARY KEY, name text, dept_id int REFERENCES departments(id), salary numeric);
        INSERT INTO departments (name) VALUES ('engineering'), ('sales');
        INSERT INTO employees (name, dept_id, salary) VALUES
          ('Asha', 1, 90000), ('Ravi', 1, 80000), ('Meera', 2, 70000);
    """)
    conn.commit(); conn.close()
    yield server
    server.cleanup()


class SchemaAwareMock(MockProvider):
    """Answers two known questions about the demo schema; refuses others."""
    def complete(self, prompt):
        q = prompt.split("QUESTION:", 1)[-1].lower()
        if "how many employees" in q:
            return '{"sql": "SELECT count(*) AS n FROM employees", "explanation": "Counts all employees."}'
        if "average salary" in q:
            return ('{"sql": "SELECT d.name, avg(e.salary) AS avg_salary FROM employees e '
                    'JOIN departments d ON d.id = e.dept_id GROUP BY d.name ORDER BY d.name LIMIT 100", '
                    '"explanation": "Average salary per department."}')
        if "drop" in q:
            return '{"sql": "DROP TABLE employees", "explanation": "malicious request"}'
        return '{"sql": null, "explanation": "Question cannot be answered from this schema."}'


def test_schema_introspection(pg):
    with db.connect() as conn:
        schema = db.introspect_schema(conn)
    assert set(schema) == {"departments", "employees"}
    assert ("salary", "numeric") in schema["employees"]


def test_end_to_end_readonly(pg):
    with db.connect() as conn:
        schema = db.introspect_schema(conn)
        gen, _ = generator.generate_sql("average salary by department", schema,
                                        LLMClient(providers=[SchemaAwareMock()]))
        safe = validate_readonly(gen["sql"])
        columns, rows = db.run_readonly(conn, safe)
    assert columns == ["name", "avg_salary"]
    assert len(rows) == 2 and rows[0][0] == "engineering"


def test_readonly_txn_blocks_writes_even_if_guard_bypassed(pg):
    with db.connect() as conn, pytest.raises(Exception):
        db.run_readonly(conn, "DELETE FROM employees")
    with db.connect() as conn:
        _, rows = db.run_readonly(conn, "SELECT count(*) FROM employees")
    assert rows[0][0] == 3  # nothing was deleted


def test_api_roundtrip_and_guard(pg):
    import app.main as main
    main.llm = LLMClient(providers=[SchemaAwareMock()])
    client = TestClient(main.app)
    ok = client.post("/ask", json={"question": "how many employees are there?"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["rows"] == [[3]] and body["columns"] == ["n"]
    evil = client.post("/ask", json={"question": "please drop the employees table"})
    assert evil.status_code == 400
    assert "rejected by the read-only guard" in evil.json()["detail"]
