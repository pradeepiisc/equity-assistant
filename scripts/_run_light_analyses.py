"""
Run a 'light' full_company_analysis for companies that have NO transcripts
but DO have financials. Skips transcript_analysis, runs everything else
(shareholding, news, financials, valuation, master report synthesis).

Usage:
    python -m scripts._run_light_analyses          # batch all eligible
    python -m scripts._run_light_analyses SYMBOL    # single company
"""
from __future__ import annotations
import json
import sys
import time
import traceback
import yaml
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from llm.client import get_config
from llm.utils import load_prompt, call_llm, save_report
from skills import (
    shareholding_analysis, news_sentiment, financial_snapshot,
    peer_comparison, valuepickr_analysis, valuation_agent,
)
from skills.company_meta import get_latest_price

DATA = ROOT / "data" / "companies"
SKIP_FOLDERS = {"543619", "544091", "544213", "544458", "544681"}


def count_files(folder: Path, ext: str | None = None) -> int:
    if not folder.exists():
        return 0
    if ext:
        return len([f for f in folder.iterdir() if f.suffix.lower() == ext])
    return len([f for f in folder.iterdir() if f.is_file()])


def get_eligible() -> list[str]:
    """Companies missing master_report.md, have financials but no transcripts."""
    eligible = []
    for d in sorted(DATA.iterdir()):
        sym = d.name
        if sym in SKIP_FOLDERS or not d.is_dir():
            continue
        has_master = (d / "reports" / "master_report.md").exists()
        if has_master:
            continue
        t = count_files(d / "transcripts", ".pdf")
        f = count_files(d / "financials")
        if t == 0 and f > 0:
            eligible.append(sym)
    return eligible


def _load_all_companies() -> list[dict]:
    companies = []
    for yf in ["watchlist.yaml", "portfolio_companies.yaml"]:
        p = ROOT / yf
        if p.exists():
            with open(p) as f:
                data = yaml.safe_load(f)
            companies.extend(data.get("stocks", []))
    return companies


def _find_company(symbol: str, companies: list[dict]) -> dict | None:
    for c in companies:
        if c.get("symbol", "").upper() == symbol.upper():
            return c
        if str(c.get("screener_symbol", "")).upper() == symbol.upper():
            return c
    return None


