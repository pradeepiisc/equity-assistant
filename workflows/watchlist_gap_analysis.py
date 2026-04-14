"""
Workflow: Watchlist Gap Analysis
=================================
Evaluates each watchlist company on TWO dimensions:
  1. Investment thesis quality  — from master_report.md (rating, cash flow, moat, valuation)
  2. Portfolio sector value-add — from sector_profiles cache (absent > thin > represented)

Combined score drives a tiered ranking:  Tier 1 (Act Now) → Tier 2 (Monitor) → Tier 3 (Watch) → Remove

Uses:
  - data/sector_profiles/{SYMBOL}.json          — portfolio sector coverage
  - data/companies/{SYMBOL}/reports/master_report.md  — full investment analysis
  - prompts/watchlist_gap_analysis.txt          — LLM synthesis prompt

Note: Run `python -m workflows.sector_analysis` first to ensure portfolio profiles are fresh.
      Run `python -m workflows.batch_company_analysis` to ensure watchlist master_report.md files exist.

Usage:
    python -m workflows.watchlist_gap_analysis
    python -m workflows.watchlist_gap_analysis --user ZV3899
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

PROFILES_DIR = PROJECT_ROOT / "data" / "sector_profiles"
COMPANIES_DIR = PROJECT_ROOT / "data" / "companies"
PORTFOLIO_DIR = PROJECT_ROOT / "portfolio"
WATCHLIST_PATH = PROJECT_ROOT / "watchlist.yaml"
PROMPT_FILE = PROJECT_ROOT / "prompts" / "watchlist_gap_analysis.txt"

# Max chars of each master_report.md included in the LLM prompt (prevents token overflow)
MAX_REPORT_CHARS = 2500


# ── helpers ────────────────────────────────────────────────────────────────────

def _load_watchlist() -> list[dict]:
    if not WATCHLIST_PATH.exists():
        raise FileNotFoundError(f"watchlist.yaml not found: {WATCHLIST_PATH}")
    with open(WATCHLIST_PATH) as f:
        data = yaml.safe_load(f)
    return data.get("stocks", [])


def _find_user_dir(user_id: str | None = None) -> Path:
    dirs = [d for d in PORTFOLIO_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if not dirs:
        raise FileNotFoundError("No portfolio data found.")
    if user_id:
        match = next((d for d in dirs if d.name.upper() == user_id.upper()), None)
        if not match:
            raise FileNotFoundError(f"User '{user_id}' not found.")
        return match
    if len(dirs) == 1:
        return dirs[0]
    raise ValueError(f"Multiple users found: {[d.name for d in dirs]}. Pass --user <ID>.")


def _load_latest_holdings(user_dir: Path) -> list[dict]:
    holdings_dir = user_dir / "holdings"
    date_dirs = sorted([d for d in holdings_dir.iterdir() if d.is_dir()], reverse=True)
    if not date_dirs:
        raise FileNotFoundError(f"No holdings snapshots under {holdings_dir}")
    hf = date_dirs[0] / "holdings.json"
    with open(hf) as f:
        return json.load(f)


def _load_cached_profile(symbol: str) -> dict | None:
    clean = symbol.upper().replace("-SM", "").replace("-BE", "").replace(" ", "_")
    p = PROFILES_DIR / f"{clean}.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None


def _load_master_report_md(symbol: str) -> str | None:
    """Load the full master_report.md for a watchlist company."""
    sym = symbol.upper()
    p = COMPANIES_DIR / sym / "reports" / "master_report.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return None


def _extract_rating_from_md(report_md: str) -> str:
    """Extract Overall Rating from master_report.md markdown."""
    m = re.search(r'\*\*Overall Rating:\*\*\s*(.+)', report_md)
    return m.group(1).strip() if m else "Unknown"


def _extract_section(report_md: str, section: str) -> str:
    """Extract a named ## section from a markdown report."""
    m = re.search(rf'## {re.escape(section)}\n(.*?)(?:\n## |\Z)', report_md, re.DOTALL)
    return m.group(1).strip() if m else ""


# ── core analysis ──────────────────────────────────────────────────────────────

