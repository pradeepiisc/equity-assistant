"""
Skill: Valuation Agent
=======================
LLM-driven, sector-aware intrinsic valuation of a company.

Detects sector from cached profile → loads matching expert prompt from
prompts/sector_valuation/ → gathers company context → calls LLM for
forward-looking fair value estimate.

Output saved to: data/companies/{SYMBOL}/reports/valuation_agent.{md,json}

Standalone usage:
    python -m skills.valuation_agent SYMBOL
"""

from __future__ import annotations

import json
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

from llm.utils import call_llm, load_company_files, save_report
from skills import concall_fetcher, financial_fetcher, transcript_analysis
from skills.company_meta import get_latest_price

SKILL_NAME = "valuation_agent"
PROJECT_ROOT = Path(__file__).parent.parent
SECTOR_PROMPTS_DIR = PROJECT_ROOT / "prompts" / "sector_valuation"
SECTOR_PROFILES_DIR = PROJECT_ROOT / "data" / "sector_profiles"
COMPANIES_DIR = PROJECT_ROOT / "data" / "companies"

MAX_FINANCIAL_CHARS = 5000
MAX_REPORT_CHARS = 3000

_REASONING_APPENDIX = """

ADDITIONAL: Also include a "reasoning" field in your JSON output:
"reasoning": {
  "quantitative": ["<key financial metrics and multiples justifying the fair value>", "..."],
  "qualitative": ["<thesis, catalysts, management quality, competitive moat observations>", "..."]
}
"""


def _to_float(v: str) -> float | None:
    try:
        s = v.strip()
        if not s:
            return None
        s = s.replace(",", "")
        s = s.replace("₹", "")
        s = s.replace("%", "")
        s = s.replace("/", " ")
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        if not m:
            return None
        return float(m.group(0))
    except Exception:
        return None


def _parse_row_values(section_text: str, row_prefix: str) -> list[float]:
    for ln in section_text.splitlines():
        if not ln.strip():
            continue
        if not ln.strip().startswith(row_prefix):
            continue
        parts = [p.strip() for p in ln.split("|")]
        if len(parts) < 3:
            return []
        vals = [_to_float(p) for p in parts[1:] if _to_float(p) is not None]
        return [v for v in vals if v is not None]
    return []


def _estimate_growth_from_sales(annual_pl: str) -> float | None:
    vals = _parse_row_values(annual_pl, "Sales +")
    if len(vals) < 4:
        return None
    base = vals[-4]
    latest = vals[-2]
    if not base or not latest or base <= 0 or latest <= 0:
        return None
    try:
        cagr = (latest / base) ** (1 / 2) - 1
    except Exception:
        return None
    if cagr < 0:
        return 0.0
    return min(max(cagr, 0.08), 0.35)


def _profile_reasoning(symbol: str) -> str:
    sym = symbol.upper()
    sym_norm = sym.replace("-SM", "").replace("-BE", "")
    for candidate in [sym, sym_norm]:
        p = SECTOR_PROFILES_DIR / f"{candidate}.json"
        if not p.exists():
            continue
        try:
            prof = json.loads(p.read_text(encoding="utf-8"))
            desc = (prof.get("business_description") or "").strip()
            inds = prof.get("output_industries") or []
            ind_txt = ", ".join([i for i in inds if isinstance(i, str)])
            if desc or ind_txt:
                return f"Business: {desc[:320]}" + (f" | Output industries: {ind_txt[:120]}" if ind_txt else "")
        except Exception:
            pass
    return ""


def _latest_financial_text(symbol: str) -> str:
    fin_dir = COMPANIES_DIR / symbol.upper() / "financials"
    if not fin_dir.exists():
        return ""
    files = sorted([p for p in fin_dir.glob("*.txt") if p.is_file()])
    if not files:
        return ""
    return files[-1].read_text(encoding="utf-8", errors="replace")


def _parse_key_ratios(fin_text: str) -> dict[str, float]:
    out: dict[str, float] = {}

    lines = fin_text.splitlines()
    in_ratios = False
    for ln in lines:
        if ln.strip().startswith("## Key Ratios"):
            in_ratios = True
            continue
        if in_ratios and ln.strip() == "---":
            break
        if not in_ratios:
            continue
        if not ln.strip():
            continue

        if ln.lower().startswith("market cap"):
            val = _to_float(ln)
            if val is not None:
                out["market_cap_cr"] = val
        elif ln.lower().startswith("current price"):
            val = _to_float(ln)
            if val is not None:
                out["current_price"] = val
        elif ln.lower().startswith("stock p/e"):
            val = _to_float(ln)
            if val is not None:
                out["pe"] = val
        elif ln.lower().startswith("book value"):
            val = _to_float(ln)
            if val is not None:
                out["book_value"] = val
        elif ln.lower().startswith("roce"):
            val = _to_float(ln)
            if val is not None:
                out["roce_pct"] = val
        elif ln.lower().startswith("roe"):
            val = _to_float(ln)
            if val is not None:
                out["roe_pct"] = val
        elif ln.lower().startswith("high / low"):
            vals = [v for v in [_to_float(x) for x in ln.split()] if v is not None]
            if len(vals) >= 2:
                out["high_52w"] = vals[0]
                out["low_52w"] = vals[1]

    return out


def _extract_md_section(report_md: str, section: str) -> str:
    m = re.search(rf"## {re.escape(section)}\n(.*?)(?:\n## |\Z)", report_md, re.DOTALL)
    return m.group(1).strip() if m else ""


