"""
Skill: Transcript Analysis
==========================
Ingests earnings call transcript PDFs, extracts structured insights
via LLM, and saves a markdown report.
"""

import traceback
from llm.utils import load_prompt, call_llm, load_company_files, save_report


SKILL_NAME = "transcript_analysis"


def _build_markdown(symbol: str, company_name: str, data: dict) -> str:
    lines = [f"# Transcript Analysis — {company_name} ({symbol})\n"]

    if summary := data.get("executive_summary"):
        lines += ["## Executive Summary", summary, ""]

    if mt := data.get("management_tone"):
        lines += [f"## Management Tone — {mt.get('rating', 'N/A').upper()}"]
        for ev in mt.get("evidence", []):
            lines.append(f"- {ev}")
        lines.append("")

    if gvd := data.get("guidance_vs_delivery"):
        lines += ["## Guidance vs Delivery"]
        for item in gvd:
            lines += [
                f"### {item.get('quarter', '?')} — {item.get('verdict', '?').upper()}",
                f"**Promised:** {item.get('what_was_promised', '')}",
                f"**Delivered:** {item.get('what_was_delivered', '')}",
                "",
            ]

    if ge := data.get("guidance_evolution"):
        lines += ["## Guidance Evolution Across Quarters"]
        if ps := ge.get("pattern_summary"):
            lines += [f"*{ps}*", ""]
        for topic in ge.get("topics", []):
            consistency = topic.get("consistency", "?").replace("_", " ").title()
            lines += [f"### {topic.get('topic', '?')} — {consistency}"]
            for entry in topic.get("timeline", []):
                lines.append(f"- **{entry.get('quarter', '?')}:** {entry.get('what_was_said', '')}")
            if assessment := topic.get("assessment"):
                lines += [f"", f"*Assessment: {assessment}*"]
            lines.append("")

    if mc := data.get("margin_commentary"):
        lines += ["## Margin Commentary", mc, ""]

    if rg := data.get("revenue_growth_narrative"):
        lines += ["## Revenue Growth Narrative", rg, ""]

    if risks := data.get("key_risks_flagged_by_management"):
        lines += ["## Key Risks Flagged by Management"]
        for r in risks:
            lines.append(f"- {r}")
        lines.append("")

    if positives := data.get("positive_signals"):
        lines += ["## Positive Signals"]
        for p in positives:
            lines.append(f"- {p}")
        lines.append("")

    if red_flags := data.get("red_flags"):
        lines += ["## Red Flags"]
        for rf in red_flags:
            lines.append(f"- {rf}")
        lines.append("")

    if ois := data.get("overall_investment_signal"):
        lines += ["## Overall Investment Signal", f"**{ois}**", ""]

    return "\n".join(lines)


def run(company: dict, config: dict) -> dict:
    """
    Run transcript analysis for a single company.

    Args:
        company: Entry from watchlist.yaml (symbol, name, sector, ...)
        config:  Loaded config.yaml dict

    Returns:
        dict with keys: skill_name, symbol, status, data, error
    """
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
        max_n = config.get("skills", {}).get("max_transcripts_per_company", 5)
        raw_files = load_company_files(symbol, "transcripts", extension=".pdf")

        if not raw_files:
            raw_files = load_company_files(symbol, "transcripts", extension=".txt")

        if not raw_files:
            result["status"] = "no_data"
            result["error"] = "No transcript files found in transcripts/ folder."
            print(f"[{SKILL_NAME}][{symbol}] No transcript data found.")
            return result

        files_to_use = raw_files[-max_n:]
        transcripts_text = "\n\n".join(files_to_use)
        n_quarters = len(files_to_use)

        print(f"[{SKILL_NAME}][{symbol}] Loaded {n_quarters} transcript(s). Calling LLM...")

        prompt = load_prompt(
            "transcript_analysis",
            company_name=company_name,
            sector=sector,
            n_quarters=n_quarters,
            transcripts=transcripts_text,
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