def run(user_id: str | None = None) -> Path:
    from llm.utils import call_llm

    user_dir = _find_user_dir(user_id)
    holdings_raw = _load_latest_holdings(user_dir)
    portfolio_symbols = {h["symbol"].upper() for h in holdings_raw}
    watchlist = _load_watchlist()
    watchlist_symbols = {c["symbol"].upper() for c in watchlist}

    print(f"\n{'='*60}")
    print(f"  WATCHLIST GAP ANALYSIS  —  {user_dir.name}")
    print(f"{'='*60}")
    print(f"  Portfolio: {len(portfolio_symbols)} holdings")
    print(f"  Watchlist: {len(watchlist_symbols)} companies\n")

    # ── Build portfolio sector map from cached profiles ────────────────────────
    # NOTE: Use ALL portfolio symbols — do NOT subtract watchlist_symbols.
    # Some companies appear in both (e.g. QPOWER held + on watchlist for adding more).
    # Subtracting them would wrongly mark their sectors as "Absent".
    print("  Loading portfolio sector profiles from cache...")
    portfolio_sectors: dict[str, list[str]] = {}   # sector → [symbols]
    missing_profiles: list[str] = []
    already_held = portfolio_symbols & watchlist_symbols  # on watchlist but already owned

    for sym in sorted(portfolio_symbols):
        prof = _load_cached_profile(sym)
        if not prof:
            missing_profiles.append(sym)
            continue
        for ind in prof.get("output_industries", []):
            if ind not in portfolio_sectors:
                portfolio_sectors[ind] = []
            portfolio_sectors[ind].append(sym)

    if missing_profiles:
        print(f"  ⚠ No cached profile for {len(missing_profiles)} portfolio stocks "
              f"(run sector_analysis --refresh to populate): {missing_profiles[:5]}{'...' if len(missing_profiles) > 5 else ''}")

    print(f"  Portfolio sectors found: {len(portfolio_sectors)}")

    # ── Build watchlist company data using master_report.md ────────────────────
    print("  Loading watchlist master reports...")
    watchlist_reports: list[dict] = []
    no_report: list[str] = []

    for wl_entry in watchlist:
        sym = wl_entry["symbol"].upper()
        name = wl_entry.get("name", sym)

        report_md = _load_master_report_md(sym)
        if not report_md:
            no_report.append(sym)
            print(f"    {sym:12} | NO master_report.md — skipping")
            continue

        rating = _extract_rating_from_md(report_md)
        thesis = _extract_section(report_md, "Investment Thesis")
        action = _extract_section(report_md, "Action Recommendation")
        high_risks = re.findall(r'### \[HIGH\] (.+)', report_md)

        # Truncate report for prompt
        report_snippet = report_md[:MAX_REPORT_CHARS]
        if len(report_md) > MAX_REPORT_CHARS:
            report_snippet += "\n... [truncated]"

        is_held = sym in already_held
        watchlist_reports.append({
            "symbol": sym,
            "name": name,
            "rating": rating,
            "already_held": is_held,
            "thesis_first_sentence": (thesis.split("\n")[0] if thesis else "")[:300],
            "action_snippet": (action.split("\n")[0] if action else "")[:200],
            "high_risks": high_risks,
            "report_snippet": report_snippet,
        })
        held_flag = " [ALREADY HELD — add-more candidate]" if is_held else ""
        print(f"    {sym:12} | rating={rating}{held_flag}")

    if no_report:
        print(f"\n  ⚠ No master_report.md for: {no_report}")
        print(f"    Run: python -m workflows.batch_company_analysis --symbols {' '.join(no_report)}")

    # ── Build compact sector reference for LLM ─────────────────────────────────
    portfolio_sectors_json = json.dumps(
        {sector: len(syms) for sector, syms in sorted(portfolio_sectors.items(), key=lambda x: -len(x[1]))},
        indent=2,
    )

    # Build compact watchlist text (one block per company)
    watchlist_reports_text = ""
    for r in watchlist_reports:
        watchlist_reports_text += (
            f"\n{'─'*60}\n"
            f"SYMBOL: {r['symbol']}  |  NAME: {r['name']}  |  RATING: {r['rating']}\n"
            f"HIGH RISKS: {', '.join(r['high_risks']) if r['high_risks'] else 'None'}\n"
            f"THESIS: {r['thesis_first_sentence']}\n"
            f"ACTION: {r['action_snippet']}\n"
        )

    # ── Call LLM ──────────────────────────────────────────────────────────────
    print("\n  Calling LLM for gap analysis synthesis...")
    prompt_template = PROMPT_FILE.read_text(encoding="utf-8")
    prompt = prompt_template.format(
        portfolio_sectors_json=portfolio_sectors_json,
        watchlist_reports_text=watchlist_reports_text,
    )

    analysis = call_llm(prompt, expect_json=True)

    # ── Build markdown report ─────────────────────────────────────────────────
    today = str(date.today())
    lines: list[str] = [
        "# Watchlist Gap Analysis",
        f"**{user_dir.name}  ·  {today}**\n",
        "*Evaluates watchlist companies on investment thesis quality × portfolio sector gap.*",
        f"*Portfolio: {len(portfolio_symbols)} holdings · {len(portfolio_sectors)} sectors covered · "
        f"Watchlist: {len(watchlist_reports)} companies analysed*\n",
        "---\n",
    ]

    # Section 1: Portfolio assessment
    lines += [
        "## Portfolio Assessment\n",
        analysis.get("portfolio_assessment", "*(not available)*"),
        "",
    ]

    # Section 2: All watchlist companies ranked
    ranked = analysis.get("ranked_watchlist", [])
    if ranked:
        lines += [
            "---\n",
            "## Full Watchlist Ranking\n",
            "| Rank | Symbol | Sector | Sector Status | Thesis | Score | Action |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in ranked:
            lines.append(
                f"| #{r.get('rank','?')} | **{r.get('symbol','?')}** | "
                f"{r.get('sector','?')} | {r.get('portfolio_sector_status','?')} | "
                f"{r.get('thesis_rating','?')} | {r.get('combined_score','?')}/10 | "
                f"{r.get('action','?')} |"
            )
        lines.append("")

    # Section 3: Tier 1 — Act Now
    tier1 = analysis.get("tier1_candidates", [])
    if tier1:
        lines += ["---\n", "## Tier 1 — Act Now\n"]
        for pick in tier1:
            sym = pick.get("symbol", "?")
            name = pick.get("name", sym)
            sector = pick.get("sector_added", "?")
            gap = pick.get("gap_type", "?")
            rationale = pick.get("rationale", "")
            screener_url = f"https://www.screener.in/company/{sym}/"
            lines += [
                f"### #{pick.get('rank','?')} — [{sym}]({screener_url}) — {name}",
                f"**Sector added:** {sector}  ·  **Gap type:** {gap}\n",
                rationale,
                "",
            ]

    # Section 4: Gaps identified (sector-level summary)
    gaps = analysis.get("gaps_identified", [])
    if gaps:
        lines += [
            "---\n",
            "## Sector Gaps Identified\n",
            "| Sector | Portfolio Status | Best Watchlist Candidate |",
            "|---|---|---|",
        ]
        for g in gaps:
            candidates = ", ".join(g.get("watchlist_candidates", []))
            lines.append(f"| {g.get('sector', '—')} | {g.get('portfolio_status', '—')} | {candidates} |")
        lines.append("")

    # Section 5: Watchlist cleanup
    cleanup = analysis.get("watchlist_cleanup", [])
    if cleanup:
        lines += ["---\n", "## Watchlist Cleanup Recommendations\n"]
        for item in cleanup:
            lines.append(f"- **{item.get('symbol')}**: {item.get('reason', '')}")
        lines.append("")

    # Section 6: Portfolio sector coverage
    lines += [
        "---\n",
        "## Portfolio Sector Coverage (reference)\n",
        "| Sector | # Stocks |",
        "|---|---|",
    ]
    for sector, syms in sorted(portfolio_sectors.items(), key=lambda x: -len(x[1])):
        lines.append(f"| {sector} | {len(syms)} |")
    lines.append("")

    report_md = "\n".join(lines)

    # ── Save ──────────────────────────────────────────────────────────────────
    out_dir = user_dir / "watchlist_gaps" / today
    out_dir.mkdir(parents=True, exist_ok=True)
    out_md = out_dir / "watchlist_gap_analysis.md"
    out_json = out_dir / "watchlist_gap_analysis.json"
    out_md.write_text(report_md, encoding="utf-8")
    out_json.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n  ✓ Report saved → {out_md}")
    print(f"  ✓ JSON saved  → {out_json}\n")

    print("  TIER 1 PICKS:")
    for pick in tier1[:5]:
        print(f"    #{pick.get('rank')} {pick.get('symbol'):12} | {pick.get('sector_added')} | {pick.get('gap_type')}")

    return out_md


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Watchlist gap analysis — thesis quality × sector gap ranking")
    parser.add_argument("--user", help="Kite user ID (auto-detected if only one)")
    args = parser.parse_args()

    run(user_id=args.user)
