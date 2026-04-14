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


# ── frontend ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def serve_ui():
    html_path = UI_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>index.html not found in ui/</h1>")
