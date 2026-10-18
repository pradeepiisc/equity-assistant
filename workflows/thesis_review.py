"""
Workflow: Post-Results Thesis Review
======================================
After quarterly results are declared, compares latest financials, shareholding,
and transcripts against the EXISTING investment thesis (master_report) to decide
if the thesis has strengthened, weakened, or broken.

Identifies companies whose results have been declared (expected quarter data in
financials), gathers fresh data + existing thesis, runs LLM, and produces
actionable per-company thesis_review reports + a consolidated summary.

Usage:
    python -m workflows.thesis_review                         # all companies with results
    python -m workflows.thesis_review --symbols DCAL BETA     # specific symbols
    python -m workflows.thesis_review --fetch                 # fetch data first, then review
    python -m workflows.thesis_review --user ZV3899           # specific portfolio user
    python -m workflows.thesis_review --include-no-thesis     # also review companies without master_report
    python -m workflows.thesis_review --dry-run               # just list companies with results, no LLM

Steps per company:
  1. Check if expected quarter's financial results exist
  2. Load existing investment thesis (master_report.json + valuation_summary.md)
  3. Load latest financials, shareholding, transcripts (last 4)
  4. Load insights_values trends (if available)
  5. Load holding context (price, allocation, P&L)
  6. Build prompt and call LLM
  7. Save per-company thesis_review.md + thesis_review.json
  8. Write consolidated summary report
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import yaml

from llm.client import get_config
from llm.utils import load_prompt, call_llm, save_report, load_company_files

PROJECT_ROOT = Path(__file__).parent.parent
PORTFOLIO_DIR = PROJECT_ROOT / "portfolio"
COMPANIES_DIR = PROJECT_ROOT / "data" / "companies"
CACHE_DIR = PORTFOLIO_DIR / ".cache"
EARNINGS_SEASON_DIR = PROJECT_ROOT / "data" / "earnings_season"

MAX_TRANSCRIPT_CHARS = 12000   # per transcript
MAX_TOTAL_TRANSCRIPT_CHARS = 40000  # total across all transcripts
MAX_THESIS_CHARS = 6000
MAX_FINANCIALS_CHARS = 4000
MAX_SHAREHOLDING_CHARS = 2000
MAX_INSIGHTS_CHARS = 3000

TRANSCRIPT_COUNT = 4  # latest N transcripts to include


# ── Helpers ──────────────────────────────────────────────────────────────────

def _expected_latest_quarter() -> str:
    """Estimate the latest quarter whose results should be available now."""
    today = date.today()
    y = today.year
    m = today.month
    if m >= 10:
        return f"Sep {y}"
    if m >= 7:
        return f"Jun {y}"
    if m >= 4:
        return f"Mar {y}"
    return f"Dec {y - 1}"


def _load_all_companies() -> list[dict]:
    """Load companies from watchlist.yaml and portfolio_companies.yaml."""
    companies: dict[str, dict] = {}
    for yaml_file in ["watchlist.yaml", "portfolio_companies.yaml"]:
        p = PROJECT_ROOT / yaml_file
        if not p.exists():
            continue
        with open(p, "r") as f:
            data = yaml.safe_load(f) or {}
        for c in data.get("stocks", []):
            sym = c.get("symbol", "").upper()
            if sym and sym not in companies:
                companies[sym] = c
    return list(companies.values())


def _load_holdings(user_id: str | None = None) -> tuple[list[dict], str | None, Path | None]:
    """Load latest portfolio holdings."""
    dirs = [d for d in PORTFOLIO_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if not dirs:
        return [], None, None

    if user_id:
        user_dir = next((d for d in dirs if d.name.upper() == user_id.upper()), None)
    elif len(dirs) == 1:
        user_dir = dirs[0]
    else:
        # Pick the first non-special directory
        user_dir = next((d for d in dirs if d.name not in ("pradeep",)), dirs[0])

    if not user_dir:
        return [], None, None

    holdings_dir = user_dir / "holdings"
    if not holdings_dir.exists():
        return [], None, user_dir

    # Try dated subdirectories first (e.g. holdings/2026-05-16/holdings.json)
    date_dirs = sorted([d for d in holdings_dir.iterdir() if d.is_dir()], key=lambda d: d.name, reverse=True)
    if date_dirs:
        latest = date_dirs[0]
        hf = latest / "holdings.json"
        if hf.exists():
            holdings = json.loads(hf.read_text())
            return holdings, latest.name, user_dir

    # Fall back to holdings.json directly in holdings/ dir
    direct_hf = holdings_dir / "holdings.json"
    if direct_hf.exists():
        holdings = json.loads(direct_hf.read_text())
        snapshot_date = datetime.fromtimestamp(direct_hf.stat().st_mtime).strftime("%Y-%m-%d")
        return holdings, snapshot_date, user_dir

    return [], None, user_dir


def _load_dma_cache() -> dict[str, dict]:
    today = date.today().isoformat()
    cache_file = CACHE_DIR / f"dma_{today}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())
    caches = sorted(CACHE_DIR.glob("dma_*.json"), reverse=True)
    if caches:
        return json.loads(caches[0].read_text())
    return {}


# ── Financial data parsing ───────────────────────────────────────────────────

def _parse_quarterly_financials(symbol: str) -> tuple[dict[str, dict], str]:
    """Parse quarterly results from latest financial data file.
    Returns ({quarter: {metric: value}}, raw_text_section)."""
    fin_dir = COMPANIES_DIR / symbol.upper() / "financials"
    if not fin_dir.exists():
        return {}, ""
    files = sorted(fin_dir.glob(f"{symbol.upper()}_financials_*.txt"), reverse=True)
    if not files:
        return {}, ""
    text = files[0].read_text(encoding="utf-8")

    match = re.search(r"## Quarterly Results\s*\n", text)
    if not match:
        return {}, ""
    section_start = match.end()
    next_section = re.search(r"\n---\n|\n## ", text[section_start:])
    section_end = section_start + next_section.start() if next_section else len(text)
    section = text[section_start:section_end]

    slines = section.strip().split("\n")
    if not slines:
        return {}, section

    header = slines[0]
    quarters = [q.strip() for q in header.split("|")[1:] if q.strip()]

    result: dict[str, dict[str, float | None]] = {q: {} for q in quarters}
    for line in slines[1:]:
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 2:
            continue
        metric_raw = parts[0].strip()
        values = [v.strip() for v in parts[1:]]
        for i, q in enumerate(quarters):
            if i < len(values):
                try:
                    val = values[i].replace("%", "").replace(",", "").strip()
                    result[q][metric_raw] = float(val) if val else None
                except (ValueError, IndexError):
                    result[q][metric_raw] = None

    # Also get full financial text (ratios + annual + quarterly)
    full_text = text[:MAX_FINANCIALS_CHARS]
    return result, full_text


def _has_expected_quarter(financials: dict[str, dict], expected_q: str) -> bool:
    """Check if the expected quarter exists with actual data in financials."""
    if expected_q not in financials:
        return False
    q_data = financials[expected_q]
    # Must have at least Sales data
    return any(v is not None for v in q_data.values())


def _get_latest_quarter(financials: dict[str, dict]) -> str | None:
    """Return the latest quarter with actual data from parsed financials."""
    if not financials:
        return None
    # Quarter names are like 'Mar 2026', 'Dec 2025', etc.
    # Sort by parsing month+year
    month_order = {'Jan':1,'Feb':2,'Mar':3,'Apr':4,'May':5,'Jun':6,
                   'Jul':7,'Aug':8,'Sep':9,'Oct':10,'Nov':11,'Dec':12}
    def q_sort_key(q: str) -> tuple:
        parts = q.split()
        if len(parts) == 2 and parts[0] in month_order:
            return (int(parts[1]), month_order[parts[0]])
        return (0, 0)

    valid = [q for q, data in financials.items()
             if any(v is not None for v in data.values())]
    if not valid:
        return None
    return max(valid, key=q_sort_key)


def _is_recent_quarter(quarter: str, min_quarter: str = 'Sep 2025') -> bool:
    """Check if a quarter is at least as recent as min_quarter."""
    month_order = {'Jan':1,'Feb':2,'Mar':3,'Apr':4,'May':5,'Jun':6,
                   'Jul':7,'Aug':8,'Sep':9,'Oct':10,'Nov':11,'Dec':12}
    def to_num(q: str) -> int:
        parts = q.split()
        if len(parts) == 2 and parts[0] in month_order:
            return int(parts[1]) * 100 + month_order[parts[0]]
        return 0
    return to_num(quarter) >= to_num(min_quarter)


def _scan_all_company_dirs() -> list[dict]:
    """Scan data/companies/ directory for all companies with meta.json."""
    companies: list[dict] = []
    for d in sorted(COMPANIES_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith('.'):
            continue
        sym = d.name.upper()
        # Try to load name from meta.json
        meta = d / 'meta.json'
        name = sym
        if meta.exists():
            try:
                import json as _json
                name = _json.loads(meta.read_text()).get('name', sym)
            except Exception:
                pass
        companies.append({'symbol': sym, 'name': name})
    return companies


# ── Context loaders ──────────────────────────────────────────────────────────

def _load_existing_thesis(symbol: str) -> str:
    """Load master_report.md + decision framework as existing thesis context."""
    reports_dir = COMPANIES_DIR / symbol.upper() / "reports"
    parts: list[str] = []

    # Master report markdown
    mr_md = reports_dir / "master_report.md"
    if mr_md.exists():
        parts.append(mr_md.read_text(encoding="utf-8")[:MAX_THESIS_CHARS])

    # Decision framework from JSON (structured)
    mr_json = reports_dir / "master_report.json"
    if mr_json.exists():
        try:
            mr_data = json.loads(mr_json.read_text())
            if df := mr_data.get("decision_framework"):
                parts.append(f"\nDECISION FRAMEWORK (JSON):\n{json.dumps(df, indent=2, ensure_ascii=False)[:2000]}")
            if frp := mr_data.get("forward_revenue_path"):
                parts.append(f"\nFORWARD REVENUE PATH:\n{json.dumps(frp, indent=2, ensure_ascii=False)[:1000]}")
        except Exception:
            pass

    if not parts:
        return "No existing investment thesis available. Build assessment from latest data only."

    return "\n\n".join(parts)


def _load_valuation_context(symbol: str) -> str:
    """Load valuation_summary.md + valuation_agent.json."""
    reports_dir = COMPANIES_DIR / symbol.upper() / "reports"
    parts: list[str] = []

    vs_md = reports_dir / "valuation_summary.md"
    if vs_md.exists():
        parts.append(vs_md.read_text(encoding="utf-8")[:2000])

    va_json = reports_dir / "valuation_agent.json"
    if va_json.exists():
        try:
            va = json.loads(va_json.read_text())
            fv_low = va.get("fair_value_low")
            fv_high = va.get("fair_value_high")
            signal = va.get("entry_signal", "")
            method = va.get("valuation_method", "")
            rationale = va.get("valuation_rationale", "")
            parts.append(
                f"Valuation Agent: Fair Value ₹{fv_low}–₹{fv_high}"
                + (f" | Signal: {signal}" if signal else "")
                + (f" | Method: {method}" if method else "")
                + (f"\nRationale: {rationale[:500]}" if rationale else "")
            )
        except Exception:
            pass

    return "\n\n".join(parts) if parts else "No previous valuation assessment available."


def _load_latest_shareholding(symbol: str) -> str:
    """Load the latest shareholding file text."""
    sh_dir = COMPANIES_DIR / symbol.upper() / "shareholding"
    if not sh_dir.exists():
        return "Shareholding data not available."
    files = sorted(sh_dir.glob(f"{symbol.upper()}_shareholding_*.txt"), reverse=True)
    if not files:
        return "Shareholding data not available."
    text = files[0].read_text(encoding="utf-8")
    return text[:MAX_SHAREHOLDING_CHARS]


def _date_key_from_filename(filename: str) -> tuple[int, int]:
    """Extract (year, month_index) from a transcript filename for chronological sort."""
    _MONTH_MAP = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    m = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{4})", filename, re.IGNORECASE)
    if m:
        return (int(m.group(2)), _MONTH_MAP.get(m.group(1).lower(), 0))
    return (0, 0)


def _load_latest_transcripts(symbol: str, count: int = TRANSCRIPT_COUNT) -> str:
    """Load the latest N transcript/PPT PDFs as text, newest first (chronological).
    Merges files from both transcripts/ and ppt/ directories, deduplicating by quarter."""
    sym = symbol.upper()
    transcripts_dir = COMPANIES_DIR / sym / "transcripts"
    ppt_dir = COMPANIES_DIR / sym / "ppt"

    # Collect PDFs from both directories
    pdf_files: list[Path] = []
    if transcripts_dir.exists():
        pdf_files.extend(transcripts_dir.glob("*.pdf"))
    if ppt_dir.exists():
        pdf_files.extend(ppt_dir.glob("*.pdf"))

    if not pdf_files:
        return "No transcripts available."

    # Sort by parsed date — newest first, deduplicate by quarter
    pdf_files.sort(key=lambda f: _date_key_from_filename(f.name), reverse=True)
    seen_quarters: set[tuple[int, int]] = set()
    selected: list[Path] = []
    for f in pdf_files:
        qk = _date_key_from_filename(f.name)
        if qk not in seen_quarters:
            seen_quarters.add(qk)
            selected.append(f)
        if len(selected) >= count:
            break

    texts: list[str] = []
    total_chars = 0
    for pdf_path in selected:
        if total_chars >= MAX_TOTAL_TRANSCRIPT_CHARS:
            break
        try:
            import pdfplumber
            pages: list[str] = []
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        pages.append(page_text)
            text = "\n".join(pages)
            if text.strip():
                truncated = text[:MAX_TRANSCRIPT_CHARS]
                texts.append(f"=== TRANSCRIPT: {pdf_path.name} ===\n{truncated}")
                total_chars += len(truncated)
        except Exception as e:
            texts.append(f"=== TRANSCRIPT: {pdf_path.name} === [ERROR: {e}]")

    if not texts:
        return "Transcripts exist but could not be read."

    return "\n\n".join(texts)


def _load_insights_values(symbol: str) -> str:
    """Load insights_values.md if available."""
    iv_path = COMPANIES_DIR / symbol.upper() / "insights" / "insights_values.md"
    if iv_path.exists():
        return iv_path.read_text(encoding="utf-8")[:MAX_INSIGHTS_CHARS]

    # Also try insights_values.txt
    iv_txt = COMPANIES_DIR / symbol.upper() / "insights" / "insights_values.txt"
    if iv_txt.exists():
        return iv_txt.read_text(encoding="utf-8")[:MAX_INSIGHTS_CHARS]

    return "No insights values data available."


def _build_holding_context(symbol: str, holdings_map: dict, dma_cache: dict) -> str:
    """Build the holding context string for the prompt."""
    holding = holdings_map.get(symbol.upper())
    if not holding:
        return f"Symbol: {symbol}\nNot currently held in portfolio (watchlist only)."

    dma = dma_cache.get(symbol.upper(), {})
    lines = [
        f"Symbol: {symbol}",
        f"Average Cost: ₹{holding.get('avg_price', 0):.2f}",
        f"Current Price: ₹{holding.get('last_price', 0):.2f}",
        f"Unrealised P&L: {holding.get('pnl_pct', 0):+.1f}%",
        f"Portfolio Allocation: {holding.get('pct_of_portfolio', 0):.1f}%",
        f"Quantity: {holding.get('quantity', 0)}",
        f"Current Value: ₹{holding.get('current_value', 0):,.0f}",
    ]
    dma200 = dma.get("dma200") or dma.get("dma100")
    dma50 = dma.get("dma50") or dma.get("dma40")
    ltp = holding.get("last_price", 0)
    if dma200 and ltp:
        pct200 = (ltp - dma200) / dma200 * 100
        lines.append(f"vs 200 DMA (₹{dma200:.1f}): {pct200:+.1f}%")
    if dma50 and ltp:
        pct50 = (ltp - dma50) / dma50 * 100
        lines.append(f"vs 50 DMA (₹{dma50:.1f}): {pct50:+.1f}%")

    return "\n".join(lines)


# ── Report builders ──────────────────────────────────────────────────────────

def _build_thesis_review_markdown(symbol: str, company_name: str, review: dict) -> str:
    """Convert LLM JSON response to a readable markdown report."""
    status = review.get("thesis_status", "unknown")
    status_emoji = {
        "strengthened": "🟢",
        "intact": "🔵",
        "weakening": "🟠",
        "broken": "🔴",
    }.get(status, "⚪")

    conviction = review.get("updated_conviction", {})
    action = conviction.get("updated_action", "Hold")
    score = conviction.get("score", "?")
    action_emoji = {
        "Strong Add": "🟢", "Add": "🔵", "Hold": "🟡",
        "Watch": "🟠", "Trim": "🔶", "Exit": "🔴",
    }.get(action, "⚪")

    lines = [
        f"# Thesis Review — {company_name} ({symbol})",
        f"*Generated: {date.today().isoformat()}*\n",
        f"## {status_emoji} Thesis Status: **{status.upper()}**  |  {action_emoji} Action: **{action}**  |  Conviction: **{score}/10**",
        "",
    ]

    # One-line summary
    if summary := review.get("one_line_summary"):
        lines += [f"> {summary}", ""]

    # Thesis status rationale
    if rationale := review.get("thesis_status_rationale"):
        lines += ["## Thesis Assessment", rationale, ""]

    # Results assessment
    if ra := review.get("results_assessment"):
        lines += [
            "## Results Assessment",
            f"**Quarter:** {ra.get('quarter', '?')}",
            f"- **Revenue vs Expectation:** {ra.get('revenue_vs_expectation', '?')}",
            f"- **Margin Trend:** {ra.get('margin_trend', '?')}",
            f"- **Profit Growth (YoY):** {ra.get('profit_growth_yoy', ra.get('profit_growth', '?'))}",
            f"- **Profit Growth (QoQ):** {ra.get('profit_growth_qoq', '?')}",
            f"- **Surprise Factor:** {ra.get('surprise_factor', '?')}",
        ]
        if commentary := ra.get("commentary"):
            lines += [f"\n{commentary}"]
        lines.append("")

    # Annual metrics
    if am := review.get("annual_metrics"):
        lines += [
            "## Annual Metrics (FY Comparison)",
            f"- **ROCE:** {am.get('roce_trend', '?')}",
            f"- **ROE:** {am.get('roe_trend', '?')}",
            f"- **Debt/Equity:** {am.get('debt_equity', '?')}",
            f"- **Working Capital Days:** {am.get('working_capital_days', '?')}",
            f"- **Cash Flow from Ops:** {am.get('cash_flow_from_operations', '?')}",
        ]
        if commentary := am.get("commentary"):
            lines += [f"\n{commentary}"]
        lines.append("")

    # Management consistency
    if mc := review.get("management_consistency"):
        lines += [
            "## Management Consistency",
            f"- **Guidance Delivery:** {mc.get('guidance_delivery', '?')}",
            f"- **Tone Shift:** {mc.get('tone_shift', '?')}",
        ]
        if promises := mc.get("key_promise_tracker"):
            lines.append("\n### Promise Tracker")
            lines.append("| Promise | Quarter | Status | Evidence |")
            lines.append("|---|---|---|---|")
            for p in promises:
                lines.append(
                    f"| {p.get('promise', '?')} | {p.get('quarter_promised', '?')} "
                    f"| {p.get('status', '?')} | {p.get('evidence', '?')} |"
                )
        if devs := mc.get("new_developments"):
            lines.append("\n**New Developments:**")
            for d in devs:
                lines.append(f"- {d}")
        if flags := mc.get("red_flags"):
            lines.append("\n**Red Flags:**")
            for f in flags:
                lines.append(f"- ⚠️ {f}")
        if inconsistencies := mc.get("inconsistencies"):
            lines.append("\n**Inconsistencies:**")
            for i in inconsistencies:
                lines.append(f"- ❌ {i}")
        lines.append("")

    # Shareholding signal
    if ss := review.get("shareholding_signal"):
        lines += [
            "## Shareholding Signal",
            f"- **Promoter:** {ss.get('promoter_trend', '?')}",
            f"- **Institutional:** {ss.get('institutional_trend', '?')}",
            f"- **Anchor Activity:** {ss.get('anchor_activity', '?')}",
            f"- **Signal:** {ss.get('overall_signal', '?')}",
            "",
        ]

    # Thesis delta
    if td := review.get("thesis_delta"):
        lines += ["## Thesis Delta"]
        lines.append(f"**Growth Trajectory:** {td.get('growth_trajectory_change', '?')}")
        if validated := td.get("validated_assumptions"):
            lines.append("\n✅ **Validated Assumptions:**")
            for v in validated:
                lines.append(f"- {v}")
        if at_risk := td.get("at_risk_assumptions"):
            lines.append("\n⚠️ **At-Risk Assumptions:**")
            for r in at_risk:
                lines.append(f"- {r}")
        if positives := td.get("new_positives"):
            lines.append("\n🟢 **New Positives:**")
            for p in positives:
                lines.append(f"- {p}")
        if negatives := td.get("new_negatives"):
            lines.append("\n🔴 **New Negatives:**")
            for n in negatives:
                lines.append(f"- {n}")
        lines.append("")

    # Price trend reasoning
    if ptr := review.get("price_trend_reasoning"):
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

    # Updated conviction
    if conviction:
        lines += [
            "## Updated Conviction",
            f"- **Previous Recommendation:** {conviction.get('previous_action_recommendation', '?')}",
            f"- **Updated Action:** {action_emoji} **{action}**",
            f"- **Conviction Score:** {score}/10",
            f"- **Rationale:** {conviction.get('action_rationale', '?')}",
        ]
        if pl := conviction.get("price_levels"):
            if pl.get("add_below"):
                val = str(pl['add_below']).lstrip('₹').strip()
                lines.append(f"- **Add below:** ₹{val}")
            if pl.get("trim_above"):
                val = str(pl['trim_above']).lstrip('₹').strip()
                lines.append(f"- **Trim above:** ₹{val}")
            if pl.get("stop_loss"):
                val = str(pl['stop_loss']).lstrip('₹').strip()
                lines.append(f"- **Stop-loss:** ₹{val}")
        lines.append("")

    # Forward outlook
    if fo := review.get("forward_outlook"):
        lines += [
            "## Forward Outlook",
            f"**Next Quarter:** {fo.get('next_quarter_expectations', '?')}",
        ]
        if monitorables := fo.get("key_monitorables"):
            lines.append("\n**Key Monitorables:**")
            for m in monitorables:
                lines.append(f"- {m}")
        if upgrade := fo.get("thesis_upgrade_trigger"):
            lines.append(f"\n**Upgrade trigger:** {upgrade}")
        if downgrade := fo.get("thesis_downgrade_trigger"):
            lines.append(f"\n**Downgrade trigger:** {downgrade}")
        lines.append("")

    return "\n".join(lines)


def _build_consolidated_summary(
    results: list[dict],
    expected_q: str,
    total_companies: int,
    companies_with_results: int,
) -> str:
    """Build the consolidated summary markdown."""
    today = date.today().isoformat()

    # Categorize
    strengthened = [r for r in results if r["review"].get("thesis_status") == "strengthened"]
    intact = [r for r in results if r["review"].get("thesis_status") == "intact"]
    weakening = [r for r in results if r["review"].get("thesis_status") == "weakening"]
    broken = [r for r in results if r["review"].get("thesis_status") == "broken"]

    lines = [
        f"# Post-Results Thesis Review — {expected_q}",
        f"*Generated: {today}*\n",
        f"## Overview",
        f"- **Total portfolio companies:** {total_companies}",
        f"- **Companies with {expected_q} results:** {companies_with_results}",
        f"- **Reviewed:** {len(results)}",
        f"- 🟢 **Strengthened:** {len(strengthened)}",
        f"- 🔵 **Intact:** {len(intact)}",
        f"- 🟠 **Weakening:** {len(weakening)}",
        f"- 🔴 **Broken:** {len(broken)}",
        "",
    ]

    # Summary table
    lines += [
        "## Summary Table",
        "| Company | Status | Action | Conviction | Revenue vs Exp | Growth | Key Change |",
        "|---|---|---|---|---|---|---|",
    ]

    status_order = {"broken": 0, "weakening": 1, "intact": 2, "strengthened": 3}
    sorted_results = sorted(results, key=lambda r: status_order.get(r["review"].get("thesis_status", ""), 2))

    for r in sorted_results:
        rv = r["review"]
        status = rv.get("thesis_status", "?")
        status_emoji = {"strengthened": "🟢", "intact": "🔵", "weakening": "🟠", "broken": "🔴"}.get(status, "⚪")
        conv = rv.get("updated_conviction", {})
        action = conv.get("updated_action", "?")
        score = conv.get("score", "?")
        ra = rv.get("results_assessment", {})
        rev_exp = ra.get("revenue_vs_expectation", "?")
        td = rv.get("thesis_delta", {})
        trajectory = td.get("growth_trajectory_change", "?")
        # Pick the most notable change
        key_change = ""
        if rv.get("management_consistency", {}).get("red_flags"):
            key_change = "⚠️ " + rv["management_consistency"]["red_flags"][0][:40]
        elif td.get("new_positives"):
            key_change = "✅ " + td["new_positives"][0][:40]
        elif td.get("at_risk_assumptions"):
            key_change = "⚠️ " + td["at_risk_assumptions"][0][:40]
        else:
            key_change = rv.get("one_line_summary", "")[:50]

        lines.append(
            f"| {r['name']} ({r['symbol']}) | {status_emoji} {status} | {action} | {score}/10 "
            f"| {rev_exp[:30]} | {trajectory} | {key_change} |"
        )
    lines.append("")

    # Action-grouped sections
    for category, emoji, label, items in [
        ("strengthened", "🟢", "Thesis Strengthened — Consider Adding", strengthened),
        ("broken", "🔴", "Thesis Broken — Consider Exiting", broken),
        ("weakening", "🟠", "Thesis Weakening — Watch / Trim", weakening),
        ("intact", "🔵", "Thesis Intact — Hold", intact),
    ]:
        if not items:
            continue
        lines += [f"## {emoji} {label}", ""]
        for r in items:
            rv = r["review"]
            conv = rv.get("updated_conviction", {})
            summary = rv.get("one_line_summary", "")
            lines.append(f"### {r['name']} ({r['symbol']})")
            lines.append(f"**{conv.get('updated_action', '?')}** (conviction {conv.get('score', '?')}/10)")
            if summary:
                lines.append(f"> {summary}")
            if rationale := conv.get("action_rationale"):
                lines.append(f"\n{rationale}")
            lines.append("")

    # Companies pending results
    lines += [
        "---",
        f"*Detailed per-company reports saved in `data/companies/{{SYMBOL}}/reports/thesis_review.md`*",
    ]

    return "\n".join(lines)


# ── Data fetch integration ───────────────────────────────────────────────────

def _fetch_data_for_companies(companies: list[dict], expected_q: str) -> None:
    """Fetch latest financials, shareholding, transcripts via earnings_season workflow."""
    print(f"\n── Fetching latest data for {len(companies)} companies ──")
    print("  This uses Chrome CDP for financials/shareholding. Transcripts use AJAX.")

    try:
        from workflows.earnings_season import run as earnings_run
        earnings_run(
            fetch=True,
            symbols=[c.get("symbol", "").upper() for c in companies],
        )
    except Exception as e:
        print(f"  ⚠️ Earnings season fetch error: {e}")
        print("  Proceeding with existing data on disk.")


# ── Main run ─────────────────────────────────────────────────────────────────

def _previous_quarter(expected_q: str) -> str:
    """Return the quarter before expected_q."""
    parts = expected_q.split(" ")
    if len(parts) != 2:
        return ""
    month, year = parts[0], int(parts[1])
    prev_map = {"Mar": ("Dec", -1), "Jun": ("Mar", 0), "Sep": ("Jun", 0), "Dec": ("Sep", 0)}
    prev_month, year_delta = prev_map.get(month, ("", 0))
    return f"{prev_month} {year + year_delta}" if prev_month else ""


def run(
    symbols: list[str] | None = None,
    user_id: str | None = None,
    fetch: bool = False,
    include_no_thesis: bool = False,
    dry_run: bool = False,
    quarter: str | None = None,
    all_companies: bool = False,
) -> Path | None:
    expected_q = quarter if quarter else _expected_latest_quarter()
    today = date.today().isoformat()

    print(f"\n{'=' * 60}")
    print(f"  POST-RESULTS THESIS REVIEW  —  {expected_q}")
    print(f"{'=' * 60}\n")

    # Load company list
    if all_companies:
        all_comps = _scan_all_company_dirs()
        print(f"  Mode: ALL companies in data/companies/ ({len(all_comps)})")
    else:
        all_comps = _load_all_companies()

    holdings, snapshot_date, user_dir = _load_holdings(user_id)
    dma_cache = _load_dma_cache()

    # Build holdings map with computed fields
    holdings_map: dict[str, dict] = {}
    total_value = sum(h.get("current_value", 0) for h in holdings)
    for h in holdings:
        sym = h.get("symbol", "").upper()
        if "pnl_pct" not in h:
            h["pnl_pct"] = (h.get("pnl", 0) / h.get("invested_value", 1) * 100) if h.get("invested_value") else 0
        if "pct_of_portfolio" not in h:
            h["pct_of_portfolio"] = (h["current_value"] / total_value * 100) if total_value else 0
        holdings_map[sym] = h

    held_symbols = set(holdings_map.keys())

    # Filter target companies
    if symbols:
        target_syms = {s.upper() for s in symbols}
        target_companies = [c for c in all_comps if c.get("symbol", "").upper() in target_syms]
    elif all_companies:
        target_companies = all_comps
    else:
        target_syms = held_symbols
        target_companies = [c for c in all_comps if c.get("symbol", "").upper() in target_syms]

    print(f"  Target companies: {len(target_companies)}")
    print(f"  Expected quarter: {expected_q}")
    print(f"  Holdings snapshot: {snapshot_date or 'none'}")

    # Optional: fetch data first
    if fetch:
        _fetch_data_for_companies(target_companies, expected_q)

    # Phase 1: Identify companies with recent results + master report
    print(f"\n── Scanning for qualifying companies ──")
    # When a specific quarter is given (e.g. "Mar 2026"), ONLY companies whose
    # latest financials match that exact quarter qualify.  When auto-detected,
    # also accept the immediately preceding quarter (results may still be coming
    # in).
    exact_quarter_mode = quarter is not None          # user explicitly passed --quarter
    if exact_quarter_mode:
        min_quarter = expected_q                      # strict: must have this quarter
    else:
        min_quarter = _previous_quarter(expected_q) or expected_q  # 1-quarter grace

    companies_qualified: list[tuple[str, str, dict[str, dict], str]] = []  # sym, name, financials, latest_q
    companies_no_report: list[str] = []
    companies_stale: list[str] = []
    companies_no_fin: list[str] = []
    companies_no_transcript: list[str] = []

    for c in target_companies:
        sym = c.get("symbol", "").upper()
        name = c.get("name", sym)
        has_thesis = (COMPANIES_DIR / sym / "reports" / "master_report.md").exists()
        financials, _ = _parse_quarterly_financials(sym)
        latest_q = _get_latest_quarter(financials)

        if not has_thesis and not include_no_thesis:
            companies_no_report.append(sym)
            continue
        if not latest_q:
            companies_no_fin.append(sym)
            continue
        if exact_quarter_mode:
            # Strict: latest quarter must BE the expected quarter
            if latest_q != expected_q:
                companies_stale.append(f"{sym}({latest_q})")
                continue
        else:
            if not _is_recent_quarter(latest_q, min_quarter):
                companies_stale.append(f"{sym}({latest_q})")
                continue

        # Check if we have a transcript or PPT for this latest quarter or newer
        has_valid_doc = False
        parts = latest_q.split()
        if len(parts) == 2:
            month_order = {'Jan':1,'Feb':2,'Mar':3,'Apr':4,'May':5,'Jun':6,
                           'Jul':7,'Aug':8,'Sep':9,'Oct':10,'Nov':11,'Dec':12}
            exp_y = int(parts[1])
            exp_m = month_order.get(parts[0], 0)
            
            docs = []
            t_dir = COMPANIES_DIR / sym / "transcripts"
            p_dir = COMPANIES_DIR / sym / "ppt"
            if t_dir.exists(): docs.extend(t_dir.glob("*.pdf"))
            if p_dir.exists(): docs.extend(p_dir.glob("*.pdf"))
            
            for doc in docs:
                dy, dm = _date_key_from_filename(doc.name)
                # Transcript must be from the same month/year or newer
                if dy > exp_y or (dy == exp_y and dm >= exp_m):
                    has_valid_doc = True
                    break
                    
        if not has_valid_doc:
            companies_no_transcript.append(sym)
            continue

        companies_qualified.append((sym, name, financials, latest_q))

    print(f"  ✓ Qualifying (report + recent results + recent doc): {len(companies_qualified)}")
    print(f"  ⏭  No master report: {len(companies_no_report)}")
    print(f"  📊 Stale financials: {len(companies_stale)}")
    print(f"  ❌ No financials: {len(companies_no_fin)}")
    print(f"  ⏳ Waiting for transcript: {len(companies_no_transcript)}")

    if companies_qualified:
        # Group by latest quarter
        q_groups: dict[str, list[str]] = {}
        for sym, name, _, lq in companies_qualified:
            q_groups.setdefault(lq, []).append(sym)
        for q in sorted(q_groups, reverse=True):
            print(f"\n  {q}: {len(q_groups[q])} companies")
            for sym in q_groups[q][:10]:
                has_thesis = (COMPANIES_DIR / sym / "reports" / "master_report.md").exists()
                thesis_flag = "📄" if has_thesis else "—"
                alloc = holdings_map.get(sym, {}).get("pct_of_portfolio", 0)
                alloc_str = f" ({alloc:.1f}%)" if alloc else ""
                print(f"    {thesis_flag} {sym:<16}{alloc_str}")
            if len(q_groups[q]) > 10:
                print(f"    ... and {len(q_groups[q]) - 10} more")

    if companies_stale:
        print(f"\n  Stale ({len(companies_stale)}): {', '.join(companies_stale[:15])}")

    if dry_run:
        print("\n  [DRY RUN] Stopping here. Use without --dry-run to run LLM analysis.")
        return None

    if not companies_qualified:
        print(f"\n  No qualifying companies found. Try --fetch or --include-no-thesis.")
        return None

    # Phase 2: Run thesis review for each qualifying company
    print(f"\n── Running thesis review for {len(companies_qualified)} companies ──")
    review_results: list[dict] = []

    for idx, (sym, name, financials, latest_q) in enumerate(companies_qualified, 1):
        print(f"\n  [{idx}/{len(companies_qualified)}] {sym} — {name} [{latest_q}]")

        # Load all context
        existing_thesis = _load_existing_thesis(sym)
        valuation_ctx = _load_valuation_context(sym)
        _, fin_text = _parse_quarterly_financials(sym)
        shareholding_text = _load_latest_shareholding(sym)
        transcripts_text = _load_latest_transcripts(sym)
        insights_text = _load_insights_values(sym)
        holding_ctx = _build_holding_context(sym, holdings_map, dma_cache)

        # Build prompt
        prompt = load_prompt(
            "thesis_review",
            company_name=name,
            symbol=sym,
            existing_thesis=existing_thesis,
            valuation_context=valuation_ctx,
            latest_financials=fin_text,
            latest_shareholding=shareholding_text,
            transcripts_text=transcripts_text,
            insights_values=insights_text,
            holding_context=holding_ctx,
        )

        # Call LLM
        try:
            review = call_llm(prompt, expect_json=True)
            status = review.get("thesis_status", "?")
            action = review.get("updated_conviction", {}).get("updated_action", "?")
            score = review.get("updated_conviction", {}).get("score", "?")
            status_emoji = {"strengthened": "🟢", "intact": "🔵", "weakening": "🟠", "broken": "🔴"}.get(status, "⚪")
            print(f"    {status_emoji} {status} | {action} | {score}/10")

            if summary := review.get("one_line_summary"):
                print(f"    → {summary[:100]}")

        except Exception as e:
            print(f"    ✗ LLM error: {e}")
            review = {
                "thesis_status": "unknown",
                "thesis_status_rationale": f"LLM call failed: {e}",
                "updated_conviction": {"score": 5, "updated_action": "Hold", "action_rationale": "Review manually."},
                "one_line_summary": "LLM analysis failed — manual review needed.",
            }

        # Save per-company report
        md = _build_thesis_review_markdown(sym, name, review)
        save_report(sym, "thesis_review", md, raw_json=review)

        review_results.append({
            "symbol": sym,
            "name": name,
            "review": review,
        })

        # Brief pause between LLM calls
        if idx < len(companies_qualified):
            time.sleep(1)

    if not review_results:
        print("\n  No companies reviewed. Ensure master reports exist or use --include-no-thesis.")
        return None

    # Phase 3: Write consolidated summary
    summary_md = _build_consolidated_summary(
        review_results, expected_q,
        total_companies=len(target_companies),
        companies_with_results=len(companies_qualified),
    )

    quarter_dir = EARNINGS_SEASON_DIR / expected_q.replace(" ", "")
    quarter_dir.mkdir(parents=True, exist_ok=True)
    summary_path = quarter_dir / "thesis_review_summary.md"
    summary_path.write_text(summary_md, encoding="utf-8")
    print(f"\n✓ Consolidated summary → {summary_path}")

    # Also save to portfolio dir if available
    if user_dir:
        portfolio_copy = user_dir / "thesis_review_summary.md"
        portfolio_copy.write_text(summary_md, encoding="utf-8")
        print(f"✓ Portfolio copy → {portfolio_copy}")

    # Print final summary
    print(f"\n{'=' * 60}")
    print(f"  THESIS REVIEW COMPLETE — {expected_q}")
    print(f"{'=' * 60}")

    for category, emoji in [
        ("strengthened", "🟢"), ("intact", "🔵"),
        ("weakening", "🟠"), ("broken", "🔴"),
    ]:
        items = [r for r in review_results if r["review"].get("thesis_status") == category]
        if items:
            names = ", ".join(f"{r['symbol']}" for r in items)
            print(f"  {emoji} {category.upper()} ({len(items)}): {names}")

    print(f"\n  Reports: data/companies/{{SYMBOL}}/reports/thesis_review.md")
    print(f"  Summary: {summary_path}")
    print()

    return summary_path


# ── Validation ──────────────────────────────────────────────────────────────

def validate(
    quarter: str | None = None,
    all_companies: bool = False,
    symbols: list[str] | None = None,
) -> None:
    """Post-execution validation: check data completeness for all planned companies."""
    expected_q = quarter if quarter else _expected_latest_quarter()
    expected_q_tag = expected_q.replace(" ", "")

    print(f"\n{'=' * 60}")
    print(f"  VALIDATION CHECK  —  {expected_q}")
    print(f"{'=' * 60}\n")

    # Build company list
    if all_companies:
        all_comps = _scan_all_company_dirs()
    else:
        all_comps = _load_all_companies()
    if symbols:
        target_syms = {s.upper() for s in symbols}
        all_comps = [c for c in all_comps if c.get("symbol", "").upper() in target_syms]

    total = len(all_comps)
    fin_ok, fin_miss = 0, []
    fin_quarter_ok, fin_quarter_miss = 0, []
    sh_ok, sh_miss = 0, []
    sh_quarter_ok, sh_quarter_miss = 0, []
    tr_ok, tr_miss = 0, []
    review_ok, review_miss = 0, []
    report_ok, report_miss = 0, []

    for c in all_comps:
        sym = c.get("symbol", "").upper()
        comp_dir = COMPANIES_DIR / sym

        # 1. Financials exist?
        fin_dir = comp_dir / "financials"
        fin_files = sorted(fin_dir.glob(f"{sym}_financials_*.txt"), reverse=True) if fin_dir.exists() else []
        if fin_files:
            fin_ok += 1
            # Check if latest quarter matches expected
            financials, _ = _parse_quarterly_financials(sym)
            latest_q = _get_latest_quarter(financials)
            if latest_q == expected_q:
                fin_quarter_ok += 1
            else:
                fin_quarter_miss.append(f"{sym}({latest_q or '?'})")
        else:
            fin_miss.append(sym)

        # 2. Shareholding — check if latest file has expected quarter
        sh_dir = comp_dir / "shareholding"
        sh_files = sorted(sh_dir.glob(f"{sym}_shareholding_*.txt"), reverse=True) if sh_dir.exists() else []
        if sh_files:
            sh_ok += 1
            sh_text = sh_files[0].read_text()[:500]
            if expected_q in sh_text:
                sh_quarter_ok += 1
            else:
                # Extract latest quarter from shareholding
                import re as _re
                m = _re.search(r"Quarter:\s*(\w+ \d{4})", sh_text)
                sh_q = m.group(1) if m else "?"
                sh_quarter_miss.append(f"{sym}({sh_q})")
        else:
            sh_miss.append(sym)

        # 3. Transcripts
        tr_dir = comp_dir / "transcripts"
        tr_files = list(tr_dir.glob("*.pdf")) if tr_dir.exists() else []
        if tr_files:
            tr_ok += 1
        else:
            tr_miss.append(sym)

        # 4. Master report
        if (comp_dir / "reports" / "master_report.md").exists():
            report_ok += 1
        else:
            report_miss.append(sym)

        # 5. Thesis review (for expected quarter)
        review_json = comp_dir / "reports" / "thesis_review.json"
        if review_json.exists():
            try:
                rv = json.loads(review_json.read_text())
                rv_q = rv.get("results_assessment", {}).get("quarter", "")
                if expected_q in rv_q or rv_q == expected_q:
                    review_ok += 1
                else:
                    review_miss.append(f"{sym}({rv_q or '?'})")
            except Exception:
                review_miss.append(sym)
        else:
            review_miss.append(sym)

    def _pct(n: int) -> str:
        return f"{n/total*100:.0f}%" if total else "0%"

    print(f"  Companies checked: {total}\n")
    print(f"  📊 FINANCIALS:")
    print(f"     Have data:           {fin_ok}/{total} ({_pct(fin_ok)})")
    print(f"     Have {expected_q}:  {fin_quarter_ok}/{total} ({_pct(fin_quarter_ok)})")
    if fin_miss:
        print(f"     Missing data:        {', '.join(fin_miss[:20])}")
    if fin_quarter_miss:
        print(f"     Missing {expected_q}: {', '.join(fin_quarter_miss[:20])}")
        if len(fin_quarter_miss) > 20:
            print(f"                          ... and {len(fin_quarter_miss) - 20} more")

    print(f"\n  📋 SHAREHOLDING:")
    print(f"     Have data:           {sh_ok}/{total} ({_pct(sh_ok)})")
    print(f"     Have {expected_q}:  {sh_quarter_ok}/{total} ({_pct(sh_quarter_ok)})")
    if sh_miss:
        print(f"     Missing data:        {', '.join(sh_miss[:20])}")
    if sh_quarter_miss:
        print(f"     Missing {expected_q}: {', '.join(sh_quarter_miss[:20])}")
        if len(sh_quarter_miss) > 20:
            print(f"                          ... and {len(sh_quarter_miss) - 20} more")

    print(f"\n  🎙️  TRANSCRIPTS:")
    print(f"     Have PDFs:           {tr_ok}/{total} ({_pct(tr_ok)})")
    if tr_miss:
        print(f"     No transcripts:      {', '.join(tr_miss[:20])}")

    print(f"\n  📄 MASTER REPORTS:")
    print(f"     Have report:         {report_ok}/{total} ({_pct(report_ok)})")
    if report_miss:
        print(f"     No report:           {', '.join(report_miss[:20])}")

    print(f"\n  🔍 THESIS REVIEWS ({expected_q}):")
    print(f"     Complete:            {review_ok}/{total} ({_pct(review_ok)})")
    if review_miss:
        print(f"     Missing/stale:       {', '.join(review_miss[:20])}")
        if len(review_miss) > 20:
            print(f"                          ... and {len(review_miss) - 20} more")

    print(f"\n{'=' * 60}")
    all_good = fin_quarter_ok == total and sh_quarter_ok == total and review_ok == total
    if all_good:
        print(f"  ✅ ALL CHECKS PASSED for {expected_q}")
    else:
        print(f"  ⚠️  GAPS DETECTED — see details above")
    print(f"{'=' * 60}\n")


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Post-results thesis review — compare latest quarterly results against existing investment thesis"
    )
    parser.add_argument(
        "--symbols", nargs="+", metavar="SYM",
        help="Review only these symbols (default: all held stocks with results)"
    )
    parser.add_argument(
        "--user", metavar="ID",
        help="Kite user ID (auto-detected if only one user)"
    )
    parser.add_argument(
        "--fetch", action="store_true",
        help="Fetch latest data (financials, shareholding, transcripts) before review"
    )
    parser.add_argument(
        "--include-no-thesis", action="store_true",
        help="Also review companies without an existing master_report"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Just list companies with results declared, no LLM calls"
    )
    parser.add_argument(
        "--quarter", metavar="Q",
        help="Override expected quarter (e.g. 'Dec 2025', 'Mar 2026')"
    )
    parser.add_argument(
        "--all-companies", action="store_true",
        help="Scan all companies in data/companies/ (not just portfolio holdings)"
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Run post-execution validation to check data completeness"
    )
    args = parser.parse_args()

    if args.validate:
        validate(
            quarter=args.quarter,
            all_companies=args.all_companies,
            symbols=args.symbols,
        )
    else:
        run(
            symbols=args.symbols,
            user_id=args.user,
            fetch=args.fetch,
            include_no_thesis=args.include_no_thesis,
            dry_run=args.dry_run,
            quarter=args.quarter,
            all_companies=args.all_companies,
        )
