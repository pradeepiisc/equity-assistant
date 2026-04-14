"""
Skill: Shareholding Analysis
=============================
Analyses FII/DII/promoter holding changes across quarters.
"""

import traceback
from llm.utils import load_prompt, call_llm, load_company_files, save_report


SKILL_NAME = "shareholding_analysis"


def _build_markdown(symbol: str, company_name: str, data: dict) -> str:
    lines = [f"# Shareholding Analysis — {company_name} ({symbol})\n"]

    for key, label in [
        ("promoter_trend", "Promoter Trend"),
        ("fii_trend", "FII Trend"),
        ("dii_trend", "DII Trend"),
    ]:
        if trend := data.get(key):
            direction = trend.get("direction", "N/A").upper()
            latest = trend.get("latest_holding_pct", "N/A")
            change = trend.get("change_over_period", "N/A")
            commentary = trend.get("commentary", "")
            lines += [
                f"## {label} — {direction}",
                f"**Latest:** {latest}%  |  **Change:** {change} pp",
                commentary,
                "",
            ]

    if notable := data.get("notable_changes"):
        lines += ["## Notable Changes"]
        for n in notable:
            lines.append(f"- {n}")
        lines.append("")

    if red_flags := data.get("red_flags"):
        lines += ["## Red Flags"]
        for rf in red_flags:
            lines.append(f"- {rf}")
        lines.append("")

    if positives := data.get("positive_signals"):
        lines += ["## Positive Signals"]
        for p in positives:
            lines.append(f"- {p}")
        lines.append("")

    if summary := data.get("summary"):
        lines += ["## Summary", summary, ""]

    return "\n".join(lines)


def run(company: dict, config: dict) -> dict:
    symbol = company["symbol"]
    company_name = company.get("name", symbol)

    result = {
        "skill_name": SKILL_NAME,
        "symbol": symbol,
        "status": "success",
        "data": {},
        "error": None,
    }

    try:
        raw_files = load_company_files(symbol, "shareholding", extension=".json")
        if not raw_files:
            raw_files = load_company_files(symbol, "shareholding", extension=".csv")
        if not raw_files:
            raw_files = load_company_files(symbol, "shareholding", extension=".txt")

        if not raw_files:
            result["status"] = "no_data"
            result["error"] = "No shareholding files found."
            print(f"[{SKILL_NAME}][{symbol}] No data found — skipping.")
            return result

        shareholding_text = "\n\n".join(raw_files)
        print(f"[{SKILL_NAME}][{symbol}] Loaded {len(raw_files)} shareholding file(s). Calling LLM...")

        prompt = load_prompt(
            "shareholding_analysis",
            company_name=company_name,
            shareholding_data=shareholding_text,
        )

        llm_output = call_llm(prompt, expect_json=True)
        result["data"] = llm_output

        markdown = _build_markdown(symbol, company_name, llm_output)
        save_report(symbol, SKILL_NAME, markdown, raw_json=llm_output)

        print(f"[{SKILL_NAME}][{symbol}] Done.")

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        result["data"] = {}
        print(f"[{SKILL_NAME}][{symbol}] ERROR: {exc}")
        traceback.print_exc()

    return result