def _growth_signals_context(symbol: str) -> str:
    """Load pre-computed growth signals from growth_signals.json if available."""
    p = COMPANIES_DIR / symbol.upper() / "reports" / "growth_signals.json"
    if not p.exists():
        return ""
    try:
        gs = json.loads(p.read_text(encoding="utf-8"))
        bucket = gs.get("growth_bucket", "?").upper()
        score = gs.get("growth_score", "?")
        trajectory = gs.get("trajectory", "?")
        outlook = gs.get("bottom_line_outlook", "?")
        mgmt_conf = gs.get("management_confidence", "?")
        guid_track = gs.get("guidance_track_record", "?")
        rev = gs.get("revenue_growth_signal", "")
        margin = gs.get("margin_signal", "")
        visibility = gs.get("visibility_signal", "")
        screener_s = gs.get("screener_insights_summary", "")
        summary = gs.get("overall_summary", "")
        drivers = gs.get("key_growth_drivers", [])
        risks = gs.get("key_risks", [])
        red_flags = gs.get("red_flags", [])
        tailwinds = gs.get("sector_tailwinds", [])
        lines = [
            f"GROWTH BUCKET: {bucket}  |  SCORE: {score}/10  |  TRAJECTORY: {trajectory}",
            f"BOTTOM-LINE OUTLOOK: {outlook}  |  MGMT CONFIDENCE: {mgmt_conf}  |  GUIDANCE TRACK: {guid_track}",
        ]
        if rev:
            lines.append(f"REVENUE SIGNAL: {rev}")
        if margin:
            lines.append(f"MARGIN SIGNAL: {margin}")
        if visibility:
            lines.append(f"VISIBILITY/ORDER-BOOK SIGNAL: {visibility}")
        if screener_s:
            lines.append(f"SCREENER INSIGHTS: {screener_s}")
        if drivers:
            lines.append("KEY GROWTH DRIVERS: " + " | ".join(drivers[:3]))
        if tailwinds:
            lines.append("SECTOR TAILWINDS: " + " | ".join(tailwinds[:2]))
        if risks:
            lines.append("KEY RISKS (from transcripts+PPTs): " + " | ".join(risks[:3]))
        if red_flags:
            lines.append("⚠️ RED FLAGS: " + " | ".join(red_flags[:3]))
        if summary:
            lines.append(f"GROWTH SYNTHESIS: {summary}")
        return "\n".join(lines)
    except Exception:
        return ""


def _master_reasoning(symbol: str) -> dict:
    p = COMPANIES_DIR / symbol.upper() / "reports" / "master_report.md"
    if not p.exists():
        return {"available": False}
    text = p.read_text(encoding="utf-8")
    thesis = _extract_md_section(text, "Investment Thesis")
    catalysts = _extract_md_section(text, "Key Catalysts")
    risks = _extract_md_section(text, "Key Risks")
    action = _extract_md_section(text, "Action Recommendation")
    return {
        "available": True,
        "thesis": thesis,
        "catalysts": catalysts,
        "risks": risks,
        "action": action,
    }


def _extract_section(fin_text: str, header: str) -> str:
    m = re.search(rf"^##\s+{re.escape(header)}\s*$", fin_text, flags=re.MULTILINE)
    if not m:
        return ""
    start = m.end()
    rest = fin_text[start:]
    m2 = re.search(r"^##\s+", rest, flags=re.MULTILINE)
    return rest[: m2.start()] if m2 else rest


def _parse_ttm_from_row(section_text: str, row_prefix: str) -> float | None:
    for ln in section_text.splitlines():
        if not ln.strip():
            continue
        if not ln.strip().startswith(row_prefix):
            continue
        parts = [p.strip() for p in ln.split("|")]
        if len(parts) < 3:
            continue
        last = parts[-1]
        return _to_float(last)
    return None


def _extract_method_from_prompt(prompt_text: str) -> str:
    m = re.search(r"\"valuation_method\"\s*:\s*\"([^\"]+)\"", prompt_text)
    return m.group(1).strip() if m else ""


def _extract_multiple_ranges(prompt_text: str, multiple_type: str) -> list[tuple[float, float]]:
    patterns: list[str]
    if multiple_type == "ev_ebitda":
        patterns = [
            r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*x\s*EV/EBITDA",
            r"EV/EBITDA\s+(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*x",
        ]
    elif multiple_type == "pe":
        patterns = [
            r"P/E\s+(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*x",
            r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*x\s*P/E",
        ]
    elif multiple_type == "pb":
        patterns = [
            r"P/B\s+(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*x",
            r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*x\s*P/B",
        ]
    else:
        return []

    out: list[tuple[float, float]] = []
    for pattern in patterns:
        for a, b in re.findall(pattern, prompt_text, flags=re.IGNORECASE):
            lo = _to_float(a)
            hi = _to_float(b)
            if lo is None or hi is None:
                continue
            if lo <= 0 or hi <= 0:
                continue
            if hi < lo:
                lo, hi = hi, lo
            out.append((lo, hi))
    return out


def _extract_multiple_range(prompt_text: str, multiple_type: str) -> tuple[float, float] | None:
    ranges = _extract_multiple_ranges(prompt_text, multiple_type)
    if not ranges:
        return None
    lows = [r[0] for r in ranges]
    highs = [r[1] for r in ranges]
    return (min(lows), max(highs))


def _round_price(v: float | None) -> int | None:
    if v is None:
        return None
    if v <= 0:
        return None
    return int(round(v))


