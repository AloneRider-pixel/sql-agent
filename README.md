# SQL Agent

Plain English to SQL over Postgres, with a hard read-only safety layer.
FastAPI service: POST /ask {"question": "..."} -> schema-aware LLM generates one
SELECT, a guard validates it, and it executes inside a READ ONLY transaction.

## What it does

- `POST /ask` - introspects the live schema (information_schema), asks the LLM for one
  read-only SELECT (+ explanation), validates it, runs it sandboxed, returns SQL + rows.
- `GET /schema` - dumps the introspected public schema.
- `GET /health` - provider + database status.
- Questions the schema cannot answer get an honest refusal, not invented SQL.

## Safety (the point of this project)

Three independent layers, each tested:

1. **sqlguard.py (parse-level):** sqlglot parses the SQL (postgres dialect); exactly one
   statement; top-level SELECT/WITH only; no Insert/Update/Delete/Drop/Alter/Create/
   Command nodes anywhere in the tree; deny-listed server functions (pg_sleep, dblink,
   pg_read_file, ...); appends LIMIT 1000 when missing.
2. **db.py (transaction-level):** every execution runs in `BEGIN TRANSACTION READ ONLY`
   with a `statement_timeout` and a row cap, and is rolled back - even if the guard were
   bypassed, Postgres itself rejects the write (proven by a test).
3. **Least privilege (operational):** point DATABASE_URL at a read-only role in production.

## LLM: free providers with retry + fallback

Groq (`GROQ_API_KEY`) -> Gemini (`GEMINI_API_KEY`) -> local Ollama -> documented mock,
with backoff and one invalid-JSON retry, same pattern as the sibling repos.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:pass@host/db
export GROQ_API_KEY=...    # or Gemini/Ollama, or skip for mock
uvicorn app.main:app --reload
```

## Demo + tests (both use a REAL embedded Postgres via pgserver)

```bash
python demo.py    # spins up Postgres, loads a demo schema, asks 3 questions
pytest tests -q   # 16 tests: guard unit tests + end-to-end on live Postgres + API
```

Verified locally: guard rejects DROP/DELETE/UPDATE/INSERT/multi-statement/pg_sleep;
auto-appends LIMIT; average-salary-by-department executes correctly on real Postgres;
the API refuses a "drop the table" request with a 400 from the guard.

## Honest limitations

- SQL execution is tested against embedded Postgres 15 (pgserver), not a managed cloud DB.
- The mock provider answers only the demo questions; real NL coverage needs an API key.
- Row results are JSON-serialized best-effort (Decimal/date need default handling in
  production hardening).
