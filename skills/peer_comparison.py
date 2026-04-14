"""
Skill: Peer Comparison
=======================
Compares the company against sector peers using available data.
"""

import traceback
from llm.utils import load_prompt, call_llm, load_company_files, save_report


SKILL_NAME = "peer_comparison"


def _build_markdown(symbol: str, company_name: str, data: dict) -> str:
    lines = [f"# Peer Comparison — {company_name} ({symbol})\n"]

    if rv := data.get("relative_valuation"):
        lines += [
            f"## Relative Valuation — {rv.get('valuation_verdict', 'N/A')}",
            f"- P/E vs peers: {rv.get('pe_vs_peers', 'N/A')}",
            f"- P/B vs peers: {rv.get('pb_vs_peers', 'N/A')}",
            f"- EV/EBITDA vs peers: {rv.get('ev_ebitda_vs_peers', 'N/A')}",
            "",
            rv.get("commentary", ""),
            "",
        ]

    if rg := data.get("relative_growth"):
        lines += [
            "## Relative Growth",
            f"- Revenue growth rank: {rg.get('revenue_growth_rank', 'N/A')}",
            f"- Earnings growth rank: {rg.get('earnings_growth_rank', 'N/A')}",
            "",
            rg.get("commentary", ""),
            "",
        ]

    if rm := data.get("relative_margins"):
        lines += [
            "## Relative Margins",
            f"- EBITDA margin rank: {rm.get('ebitda_margin_rank', 'N/A')}",
            f"- PAT margin rank: {rm.get('pat_margin_rank', 'N/A')}",
            f"- ROE rank: {rm.get('roe_rank', 'N/A')}",
            "",
            rm.get("commentary", ""),
            "",
        ]

    if cp := data.get("competitive_position"):
        lines += [
            f"## Competitive Position — {cp.get('moat_assessment', 'N/A')}",
            "**Key Differentiators:**",
        ]
        for d in cp.get("key_differentiators", []):
            lines.append(f"- {d}")
        lines += ["", "**Threats from Peers:**"]
        for t in cp.get("threats_from_peers", []):
            lines.append(f"- {t}")
        lines.append("")

    if summary := data.get("summary"):
        lines += ["## Summary", summary, ""]

    return "\n".join(lines)


def run(company: dict, config: dict) -> dict:
    symbol = company["symbol"]
    company_name = company.get("name", symbol)
    peers = company.get("peers", [])
    peer_list_str = ", ".join(peers) if peers else "No peers specified"

    result = {
        "skill_name": SKILL_NAME,
        "symbol": symbol,
        "status": "success",
        "data": {},
        "error": None,
    }

    try:
        own_financials = load_company_files(symbol, "financials", extension=".txt")
        if not own_financials:
            own_financials = load_company_files(symbol, "financials", extension=".json")

        peer_sections: list[str] = []
        if own_financials:
            peer_sections.append(f"=== {company_name} ({symbol}) ===\n" + "\n".join(own_financials))

        for peer_symbol in peers:
            peer_files = load_company_files(peer_symbol, "financials", extension=".txt")
            if not peer_files:
                peer_files = load_company_files(peer_symbol, "financials", extension=".json")
            if peer_files:
                peer_sections.append(f"=== PEER: {peer_symbol} ===\n" + "\n".join(peer_files))

        if not peer_sections:
            result["status"] = "no_data"
            result["error"] = "No financial data available for company or peers."
            print(f"[{SKILL_NAME}][{symbol}] No data found — skipping.")
            return result

        peer_data_text = "\n\n".join(peer_sections)
        print(f"[{SKILL_NAME}][{symbol}] Running peer comparison vs: {peer_list_str}. Calling LLM...")

        prompt = load_prompt(
            "peer_comparison",
            company_name=company_name,
            peer_list=peer_list_str,
            peer_data=peer_data_text,
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