def _compute_signals(current_price: float, fair_low: float, fair_high: float) -> tuple[str, str, float]:
    upside = (fair_high - current_price) / current_price * 100 if current_price else 0.0

    if current_price < fair_low:
        current_vs_fair = "Undervalued"
    elif current_price > fair_high:
        current_vs_fair = "Overvalued"
    else:
        current_vs_fair = "Fairly Valued"

    mid = (fair_low + fair_high) / 2
    if current_price <= fair_low * 0.9:
        entry_signal = "Buy Now"
    elif current_price < mid:
        entry_signal = "Accumulate on Dips"
    elif current_price <= fair_high:
        entry_signal = "Hold"
    elif current_price <= fair_high * 1.1:
        entry_signal = "Trim"
    else:
        entry_signal = "Exit"

    return current_vs_fair, entry_signal, upside


def _deterministic_valuation(
    symbol: str,
    sector_name: str,
    prompt_text: str,
    fin_text: str,
    current_price_override: float | None = None,
) -> dict:
    ratios = _parse_key_ratios(fin_text)
    annual_pl = _extract_section(fin_text, "Annual Profit & Loss")

    current_price = ratios.get("current_price")
    if current_price_override is not None:
        current_price = float(current_price_override)
    market_cap_cr = ratios.get("market_cap_cr")
    book_value = ratios.get("book_value")
    pe = ratios.get("pe")
    high_52w = ratios.get("high_52w")
    low_52w = ratios.get("low_52w")

    ttm_sales_cr = _parse_ttm_from_row(annual_pl, "Sales +")
    ttm_op_cr = _parse_ttm_from_row(annual_pl, "Operating Profit")
    ttm_np_cr = _parse_ttm_from_row(annual_pl, "Net Profit")
    ttm_eps = _parse_ttm_from_row(annual_pl, "EPS in Rs")

    if current_price is None:
        raise ValueError("Missing Current Price in financials key ratios.")

    method = _extract_method_from_prompt(prompt_text) or "Deterministic multiple-based valuation"

    evr = _extract_multiple_range(prompt_text, "ev_ebitda")
    per = _extract_multiple_range(prompt_text, "pe")
    pbr = _extract_multiple_range(prompt_text, "pb")

    method_upper = method.upper()
    model: str
    if "P/B" in method_upper:
        model = "pb"
    elif "P/E" in method_upper:
        model = "pe"
    elif "EV/EBITDA" in method_upper:
        model = "ev_ebitda"
    elif pbr:
        model = "pb"
    elif per:
        model = "pe"
    elif evr:
        model = "ev_ebitda"
    else:
        model = "pe"

    if model == "ev_ebitda" and not evr:
        evr = (10.0, 18.0)
    if model == "pe" and not per:
        per = (12.0, 20.0)
    if model == "pb" and not pbr:
        pbr = (1.0, 2.0)

    growth_assumption = 0.12
    if sector_name in ("banking", "nbfc"):
        growth_assumption = 0.14
    else:
        est = _estimate_growth_from_sales(annual_pl)
        if est is not None:
            growth_assumption = est

    roce = ratios.get("roce_pct")

    pe_ranges = _extract_multiple_ranges(prompt_text, "pe")
    if model == "pe" and pe_ranges:
        pe_ranges_sorted = sorted(pe_ranges, key=lambda x: (x[0], x[1]))
        opm_pct = (ttm_op_cr / ttm_sales_cr * 100) if ttm_op_cr is not None and ttm_sales_cr else None
        if (roce is not None and roce >= 24) or (opm_pct is not None and opm_pct >= 18) or growth_assumption >= 0.2:
            lo, hi = max(pe_ranges_sorted, key=lambda x: x[0])
            per = (lo, hi)
        elif (roce is not None and roce >= 18) or (opm_pct is not None and opm_pct >= 12) or growth_assumption >= 0.12:
            per = pe_ranges_sorted[min(1, len(pe_ranges_sorted) - 1)]
        else:
            per = pe_ranges_sorted[0]

    bull_price: float | None = None
    base_price: float | None = None
    bear_price: float | None = None
    multiple_applied: str = ""

    if model == "pe":
        if ttm_eps is None:
            raise ValueError("Missing TTM EPS in Annual Profit & Loss.")
        lo, hi = per or (12.0, 20.0)
        mid = (lo + hi) / 2
        fwd_eps = ttm_eps * (1 + growth_assumption)
        bear_price = fwd_eps * lo
        base_price = fwd_eps * mid
        bull_price = fwd_eps * hi
        multiple_applied = f"P/E {lo:.1f}x–{hi:.1f}x on EPS(TTM)×(1+{int(growth_assumption*100)}%)"

    elif model == "pb":
        if book_value is None:
            raise ValueError("Missing Book Value in Key Ratios.")
        lo, hi = pbr or (1.0, 2.0)
        mid = (lo + hi) / 2
        bear_price = book_value * lo
        base_price = book_value * mid
        bull_price = book_value * hi
        multiple_applied = f"P/B {lo:.2f}x–{hi:.2f}x on current BVPS"

    else:
        if market_cap_cr is None or ttm_op_cr is None or ttm_op_cr <= 0:
            if ttm_eps is None:
                raise ValueError("Missing Market Cap / Operating Profit for EV/EBITDA and missing EPS for fallback.")
            lo, hi = per or (12.0, 20.0)
            mid = (lo + hi) / 2
            fwd_eps = ttm_eps * (1 + growth_assumption)
            bear_price = fwd_eps * lo
            base_price = fwd_eps * mid
            bull_price = fwd_eps * hi
            model = "pe"
            method = "Forward P/E (fallback: EV/EBITDA inputs missing)"
            multiple_applied = f"P/E {lo:.1f}x–{hi:.1f}x on EPS(TTM)×(1+{int(growth_assumption*100)}%)"
        else:
            current_ev_ebitda = market_cap_cr / ttm_op_cr
            lo, hi = evr or (10.0, 18.0)
            mid = (lo + hi) / 2
            bear_price = current_price * (lo / current_ev_ebitda)
            base_price = current_price * (mid / current_ev_ebitda)
            bull_price = current_price * (hi / current_ev_ebitda)
            multiple_applied = f"EV/EBITDA {lo:.1f}x–{hi:.1f}x vs current {current_ev_ebitda:.1f}x"

    if bear_price is None or bull_price is None:
        raise ValueError("Could not compute fair value.")

    fair_low = _round_price(bear_price)
    fair_high = _round_price(bull_price)
    fair_base = _round_price(base_price)
    current_vs_fair, entry_signal, upside = _compute_signals(current_price, fair_low, fair_high)

    quantitative: list[str] = []
    quantitative.append(f"Model used: {model}")
    if high_52w is not None and low_52w is not None and current_price:
        near_high = (high_52w - current_price) / high_52w * 100 if high_52w else 0.0
        above_low = (current_price - low_52w) / low_52w * 100 if low_52w else 0.0
        quantitative.append(f"52W high/low: ₹{_round_price(high_52w)} / ₹{_round_price(low_52w)}")
        quantitative.append(f"Price vs 52W: {near_high:.1f}% below high, {above_low:.1f}% above low")
    if ttm_sales_cr is not None:
        quantitative.append(f"TTM Sales: ₹{ttm_sales_cr:.0f} Cr")
    if ttm_op_cr is not None and ttm_sales_cr:
        opm = ttm_op_cr / ttm_sales_cr * 100 if ttm_sales_cr else 0.0
        quantitative.append(f"TTM Operating Profit: ₹{ttm_op_cr:.0f} Cr (OPM ~{opm:.1f}%)")
    if ttm_np_cr is not None:
        quantitative.append(f"TTM Net Profit: ₹{ttm_np_cr:.0f} Cr")
    if pe is not None:
        quantitative.append(f"Current Stock P/E: {pe:.1f}x")
    if model == "pe" and ttm_eps is not None:
        fwd_eps = ttm_eps * (1 + growth_assumption)
        quantitative.append(f"TTM EPS: ₹{ttm_eps:.2f}; Forward EPS assumption: ₹{fwd_eps:.2f} (growth {int(growth_assumption*100)}%)")
    if model == "ev_ebitda" and market_cap_cr is not None and ttm_op_cr is not None and ttm_op_cr > 0:
        quantitative.append(f"Current EV/EBITDA proxy: {(market_cap_cr / ttm_op_cr):.1f}x")
    if model == "pb" and book_value is not None:
        quantitative.append(f"Book Value (BVPS): ₹{book_value:.2f}")
    quantitative.append(f"Multiple range applied: {multiple_applied}")

    qualitative: list[str] = []
    mr = _master_reasoning(symbol)
    if mr.get("available"):
        if mr.get("thesis"):
            qualitative.append(f"Thesis: {mr.get('thesis')[:500]}")
        if mr.get("catalysts"):
            qualitative.append(f"Catalysts: {mr.get('catalysts')[:500]}")
        if mr.get("risks"):
            qualitative.append(f"Risks: {mr.get('risks')[:500]}")
        if mr.get("action"):
            qualitative.append(f"Action: {mr.get('action')[:400]}")
    else:
        pr = _profile_reasoning(symbol)
        if pr:
            qualitative.append(pr)

    return {
        "engine": "deterministic_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "fair_value_low": _round_price(fair_low),
        "fair_value_high": _round_price(fair_high),
        "fair_value_base": _round_price(fair_base),
        "current_price": _round_price(current_price),
        "current_vs_fair": current_vs_fair,
        "upside_pct": round(upside, 1),
        "valuation_method": method,
        "entry_signal": entry_signal,
        "sector_kpis": {
            "model_used": model,
            "multiple_range_applied": multiple_applied,
            "market_cap_cr": market_cap_cr,
            "ttm_sales_cr": ttm_sales_cr,
            "ttm_operating_profit_cr": ttm_op_cr,
            "ttm_net_profit_cr": ttm_np_cr,
            "ttm_eps": ttm_eps,
            "stock_pe": pe,
            "book_value": book_value,
            "high_52w": high_52w,
            "low_52w": low_52w,
        },
        "reasoning": {
            "quantitative": quantitative,
            "qualitative": qualitative,
            "sources": [
                "financials/*.txt (Screener) for numeric inputs",
                "master_report.md (if present) for qualitative thesis/catalysts/risks",
            ],
        },
        "key_assumptions": [
            f"Forward growth assumption: {int(growth_assumption*100)}% applied only where EPS is used.",
            "EV approximated as Market Cap (net debt not derived from Screener text in this deterministic engine).",
            "Operating Profit used as EBITDA proxy when EV/EBITDA is used.",
        ],
        "scenarios": [
            {"name": "Bull", "fair_value": _round_price(bull_price), "probability": "30%", "description": "Upper-end multiple applied."},
            {"name": "Base", "fair_value": _round_price(base_price) if base_price is not None else _round_price((fair_low + fair_high) / 2), "probability": "50%", "description": "Mid multiple applied."},
            {"name": "Bear", "fair_value": _round_price(bear_price), "probability": "20%", "description": "Lower-end multiple applied."},
        ],
        "key_risks_to_valuation": [
            "Sector multiple compression in a risk-off market.",
            "Forward growth assumption may not match company-specific reality.",
        ],
        "entry_exit_triggers": {
            "buy": ["Price trades below fair value low.", "Fundamentals remain intact (no major margin/ROE deterioration)."],
            "exit": ["Price trades materially above fair value high.", "Thesis breaks (earnings downgrade / governance issues)."],
        },
    }

