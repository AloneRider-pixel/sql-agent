"""LLM access with retry + provider fallback (Groq -> Gemini -> Ollama -> mock).

Free tiers only. Set GROQ_API_KEY or GEMINI_API_KEY, or run a local Ollama.
With nothing configured the deterministic mock reviewer answers, so the
pipeline is always runnable and testable offline. AGENT_LLM=mock forces mock.
"""
from __future__ import annotations

import json
import os
import random
import time
import urllib.request


class LLMError(RuntimeError):
    pass


def _post_json(url, payload, headers=None, timeout=60):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


class Provider:
    name = "base"
    def available(self): return False
    def complete(self, prompt): raise NotImplementedError


class GroqProvider(Provider):
    def __init__(self, model="llama-3.3-70b-versatile"):
        self.key = os.getenv("GROQ_API_KEY"); self.model = model
        self.name = f"groq/{model}"
    def available(self): return bool(self.key)
    def complete(self, prompt):
        data = _post_json("https://api.groq.com/openai/v1/chat/completions",
                          {"model": self.model, "messages": [{"role": "user", "content": prompt}],
                           "temperature": 0.2},
                          headers={"Authorization": f"Bearer {self.key}"}, timeout=30)
        return data["choices"][0]["message"]["content"]


class GeminiProvider(Provider):
    def __init__(self, model="gemini-2.0-flash"):
        self.key = os.getenv("GEMINI_API_KEY"); self.model = model
        self.name = f"gemini/{model}"
    def available(self): return bool(self.key)
    def complete(self, prompt):
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent?key={self.key}")
        data = _post_json(url, {"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
        return data["candidates"][0]["content"]["parts"][0]["text"]


class OllamaProvider(Provider):
    def __init__(self, model=None, base="http://localhost:11434"):
        self.base = base.rstrip("/"); self.model = model or os.getenv("OLLAMA_MODEL", "llama3.2")
        self.name = f"ollama/{self.model}"
    def available(self):
        try:
            with urllib.request.urlopen(self.base + "/api/tags", timeout=2):
                return True
        except Exception:
            return False
    def complete(self, prompt):
        return _post_json(self.base + "/api/generate",
                          {"model": self.model, "prompt": prompt, "stream": False},
                          timeout=120)["response"]


class MockProvider(Provider):
    """Deterministic offline reviewer: pattern-scans added diff lines."""
    name = "mock/offline-deterministic"

    RULES = [
        ("eval(", "high", "eval() on dynamic input is a code-injection risk; use a safe parser."),
        ("exec(", "high", "exec() on dynamic input is a code-injection risk."),
        ("password", "medium", "Possible hardcoded credential; move secrets to environment variables."),
        ("secret", "medium", "Possible hardcoded secret; move it to configuration."),
        ("TODO", "low", "TODO left in the diff; track or resolve before merge."),
        ("print(", "low", "Debug print left in code; use structured logging."),
        ("except:", "medium", "Bare except swallows all errors; catch specific exceptions."),
    ]

    def available(self): return True

    def complete(self, prompt):
        diff = prompt.split("DIFF:", 1)[-1]
        findings = []
        current_file = "unknown"
        new_line = 0
        for line in diff.splitlines():
            if line.startswith("+++ b/"):
                current_file = line[6:]
            elif line.startswith("@@"):
                try:
                    new_line = int(line.split("+")[1].split(",")[0]) - 1
                except Exception:
                    new_line = 0
            elif line.startswith("+") and not line.startswith("+++"):
                new_line += 1
                for needle, sev, comment in self.RULES:
                    if needle in line:
                        findings.append({"file": current_file, "line": new_line,
                                         "severity": sev, "comment": comment})
                        break
            elif not line.startswith("-"):
                if new_line:
                    new_line += 1
        return json.dumps({"summary": f"Mock review: {len(findings)} finding(s) from pattern rules.",
                           "findings": findings})


class LLMClient:
    def __init__(self, providers=None, retries=2):
        if providers is None and os.getenv("AGENT_LLM") == "mock":
            providers = [MockProvider()]
        self.providers = providers or [GroqProvider(), GeminiProvider(), OllamaProvider(), MockProvider()]
        self.retries = retries
        self.attempts = []
        self.active = next((p for p in self.providers if p.available()), MockProvider())

    def complete(self, prompt):
        last_err = None
        chain = [self.active] + [p for p in self.providers if p is not self.active]
        for provider in chain:
            if not provider.available():
                continue
            for i in range(self.retries):
                try:
                    out = provider.complete(prompt)
                    self.active = provider
                    self.attempts.append((provider.name, "ok"))
                    return out
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    self.attempts.append((provider.name, f"error: {exc}"))
                    time.sleep(min(2 ** i, 4) * 0.5 + random.uniform(0, 0.3))
        raise LLMError(f"all providers failed; last error: {last_err}")
