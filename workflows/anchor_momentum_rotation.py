import os
import json
import argparse
from datetime import datetime
from pathlib import Path
import re

def get_latest_date_folder(holdings_dir):
    running_csv = holdings_dir / "running_holdings.csv"
    if running_csv.exists():
        import pandas as pd
        df = pd.read_csv(running_csv, nrows=0)
        date_cols = [c for c in df.columns if c not in ('symbol', 'quantity', 'avg_price')]
        if date_cols:
            return date_cols[-1]
    from datetime import date
    return date.today().isoformat()

def categorize_trend(ltp, d40, d90, is_exit):
    if not d40:
        return "Unknown", "Unknown"
        
    if not d90:
        if ltp > d40:
            trend = "Bullish (No 90DMA)"
            group = "Trail Stop-Loss / Phased Exit" if is_exit else "Early Accumulation"
        else:
            trend = "Bearish (No 90DMA)"
            group = "Exit Immediately" if is_exit else "WAIT / Defer Entry"
        return trend, group

    if ltp < d40 and d40 < d90:
        trend = "Strong Bearish (LTP < 40 < 90)"
        group = "Exit Immediately" if is_exit else "WAIT / Defer Entry"
    elif ltp < d40 and d40 > d90:
        trend = "Bearish Pullback (LTP < 40 > 90)"
        group = "Trail Stop-Loss / Phased Exit" if is_exit else "Early Accumulation"
    elif ltp > d40 and d40 < d90:
        trend = "Bullish Reversal (LTP > 40 < 90)"
        group = "Trail Stop-Loss / Phased Exit" if is_exit else "Early Accumulation"
    else: # ltp > d40 and d40 > d90
        trend = "Strong Bullish (LTP > 40 > 90)"
        group = "Trail Stop-Loss / Phased Exit" if is_exit else "Add Confidently"
        
    return trend, group

