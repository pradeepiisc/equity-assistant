"""
Test script: Validates each Azure OpenAI LLM configuration.

Ported from asset-assist-production/archive/test-llm-configs.ts

Run with: python scripts/test_llm_configs.py

Tests each config by sending a simple prompt and checking
if a valid JSON response comes back. GPT-5.2 constraints applied.
"""

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import AzureOpenAI

load_dotenv(Path(__file__).parent.parent / ".env")

# ─── Config definitions (mirrors llm/client.py) ───────────────────────────────

CONFIGS = [
    {
        "name": "client_gpt4o_clae",
        "azure_endpoint": "https://corellm-openai.openai.azure.com/",
        "api_key_env": "AZURE_OPENAI_API_KEY_CLAE",
        "api_version": "2024-02-15-preview",
        "model_name": "gpt-4o",
        "is_gpt52": False,
    },
    {
        "name": "client_gpt4o_itam",
        "azure_endpoint": "https://ham-ai-agents-delivery.openai.azure.com/",
        "api_key_env": "AZURE_OPENAI_API_KEY_ITAM",
        "api_version": "2024-12-01-preview",
        "model_name": "gpt-4o",
        "is_gpt52": False,
    },
    {
        "name": "client_gpt41_itam",
        "azure_endpoint": "https://ham-ai-agents-delivery.openai.azure.com/",
        "api_key_env": "AZURE_OPENAI_API_KEY_ITAM",
        "api_version": "2024-12-01-preview",
        "model_name": "gpt-4.1",
        "is_gpt52": False,
    },
    {
        "name": "client_gpt52_azure",
        "azure_endpoint": "https://arun-mlow8j4t-eastus2.openai.azure.com/",
        "api_key_env": "AZURE_OPENAI_API_KEY_GPT52",
        "api_version": "2025-03-01-preview",
        "model_name": "gpt-5.2-chat",
        "is_gpt52": True,
    },
]

TEST_SYSTEM = 'You are a test responder. Reply with ONLY a JSON object: { "status": "ok", "model": "<your model name>", "message": "Hello from Azure OpenAI" }'
TEST_USER   = "Respond with the JSON object as instructed."


def test_config(cfg: dict) -> bool:
    name       = cfg["name"]
    model      = cfg["model_name"]
    is_gpt52   = cfg["is_gpt52"]
    api_key    = os.getenv(cfg["api_key_env"], "")

    print(f"\n─── Testing: {name} (model: {model}) ───")
    print(f"    Endpoint:    {cfg['azure_endpoint']}")
    print(f"    API version: {cfg['api_version']}")
    print(f"    API key:     {api_key[:12] + '...' if api_key else 'MISSING'}")

    if not api_key:
        print(f"  ❌ SKIP — env var '{cfg['api_key_env']}' not set")
        return False

    client = AzureOpenAI(
        api_key=api_key,
        azure_endpoint=cfg["azure_endpoint"],
        api_version=cfg["api_version"],
    )

    kwargs: dict = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": TEST_SYSTEM},
            {"role": "user",   "content": TEST_USER},
        ],
    }
    if is_gpt52:
        kwargs["max_completion_tokens"] = 256
    else:
        kwargs["temperature"] = 0.1
        kwargs["max_tokens"]  = 256

    t0 = time.time()
    try:
        response = client.chat.completions.create(**kwargs)
        elapsed  = int((time.time() - t0) * 1000)
        content  = response.choices[0].message.content

        if not content:
            print(f"  ❌ FAIL — empty response ({elapsed}ms)")
            return False

        try:
            parsed = json.loads(content)
            print(f"  ✅ SUCCESS ({elapsed}ms)")
            print(f"  Response: {json.dumps(parsed)}")
            return True
        except json.JSONDecodeError:
            print(f"  ⚠️  PARTIAL — response not valid JSON ({elapsed}ms)")
            print(f"  Raw: {content[:200]}")
            return False

    except Exception as exc:
        elapsed = int((time.time() - t0) * 1000)
        msg = str(exc)
        print(f"  ❌ FAIL ({elapsed}ms)")
        print(f"  Error: {msg[:300]}")
        return False


def main():
    print("╔══════════════════════════════════════════════════╗")
    print("║  Azure OpenAI LLM Configuration Test            ║")
    print("╚══════════════════════════════════════════════════╝")
    print(f"\nConfigs to test: {', '.join(c['name'] for c in CONFIGS)}")

    results: list[tuple[str, bool]] = []
    for cfg in CONFIGS:
        ok = test_config(cfg)
        results.append((cfg["name"], ok))

    print("\n══════════════════════════════════════════════════")
    print("Summary:")
    for name, ok in results:
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}")

    working = [n for n, ok in results if ok]
    if working:
        print(f"\n🎉 Working configs: {', '.join(working)}")
    else:
        print("\n⚠️  No configs returned a successful response.")


if __name__ == "__main__":
    main()