# ── Sector → prompt file mapping ──────────────────────────────────────────────
# Order matters — first match wins. Checks against lowercased output_industries + business_description.
SECTOR_PROMPT_MAP: list[tuple[str, str]] = [
    ("oncology", "pharma"),
    ("pharma formulation", "pharma"),
    ("pharmaceutical formulation", "pharma"),
    ("pharma", "pharma"),
    ("drug", "pharma"),
    ("biotech", "pharma"),
    ("biosimilar", "pharma"),
    ("diagnostic", "pharma_services"),
    ("crdmo", "pharma_services"),
    ("cro ", "pharma_services"),
    ("lab testing", "pharma_services"),
    ("hospital", "hospital"),
    ("fertility", "fertility"),
    ("ivf", "fertility"),
    ("nbfc", "nbfc"),
    ("microfinance", "nbfc"),
    ("gold loan", "nbfc"),
    ("bank", "banking"),
    ("fintech", "fintech"),
    ("broking", "fintech"),
    ("payment gateway", "fintech"),
    ("wealth management", "fintech"),
    ("electronic manufacturing", "ems"),
    ("pcb assembl", "ems"),
    ("osat", "ems"),
    ("ems", "ems"),
    ("aerospace", "aerospace"),
    ("aviation mro", "aerospace"),
    ("defence simulation", "defence"),
    ("defence electronic", "defence"),
    ("defense", "defence"),
    ("military", "defence"),
    ("transformer", "electrical_equipment"),
    ("switchgear", "electrical_equipment"),
    ("hvdc", "electrical_equipment"),
    ("facts controller", "electrical_equipment"),
    ("power transmission", "electrical_equipment"),
    ("power distribution equipment", "electrical_equipment"),
    ("electric vehicle", "ev"),
    ("battery cell", "battery_components"),
    ("battery chemi", "battery_components"),
    ("lithium", "battery_components"),
    ("renewable energy", "power_energy"),
    ("solar", "power_energy"),
    ("wind energy", "power_energy"),
    ("power generat", "power_energy"),
    ("specialty chemical", "chemicals"),
    ("chemical", "chemicals"),
    ("fertiliser", "chemicals"),
    ("pesticide", "chemicals"),
    ("agrochem", "chemicals"),
    ("steel", "steel"),
    ("iron ore", "steel"),
    ("alloy", "steel"),
    ("metal recycl", "metal_recycling"),
    ("e-waste recycl", "metal_recycling"),
    ("copper recycl", "metal_recycling"),
    ("aluminium recycl", "metal_recycling"),
    ("lead recycl", "metal_recycling"),
    ("scrap metal", "metal_recycling"),
    ("paper recycl", "paper_recycling"),
    ("wastepaper", "paper_recycling"),
    ("recycled paper", "paper_recycling"),
    ("recycled fibre", "paper_recycling"),
    ("kraft paper", "paper_recycling"),
    ("duplex board", "paper_recycling"),
    ("newsprint", "paper_recycling"),
    ("paperboard", "paper_recycling"),
    ("visa processing", "government_outsourcing"),
    ("visa application centre", "government_outsourcing"),
    ("consular", "government_outsourcing"),
    ("e-governance", "government_outsourcing"),
    ("government outsourc", "government_outsourcing"),
    ("biometric verification", "government_outsourcing"),
    ("superabrasive", "specialty_industrials"),
    ("grinding wheel", "specialty_industrials"),
    ("precision machined", "specialty_industrials"),
    ("cutting tool", "specialty_industrials"),
    ("abrasive", "specialty_industrials"),
    ("metrology", "specialty_industrials"),
    ("dredg", "marine_services"),
    ("inland waterway", "marine_services"),
    ("marine engineering", "marine_services"),
    ("marine services", "marine_services"),
    ("hydrographic", "marine_services"),
    ("vessel management", "marine_services"),
    ("fleet management", "marine_services"),
    ("iwai", "marine_services"),
    ("logistics", "logistics"),
    ("freight", "logistics"),
    ("supply chain", "logistics"),
    ("warehouse", "logistics"),
    ("infra", "infra"),
    ("epc", "infra"),
    ("construction", "infra"),
    ("road project", "infra"),
    ("water project", "infra"),
    ("textile", "textiles"),
    ("fabric", "textiles"),
    ("yarn", "textiles"),
    ("garment", "textiles"),
    ("apparel", "textiles"),
    ("hotel", "hospitality"),
    ("resort", "hospitality"),
    ("hospitality", "hospitality"),
    ("tourism", "hospitality"),
    ("entertainment", "entertainment"),
    ("ott", "entertainment"),
    ("media content", "entertainment"),
    ("film", "entertainment"),
    ("aquaculture", "aquaculture"),
    ("shrimp", "aquaculture"),
    ("fisheries", "aquaculture"),
    ("seafood", "aquaculture"),
    ("animal feed", "animal_feeds"),
    ("poultry feed", "animal_feeds"),
    ("cattle feed", "animal_feeds"),
    ("consumer durable", "consumer_durables"),
    ("home appliance", "consumer_durables"),
    ("white goods", "consumer_durables"),
    ("precision plastic", "consumer_durables"),
    ("gems", "gems_jewellery"),
    ("jewellery", "gems_jewellery"),
    ("diamond", "gems_jewellery"),
    ("corn", "agri_commodities"),
    ("cashew", "agri_commodities"),
    ("commodity trading", "agri_commodities"),
    ("hvac", "industrial_systems"),
    ("cooling system", "industrial_systems"),
    ("heating system", "industrial_systems"),
    ("cleaning service", "industrial_systems"),
    ("flow system", "industrial_systems"),
    ("metallic hose", "industrial_systems"),
]


