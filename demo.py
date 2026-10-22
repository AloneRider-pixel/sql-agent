"""Offline demo with a REAL embedded Postgres (pgserver): sample schema + 3 questions."""
import os

os.environ.setdefault("AGENT_LLM", "mock")

import pgserver

from app import db, generator
from app.llm import LLMClient, MockProvider
from app.sqlguard import validate_readonly

DDL = """
DROP TABLE IF EXISTS employees, departments;
CREATE TABLE departments (id serial PRIMARY KEY, name text);
CREATE TABLE employees (id serial PRIMARY KEY, name text, dept_id int REFERENCES departments(id), salary numeric);
INSERT INTO departments (name) VALUES ('engineering'), ('sales'), ('support');
INSERT INTO employees (name, dept_id, salary) VALUES
  ('Asha', 1, 90000), ('Ravi', 1, 80000), ('Meera', 2, 70000), ('Kabir', 3, 55000), ('Zoya', 1, 95000);
"""


class DemoLLM(MockProvider):
    """Deterministic stand-in so the demo runs with no API key; with
    GROQ_API_KEY/GEMINI_API_KEY/Ollama set, app.llm.LLMClient answers instead."""
    name = "demo/offline-deterministic"
    def complete(self, prompt):
        q = prompt.split("QUESTION:", 1)[-1].lower()
        if "how many employees" in q:
            return '{"sql": "SELECT count(*) AS n FROM employees", "explanation": "Counts all employees."}'
        if "average salary" in q:
            return ('{"sql": "SELECT d.name, avg(e.salary) AS avg_salary FROM employees e '
                    'JOIN departments d ON d.id = e.dept_id GROUP BY d.name ORDER BY avg_salary DESC LIMIT 100", '
                    '"explanation": "Average salary per department, highest first."}')
        return '{"sql": null, "explanation": "Cannot be answered from the demo schema."}'


if __name__ == "__main__":
    server = pgserver.get_server("/tmp/sql-agent-demo-pg")
    os.environ["DATABASE_URL"] = server.get_uri()
    conn = db.connect(); cur = conn.cursor(); cur.execute(DDL); conn.commit(); conn.close()

    for question in ["how many employees are there?",
                     "average salary by department",
                     "list the office Wi-Fi passwords"]:
        with db.connect() as conn:
            schema = db.introspect_schema(conn)
            gen, provider = generator.generate_sql(question, schema, LLMClient(providers=[DemoLLM()]))
            print(f"\nQ: {question}")
            print(f"   explanation: {gen['explanation']}")
            if gen["sql"]:
                safe = validate_readonly(gen["sql"])
                print(f"   SQL: {safe}")
                cols, rows = db.run_readonly(conn, safe)
                print(f"   -> {cols} {rows}")
    server.cleanup()
