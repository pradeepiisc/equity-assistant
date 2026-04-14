"""
Workflow: Conviction Builder
==============================
For stocks already held in your portfolio, synthesises all available analysis
(growth signals, financials, transcripts, valuation) with current price and
DMA context into a single conviction review with a clear action recommendation.

Usage:
    python -m workflows.conviction_builder --symbols AURIONPRO BETA YASHO
    python -m workflows.conviction_builder --top-losers 5
    python -m workflows.conviction_builder --top-losers 5 --symbols AURIONPRO
    python -m workflows.conviction_builder --symbols BETA --user ZV3899

Output:
    portfolio/{user}/holdings/{date}/conviction_review.md
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent
PORTFOLIO_DIR = PROJECT_ROOT / "portfolio"
COMPANIES_DIR = PROJECT_ROOT / "data" / "companies"
CACHE_DIR = PORTFOLIO_DIR / ".cache"

MAX_ARTIFACT_CHARS = 3000  # cap each analysis artifact to keep prompt manageable


# ── loaders ───────────────────────────────────────────────────────────────────

def _find_user_dir(user_id: str | None = None) -> Path:
    dirs = [d for d in PORTFOLIO_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if not dirs:
        raise FileNotFoundError("No portfolio data found. Run: python -m integrations.kite_connect --save-portfolio")
    if user_id:
        match = next((d for d in dirs if d.name.upper() == user_id.upper()), None)
        if not match:
            raise FileNotFoundError(f"User '{user_id}' not found under portfolio/")
        return match
    if len(dirs) == 1:
        return dirs[0]
    raise ValueError(f"Multiple users found: {[d.name for d in dirs]}. Pass --user <ID>.")


def _load_latest_holdings(user_dir: Path) -> tuple[list[dict], str]:
    holdings_dir = user_dir / "holdings"
    if not holdings_dir.exists():
        raise FileNotFoundError(f"No holdings/ directory under {user_dir}")
    date_dirs = sorted([d for d in holdings_dir.iterdir() if d.is_dir()], key=lambda d: d.name, reverse=True)
    if not date_dirs:
        raise FileNotFoundError("No dated snapshots found")
    latest = date_dirs[0]
    hf = latest / "holdings.json"
    if not hf.exists():
        raise FileNotFoundError(f"holdings.json missing in {latest}")
    return json.loads(hf.read_text()), latest.name


def _load_prev_prices(user_dir: Path, current_date: str) -> dict[str, float]:
    holdings_dir = user_dir / "holdings"
    if not holdings_dir.exists():
        return {}
    prev_dirs = sorted(
        [d for d in holdings_dir.iterdir() if d.is_dir() and d.name < current_date],
        key=lambda d: d.name,
        reverse=True,
    )
    for prev in prev_dirs:
        hf = prev / "holdings.json"
        if hf.exists():
            prev_h = json.loads(hf.read_text())
            return {h["symbol"]: h["last_price"] for h in prev_h if "last_price" in h}
    return {}


def _load_dma_cache() -> dict[str, dict]:
    today = date.today().isoformat()
    cache_file = CACHE_DIR / f"dma_{today}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())
    # Try most recent cache
    caches = sorted(CACHE_DIR.glob("dma_*.json"), reverse=True)
    if caches:
        return json.loads(caches[0].read_text())
    return {}


def _load_analysis_artifacts(symbol: str) -> dict[str, dict]:
    """Load available LLM analysis JSON outputs for a symbol."""
    reports_dir = COMPANIES_DIR / symbol / "reports"
    artifacts: dict[str, dict] = {}
    for skill in ["growth_signals", "financial_snapshot", "transcript_analysis",
                  "valuation_agent", "shareholding_analysis"]:
        json_path = reports_dir / f"{skill}.json"
        if json_path.exists():
            try:
                artifacts[skill] = json.loads(json_path.read_text())
            except Exception:
                pass
    return artifacts


def _load_company_name(symbol: str) -> str:
    meta = COMPANIES_DIR / symbol / "meta.json"
    if meta.exists():
        try:
            return json.loads(meta.read_text()).get("name", symbol)
        except Exception:
            pass
    return symbol


# ── top losers ────────────────────────────────────────────────────────────────

def _get_top_losers(user_dir: Path, n: int) -> list[str]:
    """Return top N daily losers by day_change_pct; fall back to total P&L losers."""
    try:
        holdings, snapshot_date = _load_latest_holdings(user_dir)
        prev_prices = _load_prev_prices(user_dir, snapshot_date)
        if prev_prices:
            def day_change(h: dict) -> float:
                prev = prev_prices.get(h["symbol"])
                if prev and prev > 0:
                    return (h["last_price"] - prev) / prev * 100
                return 0.0
            losers = sorted(holdings, key=day_change)
        else:
            losers = sorted(holdings, key=lambda h: h.get("pnl_pct", 0))
        return [h["symbol"] for h in losers[:n]]
    except Exception as e:
        print(f"[conviction] Warning: could not compute top losers: {e}")
        return []


# ── context builders ──────────────────────────────────────────────────────────

def _build_holding_context(holding: dict, dma: dict, prev_prices: dict) -> str:
    lines = [
        f"Symbol: {holding['symbol']}",
        f"Average Cost: ₹{holding['avg_price']:.2f}",
        f"Current Price: ₹{holding['last_price']:.2f}",
        f"Unrealised P&L: {holding['pnl_pct']:+.1f}% (₹{holding.get('pnl', 0):,.0f})",
        f"Portfolio Allocation: {holding.get('pct_of_portfolio', 0):.1f}%",
        f"Quantity Held: {holding['quantity']}",
    ]

    prev = prev_prices.get(holding["symbol"])
    if prev and prev > 0:
        day_chg = (holding["last_price"] - prev) / prev * 100
        lines.append(f"Today's Move: {day_chg:+.1f}%")

    dma200 = dma.get("dma200")
    dma50 = dma.get("dma50")
    if dma200:
        pct200 = (holding["last_price"] - dma200) / dma200 * 100
        lines.append(f"vs 200 DMA (₹{dma200:.1f}): {pct200:+.1f}%")
    else:
        lines.append("vs 200 DMA: not available")
    if dma50:
        pct50 = (holding["last_price"] - dma50) / dma50 * 100
        lines.append(f"vs 50 DMA (₹{dma50:.1f}): {pct50:+.1f}%")

    return "\n".join(lines)


def _build_analysis_context(symbol: str, artifacts: dict) -> tuple[str, str]:
    """Returns (context_string, enrichment_hint_command)."""
    if not artifacts:
        hint = f"python -m workflows.full_company_analysis {symbol}"
        return "No analysis data available for this stock.", hint

    parts: list[str] = []

    if gs := artifacts.get("growth_signals"):
        score = gs.get("growth_score", "?")
        bucket = gs.get("growth_bucket", "?")
        trajectory = gs.get("trajectory", "?")
        summary = gs.get("overall_summary", "")
        drivers = gs.get("key_growth_drivers", [])
        risks = gs.get("key_risks", [])
        flags = gs.get("red_flags", [])
        parts.append(
            f"GROWTH SIGNALS (score {score}/10, {bucket}, {trajectory}):\n"
            + (f"Summary: {summary}\n" if summary else "")
            + (f"Drivers: {'; '.join(drivers[:3])}\n" if drivers else "")
            + (f"Risks: {'; '.join(risks[:3])}\n" if risks else "")
            + (f"Red Flags: {'; '.join(flags)}\n" if flags else "")
        )

    if fs := artifacts.get("financial_snapshot"):
        text = json.dumps(fs, ensure_ascii=False)[:MAX_ARTIFACT_CHARS]
        parts.append(f"FINANCIAL SNAPSHOT:\n{text}")

    if ta := artifacts.get("transcript_analysis"):
        text = json.dumps(ta, ensure_ascii=False)[:MAX_ARTIFACT_CHARS]
        parts.append(f"TRANSCRIPT ANALYSIS (management commentary):\n{text}")

    if va := artifacts.get("valuation_agent"):
        fv_low = va.get("fair_value_low")
        fv_high = va.get("fair_value_high")
        signal = va.get("entry_signal", "")
        method = va.get("valuation_method", "")
        rationale = va.get("valuation_rationale", "")
        parts.append(
            f"VALUATION AGENT:\n"
            + (f"Fair Value Range: ₹{fv_low}–₹{fv_high}\n" if fv_low and fv_high else "")
            + (f"Entry Signal: {signal}\n" if signal else "")
            + (f"Method: {method}\n" if method else "")
            + (f"Rationale: {rationale[:500]}\n" if rationale else "")
        )

    if sa := artifacts.get("shareholding_analysis"):
        text = json.dumps(sa, ensure_ascii=False)[:1500]
        parts.append(f"SHAREHOLDING ANALYSIS:\n{text}")

    enrichment_needed = len(artifacts) < 3
    hint = f"python -m workflows.full_company_analysis {symbol}" if enrichment_needed else ""

    return "\n\n".join(parts), hint


# ── markdown builder ──────────────────────────────────────────────────────────

def _action_emoji(action: str) -> str:
    return {
        "Strong Add": "🟢",
        "Add": "🔵",
        "Hold": "🟡",
        "Watch": "🟠",
        "Trim": "🔶",
        "Exit": "🔴",
    }.get(action, "⚪")


def _build_stock_section(
    symbol: str,
    company_name: str,
    holding: dict,
    dma: dict,
    review: dict,
) -> str:
    action = review.get("action", "Hold")
    score = review.get("conviction_score", "?")
    emoji = _action_emoji(action)
    dma200 = dma.get("dma200")
    dma50 = dma.get("dma50")
    ltp = holding["last_price"]
    pct200_str = f"{(ltp - dma200) / dma200 * 100:+.1f}%" if dma200 else "—"
    pct50_str = f"{(ltp - dma50) / dma50 * 100:+.1f}%" if dma50 else "—"

    lines = [
        f"---",
        f"## {symbol} — {company_name}",
        f"",
        f"**{emoji} Action: {action}**  |  Conviction: **{score}/10**  |  Data: *{review.get('data_quality', '?')}*",
        f"",
        f"### Holding",
        f"| Avg ₹ | Current ₹ | P&L% | Allocation | vs 200 DMA | vs 50 DMA |",
        f"|---|---|---|---|---|---|",
        f"| ₹{holding['avg_price']:.1f} | ₹{ltp:.1f} | {holding['pnl_pct']:+.1f}% "
        f"| {holding.get('pct_of_portfolio', 0):.1f}% | {pct200_str} | {pct50_str} |",
        f"",
    ]

    if rationale := review.get("action_rationale"):
        lines += [f"**Why {action}:** {rationale}", ""]

    if dma_interp := review.get("dma_interpretation"):
        lines += [f"**Momentum:** {dma_interp}", ""]

    thesis_intact = review.get("thesis_intact")
    thesis_comment = review.get("thesis_comment", "")
    if thesis_comment:
        intact_icon = "✅" if thesis_intact else "⚠️"
        lines += [f"**Thesis:** {intact_icon} {thesis_comment}", ""]

    if bull := review.get("bull_case"):
        lines.append("**Bull Case:**")
        for b in bull:
            lines.append(f"- {b}")
        lines.append("")

    if bear := review.get("bear_case"):
        lines.append("**Bear Case:**")
        for b in bear:
            lines.append(f"- {b}")
        lines.append("")

    pl = review.get("price_levels", {})
    if any(v for v in pl.values()):
        lines.append("**Price Levels:**")
        if pl.get("add_below"):
            lines.append(f"- Add below: ₹{pl['add_below']}")
        if pl.get("trim_above"):
            lines.append(f"- Trim above: ₹{pl['trim_above']}")
        if pl.get("stop_loss"):
            lines.append(f"- Stop-loss watch: ₹{pl['stop_loss']}")
        lines.append("")

    if triggers := review.get("monitoring_triggers"):
        lines.append("**Monitor:**")
        for t in triggers:
            lines.append(f"- {t}")
        lines.append("")

    if hint := review.get("enrichment_hint"):
        lines += [f"> *Run `{hint}` to enrich analysis.*", ""]

    return "\n".join(lines)


def _build_summary_table(results: list[tuple[dict, dict, dict, str, str]]) -> str:
    """Build the summary table at the top of the report."""
    lines = [
        "| Stock | Action | Conviction | P&L% | vs 200DMA | Data |",
        "|---|---|---|---|---|---|",
    ]
    for holding, dma, review, symbol, company_name in results:
        action = review.get("action", "?")
        score = review.get("conviction_score", "?")
        emoji = _action_emoji(action)
        pnl_pct = f"{holding['pnl_pct']:+.1f}%"
        dma200 = dma.get("dma200")
        ltp = holding["last_price"]
        dma_str = f"{(ltp - dma200) / dma200 * 100:+.1f}%" if dma200 else "—"
        quality = review.get("data_quality", "?")
        lines.append(f"| {symbol} | {emoji} {action} | {score}/10 | {pnl_pct} | {dma_str} | {quality} |")
    return "\n".join(lines)


# ── main run ──────────────────────────────────────────────────────────────────

def run(symbols: list[str], user_id: str | None = None) -> Path:
    from llm.utils import call_llm, load_prompt  # noqa: PLC0415

    user_dir = _find_user_dir(user_id)
    holdings_raw, snapshot_date = _load_latest_holdings(user_dir)
    prev_prices = _load_prev_prices(user_dir, snapshot_date)
    dma_cache = _load_dma_cache()

    holdings_map = {h["symbol"].upper(): h for h in holdings_raw}

    # Compute pnl_pct and pct_of_portfolio for holdings
    total_value = sum(h["current_value"] for h in holdings_raw)
    for h in holdings_raw:
        if "pnl_pct" not in h:
            h["pnl_pct"] = (h["pnl"] / h["invested_value"] * 100) if h.get("invested_value") else 0.0
        if "pct_of_portfolio" not in h:
            h["pct_of_portfolio"] = h["current_value"] / total_value * 100 if total_value else 0.0

    symbols_upper = [s.upper() for s in symbols]
    not_found = [s for s in symbols_upper if s not in holdings_map]
    if not_found:
        print(f"[conviction] Warning: not in current portfolio: {', '.join(not_found)}")
        symbols_upper = [s for s in symbols_upper if s in holdings_map]

    if not symbols_upper:
        print("[conviction] No valid symbols to review.")
        return None

    print(f"\n{'='*60}")
    print(f"  CONVICTION BUILDER — {len(symbols_upper)} stock(s) — {snapshot_date}")
    print(f"{'='*60}")

    prompt_template = load_prompt("conviction_review")
    results: list[tuple[dict, dict, dict, str, str]] = []

    for symbol in symbols_upper:
        print(f"\n[{symbol}] Building conviction review...")
        holding = holdings_map[symbol]
        dma = dma_cache.get(symbol, {})
        artifacts = _load_analysis_artifacts(symbol)
        company_name = _load_company_name(symbol)

        holding_ctx = _build_holding_context(holding, dma, prev_prices)
        analysis_ctx, _ = _build_analysis_context(symbol, artifacts)

        prompt = (
            prompt_template
            .replace("{holding_context}", holding_ctx)
            .replace("{analysis_context}", analysis_ctx)
        )

        try:
            review = call_llm(prompt, expect_json=True)
            action = review.get("action", "?")
            score = review.get("conviction_score", "?")
            print(f"  → {_action_emoji(action)} {action} | Conviction: {score}/10 | Data: {review.get('data_quality', '?')}")
        except Exception as e:
            print(f"  → LLM error: {e}")
            review = {
                "action": "Hold",
                "conviction_score": 5,
                "action_rationale": "LLM call failed — review manually.",
                "thesis_intact": None,
                "thesis_comment": "",
                "dma_interpretation": "",
                "bull_case": [],
                "bear_case": [],
                "price_levels": {},
                "monitoring_triggers": [],
                "data_quality": "thin",
                "enrichment_hint": f"python -m workflows.full_company_analysis {symbol}",
            }

        results.append((holding, dma, review, symbol, company_name))

    # Build report
    report_lines = [
        f"# Conviction Review",
        f"**{user_dir.name}  ·  {snapshot_date}**  |  {len(results)} stock(s) reviewed",
        "",
        "## Summary",
        _build_summary_table(results),
        "",
    ]
    for holding, dma, review, symbol, company_name in results:
        report_lines.append(_build_stock_section(symbol, company_name, holding, dma, review))

    report_md = "\n".join(report_lines)
    out_dir = user_dir / "holdings" / snapshot_date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "conviction_review.md"
    out_file.write_text(report_md, encoding="utf-8")

    print(f"\n✓ Conviction review saved → {out_file}")
    return out_file


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build conviction reviews for held stocks")
    parser.add_argument("--symbols", nargs="+", help="Stock symbols to review (e.g. AURIONPRO BETA)")
    parser.add_argument("--top-losers", type=int, metavar="N", help="Auto-pick top N daily losers")
    parser.add_argument("--user", help="Kite user ID (auto-detected if only one user exists)")
    args = parser.parse_args()

    if not args.symbols and not args.top_losers:
        print("Error: provide --symbols and/or --top-losers N")
        sys.exit(1)

    try:
        user_dir = _find_user_dir(args.user)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    symbols_to_review: list[str] = []

    if args.top_losers:
        losers = _get_top_losers(user_dir, args.top_losers)
        print(f"Top {args.top_losers} losers today: {', '.join(losers)}")
        symbols_to_review.extend(losers)

    if args.symbols:
        for s in args.symbols:
            if s.upper() not in [x.upper() for x in symbols_to_review]:
                symbols_to_review.append(s.upper())

    if not symbols_to_review:
        print("No symbols to review.")
        sys.exit(1)

    run(symbols_to_review, user_id=args.user)