def _detect_sector_prompt(symbol: str, sector_hint: str = "") -> str:
    """Identify best-matching sector prompt from sector profile cache.

    Falls back to matching against the watchlist 'sector' field if no
    sector_profiles JSON is cached for the symbol.
    """
    sym = symbol.upper()
    sym_norm = sym.replace("-SM", "").replace("-BE", "")
    for candidate in [sym, sym_norm]:
        profile_path = SECTOR_PROFILES_DIR / f"{candidate}.json"
        if not profile_path.exists():
            continue
        try:
            profile = json.loads(profile_path.read_text())
            industries = " ".join(profile.get("output_industries", [])).lower()
            desc = profile.get("business_description", "").lower()
            combined = industries + " " + desc
            for keyword, prompt_name in SECTOR_PROMPT_MAP:
                if len(keyword) <= 3 and keyword.isalpha():
                    if re.search(rf"\b{re.escape(keyword)}\b", combined):
                        return prompt_name
                else:
                    if keyword in combined:
                        return prompt_name
        except Exception:
            pass

    # Fallback: match against watchlist sector string
    if sector_hint:
        hint_lower = sector_hint.lower()
        for keyword, prompt_name in SECTOR_PROMPT_MAP:
            if keyword in hint_lower:
                return prompt_name

    return "generic"