def run_light_analysis(symbol: str) -> str:
    """Run analysis without transcripts — uses financials, shareholding, news, valuation, master report."""
    config = get_config()
    all_co = _load_all_companies()
    company = _find_company(symbol, all_co)

    if not company:
        print(f"[ERROR] {symbol} not found in YAML files")
        return "FAILED"

    company_name = company.get("name", symbol)
    print(f"Company: {company_name}")
    print(f"Sector:  {company.get('sector', 'Unknown')}")

    price_info = get_latest_price(symbol)
    if price_info:
        company["last_price"] = price_info["price"]
        print(f"Price:   ₹{price_info['price']} (from {price_info['source']})")

    skill_results: dict[str, dict] = {}

    # Skip transcript_analysis — not available
    print("  [transcript_analysis] skipped — no transcripts available")
    skill_results["transcript_analysis"] = {"status": "no_data", "data": {}, "error": "no transcripts"}

    optional_skills = [
        ("shareholding_analysis", shareholding_analysis),
        ("news_sentiment", news_sentiment),
        ("financial_snapshot", financial_snapshot),
        ("peer_comparison", peer_comparison),
        ("valuepickr_analysis", valuepickr_analysis),
    ]

    for skill_name, skill_module in optional_skills:
        print(f"Running: {skill_name}")
        try:
            result = skill_module.run(company, config)
            skill_results[skill_name] = result
            if result["status"] == "no_data":
                print(f"  -> No data found, continuing without it.")
            elif result["status"] == "error":
                print(f"  -> Error (non-fatal): {result['error']}")
        except Exception as e:
            skill_results[skill_name] = {"status": "error", "data": {}, "error": str(e)}
            print(f"  -> Exception (non-fatal): {e}")

    # Valuation agent
    print("Running: valuation_agent")
    try:
        va_result = valuation_agent.run(company, config, skill_results=skill_results)
        skill_results["valuation_agent"] = va_result
        if va_result["status"] == "success":
            fv_low = va_result["data"].get("fair_value_low", "?")
            fv_high = va_result["data"].get("fair_value_high", "?")
            signal = va_result["data"].get("entry_signal", "?")
            print(f"  -> Fair value: ₹{fv_low}-₹{fv_high}  |  Signal: {signal}")
    except Exception as e:
        skill_results["valuation_agent"] = {"status": "error", "data": {}, "error": str(e)}
        print(f"  -> Exception: {e}")

    # Master report synthesis
    print("Synthesising master report...")
    skill_outputs: dict = {}
    for sn, r in skill_results.items():
        if r["status"] == "success" and r.get("data"):
            skill_outputs[sn] = r["data"]
        else:
            skill_outputs[sn] = {"status": r["status"], "note": r.get("error") or "no data available"}

    _co_dir = DATA / symbol.upper()
    _iv = _co_dir / "insights" / "insights_values.md"
    if _iv.exists():
        skill_outputs["insights_values"] = {"content": _iv.read_text(encoding="utf-8")[:2500]}

    skill_json = json.dumps(skill_outputs, indent=2, ensure_ascii=False)
    master_prompt = load_prompt(
        "master_report",
        company_name=company_name,
        symbol=symbol.upper(),
        skill_outputs=skill_json,
    )
    master_data = call_llm(master_prompt, expect_json=True)

    if isinstance(master_data, dict):
        md_parts = []
        md_parts.append(f"# {company_name} ({symbol.upper()}) -- Master Report\n")
        md_parts.append(f"**Rating:** {master_data.get('rating', 'N/A')}\n")
        md_parts.append(f"**Valuation:** {master_data.get('valuation_summary', 'N/A')}\n")
        triggers = master_data.get("monitoring_triggers", [])
        if triggers:
            md_parts.append("**Monitoring triggers:**")
            for t in triggers:
                md_parts.append(f"  - {t}")
        md_text = "\n".join(md_parts)
        save_report(symbol.upper(), "master_report", md_text, raw_json=master_data)
        print(f"\nRating: {master_data.get('rating', '?')}")
        vsum = master_data.get('valuation_summary', '?')
        print(f"Valuation: {vsum[:80] if isinstance(vsum, str) else vsum}")
        return "SUCCESS"
    else:
        print(f"=> FAILED: LLM returned non-dict: {type(master_data)}")
        return "FAILED"


def main():
    # Single company mode
    if len(sys.argv) > 1:
        symbol = sys.argv[1]
        print(f"\n{'='*60}")
        print(f"  LIGHT ANALYSIS: {symbol}")
        print(f"{'='*60}\n")
        t0 = time.time()
        status = run_light_analysis(symbol)
        elapsed = time.time() - t0
        print(f"\n=> {status} ({elapsed:.0f}s)")
        return

    # Batch mode
    symbols = get_eligible()
    total = len(symbols)
    print(f"\n{'='*60}")
    print(f"  LIGHT ANALYSIS (no transcripts): {total} companies")
    print(f"{'='*60}\n")

    results = {}
    for i, sym in enumerate(symbols, 1):
        print(f"\n{'#'*60}")
        print(f"  [{i}/{total}] {sym}")
        print(f"{'#'*60}\n")

        t0 = time.time()
        try:
            status = run_light_analysis(sym)
            elapsed = time.time() - t0
            results[sym] = (status, elapsed)
            print(f"  => {status} ({elapsed:.0f}s)")
        except Exception as e:
            elapsed = time.time() - t0
            results[sym] = ("ERROR", elapsed)
            print(f"  => ERROR: {e}")
            traceback.print_exc()

    print(f"\n{'='*60}")
    print(f"  BATCH COMPLETE: {total} companies")
    print(f"{'='*60}")
    success = sum(1 for v in results.values() if v[0] == "SUCCESS")
    failed = total - success
    print(f"  Success: {success}  |  Failed: {failed}\n")
    for sym, (status, elapsed) in results.items():
        print(f"  {sym:25s} {status:10s} {elapsed:.0f}s")


if __name__ == "__main__":
    main()
