"""
Workflow: Consolidated Exit Strategy Dashboard
==============================================
Reads all generated master_report.json files across all companies
and consolidates their Decision Frameworks (Invest/Stay/Trim/Exit)
into a single actionable Markdown report.

Usage:
    python -m workflows.consolidated_exit_strategy
"""

import json
from pathlib import Path
from datetime import date

PROJECT_ROOT = Path(__file__).parent.parent
COMPANIES_DIR = PROJECT_ROOT / "data" / "companies"
OUT_DIR = PROJECT_ROOT / "portfolio"

def run() -> None:
    print(f"\n{'='*60}")
    print("  GENERATING CONSOLIDATED EXIT STRATEGY DASHBOARD")
    print(f"{'='*60}")

    all_reports = []
    
    # Collect all master_report.json files
    if COMPANIES_DIR.exists():
        for comp_dir in sorted(COMPANIES_DIR.iterdir()):
            if not comp_dir.is_dir() or comp_dir.name.startswith("."):
                continue
            
            report_path = comp_dir / "reports" / "master_report.json"
            meta_path = comp_dir / "meta.json"
            
            if report_path.exists():
                try:
                    data = json.loads(report_path.read_text(encoding="utf-8"))
                    name = comp_dir.name
                    if meta_path.exists():
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                        name = meta.get("name", name)
                    
                    all_reports.append({
                        "symbol": comp_dir.name,
                        "name": name,
                        "data": data
                    })
                except Exception as e:
                    print(f"Error reading {report_path}: {e}")

    if not all_reports:
        print("No master_report.json files found. Run batch_company_analysis first.")
        return

    # Sort by rating (Buy/Strong Buy first) or Symbol
    def rating_score(rating):
        r = str(rating).lower()
        if "strong buy" in r: return 0
        if "buy" in r: return 1
        if "accumulate" in r: return 2
        if "hold" in r: return 3
        if "reduce" in r: return 4
        if "sell" in r: return 5
        return 6
        
    all_reports.sort(key=lambda x: (rating_score(x["data"].get("overall_rating", "")), x["symbol"]))

    lines = [
        f"# Consolidated Exit Strategy & Decision Framework",
        f"**Date:** {date.today().isoformat()}  |  **Companies Analysed:** {len(all_reports)}",
        "",
        "This report consolidates the deep investment assessments, highlighting explicit entry and exit triggers.",
        "",
        "## 📊 Executive Summary",
        "| Symbol | Rating | Fair Value | Momentum | Action |",
        "|---|---|---|---|---|",
    ]

    # Summary Table
    for r in all_reports:
        sym = r["symbol"]
        rating = r["data"].get("overall_rating", "N/A")
        fv = r["data"].get("fair_value_summary", "N/A")
        if fv: fv = fv.replace("\n", " ")[:60]
        
        tech = r["data"].get("technical_position", {})
        momentum = "N/A"
        if isinstance(tech, dict):
            momentum = tech.get("momentum_assessment", "N/A")
        
        if momentum and len(momentum) > 40:
            momentum = momentum[:37] + "..."
            
        action = r["data"].get("action_recommendation", "N/A")
        if action: action = action.replace("\n", " ")[:60]
        
        lines.append(f"| **{sym}** | {rating} | {fv} | {momentum} | {action} |")

    lines.append("")
    lines.append("## 🎯 Detailed Decision Frameworks")
    lines.append("*(Only showing companies with newly generated Decision Frameworks)*")
    lines.append("")

    # Detailed Triggers Sections
    for r in all_reports:
        df = r["data"].get("decision_framework")
        if not df or not isinstance(df, dict):
            continue  # Skip older reports without the new framework
        
        sym = r["symbol"]
        lines.append(f"### {sym} — {r['name']}")
        lines.append(f"**Rating:** {r['data'].get('overall_rating', 'N/A')}")
        
        if invest := df.get("invest_more_triggers"):
            lines.append("**🟢 Invest More (Add):**")
            for t in invest:
                lines.append(f"- {t}")
            lines.append("")
            
        if partial := df.get("partial_exit_triggers"):
            lines.append("**🟠 Partial Exit (Trim 25-50%):**")
            for t in partial:
                lines.append(f"- {t}")
            lines.append("")
            
        if full := df.get("complete_exit_triggers"):
            lines.append("**🔴 Complete Exit (Sell All):**")
            for t in full:
                lines.append(f"- {t}")
            lines.append("")
            
        lines.append("---")

    out_file = OUT_DIR / "consolidated_exit_strategy.md"
    OUT_DIR.mkdir(exist_ok=True)
    out_file.write_text("\n".join(lines), encoding="utf-8")
    
    print(f"\n✓ Generated {len(all_reports)} reports overview.")
    print(f"✓ Saved consolidated report to: {out_file}")

if __name__ == "__main__":
    run()