def _load_sector_prompt_text(sector_name: str) -> str:
    """Load the sector-specific prompt file, falling back to generic."""
    specific = SECTOR_PROMPTS_DIR / f"{sector_name}.txt"
    if specific.exists():
        return specific.read_text(encoding="utf-8")
    generic = SECTOR_PROMPTS_DIR / "generic.txt"
    if generic.exists():
        return generic.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"No sector prompt '{sector_name}.txt' and no generic.txt found in {SECTOR_PROMPTS_DIR}"
    )


def _gather_context(symbol: str, company: dict, skill_results: dict) -> str:
    """Aggregate all available company data into a single context string."""
    parts: list[str] = []
    sym = symbol.upper()

    parts.append(f"COMPANY: {company.get('name', sym)} ({sym})")
    parts.append(f"DECLARED SECTOR: {company.get('sector', 'Unknown')}")

    # Current market price from portfolio (more recent than Screener snapshot)
    if company.get("last_price") is not None:
        parts.append(f"CURRENT MARKET PRICE: ₹{company['last_price']}")
    elif company.get("current_price") is not None:
        parts.append(f"CURRENT MARKET PRICE: ₹{company['current_price']}")

    # Sector profile
    sym_norm = sym.replace("-SM", "").replace("-BE", "")
    for candidate in [sym, sym_norm]:
        profile_path = SECTOR_PROFILES_DIR / f"{candidate}.json"
        if not profile_path.exists():
            continue
        try:
            p = json.loads(profile_path.read_text())
            parts.append(f"BUSINESS: {p.get('business_description', '')}")
            parts.append(f"KEY PRODUCTS: {', '.join(p.get('key_products', []))}")
            parts.append(f"INPUT SENSITIVITY: {p.get('input_sensitivity', '')}")
            break
        except Exception:
            pass

    # Growth signals — transcript+PPT+Screener derived growth trajectory
    gs_context = _growth_signals_context(sym)
    if gs_context:
        parts.append("\n=== GROWTH SIGNALS (from transcripts + PPTs + Screener Insights) ===")
        parts.append(gs_context)

    # Master report — already synthesises transcripts, shareholding, news, financials
    master_md = COMPANIES_DIR / sym / "reports" / "master_report.md"
    if master_md.exists():
        text = master_md.read_text(encoding="utf-8")
        parts.append("\n=== MASTER INVESTMENT REPORT (synthesised) ===")
        parts.append(text[:MAX_REPORT_CHARS])
        if len(text) > MAX_REPORT_CHARS:
            parts.append("... [truncated — full report available]")

    # Screener Insights metric values (LLM-extracted quarterly table from transcripts/PPTs)
    insights_values_path = COMPANIES_DIR / sym / "insights" / "insights_values.md"
    if insights_values_path.exists():
        text = insights_values_path.read_text(encoding="utf-8")
        parts.append("\n=== SCREENER INSIGHTS METRIC VALUES (quarterly, from transcripts) ===")
        parts.append(text[:2000])

    # Raw financial tables (most recent, from financial_fetcher)
    fin_files = load_company_files(sym, "financials", extension=".txt")
    if fin_files:
        parts.append("\n=== RAW FINANCIAL DATA (last 5 years) ===")
        # Alert LLM if the Screener-scraped price is stale vs live price
        live_price = company.get("last_price") or company.get("current_price")
        if live_price is not None:
            parts.append(
                f"⚠️ NOTE: The 'Current Price' in the financial data below may be stale "
                f"(from Screener scrape date). Use CURRENT MARKET PRICE ₹{live_price} "
                f"shown above for all valuation calculations."
            )
        combined_fin = "\n\n".join(fin_files)
        parts.append(combined_fin[:MAX_FINANCIAL_CHARS])
        if len(combined_fin) > MAX_FINANCIAL_CHARS:
            parts.append("... [truncated]")

    # Skill outputs from full_company_analysis pipeline (if passed in)
    for skill_name, result in skill_results.items():
        if result.get("status") != "success" or not result.get("data"):
            continue
        data = result["data"]
        parts.append(f"\n=== {skill_name.upper().replace('_', ' ')} ===")
        if skill_name == "transcript_analysis":
            if val := data.get("executive_summary"):
                parts.append(f"TRANSCRIPT EXECUTIVE SUMMARY: {str(val)[:800]}")
            if mt := data.get("management_tone"):
                rating = mt.get("rating", "")
                evidence = mt.get("evidence", [])
                ev_txt = "; ".join(str(e) for e in evidence[:3])
                parts.append(f"MANAGEMENT TONE: {rating} — {ev_txt[:400]}")
            if ge := data.get("guidance_evolution"):
                if ps := ge.get("pattern_summary"):
                    parts.append(f"GUIDANCE PATTERN: {str(ps)[:500]}")
                for topic in (ge.get("topics") or [])[:3]:
                    parts.append(
                        f"  [{topic.get('topic','')}] {topic.get('consistency','')} — {topic.get('assessment','')[:200]}"
                    )
            if val := data.get("margin_commentary"):
                parts.append(f"MARGIN COMMENTARY: {str(val)[:400]}")
            if val := data.get("revenue_growth_narrative"):
                parts.append(f"REVENUE NARRATIVE: {str(val)[:400]}")
            if pos := data.get("positive_signals"):
                parts.append(f"POSITIVE SIGNALS: {'; '.join(str(p) for p in pos[:5])}")
            if red := data.get("red_flags"):
                parts.append(f"RED FLAGS: {'; '.join(str(r) for r in red[:5])}")
            if val := data.get("overall_investment_signal"):
                parts.append(f"TRANSCRIPT SIGNAL: {str(val)[:200]}")
        elif skill_name == "shareholding_analysis":
            for field in [
                "promoter_trend", "fii_trend", "dii_trend",
                "ace_investor_activity", "concern_flags", "summary",
            ]:
                if val := data.get(field):
                    parts.append(f"{field}: {str(val)[:400]}")
        elif skill_name == "financial_snapshot":
            for field in [
                "revenue_trend", "margin_trend", "debt_situation",
                "cash_flow_quality", "key_ratios", "summary",
            ]:
                if val := data.get(field):
                    parts.append(f"{field}: {str(val)[:400]}")
        elif skill_name == "news_sentiment":
            for field in ["sentiment_summary", "key_themes", "red_flags"]:
                if val := data.get(field):
                    parts.append(f"{field}: {str(val)[:300]}")

    return "\n".join(parts)


