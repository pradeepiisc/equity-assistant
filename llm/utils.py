"""
LLM Utilities
=============
Shared helpers for prompt loading, LLM calls, token estimation,
file ingestion, and report saving.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from llm.client import get_client, get_config, get_model_name, is_gpt52

_prompt_cache: dict[str, str] = {}

PROJECT_ROOT = Path(__file__).parent.parent

TOKEN_WARNING_THRESHOLD = 800_000


# ─── Prompt Utilities ────────────────────────────────────────────────────────


def load_prompt(prompt_name: str, **kwargs) -> str:
    """
    Load prompts/{prompt_name}.txt and substitute {placeholder} values.
    Caches the raw template after first read.
    """
    config = get_config()
    prompts_root = PROJECT_ROOT / config["paths"]["prompts_root"]
    prompt_path = prompts_root / f"{prompt_name}.txt"

    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt not found: {prompt_path}")

    if prompt_name not in _prompt_cache:
        with open(prompt_path, "r", encoding="utf-8") as f:
            _prompt_cache[prompt_name] = f.read()

    template = _prompt_cache[prompt_name]

    for key, value in kwargs.items():
        template = template.replace(f"{{{key}}}", str(value))

    return template


# ─── Token Estimation ────────────────────────────────────────────────────────


def estimate_tokens(text: str) -> int:
    """
    Rough token estimate: len(text) / 4.
    Prints a warning if the estimate exceeds TOKEN_WARNING_THRESHOLD.
    """
    estimate = len(text) // 4
    if estimate > TOKEN_WARNING_THRESHOLD:
        print(
            f"[WARNING] Token estimate {estimate:,} exceeds {TOKEN_WARNING_THRESHOLD:,}. "
            f"Consider chunking or summarising input."
        )
    return estimate


# ─── LLM Call ────────────────────────────────────────────────────────────────


def call_llm(prompt: str, expect_json: bool = True) -> str | dict:
    """
    Call Azure OpenAI with the given prompt.

    - If expect_json=True: uses json_object response format, returns parsed dict.
      Retries once on JSON parse failure.
    - If expect_json=False: returns raw string content.
    - Handles gpt-5.2 API constraints: uses max_completion_tokens, skips temperature/top_p.
    Logs a token estimate before calling.
    """
    config = get_config()
    llm_cfg = config.get("llm", {})
    temperature = float(os.getenv("LLM_TEMPERATURE", str(llm_cfg.get("temperature", 0.1))))
    max_tokens = int(os.getenv("LLM_MAX_TOKENS", str(llm_cfg.get("max_tokens", 8192))))

    token_estimate = estimate_tokens(prompt)
    print(f"[LLM] Estimated prompt tokens: {token_estimate:,}  |  expect_json={expect_json}")

    client = get_client()
    model = get_model_name()
    gpt52 = is_gpt52()

    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior equity research analyst. "
                "Be precise, evidence-based, and always ground claims in the provided data."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    kwargs: dict[str, Any] = {"model": model, "messages": messages}

    if gpt52:
        kwargs["max_completion_tokens"] = max_tokens
    else:
        kwargs["temperature"] = temperature
        kwargs["max_tokens"] = max_tokens
        kwargs["top_p"] = 0.95
        kwargs["frequency_penalty"] = 0
        kwargs["presence_penalty"] = 0

    if expect_json:
        kwargs["response_format"] = {"type": "json_object"}

    max_retries = 2
    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content

            if not content:
                raise ValueError("LLM returned empty content.")

            if expect_json:
                return json.loads(content)
            else:
                return content

        except json.JSONDecodeError as exc:
            if attempt < max_retries:
                print(f"[LLM] JSON parse failed (attempt {attempt}), retrying with stricter instruction...")
                kwargs["messages"][-1]["content"] = (
                    prompt
                    + "\n\nIMPORTANT: Your previous response was not valid JSON. "
                    "Respond with ONLY a valid JSON object matching the required schema. No markdown fences."
                )
            else:
                raise ValueError(f"LLM returned invalid JSON after {max_retries} attempts: {exc}") from exc

    raise RuntimeError("call_llm: exhausted retries unexpectedly.")


# ─── File Loaders ────────────────────────────────────────────────────────────


def load_company_files(
    company_symbol: str,
    subfolder: str,
    extension: str = ".pdf",
) -> list[str]:
    """
    Load all files from data/companies/{symbol}/{subfolder}/ matching extension.

    - .pdf  → extracts text with pdfplumber
    - .txt  → reads as UTF-8 text
    - .json → reads and pretty-prints as string
    - .csv  → reads as UTF-8 text

    Returns list of text strings (one per file), sorted by filename.
    Empty list if folder missing or no matching files.
    """
    config = get_config()
    data_root = PROJECT_ROOT / config["paths"]["data_root"]
    folder = data_root / company_symbol / subfolder

    if not folder.exists():
        return []

    matched = sorted(folder.glob(f"*{extension}"))
    if not matched:
        matched = sorted(folder.glob("*"))

    results: list[str] = []
    for filepath in matched:
        if not filepath.is_file():
            continue

        suffix = filepath.suffix.lower()

        if suffix == ".pdf":
            text = _extract_pdf_text(filepath)
        elif suffix in (".txt", ".csv"):
            text = filepath.read_text(encoding="utf-8", errors="replace")
        elif suffix == ".json":
            raw = filepath.read_text(encoding="utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
                text = json.dumps(parsed, indent=2)
            except json.JSONDecodeError:
                text = raw
        else:
            continue

        if text.strip():
            results.append(f"--- FILE: {filepath.name} ---\n{text}")

    return results


def _extract_pdf_text(filepath: Path) -> str:
    """Extract text from a PDF using pdfplumber."""
    try:
        import pdfplumber
    except ImportError:
        raise ImportError("pdfplumber is required for PDF extraction. Run: pip install pdfplumber")

    pages: list[str] = []
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                pages.append(page_text)
    return "\n".join(pages)


# ─── Report Saving ───────────────────────────────────────────────────────────


def save_report(
    company_symbol: str,
    skill_name: str,
    content: str,
    raw_json: dict | None = None,
) -> None:
    """
    Save a markdown report to data/companies/{symbol}/reports/{skill_name}.md.
    If raw_json is provided and config.output.save_json_alongside is true,
    also saves data/companies/{symbol}/reports/{skill_name}.json.
    """
    config = get_config()
    data_root = PROJECT_ROOT / config["paths"]["data_root"]
    reports_dir = data_root / company_symbol / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    md_path = reports_dir / f"{skill_name}.md"
    md_path.write_text(content, encoding="utf-8")
    print(f"[REPORT] Saved → {md_path}")

    if raw_json is not None and config.get("output", {}).get("save_json_alongside", True):
        json_path = reports_dir / f"{skill_name}.json"
        json_path.write_text(json.dumps(raw_json, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[REPORT] JSON  → {json_path}")
