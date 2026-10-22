"""FastAPI surface for the SQL agent."""
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import db, generator
from .llm import LLMClient
from .sqlguard import UnsafeSQL, validate_readonly

app = FastAPI(title="SQL Agent", version="1.0.0")
llm = LLMClient()


class AskRequest(BaseModel):
    question: str = Field(..., min_length=3)


class AskResponse(BaseModel):
    question: str
    sql: str | None
    explanation: str
    provider: str
    columns: list = []
    rows: list = []
    truncated: bool = False


@app.get("/health")
def health():
    return {"status": "ok", "provider": llm.active.name,
            "database": bool(os.getenv("DATABASE_URL"))}


@app.get("/schema")
def schema():
    with db.connect() as conn:
        return db.introspect_schema(conn)


@app.post("/ask", response_model=AskResponse)
def ask(body: AskRequest):
    with db.connect() as conn:
        schema = db.introspect_schema(conn)
        if not schema:
            raise HTTPException(400, "database has no public tables")
        gen, provider = generator.generate_sql(body.question, schema, llm)
        if gen["sql"] is None:
            return AskResponse(question=body.question, sql=None,
                               explanation=gen["explanation"], provider=provider)
        try:
            safe_sql = validate_readonly(gen["sql"])
        except UnsafeSQL as exc:
            raise HTTPException(400, f"generated SQL rejected by the read-only guard: {exc}")
        columns, rows = db.run_readonly(conn, safe_sql)
    return AskResponse(question=body.question, sql=safe_sql,
                       explanation=gen["explanation"], provider=provider,
                       columns=columns, rows=[list(r) for r in rows])