def main():
    parser = argparse.ArgumentParser(description="Generate momentum rotation report based on anchor relativeness")
    parser.add_argument("--user", default="ZV3899", help="User ID")
    args = parser.parse_args()
    user_id = (args.user or "ZV3899").strip().upper()

    holdings_base = Path(f"portfolio/{user_id}/holdings")
    latest_date = get_latest_date_folder(holdings_base)
    if not latest_date:
        print("Error: No holdings data found.")
        return

    holdings_file = holdings_base / "holdings.json"
    dma_cache_file = Path(f"portfolio/.cache/dma_{latest_date}.json")

    with open(holdings_file, "r") as f:
        holdings = [h for h in json.load(f) if h.get("quantity", 0) > 0]

    rel_path = Path(f"portfolio/{user_id}/anchor_relativeness.json")
    using_fallback = False
    if rel_path.exists():
        with open(rel_path, "r") as f:
            rel_data = json.load(f)
    else:
        print(f"Notice: {rel_path} not found. Falling back to portfolio allocation for ordering.")
        using_fallback = True
        portfolio_items = []
        for h in holdings:
            sym = h.get("symbol", "")
            if not sym: continue
            qty = h.get("quantity", 0)
            ltp = h.get("last_price") or h.get("lastPrice") or 0
            portfolio_items.append({
                "symbol": sym,
                "current_value": qty * ltp
            })
            
        portfolio_items.sort(key=lambda x: x["current_value"], reverse=True)
        exits = portfolio_items[:30]
        
        portfolio_items.sort(key=lambda x: x["current_value"])
        entries = portfolio_items[:30]
        
        rel_data = {
            "exits": exits,
            "entries": entries
        }

    if not dma_cache_file.exists():
        # Try to find the latest available cache
        cache_dir = Path("portfolio/.cache")
        caches = [f for f in cache_dir.glob("dma_*.json")]
        if caches:
            dma_cache_file = max(caches, key=lambda x: x.name)
        else:
            print("Error: No DMA cache found.")
            return

    with open(dma_cache_file, "r") as f:
        dma_cache = json.load(f)

    def get_data(sym):
        clean_sym = sym.replace("-SM", "").replace("ˢᵐᵉ", "").strip()
        ltp = None
        for h in holdings:
            s = h.get("symbol", "")
            if s.replace("-SM", "") == clean_sym or s == sym:
                ltp = h.get("last_price") or h.get("lastPrice")
                break
        
        dma_info = dma_cache.get(sym) or dma_cache.get(clean_sym)
        if not dma_info or ltp is None:
            return None
            
        dma40 = dma_info.get("dma40")
        dma90 = dma_info.get("dma99") or dma_info.get("dma90")
        
        return {
            "LTP": float(ltp),
            "DMA40": float(dma40) if dma40 else None,
            "DMA90": float(dma90) if dma90 else None
        }

    report_lines = []
    if using_fallback:
        report_lines.append(f"# Portfolio Momentum Rotation — {latest_date}")
    else:
        report_lines.append(f"# Anchor Momentum Rotation — {latest_date}")
    report_lines.append(f"*User: {user_id}*")
    report_lines.append("")
    if using_fallback:
        report_lines.append("Combines Portfolio Allocation (Highest vs Lowest Weight) with Momentum Trends (LTP vs 40-DMA vs 90-DMA) to optimize entry and exit points.")
    else:
        report_lines.append("Combines Simple Relativeness (My% vs Anchor Cr) with Momentum Trends (LTP vs 40-DMA vs 90-DMA) to optimize entry and exit points.")
    report_lines.append("")

    # EXITS
    if using_fallback:
        report_lines.append("## 🔴 EXITS / REDUCE (Highest Portfolio Allocation)")
    else:
        report_lines.append("## 🔴 EXITS / REDUCE (Highest Disproportionate Allocation)")
    
    exit_groups = {
        "Exit Immediately": [],
        "Trail Stop-Loss / Phased Exit": [],
        "Unknown": []
    }
    
    for item in rel_data.get("exits", []):
        sym = item["symbol"]
        data = get_data(sym)
        if data and data["DMA40"]:
            trend, group = categorize_trend(data["LTP"], data["DMA40"], data["DMA90"], is_exit=True)
            d90_str = f"{data['DMA90']:.1f}" if data['DMA90'] else "N/A"
            line = f"- **{sym}** | LTP: ₹{data['LTP']:.1f} | 40-DMA: ₹{data['DMA40']:.1f} | 90-DMA: ₹{d90_str} | Trend: {trend}"
            exit_groups[group].append(line)
        else:
            exit_groups["Unknown"].append(f"- **{sym}** | No valid DMA/LTP data")

    for grp in ["Exit Immediately", "Trail Stop-Loss / Phased Exit", "Unknown"]:
        if exit_groups[grp]:
            report_lines.append(f"### {grp}")
            report_lines.extend(exit_groups[grp])
            report_lines.append("")

    # ENTRIES
    if using_fallback:
        report_lines.append("## 🟢 ENTRIES / INCREASE (Lowest Portfolio Allocation)")
    else:
        report_lines.append("## 🟢 ENTRIES / INCREASE (Lowest Allocation vs Anchor)")
    
    entry_groups = {
        "WAIT / Defer Entry": [],
        "Early Accumulation": [],
        "Add Confidently": [],
        "Unknown": []
    }
    
    for item in rel_data.get("entries", []):
        sym = item["symbol"]
        data = get_data(sym)
        if data and data["DMA40"]:
            trend, group = categorize_trend(data["LTP"], data["DMA40"], data["DMA90"], is_exit=False)
            d90_str = f"{data['DMA90']:.1f}" if data['DMA90'] else "N/A"
            line = f"- **{sym}** | LTP: ₹{data['LTP']:.1f} | 40-DMA: ₹{data['DMA40']:.1f} | 90-DMA: ₹{d90_str} | Trend: {trend}"
            entry_groups[group].append(line)
        else:
            entry_groups["Unknown"].append(f"- **{sym}** | No valid DMA/LTP data")

    for grp in ["WAIT / Defer Entry", "Early Accumulation", "Add Confidently", "Unknown"]:
        if entry_groups[grp]:
            report_lines.append(f"### {grp}")
            report_lines.extend(entry_groups[grp])
            report_lines.append("")

    report_md = "\n".join(report_lines)
    out_file = Path(f"portfolio/{user_id}/momentum_rotation.md")
    
    with open(out_file, "w") as f:
        f.write(report_md)
        
    print(f"============================================================")
    if using_fallback:
        print(f"  PORTFOLIO MOMENTUM ROTATION  —  {user_id}  ·  {latest_date}")
    else:
        print(f"  ANCHOR MOMENTUM ROTATION  —  {user_id}  ·  {latest_date}")
    print(f"============================================================")
    if using_fallback:
        print(f"✓ Parsed allocations from current portfolio (highest to lowest)")
    else:
        print(f"✓ Parsed relativeness limits from anchor_relativeness.json")
    print(f"✓ Applied DMA trends (LTP, 40-DMA, 90-DMA)")
    print(f"✓ Report saved → {out_file}")

if __name__ == "__main__":
    main()
