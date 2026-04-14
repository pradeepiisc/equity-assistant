from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from llm.client import get_config
from skills import financial_fetcher, valuation_agent
from workflows.sector_analysis import fetch_profile

PROJECT_ROOT = Path(__file__).parent.parent
PORTFOLIO_DIR = PROJECT_ROOT / "portfolio"
COMPANIES_DIR = PROJECT_ROOT / "data" / "companies"
SECTOR_PROFILES_DIR = PROJECT_ROOT / "data" / "sector_profiles"


def _find_user_dir(user_id: str) -> Path:
    p = PORTFOLIO_DIR / user_id
    if not p.exists():
        raise FileNotFoundError(f"User '{user_id}' not found under portfolio/")
    return p


def _load_holdings_snapshot(user_dir: Path, snapshot_date: str | None) -> tuple[list[dict], str]:
    holdings_dir = user_dir / "holdings"
    if not holdings_dir.exists():
        raise FileNotFoundError(f"No holdings/ directory found under {user_dir}")

    if snapshot_date:
        snap_dir = holdings_dir / snapshot_date
        if not snap_dir.exists():
            raise FileNotFoundError(f"Snapshot '{snapshot_date}' not found under {holdings_dir}")
    else:
        date_dirs = sorted([d for d in holdings_dir.iterdir() if d.is_dir()], key=lambda d: d.name, reverse=True)
        if not date_dirs:
            raise FileNotFoundError(f"No dated snapshots found under {holdings_dir}")
        snap_dir = date_dirs[0]
        snapshot_date = snap_dir.name

    holdings_file = snap_dir / "holdings.json"
    if not holdings_file.exists():
        raise FileNotFoundError(f"holdings.json missing in {snap_dir}")

    return json.loads(holdings_file.read_text(encoding="utf-8")), snapshot_date


def _top_holdings(holdings: list[dict], top: int | None, coverage_pct: float | None) -> list[dict]:
    by_value = sorted(holdings, key=lambda x: -x.get("current_value", 0))
    if coverage_pct is None:
        return by_value[: top or 31]

    total = sum(h.get("current_value", 0) for h in by_value) or 0
    out: list[dict] = []
    cumulative = 0.0
    for h in by_value:
        if total:
            cumulative += h.get("current_value", 0) / total * 100
        out.append(h)
        if cumulative >= coverage_pct:
            break
    return out


def _sector_profile_exists(symbol: str) -> bool:
    sym = symbol.upper()
    sym_norm = sym.replace("-SM", "").replace("-BE", "")
    return (SECTOR_PROFILES_DIR / f"{sym}.json").exists() or (SECTOR_PROFILES_DIR / f"{sym_norm}.json").exists()


def _ensure_sector_profile(symbol: str, exchange: str, refresh: bool = False) -> dict:
    holding = {"symbol": symbol, "exchange": exchange, "current_value": 0, "pnl_pct": 0, "pct_of_portfolio": 0}
    return fetch_profile(symbol, holding=holding, use_llm=False, refresh=refresh)


def _ensure_financials(symbol: str, screener_url: str, isin: str | None = None, refresh: bool = False) -> dict:
    cfg = get_config()
    company = {"symbol": symbol, "screener_url": screener_url, "isin": isin}
    return financial_fetcher.run(company, cfg, refresh=refresh)


def _valuation_json_path(symbol: str) -> Path:
    return COMPANIES_DIR / symbol.upper() / "reports" / "valuation_agent.json"


def _read_watchlist() -> dict[str, dict]:
    wl_path = PROJECT_ROOT / "watchlist.yaml"
    if not wl_path.exists():
        return {}
    wl = yaml.safe_load(wl_path.read_text(encoding="utf-8")) or {}
    return {c.get("symbol", "").upper(): c for c in wl.get("stocks", []) if isinstance(c, dict)}


def _action_hint(entry_signal: str | None) -> str:
    s = (entry_signal or "").strip()
    if s in ("Buy Now", "Accumulate on Dips"):
        return "Invest more"
    if s == "Hold":
        return "Hold"
    if s == "Trim":
        return "Reduce"
    if s == "Exit":
        return "Exit"
    return "Review"


