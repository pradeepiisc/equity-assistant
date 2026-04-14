"""
Skill: Financial Snapshot
==========================
Analyses key financial metrics and trends for a company.
"""

import traceback
from llm.utils import load_prompt, call_llm, load_company_files, save_report


SKILL_NAME = "financial_snapshot"


def _build_markdown(symbol: str, company_name: str, data: dict) -> str:
    lines = [f"# Financial Snapshot — {company_name} ({symbol})\n"]

    if rt := data.get("revenue_trend"):
        lines += [
            f"## Revenue Trend — {rt.get('direction', 'N/A').upper()}",
            f"**Estimated CAGR:** {rt.get('cagr_estimate', 'N/A')}",
            rt.get("commentary", ""),
            "",
        ]

    if mt := data.get("margin_trend"):
        lines += [
            "## Margin Trend",
            f"- Gross Margin: {mt.get('gross_margin_direction', 'N/A')}",
            f"- EBITDA Margin: {mt.get('ebitda_margin_direction', 'N/A')} (latest: {mt.get('latest_ebitda_margin', 'N/A')})",
            f"- PAT Margin: {mt.get('pat_margin_direction', 'N/A')}",
            "",
            mt.get("commentary", ""),
            "",
        ]

    if ds := data.get("debt_situation"):
        lines += [
            f"## Debt Situation — {ds.get('assessment', 'N/A').upper()}",
            f"**Net D/E:** {ds.get('net_debt_equity', 'N/A')}",
            ds.get("commentary", ""),
            "",
        ]

    if cf := data.get("cash_flow_quality"):
        lines += [
            "## Cash Flow Quality",
            f"- Operating CF: {cf.get('operating_cf_trend', 'N/A')}",
            f"- FCF Generation: {cf.get('fcf_generation', 'N/A')}",
            "",
            cf.get("commentary", ""),
            "",
        ]

    if kr := data.get("key_ratios"):
        lines += [
            "## Key Ratios",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| P/E | {kr.get('pe_ratio', 'N/A')} |",
            f"| P/B | {kr.get('pb_ratio', 'N/A')} |",
            f"| ROE | {kr.get('roe', 'N/A')} |",
            f"| ROCE | {kr.get('roce', 'N/A')} |",
            f"| D/E | {kr.get('debt_to_equity', 'N/A')} |",
            f"| Current Ratio | {kr.get('current_ratio', 'N/A')} |",
            "",
        ]

    if red_flags := data.get("red_flags"):
        lines += ["## Red Flags"]
        for rf in red_flags:
            lines.append(f"- {rf}")
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
        raw_files = load_company_files(symbol, "financials", extension=".txt")
        if not raw_files:
            raw_files = load_company_files(symbol, "financials", extension=".json")
        if not raw_files:
            raw_files = load_company_files(symbol, "financials", extension=".csv")

        if not raw_files:
            result["status"] = "no_data"
            result["error"] = "No financial files found in financials/ folder."
            print(f"[{SKILL_NAME}][{symbol}] No data found — skipping.")
            return result

        financial_text = "\n\n".join(raw_files)
        print(f"[{SKILL_NAME}][{symbol}] Loaded {len(raw_files)} financial file(s). Calling LLM...")

        prompt = load_prompt(
            "financial_snapshot",
            company_name=company_name,
            financial_data=financial_text,
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
