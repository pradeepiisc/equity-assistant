"""
Skill: Growth Signals
======================
Analyses earnings call transcripts, investor PPTs, and Screener Insights
to produce a structured growth signal for a company.

Output:
  growth_bucket  : "high" | "medium" | "low"
  growth_score   : 1–10
  trajectory     : accelerating | steady | decelerating | declining | turnaround
  bottom_line_outlook: improving | stable | under_pressure | deteriorating
  + key drivers, risks, red flags, overall summary

Saves to: data/companies/{SYMBOL}/reports/growth_signals.{md,json}

Run standalone:
    python -m skills.growth_signals SYMBOL [--refresh]
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import traceback
from pathlib import Path

from llm.client import get_config
from llm.utils import load_prompt, call_llm, save_report
from skills.company_meta import get_company_name

SKILL_NAME = "growth_signals"
PROJECT_ROOT = Path(__file__).parent.parent
COMPANIES_DIR = PROJECT_ROOT / "data" / "companies"

MAX_CHARS_PER_FILE = 8_000
MAX_TRANSCRIPTS = 8  # default; overridden by config skills.max_transcripts_per_company
MAX_PPTS = 4
MAX_PDF_PAGES = 25


def _load_pdfs_capped(symbol: str, subfolder: str, max_files: int) -> list[str]:
    """Load PDFs from a company subfolder, capping at MAX_PDF_PAGES per file."""
    folder = COMPANIES_DIR / symbol.upper() / subfolder
    if not folder.exists():
        return []
    try:
        import pdfplumber
    except ImportError:
        return []
    results: list[str] = []
    pdf_files = sorted(folder.glob("*.pdf"))[-max_files:]
    txt_files = sorted(folder.glob("*.txt"))[-max_files:] if not pdf_files else []
    for fp in (pdf_files or txt_files):
        try:
            if fp.suffix.lower() == ".pdf":
                pages: list[str] = []
                old_err = sys.stderr
                sys.stderr = io.StringIO()
                try:
                    with pdfplumber.open(fp) as pdf:
                        for page in pdf.pages[:MAX_PDF_PAGES]:
                            t = page.extract_text()
                            if t:
                                pages.append(t)
                finally:
                    sys.stderr = old_err
                text = "\n".join(pages)
            else:
                text = fp.read_text(encoding="utf-8", errors="replace")
            if text.strip():
                results.append(f"--- FILE: {fp.name} ---\n{text}")
        except Exception:
            pass
    return results


def _load_screener_insights(symbol: str) -> str:
    ins_dir = COMPANIES_DIR / symbol.upper() / "insights"
    if not ins_dir.exists():
        return ""
    files = sorted(ins_dir.glob("*.txt"))
    if not files:
        return ""
    return files[-1].read_text(encoding="utf-8", errors="replace")


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... [truncated at {max_chars} chars]"


def _build_context(
    symbol: str,
    company_name: str,
    transcript_texts: list[str],
    ppt_texts: list[str],
    insights_text: str,
) -> tuple[str, list[str]]:
    parts: list[str] = []
    sources_used: list[str] = []

    if transcript_texts:
        parts.append("=== EARNINGS CALL TRANSCRIPTS ===")
        for i, t in enumerate(transcript_texts, 1):
            parts.append(f"--- Transcript {i} ---")
            parts.append(_truncate(t, MAX_CHARS_PER_FILE))
        sources_used.append("transcripts")

    if ppt_texts:
        parts.append("\n=== INVESTOR PRESENTATIONS (PPTs) ===")
        for i, p in enumerate(ppt_texts, 1):
            parts.append(f"--- Presentation {i} ---")
            parts.append(_truncate(p, MAX_CHARS_PER_FILE))
        sources_used.append("ppts")

    if insights_text and "Total insights:** 0" not in insights_text:
        parts.append("\n=== SCREENER INSIGHTS (BETA METRICS) ===")
        parts.append(insights_text[:3_000])
        sources_used.append("screener_insights")

    return "\n\n".join(parts), sources_used


def _build_markdown(symbol: str, company_name: str, data: dict) -> str:
    bucket = data.get("growth_bucket", "N/A").upper()
    score = data.get("growth_score", "N/A")
    trajectory = data.get("trajectory", "N/A")
    outlook = data.get("bottom_line_outlook", "N/A")

    lines = [
        f"# Growth Signals — {company_name} ({symbol})",
        "",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| **Growth Bucket** | {bucket} |",
        f"| **Growth Score** | {score}/10 |",
        f"| **Trajectory** | {trajectory} |",
        f"| **Bottom-Line Outlook** | {outlook} |",
        f"| **Management Confidence** | {data.get('management_confidence', 'N/A')} |",
        f"| **Guidance Track Record** | {data.get('guidance_track_record', 'N/A')} |",
        f"| **Data Quality** | {data.get('data_quality', 'N/A')} |",
        "",
    ]

    for label, key in [
        ("Revenue Growth Signal", "revenue_growth_signal"),
        ("Margin Signal", "margin_signal"),
        ("Visibility Signal", "visibility_signal"),
        ("Screener Insights Summary", "screener_insights_summary"),
    ]:
        if val := data.get(key):
            lines += [f"**{label}:** {val}", ""]

    if drivers := data.get("key_growth_drivers"):
        lines += ["## Key Growth Drivers"]
        for d in drivers:
            lines.append(f"- {d}")
        lines.append("")

    if tailwinds := data.get("sector_tailwinds"):
        lines += ["## Sector Tailwinds"]
        for t in tailwinds:
            lines.append(f"- {t}")
        lines.append("")

    if risks := data.get("key_risks"):
        lines += ["## Key Risks"]
        for r in risks:
            lines.append(f"- {r}")
        lines.append("")

    if red_flags := data.get("red_flags"):
        lines += ["## Red Flags"]
        for rf in red_flags:
            lines.append(f"⚠️  {rf}")
        lines.append("")

    if summary := data.get("overall_summary"):
        lines += ["## Overall Summary", summary, ""]

    return "\n".join(lines)


def run(company: dict, config: dict, refresh: bool = False) -> dict:
    """
    Run growth signals analysis for a single company.

    Args:
        company: Company dict with symbol, name, sector, screener_url
        config:  Loaded config.yaml dict
        refresh: Force re-analysis even if report already exists

    Returns:
        dict with: skill_name, symbol, status, data, error
    """
    symbol = company["symbol"]
    company_name = get_company_name(symbol, company.get("name", symbol))
    sector = company.get("sector", "Unknown")

    result = {
        "skill_name": SKILL_NAME,
        "symbol": symbol,
        "status": "success",
        "data": {},
        "error": None,
    }

    try:
        reports_dir = COMPANIES_DIR / symbol.upper() / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_json = reports_dir / f"{SKILL_NAME}.json"

        if report_json.exists() and not refresh:
            existing = json.loads(report_json.read_text(encoding="utf-8"))
            result["data"] = existing
            result["status"] = "skipped"
            print(f"[{SKILL_NAME}][{symbol}] Report exists — skipping (use refresh=True to force)")
            return result

        max_tr = config.get("skills", {}).get("max_transcripts_per_company", MAX_TRANSCRIPTS)
        transcript_texts = _load_pdfs_capped(symbol, "transcripts", max_tr)
        ppt_texts = _load_pdfs_capped(symbol, "ppt", MAX_PPTS)

        insights_text = _load_screener_insights(symbol)

        if not transcript_texts and not ppt_texts and not insights_text:
            result["status"] = "no_data"
            result["error"] = "No transcripts, PPTs, or Screener insights found."
            print(f"[{SKILL_NAME}][{symbol}] No data available — skipping.")
            return result

        context, sources_used = _build_context(
            symbol, company_name, transcript_texts, ppt_texts, insights_text
        )

        n_t = len(transcript_texts)
        n_p = len(ppt_texts)
        print(f"[{SKILL_NAME}][{symbol}] Loaded: {n_t} transcript(s), {n_p} PPT(s), "
              f"{'insights' if insights_text else 'no insights'}. Calling LLM...")

        prompt = load_prompt(
            "growth_signals",
            company_name=company_name,
            symbol=symbol,
            sector=sector,
            n_transcripts=n_t,
            n_ppts=n_p,
            context=context,
        )

        llm_output = call_llm(prompt, expect_json=True)

        if "data_sources_used" not in llm_output:
            llm_output["data_sources_used"] = sources_used

        result["data"] = llm_output

        markdown = _build_markdown(symbol, company_name, llm_output)
        save_report(symbol, SKILL_NAME, markdown, raw_json=llm_output)

        bucket = llm_output.get("growth_bucket", "?")
        score = llm_output.get("growth_score", "?")
        print(f"[{SKILL_NAME}][{symbol}] Done — bucket={bucket.upper()}, score={score}/10")

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        result["data"] = {}
        print(f"[{SKILL_NAME}][{symbol}] ERROR: {exc}")
        traceback.print_exc()

    return result


if __name__ == "__main__":
    import sys
    import yaml

    if len(sys.argv) < 2:
        print("Usage: python -m skills.growth_signals <SYMBOL> [--refresh]")
        sys.exit(1)

    target_symbol = sys.argv[1].upper()
    force_refresh = "--refresh" in sys.argv

    cfg = get_config()
    company_entry = None
    for yaml_file in ["watchlist.yaml", "portfolio_companies.yaml"]:
        yaml_path = PROJECT_ROOT / yaml_file
        if not yaml_path.exists():
            continue
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        company_entry = next(
            (c for c in data.get("stocks", [])
             if c.get("symbol", "").upper() == target_symbol
             or str(c.get("screener_symbol", "")).upper() == target_symbol),
            None,
        )
        if company_entry:
            break

    if company_entry is None:
        print(f"[ERROR] Symbol '{target_symbol}' not found in YAML files.")
        sys.exit(1)

    fetch_result = run(company_entry, cfg, refresh=force_refresh)
    print(f"\nResult: {fetch_result['status']}")
    d = fetch_result.get("data", {})
    print(f"  Growth bucket : {d.get('growth_bucket', 'N/A')}")
    print(f"  Growth score  : {d.get('growth_score', 'N/A')}/10")
    print(f"  Trajectory    : {d.get('trajectory', 'N/A')}")
    print(f"  Bottom line   : {d.get('bottom_line_outlook', 'N/A')}")
    if summary := d.get("overall_summary"):
        print(f"\nSummary:\n  {summary}")