def _build_valuation_markdown(
    symbol: str, company_name: str, data: dict, sector: str
) -> str:
    lines = [
        f"# Valuation Report — {company_name} ({symbol})",
        f"*Sector methodology: **{sector}***",
        "",
    ]

    signal = data.get("entry_signal", "N/A")
    cv_fair = data.get("current_vs_fair", "N/A")
    fv_low = data.get("fair_value_low", "N/A")
    fv_high = data.get("fair_value_high", "N/A")
    upside = data.get("upside_pct", "N/A")
    method = data.get("valuation_method", "N/A")
    cur_price = data.get("current_price", "N/A")

    lines += [
        f"## Verdict: {signal}  ·  {cv_fair}",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Current Price | ₹{cur_price} |",
        f"| **Fair Value Range** | **₹{fv_low} – ₹{fv_high}** |",
        f"| Upside / (Downside) | {upside}% |",
        f"| Primary Method | {method} |",
        "",
    ]

    if kpis := data.get("sector_kpis"):
        lines += ["## Sector KPIs", "| KPI | Value |", "|-----|-------|"]
        if isinstance(kpis, dict):
            for k, v in kpis.items():
                lines.append(f"| {k} | {v} |")
        lines.append("")

    if reasoning := data.get("reasoning"):
        lines += ["## Reasoning"]
        if q := reasoning.get("quantitative"):
            lines.append("### Quantitative")
            for item in q:
                lines.append(f"- {item}")
            lines.append("")
        if ql := reasoning.get("qualitative"):
            lines.append("### Qualitative (from available reports)")
            for item in ql:
                lines.append(f"- {item}")
            lines.append("")

    if assumptions := data.get("key_assumptions"):
        lines += ["## Key Assumptions"]
        for a in assumptions if isinstance(assumptions, list) else [assumptions]:
            lines.append(f"- {a}")
        lines.append("")

    if scenarios := data.get("scenarios"):
        lines += ["## Scenario Analysis"]
        for s in scenarios if isinstance(scenarios, list) else [scenarios]:
            name = s.get("name", "?")
            fv = s.get("fair_value", "N/A")
            prob = s.get("probability", "N/A")
            desc = s.get("description", "")
            lines += [f"### {name} — ₹{fv}  (prob: {prob})", desc, ""]

    if risks := data.get("key_risks_to_valuation"):
        lines += ["## Key Risks to Valuation"]
        for r in risks if isinstance(risks, list) else [risks]:
            lines.append(f"- {r}")
        lines.append("")

    if triggers := data.get("entry_exit_triggers"):
        lines += ["## Entry / Exit Triggers"]
        if buy := triggers.get("buy"):
            lines.append("**Add / Buy more when:**")
            for t in buy if isinstance(buy, list) else [buy]:
                lines.append(f"- {t}")
        if exit_ := triggers.get("exit"):
            lines.append("\n**Trim / Exit when:**")
            for t in exit_ if isinstance(exit_, list) else [exit_]:
                lines.append(f"- {t}")
        lines.append("")

    return "\n".join(lines)


