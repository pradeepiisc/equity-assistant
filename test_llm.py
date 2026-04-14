"""
LLM Connection Test
====================
Quick sanity-check that the Azure OpenAI client is configured correctly
and can reach the model. Tests both JSON mode and plain text mode.

Usage:
    python test_llm.py                        # uses active LLM_CLIENT from .env
    LLM_CLIENT=client_gpt41_itam python test_llm.py   # override to gpt-4.1
"""

import sys
import json
import time

from llm.client import get_active_config_name, get_model_name, is_gpt52, get_client, LLM_CONFIGS
from llm.utils import call_llm


def _separator(title: str) -> None:
    print(f"\n{'─' * 55}")
    print(f"  {title}")
    print(f"{'─' * 55}")


def test_plain_text() -> bool:
    _separator("TEST 1: Plain text response")
    prompt = "In exactly one sentence, what is equity research?"
    print(f"  Prompt: {prompt!r}")
    try:
        t0 = time.time()
        result = call_llm(prompt, expect_json=False)
        elapsed = time.time() - t0
        print(f"  Response ({elapsed:.1f}s): {result!r}")
        print("  ✓ PASS")
        return True
    except Exception as exc:
        print(f"  ✗ FAIL: {exc}")
        return False


def test_json_mode() -> bool:
    _separator("TEST 2: JSON mode response")
    prompt = (
        'Return a JSON object with two keys: '
        '"model_self_report" (the model name you believe you are) '
        'and "status" (always the string "ok").'
    )
    print(f"  Prompt: {prompt!r}")
    try:
        t0 = time.time()
        result = call_llm(prompt, expect_json=True)
        elapsed = time.time() - t0
        print(f"  Response ({elapsed:.1f}s): {json.dumps(result, indent=2)}")
        assert isinstance(result, dict), "Expected dict"
        assert "status" in result, "Expected 'status' key"
        print("  ✓ PASS")
        return True
    except Exception as exc:
        print(f"  ✗ FAIL: {exc}")
        return False


def main() -> None:
    print("\n" + "=" * 55)
    print("  EQUITY ASSISTANT — LLM CONNECTION TEST")
    print("=" * 55)

    active = get_active_config_name()
    model = get_model_name()
    gpt52 = is_gpt52()

    print(f"\n  Active config : {active}")
    print(f"  Model         : {model}")
    print(f"  GPT-5.2 mode  : {gpt52}  ({'max_completion_tokens, no temperature' if gpt52 else 'max_tokens, temperature=0.1'})")
    print(f"\n  Available configs:")
    for cfg in LLM_CONFIGS:
        marker = " ◀ active" if cfg["name"] == active else ""
        print(f"    • {cfg['name']} → {cfg['model_name']}{marker}")

    results = []
    results.append(test_plain_text())
    results.append(test_json_mode())

    _separator("SUMMARY")
    passed = sum(results)
    total = len(results)
    status = "ALL PASS ✓" if passed == total else f"{passed}/{total} passed"
    print(f"  {status}")
    print()

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
