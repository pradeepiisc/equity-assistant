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


def _load_dma_for_symbol(symbol: str) -> dict:
    """Load DMA data for a symbol from the portfolio .cache/ directory."""
    from datetime import date as _date
    cache_dir = Path(__file__).parent.parent / "portfolio" / ".cache"
    if not cache_dir.exists():
        return {}
    today = _date.today().isoformat()
    cache_file = cache_dir / f"dma_{today}.json"
    if not cache_file.exists():
        caches = sorted(cache_dir.glob("dma_*.json"), reverse=True)
        if caches:
            cache_file = caches[0]
        else:
            return {}
    try:
        all_dma = json.loads(cache_file.read_text())
        return all_dma.get(symbol.upper(), {})
    except Exception:
        return {}


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

    # ── Investment Thesis ─────────────────────────────────────────────────
    if thesis := data.get("investment_thesis"):
        lines += ["## Investment Thesis", thesis, ""]

    # ── Industry & Market Position ────────────────────────────────────────
    if imp := data.get("industry_and_market_position"):
        lines += ["## Industry & Market Position"]
        if overview := imp.get("industry_overview"):
            lines += ["### Industry Overview", overview, ""]
        if position := imp.get("company_position"):
            lines += ["### Company Position", position, ""]
        if export_info := imp.get("export_and_global"):
            lines += ["### Export & Global Opportunity", export_info, ""]
        if threats := imp.get("competitive_threats"):
            lines += ["### Competitive Threats", threats, ""]

    # ── Forward Revenue Path ──────────────────────────────────────────────
    if frp := data.get("forward_revenue_path"):
        lines += ["## Forward Revenue Path"]
        lines.append("| Period | Revenue (₹ Cr) | Basis |")
        lines.append("|---|---|---|")
        for key in ["fy_current", "fy_next", "fy_next2"]:
            fy = frp.get(key)
            if fy and isinstance(fy, dict):
                lines.append(f"| {fy.get('period', '?')} | {fy.get('revenue_cr', '?')} | {fy.get('basis', '?')} |")
        lines.append("")
        if et := frp.get("earnings_trajectory"):
            lines += ["### Earnings Trajectory", et, ""]
        if assumptions := frp.get("key_assumptions"):
            lines.append("### Key Assumptions")
            for a in assumptions:
                lines.append(f"- {a}")
            lines.append("")
        if risk := frp.get("risk_to_estimates"):
            lines += ["### Risk to Estimates", risk, ""]

    # ── Sector KPI Dashboard ──────────────────────────────────────────────
    if kpis := data.get("sector_kpi_dashboard"):
        lines += ["## Sector KPI Dashboard"]
        lines.append("| KPI | Value | Assessment | Trend | Comment |")
        lines.append("|---|---|---|---|---|")
        for kpi in kpis:
            assessment = kpi.get("assessment", "?")
            emoji = {"strong": "🟢", "adequate": "🟡", "weak": "🔴"}.get(assessment, "⚪")
            lines.append(
                f"| {kpi.get('metric', '?')} | {kpi.get('value', '?')} "
                f"| {emoji} {assessment} | {kpi.get('trend', '?')} "
                f"| {kpi.get('comment', '')} |"
            )
        lines.append("")

    # ── Technical Position ────────────────────────────────────────────────
    if tp := data.get("technical_position"):
        lines += ["## Technical Position"]
        for key, label in [
            ("price_vs_50dma", "Price vs 50 DMA"),
            ("price_vs_200dma", "Price vs 200 DMA"),
            ("dma_crossover", "DMA Crossover"),
            ("price_vs_52w", "Price vs 52-Week Range"),
        ]:
            if val := tp.get(key):
                lines.append(f"- **{label}:** {val}")
        if momentum := tp.get("momentum_assessment"):
            lines += ["", f"**Momentum:** {momentum}"]
        lines.append("")

    # ── Price Trend Reasoning ────────────────────────────────────────────
    if ptr := data.get("price_trend_reasoning"):
        trend_emoji = {
            "increasing_trend": "📈", "increasing_with_cap": "📈🔒",
            "decreasing_with_floor": "📉🛡️", "decreasing_trend": "📉",
            "range_bound": "↔️",
        }
        tc = ptr.get("trend_classification", "?")
        lines += [
            "## Price Trend Reasoning",
            f"**Trend:** {trend_emoji.get(tc, '📊')} {tc.replace('_', ' ').title()}",
        ]
        if ts := ptr.get("trend_summary"):
            lines.append(f"\n{ts}")
        if fd := ptr.get("fundamental_drivers"):
            lines.append("\n**Fundamental Drivers:**")
            for d in fd:
                impact_icon = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(d.get("impact", ""), "")
                lines.append(f"- {impact_icon} **{d.get('reason', '?')}** — {d.get('evidence', '?')}")
        if sd := ptr.get("sentiment_drivers"):
            lines.append("\n**Sentiment Drivers:**")
            for d in sd:
                impact_icon = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(d.get("impact", ""), "")
                lines.append(f"- {impact_icon} **{d.get('reason', '?')}** — {d.get('evidence', '?')}")
        if gap := ptr.get("price_vs_fundamentals_gap"):
            lines.append(f"\n**Price vs Fundamentals:** {gap}")
        lines.append("")

    # ── Key Risks ─────────────────────────────────────────────────────────
    if risks := data.get("key_risks"):
        lines += ["## Key Risks"]
        for r in risks:
            severity = r.get("severity", "?").upper()
            lines += [
                f"### [{severity}] {r.get('risk', '?')}",
                f"{r.get('detail', '')}",
            ]
            if mitigation := r.get("mitigation"):
                lines.append(f"*Mitigation: {mitigation}*")
            lines.append(f"*Source: {r.get('source', 'N/A')}*")
            lines.append("")

    # ── Key Catalysts ─────────────────────────────────────────────────────
    if catalysts := data.get("key_catalysts"):
        lines += ["## Key Catalysts"]
        for c in catalysts:
            prob = c.get("probability", "")
            prob_str = f" (probability: {prob})" if prob else ""
            lines += [
                f"### {c.get('catalyst', '?')} — {c.get('timeline', 'N/A')}{prob_str}",
                c.get("detail", ""),
            ]
            if source := c.get("source"):
                lines.append(f"*Source: {source}*")
            lines.append("")

    # ── Decision Framework ────────────────────────────────────────────────
    if df := data.get("decision_framework"):
        lines += ["## Decision Framework"]
        if invest := df.get("invest_more_triggers"):
            lines.append("### 🟢 Triggers to Invest More")
            for t in invest:
                lines.append(f"- {t}")
            lines.append("")
        if stay := df.get("stay_invested_reasons"):
            lines.append("### 🔵 Why Stay Invested (Thesis Intact)")
            for t in stay:
                lines.append(f"- {t}")
            lines.append("")
        if partial := df.get("partial_exit_triggers"):
            lines.append("### 🟠 Partial Exit Triggers (Trim 25-50%)")
            for t in partial:
                lines.append(f"- {t}")
            lines.append("")
        if full := df.get("complete_exit_triggers"):
            lines.append("### 🔴 Complete Exit Triggers (Sell All)")
            for t in full:
                lines.append(f"- {t}")
            lines.append("")

    # ── Monitoring Triggers ───────────────────────────────────────────────
    if triggers := data.get("monitoring_triggers"):
        lines += ["## Monitoring Triggers"]
        for t in triggers:
            lines.append(f"- {t}")
        lines.append("")

    # ── Fair Value Summary ────────────────────────────────────────────────
    if fv := data.get("fair_value_summary"):
        lines += ["## Fair Value Summary", fv, ""]

    # ── Action Recommendation ─────────────────────────────────────────────
    if action := data.get("action_recommendation"):
        lines += ["## Action Recommendation", action, ""]

    # ── Transcript Evidence Map ───────────────────────────────────────────
    if evidence := data.get("transcript_evidence_map"):
        lines += ["## Evidence Trail"]
        lines.append("| Conclusion | Supporting Evidence | Source |")
        lines.append("|---|---|---|")
        for e in evidence:
            lines.append(
                f"| {e.get('conclusion', '?')} "
                f"| {e.get('supporting_evidence', '?')} "
                f"| {e.get('quarter_source', '?')} |"
            )
        lines.append("")

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
        print(f"\n[WARN] transcript_analysis failed: {ta_result['error']}")
        print("  → Continuing without transcript data.")
        print("  → To add transcripts: python -m skills.concall_fetcher {symbol}")

    if ta_result["status"] == "no_data":
        print(f"\n[WARN] No transcripts found for {symbol}.")
        print("  → Continuing with other available data (financials, shareholding, news).")

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

    # ── Inject DMA / technical data ──────────────────────────────────────
    dma_data = _load_dma_for_symbol(symbol)
    if dma_data:
        ltp = price_info["price"] if price_info else None
        dma_context = {"available": True}
        for key in ["dma50", "dma200", "dma40", "dma100"]:
            if key in dma_data:
                dma_context[key] = dma_data[key]
        if ltp:
            dma_context["current_price"] = ltp
            if dma_data.get("dma50"):
                pct50 = (ltp - dma_data["dma50"]) / dma_data["dma50"] * 100
                dma_context["price_vs_50dma_pct"] = round(pct50, 1)
            if dma_data.get("dma200"):
                pct200 = (ltp - dma_data["dma200"]) / dma_data["dma200"] * 100
                dma_context["price_vs_200dma_pct"] = round(pct200, 1)
            if dma_data.get("dma50") and dma_data.get("dma200"):
                dma_context["dma50_vs_dma200"] = "above" if dma_data["dma50"] > dma_data["dma200"] else "below"
        skill_outputs_for_prompt["technical_dma_context"] = dma_context
    else:
        skill_outputs_for_prompt["technical_dma_context"] = {"available": False, "note": "DMA data not available — run daily portfolio report to compute"}

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

    if df := master_data.get("decision_framework"):
        if invest := df.get("invest_more_triggers"):
            print("  🟢 Invest more when:")
            for t in invest[:2]:
                print(f"    • {t}")
        if partial := df.get("partial_exit_triggers"):
            print("  🟠 Trim when:")
            for t in partial[:2]:
                print(f"    • {t}")
        if full := df.get("complete_exit_triggers"):
            print("  🔴 Exit when:")
            for t in full[:2]:
                print(f"    • {t}")

    if triggers := master_data.get("monitoring_triggers"):
        print("\n  Monitoring triggers:")
        for t in triggers[:3]:
            print(f"    • {t}")
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m workflows.full_company_analysis <SYMBOL>")
        print("Example: python -m workflows.full_company_analysis QPOWER")
        sys.exit(1)

    run(sys.argv[1].upper())