def _build_ranking_report(
    snapshot_date: str,
    user_id: str,
    holdings: list[dict],
    symbols: list[str],
) -> str:
    holdings_map = {h["symbol"].upper(): h for h in holdings}
    total_value = sum(h.get("current_value", 0) for h in holdings) or 0

    rows = []
    missing = []

    for sym in symbols:
        p = _valuation_json_path(sym)
        if not p.exists():
            missing.append(sym)
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        h = holdings_map.get(sym.upper(), {})
        alloc = (h.get("current_value", 0) / total_value * 100) if total_value else 0
        entry_signal = data.get("entry_signal")
        reasoning = data.get("reasoning") or {}
        quant = reasoning.get("quantitative")
        qual = reasoning.get("qualitative")
        reasoning_quant = quant if isinstance(quant, list) else []
        reasoning_qual = qual if isinstance(qual, list) else []
        rows.append(
            {
                "symbol": sym,
                "alloc_pct": round(alloc, 2),
                "current_price": data.get("current_price"),
                "fair_low": data.get("fair_value_low"),
                "fair_high": data.get("fair_value_high"),
                "upside_pct": data.get("upside_pct"),
                "entry_signal": entry_signal,
                "action": _action_hint(entry_signal),
                "current_vs_fair": data.get("current_vs_fair"),
                "method": data.get("valuation_method"),
                "engine": data.get("engine") or "legacy",
                "reasoning_quant": reasoning_quant,
                "reasoning_qual": reasoning_qual,
            }
        )

    def _sort_key(r: dict) -> float:
        v = r.get("upside_pct")
        try:
            return float(v)
        except Exception:
            return -10_000.0

    rows_sorted = sorted(rows, key=_sort_key, reverse=True)

    lines: list[str] = []
    lines += [
        "# Valuation Ranking",
        f"**{user_id}  ·  snapshot {snapshot_date}**",
        "",
        "*Source: data/companies/*/reports/valuation_agent.json*",
        "",
        "## Ranked List (by Upside %)",
        "| # | Symbol | Alloc% | Price | Fair Low | Fair High | Upside% | Signal | Action | Engine | vs Fair | Method |",
        "|---|---|---:|---:|---:|---:|---:|---|---|---|---|---|",
    ]

    for i, r in enumerate(rows_sorted, 1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(i),
                    r["symbol"],
                    f"{r['alloc_pct']:.2f}%",
                    str(r.get("current_price") or "—"),
                    str(r.get("fair_low") or "—"),
                    str(r.get("fair_high") or "—"),
                    str(r.get("upside_pct") if r.get("upside_pct") is not None else "—"),
                    str(r.get("entry_signal") or "—"),
                    str(r.get("action") or "—"),
                    str(r.get("engine") or "—"),
                    str(r.get("current_vs_fair") or "—"),
                    str(r.get("method") or "—"),
                ]
            )
            + " |"
        )

    if rows_sorted:
        lines += [
            "",
            "## Reasons (Short)",
            "*Source: each company’s valuation_agent.json → reasoning (quantitative/qualitative)*",
        ]
        for r in rows_sorted:
            q = r.get("reasoning_quant") or []
            ql = r.get("reasoning_qual") or []
            q_items = [str(x) for x in q if isinstance(x, str) and x.strip()][:3]
            ql_items = [str(x) for x in ql if isinstance(x, str) and x.strip()][:1]
            if not q_items and not ql_items:
                continue

            upside = r.get("upside_pct")
            upside_s = f"{upside}%" if upside is not None else "—"
            signal = r.get("entry_signal") or "—"

            lines.append(f"### {r['symbol']} — {signal} (Upside: {upside_s})")
            if q_items:
                for item in q_items:
                    lines.append(f"- {item}")
            if ql_items:
                for item in ql_items:
                    lines.append(f"- {item}")
            lines.append("")

    if missing:
        lines += [
            "",
            "## Missing valuations",
            ", ".join(sorted(missing)),
        ]

    lines += [
        "",
        "## Interpretation",
        "- **Buy Now / Accumulate on Dips**: candidate to add (subject to thesis + concentration limits)",
        "- **Hold**: fairly valued vs this deterministic model",
        "- **Trim / Exit**: overvalued vs this deterministic model",
        "",
        "*Note: Valuations are generated by sector-expert LLM prompts. Use as a screening tool, not a final decision.*",
    ]

    return "\n".join(lines)


def _build_ranking_report_all_companies() -> str:
    rows = []
    for company_dir in sorted([p for p in COMPANIES_DIR.iterdir() if p.is_dir()], key=lambda p: p.name):
        sym = company_dir.name
        p = company_dir / "reports" / "valuation_agent.json"
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        entry_signal = data.get("entry_signal")
        rows.append(
            {
                "symbol": sym,
                "current_price": data.get("current_price"),
                "fair_low": data.get("fair_value_low"),
                "fair_high": data.get("fair_value_high"),
                "upside_pct": data.get("upside_pct"),
                "entry_signal": entry_signal,
                "action": _action_hint(entry_signal),
                "current_vs_fair": data.get("current_vs_fair"),
                "method": data.get("valuation_method"),
                "engine": data.get("engine") or "legacy",
            }
        )

    def _sort_key(r: dict) -> float:
        v = r.get("upside_pct")
        try:
            return float(v)
        except Exception:
            return -10_000.0

    rows_sorted = sorted(rows, key=_sort_key, reverse=True)

    lines: list[str] = []
    lines += [
        "# Valuation Ranking — All Companies",
        "",
        "*Source: data/companies/*/reports/valuation_agent.json*",
        "",
        "| # | Symbol | Price | Fair Low | Fair High | Upside% | Signal | Action | Engine | vs Fair | Method |",
        "|---|---|---:|---:|---:|---:|---|---|---|---|---|",
    ]

    for i, r in enumerate(rows_sorted, 1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(i),
                    r["symbol"],
                    str(r.get("current_price") or "—"),
                    str(r.get("fair_low") or "—"),
                    str(r.get("fair_high") or "—"),
                    str(r.get("upside_pct") if r.get("upside_pct") is not None else "—"),
                    str(r.get("entry_signal") or "—"),
                    str(r.get("action") or "—"),
                    str(r.get("engine") or "—"),
                    str(r.get("current_vs_fair") or "—"),
                    str(r.get("method") or "—"),
                ]
            )
            + " |"
        )

    lines += [
        "",
        "## Interpretation",
        "- **Buy Now / Accumulate on Dips**: consider adding",
        "- **Hold**: no action",
        "- **Trim / Exit**: consider reducing/exiting",
    ]

    return "\n".join(lines)


