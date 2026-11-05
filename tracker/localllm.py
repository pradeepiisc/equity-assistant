"""
The local model (ollama on this Mac) — free, so its calls never count against llm.daily_budget_inr.

  chat_json(conn, settings, prompt, purpose=…)   one JSON-mode call, cached on disk like extract.llm_json
  available(settings)                             is the ollama server up with llm.local_model pulled?

Calls are logged in llm_calls with model "local:<name>" (tokens and time, ₹0). Thinking is off: the tasks here are
extraction and short writing, where the reasoning trace only costs minutes.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time

import requests

from .util import now_ist

LOCAL_PREFIX = "local:"


def _cfg(settings: dict) -> tuple[str, str, float]:
    llm = settings["llm"]
    return (llm.get("local_url") or "http://127.0.0.1:11434").rstrip("/"), llm.get("local_model") or "qwen3.8:27b", \
        float(llm.get("local_timeout_s", 600))


def available(settings: dict, model: str | None = None) -> bool:
    url, default, _ = _cfg(settings)
    model = model or default
    try:
        tags = requests.get(f"{url}/api/tags", timeout=3).json().get("models") or []
    except Exception:
        return False
    return any(t.get("name") == model or t.get("model") == model for t in tags)


def unload(settings: dict, model: str | None = None) -> None:
    """Free the model's memory now instead of after keep_alive."""
    url, default, _ = _cfg(settings)
    try:
        requests.post(f"{url}/api/generate", json={"model": model or default, "keep_alive": 0}, timeout=30)
    except Exception:
        pass


def chat_json(conn: sqlite3.Connection | None, settings: dict, prompt: str, *, purpose: str, sid: str | None = None,
              doc_id: int | None = None, cache_name: str | None = None, max_tokens: int = 4000,
              system: str = "You are a careful financial-document extraction engine. Output one strict JSON object only — "
                            "no prose, no code fences.",
              examples: list[tuple[str, str]] | None = None, num_ctx: int = 32768, model: str | None = None) -> dict:
    """`examples`: (user, assistant) turns placed before the prompt (one-shot). `model`: instead of llm.local_model."""
    from .extract import RESPONSE_DIR, json_repair

    url, default, timeout = _cfg(settings)
    model = model or default
    cached = RESPONSE_DIR / f"{cache_name}.json" if cache_name else None
    if cached and cached.exists():
        return json.loads(cached.read_text())
    messages = [{"role": "system", "content": system}]
    for u, a in examples or []:
        messages += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
    messages.append({"role": "user", "content": prompt})
    # no "format": "json" — qwen3.8 under ollama 0.40 answers 501 "structured output is unavailable"; the prompt asks
    # for one JSON object and the reply is parsed leniently below
    body = {"model": model, "messages": messages, "stream": False, "think": False, "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": max_tokens}}
    t0 = time.monotonic()
    stamp = now_ist().isoformat(timespec="seconds")
    day = now_ist().date().isoformat()
    try:
        r = requests.post(f"{url}/api/chat", json=body, timeout=timeout)
        r.raise_for_status()
        out = r.json()
    except Exception as exc:
        if conn is not None:
            conn.execute("INSERT INTO llm_calls(ts, day, doc_id, sid, model, purpose, prompt_tokens, completion_tokens, ms, "
                         "ok, error) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (stamp, day, doc_id, sid, LOCAL_PREFIX + model, purpose, len(prompt) // 4, 0,
                          int((time.monotonic() - t0) * 1000), 0, f"{exc.__class__.__name__}: {exc}"[:300]))
            conn.commit()
        raise
    if conn is not None:
        conn.execute("INSERT INTO llm_calls(ts, day, doc_id, sid, model, purpose, prompt_tokens, completion_tokens, ms, ok, "
                     "error) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (stamp, day, doc_id, sid, LOCAL_PREFIX + model, purpose, out.get("prompt_eval_count") or 0,
                      out.get("eval_count") or 0, int((time.monotonic() - t0) * 1000), 1, None))
        conn.commit()
    content = (out.get("message") or {}).get("content") or "{}"
    content = re.sub(r"^\s*<think>.*?</think>", "", content, flags=re.S)
    content = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content.strip())
    data = _first_object(content, json_repair)
    if cached:
        RESPONSE_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(data, ensure_ascii=False))
    return data


def _first_object(content: str, repair) -> dict:
    """The first JSON object in the reply — the model sometimes writes a second one, or a sentence, after it."""
    i = content.find("{")
    if i < 0:
        return {}
    try:
        obj, _ = json.JSONDecoder().raw_decode(content[i:])
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", content[i:], re.S)
    try:
        return repair(m.group(0)) if m else {}
    except ValueError:
        return {}