def _fill_sector_prompt(template: str, company_name: str, symbol: str, company_data: str) -> str:
    out = template
    out = out.replace("{company_name}", company_name)
    out = out.replace("{symbol}", symbol)
    out = out.replace("{company_data}", company_data)
    # Un-escape doubled braces used in prompt templates for JSON schema examples
    out = out.replace("{{", "{").replace("}}", "}")
    return out


def run(company: dict, config: dict, skill_results: dict | None = None) -> dict:
    """
    Run valuation analysis for the company.

    Uses sector-specific expert prompt + gathered context → LLM call → structured JSON.

    Args:
        company: dict with at least 'symbol' and 'name' keys.
        config: LLM config dict (reserved for future use).
        skill_results: dict of previously run skill outputs (optional).
    """
    symbol = company["symbol"].upper()
    company_name = company.get("name", symbol)

    result: dict = {
        "skill_name": SKILL_NAME,
        "symbol": symbol,
        "status": "success",
        "data": {},
        "error": None,
    }

    try:
        if skill_results is None:
            skill_results = {}

        # ── Inject latest stock price if not already present ──────────────────
        if company.get("last_price") is None:
            price_info = get_latest_price(symbol)
            if price_info:
                company["last_price"] = price_info["price"]
                print(f"[{SKILL_NAME}][{symbol}] Latest price: ₹{price_info['price']} ({price_info['source']})")

        # ── Auto-fetch financials if not already in skill_results ──────────────
        if "financial_snapshot" not in skill_results:
            print(f"[{SKILL_NAME}][{symbol}] Fetching latest financials...")
            try:
                fin_result = financial_fetcher.run(company, config, refresh=False)
                skill_results["financial_snapshot"] = fin_result
            except Exception as _exc:
                print(f"[{SKILL_NAME}][{symbol}] financial_fetcher warning: {_exc}")

        # ── Auto-fetch concall transcripts (last 4 quarters) ──────────────────
        if "transcript_analysis" not in skill_results:
            print(f"[{SKILL_NAME}][{symbol}] Fetching concall transcripts (last 4 quarters)...")
            try:
                cf_result = concall_fetcher.run(company, config, limit=4)
                if cf_result.get("status") == "success":
                    print(f"[{SKILL_NAME}][{symbol}] Transcripts ready — running transcript analysis...")
                    ta_result = transcript_analysis.run(company, config)
                    skill_results["transcript_analysis"] = ta_result
                else:
                    print(f"[{SKILL_NAME}][{symbol}] concall_fetcher returned: {cf_result.get('error')}")
            except Exception as _exc:
                print(f"[{SKILL_NAME}][{symbol}] transcript pipeline warning: {_exc}")

        # Detect sector → load expert prompt template
        sector_name = _detect_sector_prompt(symbol, sector_hint=company.get("sector", ""))
        prompt_template = _load_sector_prompt_text(sector_name)
        print(f"[{SKILL_NAME}][{symbol}] Sector detected → {sector_name}")

        # Gather comprehensive company context
        context = _gather_context(symbol, company, skill_results)

        # Fill the sector prompt template with company-specific data
        filled_prompt = _fill_sector_prompt(
            prompt_template,
            company_name=str(company_name),
            symbol=str(symbol),
            company_data=context,
        )

        # Append reasoning requirement (not in sector templates but needed for reports)
        filled_prompt += _REASONING_APPENDIX

        # Call LLM for expert valuation
        print(f"[{SKILL_NAME}][{symbol}] Calling LLM for expert valuation...")
        llm_output = call_llm(filled_prompt, expect_json=True)

        if not isinstance(llm_output, dict):
            raise ValueError(f"LLM returned non-dict type: {type(llm_output)}")

        # Enrich with metadata
        llm_output["engine"] = "expert_llm_v1"
        llm_output["generated_at"] = datetime.now().isoformat(timespec="seconds")
        llm_output["sector_prompt_used"] = sector_name

        result["data"] = llm_output
        result["sector_prompt_used"] = sector_name

        # Save report (markdown + JSON)
        markdown = _build_valuation_markdown(symbol, company_name, llm_output, sector_name)
        save_report(symbol, SKILL_NAME, markdown, raw_json=llm_output)

        fv_low = llm_output.get("fair_value_low", "?")
        fv_high = llm_output.get("fair_value_high", "?")
        signal = llm_output.get("entry_signal", "?")
        upside = llm_output.get("upside_pct", "?")
        print(
            f"[{SKILL_NAME}][{symbol}] "
            f"Fair value: ₹{fv_low}–₹{fv_high}  |  Signal: {signal}  |  Upside: {upside}%"
        )

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        result["data"] = {}
        print(f"[{SKILL_NAME}][{symbol}] ERROR: {exc}")
        traceback.print_exc()

    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m skills.valuation_agent SYMBOL")
        print("Example: python -m skills.valuation_agent TARIL")
        sys.exit(1)

    import yaml

    _sym = sys.argv[1].upper()
    _watchlist_path = PROJECT_ROOT / "watchlist.yaml"
    _company: dict = {"symbol": _sym, "name": _sym, "sector": "Unknown"}

    if _watchlist_path.exists():
        _wl_data = yaml.safe_load(_watchlist_path.read_text())
        for _c in _wl_data.get("stocks", []):
            if _c.get("symbol", "").upper() == _sym:
                _company = _c
                break

    run(_company, {})
