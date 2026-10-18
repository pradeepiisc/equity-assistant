"""
Read the research the existing workflows already produced for a company
(data/companies/<folder>/...) and shape it for the dashboard. Nothing here calls an LLM.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .config import COMPANIES_DIR, ROOT
from .util import to_float

VERDICTS = {"delivered": "delivered", "exceeded": "exceeded", "missed": "missed", "pending": "pending",
            "partially_delivered": "partly", "largely_delivered": "delivered", "on_track": "pending"}
ORDER_RX = re.compile(r"order\s*(book|backlog|inflow|intake|pipeline)|book[- ]to[- ]bill|L1|bid pipeline", re.I)


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(errors="replace"))
        return data if isinstance(data, dict) else None
    except ValueError:
        return None


def _mtime(path: Path) -> str | None:
    return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat() if path.exists() else None


def quarterly_financials(folder: str) -> dict:
    """Screener 'Quarterly Results' table → {quarters, rows{label: [values]}} (oldest first)."""
    files = sorted((COMPANIES_DIR / folder / "financials").glob("*_financials_*.txt"))
    if not files:
        return {}
    text = files[-1].read_text(errors="replace")
    part = text.split("## Quarterly Results", 1)
    if len(part) < 2:
        return {}
    lines = [l for l in part[1].split("---", 1)[0].splitlines() if "|" in l]
    if not lines:
        return {}
    quarters = [c.strip() for c in lines[0].split("|") if c.strip()]
    rows: dict[str, list] = {}
    for line in lines[1:]:
        cells = [c.strip() for c in line.split("|")]
        label = cells[0].rstrip("+ ").strip()
        if label in ("Sales", "Revenue", "Operating Profit", "Financing Profit", "OPM %", "Financing Margin %",
                     "Net Profit", "EPS in Rs", "Profit before tax"):
            vals = [to_float(c) for c in cells[1:1 + len(quarters)]]
            rows[label] = vals + [None] * (len(quarters) - len(vals))
    m = re.search(r"_financials_(\d{8})", files[-1].name)
    return {"quarters": quarters, "rows": rows, "file": files[-1].name,
            "fetched": f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else None}


def _insights(folder: str) -> dict | None:
    d = _load(COMPANIES_DIR / folder / "insights" / "insights_values.json")
    if not d or not d.get("metrics"):
        return None
    quarters = d.get("quarters") or []
    metrics = {}
    for name, vals in d["metrics"].items():
        if not isinstance(vals, dict) or re.fullmatch(r"[x.\s]+", name or "", re.I):
            continue
        clean = {q: v for q, v in vals.items() if v not in (None, "", "—", "-")}
        if clean:
            metrics[name] = clean
    return {"quarters": quarters, "metrics": metrics} if metrics else None


def _order_book(master: dict | None, ta: dict | None, insights: dict | None, val: dict | None) -> list[dict]:
    out = []
    for k in (master or {}).get("sector_kpi_dashboard") or []:
        if isinstance(k, dict) and ORDER_RX.search(str(k.get("metric", ""))):
            out.append({"source": "Master report", "metric": k.get("metric"), "value": k.get("value"),
                        "trend": k.get("trend"), "comment": k.get("comment")})
    for k in (ta or {}).get("sector_specific_kpis") or []:
        if isinstance(k, dict) and ORDER_RX.search(str(k.get("metric", ""))):
            out.append({"source": "Concall analysis", "metric": k.get("metric"), "value": k.get("latest_value"),
                        "previous": k.get("previous_value"), "trend": k.get("trend"),
                        "comment": k.get("management_comment"), "quarter": k.get("quarter_source")})
    kp = (val or {}).get("sector_kpis") or {}
    if isinstance(kp, dict):
        for key in ("order_book_cr", "order_book_to_revenue_x", "order_intake_trend"):
            if kp.get(key) not in (None, "", "null"):
                out.append({"source": "Valuation", "metric": key.replace("_", " "), "value": kp.get(key)})
    if insights:
        for name, vals in insights["metrics"].items():
            if ORDER_RX.search(name):
                out.append({"source": "Quarterly insights", "metric": name, "series": vals})
    return out


def summary(folder: str | None) -> dict:
    if not folder:
        return {"available": False}
    base = COMPANIES_DIR / folder
    rep = base / "reports"
    master = _load(rep / "master_report.json")
    val = _load(rep / "valuation_agent.json")
    thesis = _load(rep / "thesis_review.json")
    ta = _load(rep / "transcript_analysis.json")
    insights = _insights(folder)

    concall = None
    if ta:
        gvd = []
        for g in ta.get("guidance_vs_delivery") or []:
            if isinstance(g, dict):
                v = str(g.get("verdict", "")).lower()
                gvd.append({**g, "verdict": VERDICTS.get(v, v or "pending")})
        concall = {
            "as_of": _mtime(rep / "transcript_analysis.json"),
            "executive_summary": ta.get("executive_summary"),
            "tone": ta.get("management_tone"),
            "guidance_vs_delivery": gvd,
            "guidance_evolution": (ta.get("guidance_evolution") or {}),
            "forward_estimates": ta.get("forward_estimates") or {},
            "new_facilities": ta.get("new_facilities_and_products") or [],
            "kpis": ta.get("sector_specific_kpis") or [],
            "positive_signals": ta.get("positive_signals") or [],
            "red_flags": ta.get("red_flags") or [],
            "risks": ta.get("key_risks_flagged_by_management") or [],
            "signal": ta.get("overall_investment_signal"),
            "transcripts_used": ta.get("transcripts_used") or [],
            "margin_commentary": ta.get("margin_commentary"),
            "revenue_narrative": ta.get("revenue_growth_narrative"),
        }

    docs = []
    for sub, kind in (("transcripts", "Transcript"), ("ppt", "Presentation")):
        for f in sorted((base / sub).glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True):
            m = re.search(r"_(?:Transcript|PPT)_([A-Za-z]{3}\d{4})", f.name)
            docs.append({"kind": kind, "name": f.name, "period": m.group(1) if m else "", "path": str(f.relative_to(ROOT))})
    md_reports = []
    for name in ("master_report", "thesis_review", "transcript_analysis", "valuation_summary", "valuation_agent",
                 "financial_snapshot", "shareholding_analysis", "news_sentiment", "peer_comparison",
                 "valuepickr_analysis"):
        p = rep / f"{name}.md"
        if p.exists():
            md_reports.append({"name": name, "path": str(p.relative_to(ROOT)), "date": _mtime(p)})

    return {
        "available": True,
        "folder": folder,
        "master": None if not master else {
            "as_of": _mtime(rep / "master_report.json"),
            "rating": master.get("overall_rating"),
            "action": master.get("action_recommendation"),
            "fair_value": master.get("fair_value_summary"),
            "thesis": master.get("investment_thesis"),
            "catalysts": master.get("key_catalysts") or [],
            "risks": master.get("key_risks") or [],
            "decision_framework": master.get("decision_framework") or {},
            "monitoring_triggers": master.get("monitoring_triggers") or [],
            "kpis": master.get("sector_kpi_dashboard") or [],
            "forward_revenue_path": master.get("forward_revenue_path") or {},
        },
        "valuation": None if not val else {
            "as_of": (val.get("generated_at") or "")[:10] or _mtime(rep / "valuation_agent.json"),
            "fair_low": to_float(val.get("fair_value_low")),
            "fair_high": to_float(val.get("fair_value_high")),
            "price_then": to_float(val.get("current_price")),
            "vs_fair": val.get("current_vs_fair"),
            "upside_pct": to_float(val.get("upside_pct")),
            "entry_signal": val.get("entry_signal"),
            "method": val.get("valuation_method"),
            "triggers": val.get("entry_exit_triggers"),
            "scenarios": val.get("scenarios"),
        },
        "thesis": None if not thesis else {
            "as_of": _mtime(rep / "thesis_review.json"),
            "status": thesis.get("thesis_status"),
            "rationale": thesis.get("thesis_status_rationale"),
            "conviction": thesis.get("updated_conviction"),
            "summary": thesis.get("one_line_summary"),
            "outlook": thesis.get("forward_outlook"),
            "consistency": thesis.get("management_consistency"),
        },
        "concall": concall,
        "insights": insights,
        "order_book": _order_book(master, ta, insights, val),
        "financials": quarterly_financials(folder),
        "documents": docs[:16],
        "reports": md_reports,
    }


def ranking() -> dict[str, dict]:
    """data/valuation/master_ranking.json → {symbol: {rating, composite, ...}}"""
    p = ROOT / "data" / "valuation" / "master_ranking.json"
    if not p.exists():
        return {}
    try:
        rows = json.loads(p.read_text())
    except ValueError:
        return {}
    out = {}
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and r.get("symbol"):
            out[str(r["symbol"]).upper()] = {"rating": r.get("rating"),
                                             "composite": (r.get("scores") or {}).get("composite")}
    return out
