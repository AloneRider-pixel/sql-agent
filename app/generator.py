"""NL -> SQL generation: schema-aware prompt, JSON extraction, guarded retry."""
import json
import re

SQL_PROMPT = """SQL_JSON:
You translate natural-language questions into ONE read-only PostgreSQL SELECT.
Schema (table: column type, ...):
{schema}

Reply with ONE JSON object (no prose, no markdown):
{{"sql": "SELECT ...", "explanation": "one sentence"}}
Rules: SELECT only, never INSERT/UPDATE/DELETE/DDL; always include a LIMIT (max 1000);
use only tables and columns from the schema above; if the question cannot be answered
from the schema, return {{"sql": null, "explanation": "why not"}}.
QUESTION: {question}
"""


def extract_json(text):
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text)
    start = cleaned.find("{")
    if start == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _valid(obj):
    return "sql" in obj and (obj["sql"] is None or isinstance(obj["sql"], str)) \
        and isinstance(obj.get("explanation", ""), str)


def generate_sql(question, schema, llm, retries=1):
    schema_text = "\n".join(
        f"{t}: " + ", ".join(f"{c} {d}" for c, d in cols) for t, cols in schema.items())
    prompt = SQL_PROMPT.format(schema=schema_text, question=question)
    for _ in range(retries + 1):
        raw = llm.complete(prompt)
        obj = extract_json(raw)
        if obj and _valid(obj):
            return obj, llm.active.name
        prompt += "\nPrevious reply was not valid JSON. Reply with JSON only."
    return {"sql": None, "explanation": "LLM output could not be parsed."}, llm.active.name
