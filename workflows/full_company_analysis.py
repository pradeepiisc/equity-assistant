"""
Workflow: Full Company Analysis
================================
Orchestrates all skills for a single company and produces a master report.

Usage:
    python -m workflows.full_company_analysis QPOWER
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from llm.client import get_config
from llm.utils import load_prompt, call_llm, save_report
from skills import (
    transcript_analysis,
    shareholding_analysis,
    news_sentiment,
    financial_snapshot,
    peer_comparison,
    valuation_agent,
    valuepickr_analysis,
)
from skills.company_meta import get_latest_price


def _load_all_companies() -> list[dict]:
    """Load companies from watchlist.yaml and portfolio_companies.yaml combined."""
    root = Path(__file__).parent.parent
    companies: dict[str, dict] = {}
    for yaml_file in ["watchlist.yaml", "portfolio_companies.yaml"]:
        p = root / yaml_file
        if not p.exists():
            continue
        with open(p, "r") as f:
            data = yaml.safe_load(f) or {}
        for c in data.get("stocks", []):
            sym = c.get("symbol", "").upper()
            if sym and sym not in companies:
                companies[sym] = c
    return list(companies.values())


def _find_company(symbol: str, all_companies: list[dict]) -> dict | None:
    symbol_upper = symbol.upper()
    for company in all_companies:
        if company.get("symbol", "").upper() == symbol_upper:
            return company
    return None


def _build_master_markdown(symbol: str, company_name: str, data: dict) -> str:
    lines = [
        f"# Master Investment Report — {company_name} ({symbol})",
        "",
        f"**Overall Rating:** {data.get('overall_rating', 'N/A')}",
        "",
    ]

    if thesis := data.get("investment_thesis"):
        lines += ["## Investment Thesis", thesis, ""]

    if risks := data.get("key_risks"):
        lines += ["## Key Risks"]
        for r in risks:
            severity = r.get("severity", "?").upper()
            lines += [
                f"### [{severity}] {r.get('risk', '?')}",
                f"{r.get('detail', '')}",
                f"*Source: {r.get('source', 'N/A')}*",
                "",
            ]

    if catalysts := data.get("key_catalysts"):
        lines += ["## Key Catalysts"]
        for c in catalysts:
            lines += [
                f"### {c.get('catalyst', '?')} — {c.get('timeline', 'N/A')}",
                c.get("detail", ""),
                "",
            ]

    if triggers := data.get("monitoring_triggers"):
        lines += ["## Monitoring Triggers"]
        for t in triggers:
            lines.append(f"- {t}")
        lines.append("")

    if fv := data.get("fair_value_summary"):
        lines += ["## Fair Value Summary", fv, ""]

    if action := data.get("action_recommendation"):
        lines += ["## Action Recommendation", action, ""]

    return "\n".join(lines)


def run(symbol: str) -> None:
    """
    Run a full company analysis for the given stock symbol.

    Steps:
    1. Load company from watchlist.yaml or portfolio_companies.yaml
    2. Run all skills (transcript required; others optional)
    3. Synthesise with master_report.txt prompt
    4. Save final report
    5. Print summary
    """
    print(f"\n{'='*60}")
    print(f"  FULL COMPANY ANALYSIS: {symbol.upper()}")
    print(f"{'='*60}\n")

    config = get_config()
    all_companies = _load_all_companies()
    company = _find_company(symbol, all_companies)

    if company is None:
        print(f"[ERROR] Symbol '{symbol}' not found in watchlist.yaml or portfolio_companies.yaml.")
        print("Add it first with the required fields (symbol, name, sector, screener_url).")
        return

    company_name = company.get("name", symbol)
    print(f"Company: {company_name}")
    print(f"Sector:  {company.get('sector', 'Unknown')}")

    # ── Inject latest stock price ─────────────────────────────────────────────
    price_info = get_latest_price(symbol)
    if price_info:
        company["last_price"] = price_info["price"]
        print(f"Price:   ₹{price_info['price']} (from {price_info['source']})")
    print()

    skill_results: dict[str, dict] = {}

    # ── Transcript Analysis (required) ───────────────────────────────────────
    print("─" * 40)
    print("Running: transcript_analysis")
    ta_result = transcript_analysis.run(company, config)
    skill_results["transcript_analysis"] = ta_result

    if ta_result["status"] == "error":
        print(f"\n[ABORT] transcript_analysis failed: {ta_result['error']}")
        print("Ensure transcript PDFs exist in data/companies/{symbol}/transcripts/")
        print("Or run: python -m skills.concall_fetcher {symbol}")
        return

    if ta_result["status"] == "no_data":
        print(f"\n[ABORT] No transcripts found for {symbol}.")
        print(f"Place PDF files in: data/companies/{symbol}/transcripts/")
        return

    # ── Optional Skills ───────────────────────────────────────────────────────
    optional_skills = [
        ("shareholding_analysis", shareholding_analysis),
        ("news_sentiment", news_sentiment),
        ("financial_snapshot", financial_snapshot),
        ("peer_comparison", peer_comparison),
        ("valuepickr_analysis", valuepickr_analysis),
    ]

    for skill_name, skill_module in optional_skills:
        print("─" * 40)
        print(f"Running: {skill_name}")
        result = skill_module.run(company, config)
        skill_results[skill_name] = result
        if result["status"] == "no_data":
            print(f"  → No data found, continuing without it.")
        elif result["status"] == "error":
            print(f"  → Error (non-fatal): {result['error']}")

    # ── Valuation Agent ───────────────────────────────────────────────────────
    print("─" * 40)
    print("Running: valuation_agent")
    va_result = valuation_agent.run(company, config, skill_results=skill_results)
    skill_results["valuation_agent"] = va_result
    if va_result["status"] == "error":
        print(f"  → Error (non-fatal): {va_result['error']}")
    elif va_result["status"] == "success":
        fv_low = va_result["data"].get("fair_value_low", "?")
        fv_high = va_result["data"].get("fair_value_high", "?")
        signal = va_result["data"].get("entry_signal", "?")
        print(f"  → Fair value: ₹{fv_low}–₹{fv_high}  |  Signal: {signal}")

    # ── Assemble Master Context ───────────────────────────────────────────────
    print("\n─" * 40)
    print("Synthesising master report...")

    skill_outputs_for_prompt: dict[str, object] = {}
    for skill_name, result in skill_results.items():
        if result["status"] == "success" and result.get("data"):
            skill_outputs_for_prompt[skill_name] = result["data"]
        else:
            skill_outputs_for_prompt[skill_name] = {
                "status": result["status"],
                "note": result.get("error") or "no data available",
            }

    # Inject insights_values + valuepickr from disk (generated by separate steps)
    _co_dir = Path(__file__).parent.parent / "data" / "companies" / symbol.upper()
    _iv_path = _co_dir / "insights" / "insights_values.md"
    if _iv_path.exists():
        skill_outputs_for_prompt["insights_values"] = {"content": _iv_path.read_text(encoding="utf-8")[:2500]}
    # valuepickr_analysis is already in skill_results from optional_skills above

    skill_outputs_json = json.dumps(skill_outputs_for_prompt, indent=2, ensure_ascii=False)

    master_prompt = load_prompt(
        "master_report",
        company_name=company_name,
        symbol=symbol.upper(),
        skill_outputs=skill_outputs_json,
    )

    master_data = call_llm(master_prompt, expect_json=True)

    markdown = _build_master_markdown(symbol, company_name, master_data)
    save_report(symbol, "master_report", markdown, raw_json=master_data)

    # ── Console Summary ───────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  ANALYSIS COMPLETE: {company_name}")
    print(f"{'='*60}")
    print(f"  Rating:     {master_data.get('overall_rating', 'N/A')}")
    if fv_summary := master_data.get("fair_value_summary"):
        print(f"  Valuation:  {fv_summary[:80]}")
    print(f"  Report:     data/companies/{symbol}/reports/master_report.md")
    print()

    if triggers := master_data.get("monitoring_triggers"):
        print("  Monitoring triggers:")
        for t in triggers[:3]:
            print(f"    • {t}")
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m workflows.full_company_analysis <SYMBOL>")
        print("Example: python -m workflows.full_company_analysis QPOWER")
        sys.exit(1)

    run(sys.argv[1].upper())
