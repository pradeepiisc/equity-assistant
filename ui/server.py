"""
Equity Assistant UI — FastAPI backend
Run: uvicorn ui.server:app --reload --port 7777
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator

import yaml
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# ── paths ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent.resolve()
UI_DIR = Path(__file__).parent.resolve()
PYTHON = sys.executable          # same env that launched the server
DATA_DIR = ROOT / "data" / "companies"
PORTFOLIO_DIR = ROOT / "portfolio"

# ── in-memory job store ─────────────────────────────────────────────────────
jobs: dict[str, dict[str, Any]] = {}   # job_id → {status, lines, proc}

# ── app ────────────────────────────────────────────────────────────────────
app = FastAPI(title="Equity Assistant UI")


# ── helpers ────────────────────────────────────────────────────────────────

def _load_yaml_companies() -> list[dict]:
    """Return merged list of {symbol, name, sector, source} dicts."""
    companies: list[dict] = []
    seen: set[str] = set()

    watchlist_path = ROOT / "watchlist.yaml"
    if watchlist_path.exists():
        data = yaml.safe_load(watchlist_path.read_text()) or {}
        for s in data.get("stocks", []):
            sym = str(s.get("symbol", "")).strip()
            if sym and sym not in seen:
                seen.add(sym)
                companies.append({
                    "symbol": sym,
                    "name": s.get("name", sym),
                    "sector": s.get("sector", ""),
                    "source": "watchlist",
                })

    portfolio_path = ROOT / "portfolio_companies.yaml"
    if portfolio_path.exists():
        data = yaml.safe_load(portfolio_path.read_text()) or {}
        for s in data.get("stocks", []):
            sym = str(s.get("symbol", "")).strip()
            if sym and sym not in seen:
                seen.add(sym)
                companies.append({
                    "symbol": sym,
                    "name": s.get("name", sym),
                    "sector": s.get("sector", ""),
                    "source": "portfolio",
                })

    return sorted(companies, key=lambda c: c["name"])


def _list_reports(symbol: str) -> list[dict]:
    """Return list of report file metadata for a company."""
    reports_dir = DATA_DIR / symbol / "reports"
    if not reports_dir.exists():
        return []
    files = []
    for f in sorted(reports_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.suffix in (".md", ".txt", ".json"):
            files.append({
                "name": f.name,
                "path": str(f),
                "size": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                "type": f.suffix.lstrip("."),
            })
    return files


def _list_portfolio_reports() -> list[dict]:
    """Return recent portfolio-level report files."""
    files = []
    if not PORTFOLIO_DIR.exists():
        return files
    for date_dir in sorted(PORTFOLIO_DIR.rglob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
        files.append({
            "name": date_dir.name,
            "path": str(date_dir),
            "size": date_dir.stat().st_size,
            "modified": datetime.fromtimestamp(date_dir.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "type": "md",
        })
    return files[:30]   # cap at 30


async def _stream_process(job_id: str, cmd: list[str]) -> None:
    """Run cmd, stream lines into jobs[job_id]['lines'], set final status."""
    jobs[job_id]["status"] = "running"
    jobs[job_id]["started_at"] = datetime.now().isoformat()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(ROOT),
        )
        jobs[job_id]["pid"] = proc.pid
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            jobs[job_id]["lines"].append(line)
        await proc.wait()
        jobs[job_id]["returncode"] = proc.returncode
        jobs[job_id]["status"] = "done" if proc.returncode == 0 else "error"
    except Exception as exc:
        jobs[job_id]["lines"].append(f"[server error] {exc}")
        jobs[job_id]["status"] = "error"
    jobs[job_id]["finished_at"] = datetime.now().isoformat()


# ── API routes ─────────────────────────────────────────────────────────────

@app.get("/api/companies")
def get_companies():
    return _load_yaml_companies()


@app.get("/api/reports/{symbol}")
def get_company_reports(symbol: str):
    return _list_reports(symbol)


@app.get("/api/portfolio-reports")
def get_portfolio_reports():
    return _list_portfolio_reports()


@app.get("/api/report-content")
def get_report_content(path: str = Query(...)):
    p = Path(path)
    if not p.exists():
        return {"error": "File not found"}
    if not str(p).startswith(str(ROOT)):
        return {"error": "Access denied"}
    try:
        return {"content": p.read_text(errors="replace"), "name": p.name}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/run")
async def run_workflow(payload: dict):
    """
    payload: { workflow: str, args: list[str] }
    Returns: { job_id: str }
    """
    workflow = payload.get("workflow", "")
    args: list[str] = payload.get("args", [])

    # Map workflow → command
    cmd_map: dict[str, list[str]] = {
        # Daily
        "portfolio_daily_report":    [PYTHON, "-m", "workflows.portfolio_daily_report"],
        "portfolio_daily_report_nodma": [PYTHON, "-m", "workflows.portfolio_daily_report", "--no-dma"],
        "watchlist_alerts":          [PYTHON, "-m", "workflows.watchlist_alerts"],
        "news_digest":               [PYTHON, "-m", "workflows.news_digest"],
        "conviction_builder_losers": [PYTHON, "-m", "workflows.conviction_builder", "--top-losers", "5"],
        # Weekly
        "investor_activity":         [PYTHON, "-m", "workflows.investor_activity"],
        "sector_analysis":           [PYTHON, "-m", "workflows.sector_analysis"],
        "watchlist_gap_analysis":    [PYTHON, "-m", "workflows.watchlist_gap_analysis"],
        # Per company
        "data_fetch":                [PYTHON, "-m", "workflows.data_fetch"],
        "full_company_analysis":     [PYTHON, "-m", "workflows.full_company_analysis"],
        "conviction_builder":        [PYTHON, "-m", "workflows.conviction_builder", "--symbols"],
        "valuation":                 [PYTHON, "-m", "skills.valuation_agent"],
        "valuepickr":                [PYTHON, "-m", "skills.valuepickr_fetcher"],
        # Quarterly
        "batch_company_analysis":    [PYTHON, "-m", "workflows.batch_company_analysis"],
        "batch_skip_existing":       [PYTHON, "-m", "workflows.batch_company_analysis", "--skip-existing"],
        "growth_ranking":            [PYTHON, "-m", "workflows.growth_ranking"],
        "extract_insights_values":   [PYTHON, "-m", "workflows.extract_insights_values"],
        "top_holdings_valuation":    [PYTHON, "-m", "workflows.top_holdings_valuation"],
        # Onboard
        "onboard_company":           [PYTHON, "-m", "workflows.onboard_company"],
    }

    base_cmd = cmd_map.get(workflow)
    if not base_cmd:
        return {"error": f"Unknown workflow: {workflow}"}

    cmd = base_cmd + args

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "id": job_id,
        "workflow": workflow,
        "args": args,
        "status": "queued",
        "lines": [f"$ {' '.join(cmd)}"],
        "started_at": None,
        "finished_at": None,
        "returncode": None,
        "pid": None,
    }

    asyncio.create_task(_stream_process(job_id, cmd))
    return {"job_id": job_id}


@app.get("/api/stream/{job_id}")
async def stream_job(job_id: str):
    """SSE endpoint — streams new lines as they arrive."""
    if job_id not in jobs:
        return {"error": "Job not found"}

    async def event_generator() -> AsyncGenerator[str, None]:
        sent = 0
        while True:
            lines = jobs[job_id]["lines"]
            while sent < len(lines):
                payload = json.dumps({"line": lines[sent], "status": jobs[job_id]["status"]})
                yield f"data: {payload}\n\n"
                sent += 1
            if jobs[job_id]["status"] in ("done", "error"):
                # Drain any remaining lines then close
                lines = jobs[job_id]["lines"]
                while sent < len(lines):
                    payload = json.dumps({"line": lines[sent], "status": jobs[job_id]["status"]})
                    yield f"data: {payload}\n\n"
                    sent += 1
                # Final status event
                yield f"data: {json.dumps({'line': None, 'status': jobs[job_id]['status'], 'done': True})}\n\n"
                break
            await asyncio.sleep(0.15)

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/job/{job_id}")
def job_status(job_id: str):
    if job_id not in jobs:
        return {"error": "Not found"}
    j = jobs[job_id]
    return {k: v for k, v in j.items() if k != "lines"}


@app.get("/api/jobs")
def list_jobs():
    return [
        {k: v for k, v in j.items() if k != "lines"}
        for j in sorted(jobs.values(), key=lambda x: x.get("started_at") or "", reverse=True)
    ]


# ── Dashboard API ─────────────────────────────────────────────────────────

def _load_holdings_for_user(user_id: str) -> list[dict]:
    """Load holdings.json for a user."""
    hf = PORTFOLIO_DIR / user_id / "holdings" / "holdings.json"
    if not hf.exists():
        return []
    return json.loads(hf.read_text())


def _load_dma_cache() -> dict:
    """Load latest DMA cache file."""
    cache_dir = PORTFOLIO_DIR / ".cache"
    if not cache_dir.exists():
        return {}
    dma_files = sorted([f for f in cache_dir.iterdir() if f.name.startswith("dma_")], reverse=True)
    if not dma_files:
        return {}
    return json.loads(dma_files[0].read_text())


def _parse_financials(symbol: str) -> dict:
    """Parse the latest financials text file into structured data."""
    fin_dir = DATA_DIR / symbol / "financials"
    if not fin_dir.exists():
        return {}
    files = sorted([f for f in fin_dir.iterdir() if f.suffix == ".txt"], reverse=True)
    if not files:
        return {}
    content = files[0].read_text(errors="replace")
    result = {"raw_file": files[0].name}

    # Parse key ratios
    ratios = {}
    for line in content.split("\n"):
        line = line.strip()
        for key in ["Stock P/E", "Book Value", "ROCE", "ROE", "Debt to equity",
                    "Current Price", "Market Cap", "Dividend Yield", "Current ratio",
                    "EVEBITDA", "Int Coverage", "PEG Ratio", "Return on assets",
                    "Qtr Sales Var", "Sales growth 3Years", "Profit Var 3Yrs",
                    "Pledged percentage"]:
            if line.startswith(key):
                val = line[len(key):].strip().replace("₹", "").replace("Cr.", "").replace("%", "").strip()
                ratios[key.lower().replace(" ", "_")] = val
    result["ratios"] = ratios

    # Parse quarterly results
    quarters = []
    in_quarterly = False
    headers = []
    for line in content.split("\n"):
        if "Quarterly Results" in line:
            in_quarterly = True
            continue
        if in_quarterly and "|" in line:
            parts = [p.strip() for p in line.split("|")]
            if not headers:
                headers = [p for p in parts if p]  # filter empty
                continue
            label = parts[0].strip()
            vals = parts[1:]  # values after label
            if label.startswith("Sales"):
                quarters = [{"quarter": h, "sales": vals[i] if i < len(vals) else ""} for i, h in enumerate(headers) if h]
            elif label.startswith("Operating Profit") and not label.startswith("OPM"):
                for i, h in enumerate(headers):
                    if h and i < len(vals):
                        for q in quarters:
                            if q["quarter"] == h:
                                q["op_profit"] = vals[i]
            elif label.startswith("OPM"):
                for i, h in enumerate(headers):
                    if h and i < len(vals):
                        for q in quarters:
                            if q["quarter"] == h:
                                q["opm"] = vals[i]
            elif label.startswith("Net Profit"):
                for i, h in enumerate(headers):
                    if h and i < len(vals):
                        for q in quarters:
                            if q["quarter"] == h:
                                q["net_profit"] = vals[i]
            elif label.startswith("EPS"):
                for i, h in enumerate(headers):
                    if h and i < len(vals):
                        for q in quarters:
                            if q["quarter"] == h:
                                q["eps"] = vals[i]
        if in_quarterly and line.startswith("---"):
            if quarters:
                break

    # Parse cash flow
    in_cf = False
    cf_headers = []
    cf_data = {}
    for line in content.split("\n"):
        if "Cash Flows" in line or "Cash Flow" in line:
            in_cf = True
            continue
        if in_cf and "|" in line:
            parts = [p.strip() for p in line.split("|")]
            if not cf_headers:
                cf_headers = [p for p in parts if p]
                continue
            label = parts[0].strip() if parts else ""
            vals = parts[1:]
            if "operating" in label.lower() and "cash" in label.lower():
                cf_data["operating_cf"] = {cf_headers[i]: vals[i] for i in range(min(len(cf_headers), len(vals))) if i < len(cf_headers)}
            elif "investing" in label.lower():
                cf_data["investing_cf"] = {cf_headers[i]: vals[i] for i in range(min(len(cf_headers), len(vals))) if i < len(cf_headers)}
            elif "financing" in label.lower():
                cf_data["financing_cf"] = {cf_headers[i]: vals[i] for i in range(min(len(cf_headers), len(vals))) if i < len(cf_headers)}
            elif "net cash" in label.lower() or ("net" in label.lower() and "flow" in label.lower()):
                cf_data["net_cf"] = {cf_headers[i]: vals[i] for i in range(min(len(cf_headers), len(vals))) if i < len(cf_headers)}
        if in_cf and line.startswith("---") and cf_data:
            break

    result["quarterly"] = quarters[-6:] if quarters else []  # last 6 quarters
    result["cash_flow"] = cf_data
    return result


def _compute_flags(fin_snapshot: dict, financials: dict, valuation: dict) -> list[dict]:
    """Compute red/orange/green flags from financial data."""
    flags = []

    # Red flags from financial snapshot
    for rf in fin_snapshot.get("red_flags", []):
        flags.append({"level": "red", "category": "Financial", "message": rf})

    # Cash flow quality
    cf_quality = fin_snapshot.get("cash_flow_quality", {})
    cf_trend = cf_quality.get("operating_cf_trend", "")
    fcf = cf_quality.get("fcf_generation", "")
    if cf_trend in ("volatile", "declining") or fcf in ("weak", "negative"):
        flags.append({"level": "red", "category": "Cash Flow",
                      "message": f"Operating CF: {cf_trend}, FCF: {fcf}"})
    elif cf_trend == "stable" and fcf == "moderate":
        flags.append({"level": "orange", "category": "Cash Flow",
                      "message": f"Operating CF: {cf_trend}, FCF: {fcf}"})
    elif cf_trend in ("growing", "strong") or fcf in ("strong", "healthy"):
        flags.append({"level": "green", "category": "Cash Flow",
                      "message": f"Operating CF: {cf_trend}, FCF: {fcf}"})

    # Revenue trend
    rev = fin_snapshot.get("revenue_trend", {})
    rev_dir = rev.get("direction", "")
    if rev_dir in ("growing", "accelerating"):
        flags.append({"level": "green", "category": "Revenue",
                      "message": f"Revenue {rev_dir} — {rev.get('cagr_estimate', '')}"})
    elif rev_dir in ("flat", "slowing"):
        flags.append({"level": "orange", "category": "Revenue",
                      "message": f"Revenue {rev_dir}"})
    elif rev_dir in ("declining",):
        flags.append({"level": "red", "category": "Revenue", "message": "Revenue declining"})

    # Margin trend
    margin = fin_snapshot.get("margin_trend", {})
    ebitda_dir = margin.get("ebitda_margin_direction", "")
    if ebitda_dir in ("expanding",):
        flags.append({"level": "green", "category": "Margins",
                      "message": f"EBITDA margin expanding → {margin.get('latest_ebitda_margin', '')}"})
    elif ebitda_dir in ("contracting", "declining"):
        flags.append({"level": "red", "category": "Margins",
                      "message": f"EBITDA margin contracting"})

    # Debt
    debt = fin_snapshot.get("debt_situation", {})
    debt_assess = debt.get("assessment", "")
    if debt_assess in ("comfortable", "negligible", "debt-free"):
        flags.append({"level": "green", "category": "Debt", "message": f"Debt: {debt_assess}"})
    elif debt_assess in ("moderate",):
        flags.append({"level": "orange", "category": "Debt", "message": f"Debt: {debt_assess}"})
    elif debt_assess in ("high", "stressed", "risky"):
        flags.append({"level": "red", "category": "Debt", "message": f"Debt: {debt_assess}"})

    # Valuation signal
    if valuation:
        signal = valuation.get("entry_signal", "")
        vs_fair = valuation.get("current_vs_fair", "")
        if "undervalued" in vs_fair.lower() or "accumulate" in signal.lower() or "buy" in signal.lower():
            flags.append({"level": "green", "category": "Valuation", "message": f"{vs_fair} — {signal}"})
        elif "overvalued" in vs_fair.lower() or "exit" in signal.lower() or "sell" in signal.lower():
            flags.append({"level": "red", "category": "Valuation", "message": f"{vs_fair} — {signal}"})
        elif "fair" in vs_fair.lower() or "hold" in signal.lower():
            flags.append({"level": "orange", "category": "Valuation", "message": f"{vs_fair} — {signal}"})

    return flags


@app.get("/api/dashboard/holdings")
def get_dashboard_holdings(user: str = Query(None)):
    """Get holdings sorted by current_value desc. user=ZV3899|WN5759|combined"""
    PLATFORM_SUFFIXES = ("-SM", "-ST", "-BE")
    users = ["ZV3899", "WN5759"]
    if user and user != "combined":
        users = [user]

    # Merge holdings
    merged: dict[str, dict] = {}
    for uid in users:
        for h in _load_holdings_for_user(uid):
            sym = h.get("symbol", "")
            if not sym:
                continue
            if sym in merged:
                merged[sym]["quantity"] = merged[sym].get("quantity", 0) + h.get("quantity", 0)
                merged[sym]["current_value"] = merged[sym].get("current_value", 0) + h.get("current_value", 0)
                merged[sym]["invested_value"] = merged[sym].get("invested_value", 0) + h.get("invested_value", 0)
                merged[sym]["users"] = merged[sym].get("users", []) + [uid]
            else:
                merged[sym] = {**h, "users": [uid]}

    # Enrich with meta + resolve folder symbol
    dma_cache = _load_dma_cache()
    result = []
    for sym, h in merged.items():
        # Resolve folder
        folder_sym = sym
        if not (DATA_DIR / sym).exists():
            base = sym
            for sfx in PLATFORM_SUFFIXES:
                if sym.endswith(sfx):
                    base = sym[:-len(sfx)]
                    break
            for cand in [base] + [base + s for s in PLATFORM_SUFFIXES]:
                if (DATA_DIR / cand).exists():
                    folder_sym = cand
                    break

        # Meta
        meta = {}
        meta_path = DATA_DIR / folder_sym / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except Exception:
                pass

        # DMA
        dma = dma_cache.get(sym, dma_cache.get(folder_sym, {}))
        if not isinstance(dma, dict):
            dma = {}

        # Quick flag count from financial_snapshot
        fs_path = DATA_DIR / folder_sym / "reports" / "financial_snapshot.json"
        red_flag_count = 0
        if fs_path.exists():
            try:
                fs = json.loads(fs_path.read_text())
                red_flag_count = len(fs.get("red_flags", []))
            except Exception:
                pass

        has_reports = (DATA_DIR / folder_sym / "reports" / "master_report.md").exists()

        entry = {
            "symbol": sym,
            "folder_symbol": folder_sym,
            "name": meta.get("name", sym),
            "sector": meta.get("sector", ""),
            "quantity": h.get("quantity", 0),
            "avg_price": h.get("avg_price", 0),
            "last_price": h.get("last_price", 0),
            "current_value": h.get("current_value", 0),
            "invested_value": h.get("invested_value", 0),
            "pnl": h.get("pnl", 0),
            "pnl_pct": h.get("pnl_pct", 0),
            "users": h.get("users", []),
            "dma40": dma.get("dma40"),
            "dma100": dma.get("dma100"),
            "red_flags": red_flag_count,
            "has_reports": has_reports,
        }
        result.append(entry)

    result.sort(key=lambda x: x["current_value"], reverse=True)
    total = sum(x["current_value"] for x in result)
    for r in result:
        r["pct_of_portfolio"] = round(r["current_value"] / total * 100, 2) if total else 0
    return {"holdings": result, "total_value": total, "count": len(result)}


@app.get("/api/dashboard/company/{symbol}")
def get_company_dashboard(symbol: str):
    """Full company detail for dashboard."""
    PLATFORM_SUFFIXES = ("-SM", "-ST", "-BE")
    folder_sym = symbol
    if not (DATA_DIR / symbol).exists():
        base = symbol
        for sfx in PLATFORM_SUFFIXES:
            if symbol.endswith(sfx):
                base = symbol[:-len(sfx)]
                break
        for cand in [base] + [base + s for s in PLATFORM_SUFFIXES]:
            if (DATA_DIR / cand).exists():
                folder_sym = cand
                break

    company_dir = DATA_DIR / folder_sym
    if not company_dir.exists():
        return {"error": f"No data for {symbol}"}

    # Meta
    meta = {}
    if (company_dir / "meta.json").exists():
        meta = json.loads((company_dir / "meta.json").read_text())

    # Financial snapshot
    fin_snapshot = {}
    fs_path = company_dir / "reports" / "financial_snapshot.json"
    if fs_path.exists():
        fin_snapshot = json.loads(fs_path.read_text())

    # Valuation
    valuation = {}
    va_path = company_dir / "reports" / "valuation_agent.json"
    if va_path.exists():
        valuation = json.loads(va_path.read_text())

    # Master report
    master = {}
    mr_path = company_dir / "reports" / "master_report.json"
    if mr_path.exists():
        master = json.loads(mr_path.read_text())

    # Growth signals
    growth = {}
    gs_path = company_dir / "reports" / "growth_signals.json"
    if gs_path.exists():
        growth = json.loads(gs_path.read_text())

    # Parsed financials (quarterly, ratios, cash flow)
    financials = _parse_financials(folder_sym)

    # DMA
    dma_cache = _load_dma_cache()
    dma = dma_cache.get(symbol, dma_cache.get(folder_sym, {}))
    if not isinstance(dma, dict):
        dma = {}

    # Flags
    flags = _compute_flags(fin_snapshot, financials, valuation)

    # Reports list
    reports = _list_reports(folder_sym)

    # Master report markdown
    master_md = ""
    mr_md_path = company_dir / "reports" / "master_report.md"
    if mr_md_path.exists():
        master_md = mr_md_path.read_text(errors="replace")

    # Valuation summary markdown
    val_md = ""
    val_md_path = company_dir / "reports" / "valuation_summary.md"
    if val_md_path.exists():
        val_md = val_md_path.read_text(errors="replace")

    # Thesis review markdown
    thesis_md = ""
    th_path = company_dir / "reports" / "thesis_review.md"
    if th_path.exists():
        thesis_md = th_path.read_text(errors="replace")

    return {
        "symbol": symbol,
        "folder_symbol": folder_sym,
        "meta": meta,
        "financial_snapshot": fin_snapshot,
        "valuation": valuation,
        "master_report": master,
        "growth_signals": growth,
        "financials": financials,
        "dma": dma,
        "flags": flags,
        "reports": reports,
        "master_report_md": master_md,
        "valuation_summary_md": val_md,
        "thesis_review_md": thesis_md,
    }


# ── frontend ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def serve_ui():
    html_path = UI_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>index.html not found in ui/</h1>")


@app.get("/design/{num}", response_class=HTMLResponse)
def serve_design(num: int):
    html_path = UI_DIR / f"design{num}.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse(f"<h1>design{num}.html not found</h1>")
