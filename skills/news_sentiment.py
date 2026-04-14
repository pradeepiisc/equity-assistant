"""
Skill: News Sentiment
=====================
Analyses a batch of news articles for a company and returns
structured sentiment, themes, and signals.
"""

import traceback
from llm.utils import load_prompt, call_llm, load_company_files, save_report


SKILL_NAME = "news_sentiment"


_WEIGHT_ICON = {"high": "🔴", "medium": "🟡", "low": "⚪"}
_TYPE_LABEL = {
    "company_event": "Company Event",
    "regulatory": "Regulatory",
    "management_statement": "Management Statement",
    "sector_news": "Sector News",
    "analyst_opinion": "Analyst Opinion",
}


def _build_markdown(symbol: str, company_name: str, data: dict) -> str:
    lines = [f"# News Sentiment — {company_name} ({symbol})\n"]

    sentiment = data.get("overall_sentiment", "N/A").upper()
    lines += [f"## Overall Sentiment: {sentiment}", ""]

    if breakdown := data.get("news_breakdown"):
        lines += ["## Article Breakdown"]
        for item in breakdown:
            w = item.get("weight", "low")
            t = _TYPE_LABEL.get(item.get("type", ""), item.get("type", ""))
            sig = item.get("signal", "?").upper()
            lines += [
                f"- **[{t}]** `{sig}` {item.get('title', '')}",
                f"  *{item.get('one_line', '')}*",
                "",
            ]
        lines.append("")

    if themes := data.get("key_themes"):
        lines += ["## Key Themes (Confirmed Events Only)"]
        for t in themes:
            lines.append(f"- {t}")
        lines.append("")

    if reg := data.get("regulatory_mentions"):
        lines += ["## Regulatory Mentions"]
        for item in reg:
            lines += [
                f"### {item.get('topic', '?')}",
                f"**Implication:** {item.get('implication', '')}",
                f"*Source: {item.get('source_hint', 'N/A')}*",
                "",
            ]

    if mgmt := data.get("management_mentions"):
        lines += ["## Management Commentary in News"]
        for item in mgmt:
            lines += [
                f"**{item.get('person', 'Unknown')}** [{item.get('signal', '?').upper()}]",
                item.get("statement_summary", ""),
                "",
            ]

    if tailwinds := data.get("sector_tailwinds"):
        lines += ["## Sector Tailwinds"]
        for t in tailwinds:
            lines.append(f"- {t}")
        lines.append("")

    if headwinds := data.get("sector_headwinds"):
        lines += ["## Sector Headwinds"]
        for h in headwinds:
            lines.append(f"- {h}")
        lines.append("")

    if ignored := data.get("analyst_opinions_ignored"):
        lines += ["## Analyst Opinions (Deprioritised)"]
        for item in ignored:
            lines.append(f"- {item}")
        lines.append("")

    if summary := data.get("summary"):
        lines += ["## Summary", summary, ""]

    return "\n".join(lines)


def run(company: dict, config: dict) -> dict:
    symbol = company["symbol"]
    company_name = company.get("name", symbol)
    sector = company.get("sector", "Unknown")

    result = {
        "skill_name": SKILL_NAME,
        "symbol": symbol,
        "status": "success",
        "data": {},
        "error": None,
    }

    try:
        raw_files = load_company_files(symbol, "news", extension=".txt")
        if not raw_files:
            raw_files = load_company_files(symbol, "news", extension=".md")

        if not raw_files:
            result["status"] = "no_data"
            result["error"] = "No news files found in news/ folder."
            print(f"[{SKILL_NAME}][{symbol}] No data found — skipping.")
            return result

        news_text = "\n\n".join(raw_files)
        print(f"[{SKILL_NAME}][{symbol}] Loaded {len(raw_files)} news file(s). Calling LLM...")

        prompt = load_prompt(
            "news_sentiment",
            company_name=company_name,
            sector=sector,
            news_articles=news_text,
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