def run(
    user_id: str,
    snapshot_date: str | None,
    top: int | None,
    coverage: float | None,
    symbols: list[str] | None = None,
    refresh: bool = False,
) -> Path:
    user_dir = _find_user_dir(user_id)
    holdings, snap = _load_holdings_snapshot(user_dir, snapshot_date)

    report_target = _top_holdings(holdings, top=top, coverage_pct=coverage)
    report_symbols = [h["symbol"].upper() for h in report_target]

    if symbols:
        wanted = {s.upper() for s in symbols}
        target = [h for h in holdings if h.get("symbol", "").upper() in wanted]
        processed_symbols = [h.get("symbol", "").upper() for h in target if h.get("symbol")]
    else:
        target = report_target
        processed_symbols = report_symbols

    watchlist = _read_watchlist()

    print(f"\n{'='*60}")
    print(f"TOP HOLDINGS VALUATION — {user_id} · snapshot {snap}")
    print(f"{'='*60}")
    print(
        f"Valuing {len(processed_symbols)} holdings: {', '.join(processed_symbols[:10])}"
        f"{' …' if len(processed_symbols) > 10 else ''}\n"
    )

    for i, h in enumerate(target, 1):
        sym = h["symbol"].upper()
        exch = h.get("exchange", "NSE")
        isin = h.get("isin")

        print(f"[{i}/{len(target)}] {sym}")

        profile = None
        if refresh or not _sector_profile_exists(sym):
            profile = _ensure_sector_profile(sym, exch, refresh=refresh)

        if profile is None and _sector_profile_exists(sym):
            pass

        screener_url = None
        if profile and isinstance(profile, dict):
            screener_url = profile.get("screener_url")

        wl_entry = watchlist.get(sym)
        if not screener_url and wl_entry:
            screener_url = wl_entry.get("screener_url")

        if not screener_url:
            screener_url = f"https://www.screener.in/company/{sym.replace('-SM','').replace('-BE','')}/"

        fin_dir = COMPANIES_DIR / sym / "financials"
        fin_exists = fin_dir.exists() and any(p.is_file() for p in fin_dir.glob("*.txt"))
        if refresh or not fin_exists:
            fr = _ensure_financials(sym, screener_url, isin=isin, refresh=refresh)
            if fr.get("status") != "success":
                print(f"  [financials] {fr.get('status')}: {fr.get('error')}")

        fin_exists = fin_dir.exists() and any(p.is_file() for p in fin_dir.glob("*.txt"))
        if not fin_exists:
            print("  [valuation] skipped (no financials)")
            continue

        company = {
            "symbol": sym,
            "name": (wl_entry.get("name") if wl_entry else None) or (profile.get("name") if profile else None) or sym,
            "sector": (wl_entry.get("sector") if wl_entry else "") or "",
            "last_price": h.get("last_price"),
        }

        vr = valuation_agent.run(company, config={}, skill_results={})
        if vr.get("status") != "success":
            print(f"  [valuation] {vr.get('status')}: {vr.get('error')}")

    report = _build_ranking_report(snap, user_id, holdings, report_symbols)

    out_file = user_dir / "holdings" / snap / "valuation_ranking.md"
    out_file.write_text(report, encoding="utf-8")
    print(f"\n✓ Ranking report saved → {out_file}\n")

    report_all = _build_ranking_report_all_companies()
    out_all = user_dir / "holdings" / snap / "valuation_ranking_all_companies.md"
    out_all.write_text(report_all, encoding="utf-8")
    print(f"✓ All-companies ranking saved → {out_all}\n")
    return out_file


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Expert LLM valuation for top portfolio holdings")
    parser.add_argument("--user", required=True, help="Portfolio user ID (e.g., ZV3899)")
    parser.add_argument("--date", help="Snapshot date folder (YYYY-MM-DD). Default: latest")
    parser.add_argument("--top", type=int, default=None, help="Top N holdings by current value")
    parser.add_argument("--coverage", type=float, default=None, help="Cumulative coverage target (e.g., 50 for 50%%)")
    parser.add_argument("--symbols", nargs="+", metavar="SYM", help="Only process these symbols")
    parser.add_argument("--refresh", action="store_true", help="Re-fetch sector profile and financials")
    args = parser.parse_args()

    run(
        user_id=args.user,
        snapshot_date=args.date,
        top=args.top,
        coverage=args.coverage,
        symbols=args.symbols,
        refresh=args.refresh,
    )
