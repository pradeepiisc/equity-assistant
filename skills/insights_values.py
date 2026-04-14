"""
Skill: Insights Values
=======================
Extracts actual values for Screener Insights metrics from earnings call
transcripts and investor PPTs using LLM, then formats as a quarterly table.

When Screener Insights show "[values require Screener login]", this skill
mines the metric values directly from transcript/PPT text.

Output:
  data/companies/{SYMBOL}/insights/insights_values.txt   — markdown table

Run standalone:
    python -m skills.insights_values SYMBOL [--refresh]
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import traceback
from pathlib import Path

from llm.client import get_config
from llm.utils import load_prompt, call_llm, save_report
from skills.company_meta import get_company_name

SKILL_NAME = "insights_values"
PROJECT_ROOT = Path(__file__).parent.parent
COMPANIES_DIR = PROJECT_ROOT / "data" / "companies"

MAX_PDF_PAGES = 30
MAX_CHARS_TRANSCRIPT = 10_000
MAX_TRANSCRIPTS = 8
MAX_PPTS = 4


# ── PDF loader (page-capped) ──────────────────────────────────────────────────

@contextlib.contextmanager
def _quiet_stderr():
    """Suppress pdfplumber/pdfminer color-warning spam sent directly to stderr."""
    old = sys.stderr
    sys.stderr = io.StringIO()
    try:
        yield
    finally:
        sys.stderr = old


def _load_pdfs_capped(symbol: str, subfolder: str, max_files: int, max_pages: int = MAX_PDF_PAGES) -> list[tuple[str, str]]:
    """
    Load PDFs from a company subfolder, capping at max_pages per file.
    Returns list of (filename, text) tuples, newest first.
    """
    folder = COMPANIES_DIR / symbol.upper() / subfolder
    if not folder.exists():
        return []
    try:
        import pdfplumber
    except ImportError:
        return []

    results: list[tuple[str, str]] = []
    candidates = sorted(folder.glob("*.pdf"))[-max_files:] or sorted(folder.glob("*.txt"))[-max_files:]
    for fp in reversed(candidates):  # newest first
        try:
            if fp.suffix.lower() == ".pdf":
                pages: list[str] = []
                with _quiet_stderr():
                    with pdfplumber.open(fp) as pdf:
                        for page in pdf.pages[:max_pages]:
                            t = page.extract_text()
                            if t:
                                pages.append(t)
                text = "\n".join(pages)
            else:
                text = fp.read_text(encoding="utf-8", errors="replace")
            if text.strip():
                results.append((fp.name, text))
        except Exception:
            pass
    return results


# ── Insights file parser ──────────────────────────────────────────────────────

def _parse_insight_metrics(symbol: str) -> list[str]:
    """
    Read the latest insights .txt file and return metrics that need extraction
    (those with '[values require Screener login]' or any defined metric).
    """
    ins_dir = COMPANIES_DIR / symbol.upper() / "insights"
    if not ins_dir.exists():
        return []

    files = sorted(ins_dir.glob(f"{symbol.upper()}_insights_*.txt"))
    if not files:
        return []

    content = files[-1].read_text(encoding="utf-8", errors="replace")
    metrics: list[str] = []

    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        # Strip sentiment icons
        line = re.sub(r"^-\s*[✅⚠️ℹ️]\s*", "", line).strip()
        if not line:
            continue

        # Remove login note to get clean metric name
        clean = line.replace("[values require Screener login]", "").strip().rstrip(":").strip()

        # Skip noise
        if len(clean) < 5 or clean.lower() in ("general", "revenue", "profitability", "debt"):
            continue

        # Only include meaningful metric names (not category headings)
        if re.match(r"^##\s", clean):
            continue

        metrics.append(clean)

    return metrics


# ── Context builder ───────────────────────────────────────────────────────────

def _build_context(symbol: str) -> tuple[str, list[str]]:
    """Concatenate transcript and PPT text into LLM context. Returns (text, sources)."""
    parts: list[str] = []
    sources: list[str] = []

    transcripts = _load_pdfs_capped(symbol, "transcripts", MAX_TRANSCRIPTS)
    ppts = _load_pdfs_capped(symbol, "ppt", MAX_PPTS)

    for fname, text in transcripts:
        parts.append(f"=== TRANSCRIPT: {fname} ===\n{text[:MAX_CHARS_TRANSCRIPT]}")
        sources.append(fname)

    for fname, text in ppts:
        parts.append(f"=== PRESENTATION: {fname} ===\n{text[:MAX_CHARS_TRANSCRIPT]}")
        sources.append(fname)

    return "\n\n".join(parts), sources


# ── Markdown table builder ────────────────────────────────────────────────────

def _build_markdown_table(symbol: str, company_name: str, data: dict) -> str:
    """Convert LLM JSON output into a markdown table with source citations."""
    quarters: list[str] = data.get("quarters", [])
    metrics_data: dict = data.get("metrics", {})
    sources: dict = data.get("sources", {})
    notes: str = data.get("extraction_notes", "")

    if not quarters or not metrics_data:
        return f"# Insights Values — {company_name} ({symbol})\n\n_No data extracted._\n"

    lines = [
        f"# Insights Values — {company_name} ({symbol})",
        "",
        "Extracted from earnings call transcripts and investor presentations.",
        "",
    ]
    if notes:
        lines += [f"_Note: {notes}_", ""]

    # Table header
    header = "| Metric | " + " | ".join(quarters) + " |"
    separator = "|--------" + ("|" + "|".join(["--------"] * len(quarters))) + "|"
    lines += [header, separator]

    for metric, values in metrics_data.items():
        row_cells: list[str] = [metric]
        for q in quarters:
            v = values.get(q)
            row_cells.append(str(v) if v is not None else "—")
        lines.append("| " + " | ".join(row_cells) + " |")

    lines.append("")

    # Sources section
    if sources:
        lines += ["## Sources", ""]
        lines += ["> Each value below is cited with the document and verbatim excerpt it was drawn from.", ""]
        for key, src in sources.items():
            if not isinstance(src, dict):
                continue
            file_name = src.get("file", "unknown")
            excerpt = src.get("excerpt", "")
            if excerpt:
                metric_q = key.replace("|", " — ")
                lines += [
                    f"**{metric_q}**",
                    f"*Source: `{file_name}`*",
                    f"> {excerpt}",
                    "",
                ]

    return "\n".join(lines)


# ── Main run ──────────────────────────────────────────────────────────────────

def run(company: dict, config: dict, refresh: bool = False) -> dict:
    """
    Extract Screener Insights metric values from transcripts/PPTs via LLM.

    Args:
        company: Company dict with symbol, name, screener_url
        config:  Loaded config.yaml dict
        refresh: Force re-extraction even if output file exists

    Returns:
        dict with: skill_name, symbol, status, data, error
    """
    symbol = company["symbol"].upper()
    company_name = get_company_name(symbol, company.get("name", symbol))

    result = {
        "skill_name": SKILL_NAME,
        "symbol": symbol,
        "status": "success",
        "data": {"saved": None, "metrics_count": 0, "quarters_found": []},
        "error": None,
    }

    try:
        ins_dir = COMPANIES_DIR / symbol / "insights"
        ins_dir.mkdir(parents=True, exist_ok=True)
        output_path = ins_dir / "insights_values.md"

        if output_path.exists() and not refresh:
            print(f"[{SKILL_NAME}][{symbol}] Already exists — skipping (use refresh=True to force)")
            result["status"] = "skipped"
            result["data"]["saved"] = str(output_path)
            return result

        # Also skip if old .txt version exists and .md not yet created
        old_txt = ins_dir / "insights_values.txt"
        if old_txt.exists() and not refresh and not output_path.exists():
            print(f"[{SKILL_NAME}][{symbol}] Found legacy .txt — will regenerate as .md with citations")

        # Parse metric names from insights file
        metrics = _parse_insight_metrics(symbol)
        if not metrics:
            result["status"] = "no_data"
            result["error"] = "No insight metrics found in insights file."
            print(f"[{SKILL_NAME}][{symbol}] No insight metrics — skipping.")
            return result

        # Load transcripts + PPTs
        context, sources = _build_context(symbol)
        if not context.strip():
            result["status"] = "no_data"
            result["error"] = "No transcripts or PPTs found."
            print(f"[{SKILL_NAME}][{symbol}] No transcript/PPT data — skipping.")
            return result

        metrics_list = "\n".join(f"- {m}" for m in metrics)
        print(f"[{SKILL_NAME}][{symbol}] Extracting {len(metrics)} metric(s) from {len(sources)} file(s)...")

        # Build prompt
        prompt_template = load_prompt("insights_values")
        # Use str.replace instead of .format() — the prompt contains JSON examples
        # with curly braces which would cause KeyError with .format()
        user_prompt = (
            prompt_template
            .replace("{company_name}", company_name)
            .replace("{symbol}", symbol)
            .replace("{metrics_list}", metrics_list)
            .replace("{transcripts_text}", context)
        )

        llm_response = call_llm(user_prompt, expect_json=True)

        if not llm_response or not isinstance(llm_response, dict):
            result["status"] = "error"
            result["error"] = "LLM returned invalid response."
            print(f"[{SKILL_NAME}][{symbol}] LLM response invalid.")
            return result

        # Build and save markdown
        markdown = _build_markdown_table(symbol, company_name, llm_response)
        output_path.write_text(markdown, encoding="utf-8")
        print(f"[{SKILL_NAME}][{symbol}] Saved: {output_path}")

        # Also save raw JSON
        json_path = ins_dir / "insights_values.json"
        json_path.write_text(json.dumps(llm_response, indent=2, ensure_ascii=False), encoding="utf-8")

        quarters = llm_response.get("quarters", [])
        metrics_found = len(llm_response.get("metrics", {}))
        result["data"]["saved"] = str(output_path)
        result["data"]["metrics_count"] = metrics_found
        result["data"]["quarters_found"] = quarters

        print(f"[{SKILL_NAME}][{symbol}] Done — {metrics_found} metric(s), {len(quarters)} quarter(s)")

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        print(f"[{SKILL_NAME}][{symbol}] ERROR: {exc}")
        traceback.print_exc()

    return result


if __name__ == "__main__":
    import sys
    import yaml

    if len(sys.argv) < 2:
        print("Usage: python -m skills.insights_values SYMBOL [--refresh]")
        sys.exit(1)

    target_symbol = sys.argv[1].upper()
    force_refresh = "--refresh" in sys.argv

    cfg = get_config()
    company_entry = None
    for yaml_file in ["watchlist.yaml", "portfolio_companies.yaml"]:
        p = PROJECT_ROOT / yaml_file
        if not p.exists():
            continue
        with open(p) as f:
            data = yaml.safe_load(f)
        company_entry = next(
            (c for c in data.get("stocks", [])
             if c.get("symbol", "").upper() == target_symbol),
            None,
        )
        if company_entry:
            break

    if not company_entry:
        company_entry = {"symbol": target_symbol, "name": target_symbol}

    r = run(company_entry, cfg, refresh=force_refresh)
    print(f"\nResult: {r['status']}")
    if r["data"].get("saved"):
        print(f"  Saved: {r['data']['saved']}")
        print(f"  Metrics: {r['data']['metrics_count']}, Quarters: {r['data']['quarters_found']}")
