"""
Skill: ValuePickr Analysis
===========================
Reads the raw ValuePickr forum thread (valuepickr.md) for a company and
uses an LLM to extract a structured peer-review — surfacing community-sourced
signals that may not appear in transcripts, financials, or Screener data.

Extracted fields:
  - community_sentiment   : bullish | neutral | bearish
  - valuation_view        : undervalued | fairly_valued | overvalued | unclear
  - tailwinds             : list of positive factors the community identified
  - headwinds             : list of risks / concerns raised
  - promoter_scrutiny     : any concerns about promoters (pledging, exits, conduct)
  - legal_regulatory      : any legal cases, SEBI actions, audit qualifications
  - missed_signals        : insights the community found that our analysis may have missed
  - key_debates           : main investment debates / disagreements in the thread
  - summary               : 2–3 sentence overall community verdict

Output saved to: data/companies/{SYMBOL}/reports/valuepickr_analysis.{md,json}

Run standalone:
    python -m skills.valuepickr_analysis SYMBOL
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from llm.utils import call_llm, save_report

SKILL_NAME = "valuepickr_analysis"
PROJECT_ROOT = Path(__file__).parent.parent
COMPANIES_DIR = PROJECT_ROOT / "data" / "companies"

MAX_VP_CHARS = 8000   # cap raw forum content sent to LLM

_PROMPT_TEMPLATE = """\
You are a senior equity analyst reviewing a community forum discussion about {company_name} ({symbol}).

Below is the ValuePickr investor forum thread — a crowd-sourced discussion by retail and professional
investors in India. Your job is to act as a **peer reviewer** of our own analysis: identify what the
community knows that standard financial analysis (transcripts, financials, Screener data) might miss.

Focus on:
1. Overall community sentiment and conviction level
2. Community view on valuation (cheap / fair / expensive)
3. Key tailwinds identified by the community
4. Key headwinds / concerns raised
5. Any promoter-related scrutiny (pledging, unusual transactions, management conduct)
6. Legal, regulatory, or audit issues mentioned
7. Insights or angles NOT typically found in management transcripts or financial data
8. The main investment debate / disagreement within the community

=== VALUEPICKR FORUM CONTENT ===
{vp_content}
=== END OF FORUM CONTENT ===

Respond ONLY with a valid JSON object (no markdown fences) with this exact structure:
{{
  "community_sentiment": "bullish" | "neutral" | "bearish",
  "sentiment_confidence": "high" | "medium" | "low",
  "valuation_view": "undervalued" | "fairly_valued" | "overvalued" | "unclear",
  "tailwinds": ["<string>", ...],
  "headwinds": ["<string>", ...],
  "promoter_scrutiny": ["<string>"] | [],
  "legal_regulatory": ["<string>"] | [],
  "missed_signals": ["<insight not in standard analysis>", ...],
  "key_debates": ["<main debate or disagreement>", ...],
  "summary": "<2-3 sentence community verdict>"
}}
"""


def _build_valuepickr_markdown(symbol: str, company_name: str, data: dict) -> str:
    lines = [
        f"# ValuePickr Community Analysis — {company_name} ({symbol})",
        "",
        f"**Community Sentiment:** {data.get('community_sentiment', 'N/A').title()}  "
        f"*(confidence: {data.get('sentiment_confidence', 'N/A')})*",
        f"**Valuation View:** {data.get('valuation_view', 'N/A').replace('_', ' ').title()}",
        "",
        "## Summary",
        data.get("summary", ""),
        "",
    ]

    def _section(title: str, items: list) -> list[str]:
        if not items:
            return []
        return [f"## {title}", *[f"- {i}" for i in items], ""]

    lines += _section("Tailwinds (Community-identified)", data.get("tailwinds", []))
    lines += _section("Headwinds / Concerns", data.get("headwinds", []))
    lines += _section("Key Debates", data.get("key_debates", []))
    lines += _section("Missed Signals (not in our standard analysis)", data.get("missed_signals", []))

    if ps := data.get("promoter_scrutiny"):
        lines += ["## ⚠️ Promoter Scrutiny", *[f"- {i}" for i in ps], ""]
    if lr := data.get("legal_regulatory"):
        lines += ["## ⚠️ Legal / Regulatory Issues", *[f"- {i}" for i in lr], ""]

    return "\n".join(lines)


def run(company: dict, config: dict) -> dict:
    """
    Run ValuePickr community analysis for the company.

    Returns standard skill result dict with status / data / error.
    """
    symbol = company["symbol"].upper()
    company_name = company.get("name", symbol)

    result: dict = {
        "skill_name": SKILL_NAME,
        "symbol": symbol,
        "status": "success",
        "data": {},
        "error": None,
    }

    try:
        vp_path = COMPANIES_DIR / symbol / "valuepickr.md"
        if not vp_path.exists():
            result["status"] = "no_data"
            result["error"] = f"valuepickr.md not found — run: python -m workflows.data_fetch {symbol}"
            return result

        vp_content = vp_path.read_text(encoding="utf-8")
        if not vp_content.strip():
            result["status"] = "no_data"
            result["error"] = "valuepickr.md is empty"
            return result

        # Cap content to avoid token overflow
        if len(vp_content) > MAX_VP_CHARS:
            vp_content = vp_content[:MAX_VP_CHARS] + "\n... [truncated]"

        prompt = _PROMPT_TEMPLATE.format(
            company_name=company_name,
            symbol=symbol,
            vp_content=vp_content,
        )

        print(f"[{SKILL_NAME}][{symbol}] Calling LLM for ValuePickr analysis...")
        llm_output = call_llm(prompt, expect_json=True)

        if not isinstance(llm_output, dict):
            raise ValueError(f"LLM returned non-dict: {type(llm_output)}")

        llm_output["generated_at"] = datetime.now().isoformat(timespec="seconds")

        markdown = _build_valuepickr_markdown(symbol, company_name, llm_output)
        save_report(symbol, SKILL_NAME, markdown, raw_json=llm_output)

        result["data"] = llm_output

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        print(f"[{SKILL_NAME}][{symbol}] ERROR: {exc}")

    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m skills.valuepickr_analysis SYMBOL")
        sys.exit(1)
    from llm.client import get_config
    from workflows.data_fetch import _load_company  # noqa: PLC0415
    sym = sys.argv[1].upper()
    co = _load_company(sym)
    cfg = get_config()
    res = run(co, cfg)
    print(json.dumps(res.get("data", {}), indent=2, ensure_ascii=False))
