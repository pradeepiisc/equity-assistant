"""
Workflow: Growth Ranking
=========================
Runs growth_signals analysis for all portfolio companies, clusters them into
HIGH / MEDIUM / LOW growth buckets based on bottom-line trajectory, and
generates a single portfolio-wide markdown report.

Usage:
    python -m workflows.growth_ranking [--refresh] [--symbols S1 S2 ...]
                                       [--batch-size N] [--offset N]

Outputs:
    data/companies/{SYMBOL}/reports/growth_signals.{md,json}  (per company)
    portfolio/ZV3899/growth_ranking_{YYYYMMDD}.md             (master report)
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from llm.client import get_config
from skills.growth_signals import run as _growth_signals_run
from skills.company_meta import get_company_name as _get_name

PROJECT_ROOT = Path(__file__).parent.parent
COMPANIES_DIR = PROJECT_ROOT / "data" / "companies"

SKIP_SYMBOLS = {"GAUDIUMIVF", "BEWLTD-SM"}


# ── Company loading ───────────────────────────────────────────────────────────

def _load_all_companies() -> list[dict]:
    companies: list[dict] = []
    seen: set[str] = set()
    for yaml_file in ["watchlist.yaml", "portfolio_companies.yaml"]:
        p = PROJECT_ROOT / yaml_file
        if not p.exists():
            continue
        with open(p) as f:
            data = yaml.safe_load(f)
        for c in data.get("stocks", []):
            sym = c.get("symbol", "").upper()
            if sym and sym not in seen and sym not in SKIP_SYMBOLS:
                seen.add(sym)
                companies.append(c)
    return companies


# ── Report generation ─────────────────────────────────────────────────────────

def _bucket_emoji(bucket: str) -> str:
    return {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(bucket.lower(), "⚪")


def _trajectory_arrow(trajectory: str) -> str:
    return {
        "accelerating": "↑↑",
        "steady": "→",
        "decelerating": "↓",
        "declining": "↓↓",
        "turnaround": "↗",
    }.get(trajectory.lower(), "?")


def _build_cluster_table(companies_data: list[dict]) -> str:
    rows = ["| Symbol | Company | Score | Trajectory | BL Outlook | Top Driver | Red Flags |",
            "|--------|---------|-------|------------|------------|------------|-----------|"]
    for item in companies_data:
        sym = item["symbol"]
        name = _get_name(sym, item.get("name", sym))[:30]
        d = item.get("data", {})
        score = d.get("growth_score", "?")
        traj = d.get("trajectory", "?")
        arr = _trajectory_arrow(traj)
        outlook = d.get("bottom_line_outlook", "?")
        drivers = d.get("key_growth_drivers", [])
        top_driver = (drivers[0][:40] if drivers else "—")
        red_flags = d.get("red_flags", [])
        flag_col = "⚠️ " + red_flags[0][:35] if red_flags else "—"
        rows.append(f"| **{sym}** | {name} | {score}/10 | {arr} {traj} | {outlook} | {top_driver} | {flag_col} |")
    return "\n".join(rows)


def _build_report(
    results: list[dict],
    date_str: str,
    total: int,
    elapsed_min: float,
) -> str:
    high = [r for r in results if r.get("data", {}).get("growth_bucket", "").lower() == "high"]
    medium = [r for r in results if r.get("data", {}).get("growth_bucket", "").lower() == "medium"]
    low = [r for r in results if r.get("data", {}).get("growth_bucket", "").lower() == "low"]
    errors = [r for r in results if r.get("status") == "error"]
    no_data = [r for r in results if r.get("status") == "no_data"]

    high_s = sorted(high, key=lambda r: r.get("data", {}).get("growth_score", 0), reverse=True)
    medium_s = sorted(medium, key=lambda r: r.get("data", {}).get("growth_score", 0), reverse=True)
    low_s = sorted(low, key=lambda r: r.get("data", {}).get("growth_score", 0), reverse=True)

    lines = [
        f"# Portfolio Growth Ranking — {date_str}",
        "",
        f"**Companies analysed:** {total}  |  "
        f"🟢 High: {len(high)}  |  🟡 Medium: {len(medium)}  |  🔴 Low: {len(low)}  |  "
        f"⚪ No data: {len(no_data)}  |  ❌ Errors: {len(errors)}",
        f"**Generated in:** {elapsed_min:.1f} min",
        "",
        "> *Clusters based on LLM assessment of transcripts, PPTs, and Screener Insights.*",
        "> *growth_score 1–10: 9–10=exceptional, 7–8=solid, 5–6=moderate, 3–4=slowing, 1–2=declining*",
        "",
    ]

    if high_s:
        lines += [
            "---",
            "## 🟢 HIGH GROWTH BUCKET",
            f"*{len(high_s)} companies with strong bottom-line trajectory*",
            "",
            _build_cluster_table(high_s),
            "",
        ]
        for item in high_s:
            d = item.get("data", {})
            sym = item["symbol"]
            name = _get_name(sym, item.get("name", sym))
            score = d.get("growth_score", "?")
            summary = d.get("overall_summary", "No summary.")
            drivers = d.get("key_growth_drivers", [])
            risks = d.get("key_risks", [])
            lines += [
                f"### {sym} — {name}",
                f"**Score:** {score}/10 | **Trajectory:** {d.get('trajectory','?')} | "
                f"**Bottom Line:** {d.get('bottom_line_outlook','?')} | "
                f"**Mgmt Confidence:** {d.get('management_confidence','?')}",
                "",
                summary,
                "",
            ]
            if drivers:
                lines.append("**Drivers:** " + " · ".join(f"_{d_}_" for d_ in drivers[:3]))
            if risks:
                lines.append("**Risks:** " + " · ".join(risks[:2]))
            if rf := d.get("red_flags"):
                lines.append("**⚠️ Red flags:** " + " · ".join(rf[:2]))
            lines.append("")

    if medium_s:
        lines += [
            "---",
            "## 🟡 MEDIUM GROWTH BUCKET",
            f"*{len(medium_s)} companies with moderate/mixed trajectory*",
            "",
            _build_cluster_table(medium_s),
            "",
        ]
        for item in medium_s:
            d = item.get("data", {})
            sym = item["symbol"]
            name = _get_name(sym, item.get("name", sym))
            score = d.get("growth_score", "?")
            summary = d.get("overall_summary", "No summary.")
            lines += [
                f"### {sym} — {name}",
                f"**Score:** {score}/10 | **Trajectory:** {d.get('trajectory','?')} | "
                f"**Bottom Line:** {d.get('bottom_line_outlook','?')}",
                "",
                summary,
                "",
            ]
            if drivers := d.get("key_growth_drivers"):
                lines.append("**Drivers:** " + " · ".join(f"_{d_}_" for d_ in drivers[:3]))
            if risks := d.get("key_risks"):
                lines.append("**Risks:** " + " · ".join(risks[:2]))
            if rf := d.get("red_flags"):
                lines.append("**⚠️ Red flags:** " + " · ".join(rf[:2]))
            lines.append("")

    if low_s:
        lines += [
            "---",
            "## 🔴 LOW GROWTH BUCKET",
            f"*{len(low_s)} companies with weak or declining trajectory*",
            "",
            _build_cluster_table(low_s),
            "",
        ]
        for item in low_s:
            d = item.get("data", {})
            sym = item["symbol"]
            name = _get_name(sym, item.get("name", sym))
            score = d.get("growth_score", "?")
            summary = d.get("overall_summary", "No summary.")
            lines += [
                f"### {sym} — {name}",
                f"**Score:** {score}/10 | **Trajectory:** {d.get('trajectory','?')} | "
                f"**Bottom Line:** {d.get('bottom_line_outlook','?')}",
                "",
                summary,
                "",
            ]
            if drivers := d.get("key_growth_drivers"):
                lines.append("**Drivers:** " + " · ".join(f"_{d_}_" for d_ in drivers[:3]))
            if risks := d.get("key_risks"):
                lines.append("**Risks:** " + " · ".join(risks[:2]))
            if rf := d.get("red_flags"):
                lines.append("**⚠️ Red flags:** " + " · ".join(rf[:2]))
            lines.append("")

    if no_data:
        lines += [
            "---",
            "## ⚪ NO DATA",
            f"*{len(no_data)} companies with insufficient transcript/PPT/insights data*",
            "",
        ]
        for item in no_data:
            sym = item["symbol"]
            lines.append(f"- **{sym}** — {_get_name(sym, item.get('name', ''))} ({item.get('error', 'no data')})")
        lines.append("")

    if errors:
        lines += [
            "---",
            "## ❌ ERRORS",
            "",
        ]
        for item in errors:
            lines.append(f"- **{item['symbol']}**: {item.get('error', 'unknown error')}")
        lines.append("")

    return "\n".join(lines)


# ── Main run ──────────────────────────────────────────────────────────────────

def run(
    symbols: list[str] | None = None,
    refresh: bool = False,
    batch_size: int | None = None,
    offset: int = 0,
    delay_seconds: float = 3.0,
) -> dict:
    """
    Run growth signals for all companies and produce a ranking report.

    Args:
        symbols:       Specific symbols to process (None = all)
        refresh:       Force re-run even if growth_signals.json exists
        batch_size:    Max companies per run
        offset:        Skip first N companies
        delay_seconds: Seconds between LLM calls (rate-limit protection)
    """
    config = get_config()
    all_companies = _load_all_companies()

    if symbols:
        requested = {s.upper() for s in symbols}
        companies = [
            c for c in all_companies
            if c.get("symbol", "").upper() in requested
            or str(c.get("screener_symbol") or "").upper() in requested
        ]
    else:
        companies = all_companies

    if offset:
        companies = companies[offset:]
        print(f"[INFO] Starting from offset {offset} ({len(companies)} remaining).")

    if batch_size and len(companies) > batch_size:
        companies = companies[:batch_size]
        print(f"[INFO] Batch limited to {batch_size} companies (offset={offset}).")

    total = len(companies)
    date_str = datetime.now().strftime("%Y%m%d")

    print()
    print("=" * 60)
    print(f"  GROWTH RANKING  —  {total} companies")
    print(f"  Mode: {'refresh' if refresh else 'skip-existing'}")
    print("=" * 60)
    print()

    results: list[dict] = []
    t0 = time.time()

    for i, company in enumerate(companies, 1):
        sym = company["symbol"]
        name = company.get("name", sym)
        print(f"[{i:3}/{total}] {sym:<20} {name[:40]}")

        r = _growth_signals_run(company, config, refresh=refresh)
        r["name"] = name
        results.append(r)

        if r["status"] == "success":
            d = r.get("data", {})
            bucket = d.get("growth_bucket", "?")
            score = d.get("growth_score", "?")
            print(f"       → {bucket.upper()} ({score}/10) | {d.get('trajectory','?')} | {d.get('bottom_line_outlook','?')}")
        elif r["status"] == "skipped":
            d = r.get("data", {})
            print(f"       → [cached] {d.get('growth_bucket','?').upper()} ({d.get('growth_score','?')}/10)")
        elif r["status"] == "no_data":
            print(f"       → [no data] {r.get('error','')[:60]}")
        else:
            print(f"       → [ERROR] {r.get('error','')[:80]}")

        if i < total and r["status"] not in ("skipped",):
            time.sleep(delay_seconds)

    elapsed = (time.time() - t0) / 60.0

    report_md = _build_report(results, date_str, total, elapsed)

    portfolio_dir = PROJECT_ROOT / "portfolio" / "ZV3899"
    portfolio_dir.mkdir(parents=True, exist_ok=True)
    report_path = portfolio_dir / f"growth_ranking_{date_str}.md"
    report_path.write_text(report_md, encoding="utf-8")
    print(f"\n[REPORT] Saved → {report_path}")

    high_count = sum(1 for r in results if r.get("data", {}).get("growth_bucket", "").lower() == "high")
    med_count = sum(1 for r in results if r.get("data", {}).get("growth_bucket", "").lower() == "medium")
    low_count = sum(1 for r in results if r.get("data", {}).get("growth_bucket", "").lower() == "low")

    print()
    print("=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Companies processed : {total}")
    print(f"  🟢 High growth      : {high_count}")
    print(f"  🟡 Medium growth    : {med_count}")
    print(f"  🔴 Low growth       : {low_count}")
    print(f"  ⚪ No data          : {sum(1 for r in results if r['status'] == 'no_data')}")
    print(f"  ❌ Errors           : {sum(1 for r in results if r['status'] == 'error')}")
    print(f"  Time                : {elapsed:.1f} min")
    print(f"  Report              : portfolio/ZV3899/growth_ranking_{date_str}.md")
    print()

    return {
        "total": total,
        "high": high_count,
        "medium": med_count,
        "low": low_count,
        "report_path": str(report_path),
        "results": results,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Portfolio growth ranking via LLM analysis")
    parser.add_argument("--symbols", nargs="+", help="Specific symbols to process")
    parser.add_argument("--refresh", action="store_true", help="Force re-run for all (ignore cached)")
    parser.add_argument("--batch-size", type=int, default=None, help="Max companies per run")
    parser.add_argument("--offset", type=int, default=0, help="Skip first N companies")
    parser.add_argument("--delay", type=float, default=3.0, help="Seconds between LLM calls")
    args = parser.parse_args()

    run(
        symbols=args.symbols,
        refresh=args.refresh,
        batch_size=args.batch_size,
        offset=args.offset,
        delay_seconds=args.delay,
    )
