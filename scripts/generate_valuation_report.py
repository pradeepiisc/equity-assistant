#!/usr/bin/env python3
"""
Generate comprehensive valuation report for ALL companies in data/companies/.
Reads financials, shareholding, insights, meta.json and produces:
  1. Individual company analysis summaries
  2. Master ranked report at data/valuation/master_ranking.md
"""

import json, os, re, sys
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent.parent
COMPANIES_DIR = BASE / "data" / "companies"
VALUATION_DIR = BASE / "data" / "valuation"
VALUATION_DIR.mkdir(parents=True, exist_ok=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _latest_file(folder: Path) -> Path | None:
    """Return the most recently modified file in folder."""
    if not folder.exists():
        return None
    files = sorted(folder.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _parse_number(s: str) -> float | None:
    """Parse a number string like '1,234' or '₹ 10,047 Cr.' or '16.5 %' into float."""
    if not s or s.strip() in ("", "—", "-", "N/A"):
        return None
    s = s.replace("₹", "").replace("Cr.", "").replace("Cr", "").replace("%", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _parse_key_ratios(text: str) -> dict:
    """Extract key ratios from the '## Key Ratios (Current)' section only."""
    ratios = {}
    mapping = {
        "Market Cap": "mcap",
        "Current Price": "cmp",
        "Stock P/E": "pe",
        "Book Value": "book_value",
        "Dividend Yield": "div_yield",
        "ROCE": "roce",
        "ROE": "roe",
        "Debt to equity": "de_ratio",
        "Pledged percentage": "pledged",
        "Sales growth 5Years": "sales_growth_5y",
        "Sales growth 3Years": "sales_growth_3y",
        "Profit Var 3Yrs": "profit_var_3y",
        "Profit Var 5Yrs": "profit_var_5y",
        "ROE 5Yr": "roe_5y",
        "ROE 3Yr": "roe_3y",
        "Current ratio": "current_ratio",
        "EVEBITDA": "ev_ebitda",
        "Int Coverage": "int_coverage",
        "Qtr Sales Var": "qtr_sales_var",
        "PEG Ratio": "peg",
        "Return on assets": "roa",
        "Enterprise Value": "ev",
        "High / Low": "high_low",
        "Face Value": "face_value",
    }
    # Only parse lines within the Key Ratios section (stop at first ---)
    in_section = False
    for line in text.split("\n"):
        if "## Key Ratios" in line:
            in_section = True
            continue
        if in_section and line.strip() == "---":
            break
        if not in_section:
            continue
        # Check longer labels first to avoid partial matches (e.g. "ROE 5Yr" vs "ROE")
        for label, key in sorted(mapping.items(), key=lambda x: -len(x[0])):
            if line.startswith(label):
                val_str = line[len(label):].strip()
                if key == "high_low":
                    parts = val_str.replace("₹", "").split("/")
                    if len(parts) == 2:
                        h = _parse_number(parts[0])
                        l = _parse_number(parts[1])
                        if h and l:
                            ratios["52w_high"] = h
                            ratios["52w_low"] = l
                else:
                    ratios[key] = _parse_number(val_str)
                break
    return ratios


def _parse_table_row(header_line: str, data_line: str) -> dict:
    """Parse a simple table row returning dict of header->value."""
    headers = [h.strip() for h in re.split(r"\s*\|\s*", header_line) if h.strip()]
    values = [v.strip() for v in re.split(r"\s*\|\s*", data_line) if v.strip()]
    # first col is label, rest are periods
    return dict(zip(headers, values))


def _parse_annual_pl(text: str) -> dict:
    """Extract annual P&L data. Returns dict with latest year and TTM figures."""
    result = {}
    section = ""
    lines = text.split("\n")
    in_annual = False
    header_periods = []
    for i, line in enumerate(lines):
        if "## Annual Profit & Loss" in line:
            in_annual = True
            continue
        if in_annual and line.startswith("---"):
            break
        if in_annual and line.startswith("|") and not header_periods:
            header_periods = [p.strip() for p in line.split("|") if p.strip()]
            continue
        if in_annual and header_periods:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if not parts:
                continue
            label = parts[0].replace("+", "").strip()
            values = parts[1:] if len(parts) > 1 else []
            if label == "Sales":
                result["revenue_history"] = values
                if values:
                    result["revenue_latest_annual"] = _parse_number(values[-2] if len(values) >= 2 and "TTM" in header_periods else values[-1])
                    if "TTM" in header_periods and len(values) >= 1:
                        result["revenue_ttm"] = _parse_number(values[-1])
            elif label == "OPM %":
                result["opm_history"] = values
                if values:
                    result["opm_latest"] = values[-1] if values else None
            elif label == "Net Profit":
                result["np_history"] = values
                if values:
                    result["np_latest_annual"] = _parse_number(values[-2] if len(values) >= 2 and "TTM" in header_periods else values[-1])
                    if "TTM" in header_periods and len(values) >= 1:
                        result["np_ttm"] = _parse_number(values[-1])
            elif label == "EPS in Rs":
                result["eps_history"] = values
                if values:
                    result["eps_ttm"] = _parse_number(values[-1]) if "TTM" in header_periods else _parse_number(values[-1])
            elif label == "Dividend Payout %":
                result["div_payout_history"] = values
    result["annual_periods"] = header_periods
    return result


def _parse_quarterly(text: str) -> dict:
    """Extract latest quarterly trends."""
    result = {}
    lines = text.split("\n")
    in_qtr = False
    header_periods = []
    for line in lines:
        if "## Quarterly Results" in line:
            in_qtr = True
            continue
        if in_qtr and line.startswith("---"):
            break
        if in_qtr and line.startswith("|") and not header_periods:
            header_periods = [p.strip() for p in line.split("|") if p.strip()]
            continue
        if in_qtr and header_periods:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if not parts:
                continue
            label = parts[0].replace("+", "").strip()
            values = parts[1:]
            if label == "Sales" and values:
                result["qtr_revenue"] = values
            elif label == "OPM %" and values:
                result["qtr_opm"] = values
            elif label == "Net Profit" and values:
                result["qtr_np"] = values
            elif label == "EPS in Rs" and values:
                result["qtr_eps"] = values
    result["qtr_periods"] = header_periods
    n = len(header_periods)
    if n >= 2:
        result["latest_qtr"] = header_periods[-1]
        result["n_quarters"] = n
    return result


def _parse_balance_sheet(text: str) -> dict:
    """Extract key balance sheet items."""
    result = {}
    lines = text.split("\n")
    in_bs = False
    header_periods = []
    for line in lines:
        if "## Balance Sheet" in line:
            in_bs = True
            continue
        if in_bs and line.startswith("---"):
            break
        if in_bs and line.startswith("|") and not header_periods:
            header_periods = [p.strip() for p in line.split("|") if p.strip()]
            continue
        if in_bs and header_periods:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if not parts:
                continue
            label = parts[0].replace("+", "").strip()
            values = parts[1:]
            if values:
                latest = _parse_number(values[-1])
                if label == "Borrowings":
                    result["borrowings"] = latest
                elif label == "Reserves":
                    result["reserves"] = latest
                elif label == "Total Assets":
                    result["total_assets"] = latest
                elif label == "Equity Capital":
                    result["equity_capital"] = latest
    return result


def _parse_cash_flow(text: str) -> dict:
    """Extract cash flow data."""
    result = {}
    lines = text.split("\n")
    in_cf = False
    header_periods = []
    for line in lines:
        if "## Cash Flow Statement" in line:
            in_cf = True
            continue
        if in_cf and line.startswith("---"):
            break
        if in_cf and line.startswith("|") and not header_periods:
            header_periods = [p.strip() for p in line.split("|") if p.strip()]
            continue
        if in_cf and header_periods:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if not parts:
                continue
            label = parts[0].replace("+", "").strip()
            values = parts[1:]
            if values:
                latest = _parse_number(values[-1])
                if label == "Cash from Operating Activity":
                    result["ocf"] = latest
                elif label == "Free Cash Flow":
                    result["fcf"] = latest
                elif label == "CFO/OP":
                    result["cfo_op"] = values[-1] if values else None
    return result


def _parse_historical_ratios(text: str) -> dict:
    """Extract historical ratio trends."""
    result = {}
    lines = text.split("\n")
    in_hr = False
    header_periods = []
    for line in lines:
        if "## Historical Key Ratios" in line:
            in_hr = True
            continue
        if in_hr and line.startswith("---"):
            break
        if in_hr and line.startswith("|") and not header_periods:
            header_periods = [p.strip() for p in line.split("|") if p.strip()]
            continue
        if in_hr and header_periods:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if not parts:
                continue
            label = parts[0].replace("+", "").strip()
            values = parts[1:]
            if values:
                if label == "ROCE %":
                    result["roce_history"] = values
                elif label == "Cash Conversion Cycle":
                    result["ccc_latest"] = _parse_number(values[-1])
                elif label == "Working Capital Days":
                    result["wc_days_latest"] = _parse_number(values[-1])
                elif label == "Debtor Days":
                    result["debtor_days_latest"] = _parse_number(values[-1])
    return result


def _normalize_investor_name_valuation(name: str) -> str:
    """Normalize investor name for fuzzy matching.
    Handles: Pvt/Private, Ltd/Limited, Ins/Institute, camelCase, punctuation."""
    # Split camelCase before lowering
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    s = s.lower().strip()
    s = re.sub(r"\bpvt\b\.?", "private", s)
    s = re.sub(r"\bltd\b\.?", "limited", s)
    s = re.sub(r"\bco\b\.?", "company", s)
    s = re.sub(r"\bcorp\b\.?", "corporation", s)
    s = re.sub(r"\bins\b\.?", "institute", s)
    s = re.sub(r"\btechno\b", "technology", s)
    s = re.sub(r"['\"\.\-,&\(\)]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _load_investor_watchlist() -> list[str]:
    """Load all tracked investor names from investor_watchlist.yaml."""
    wl_path = Path(__file__).parent.parent / "investor_watchlist.yaml"
    if not wl_path.exists():
        return []
    import yaml
    data = yaml.safe_load(wl_path.read_text()) or {}
    tracked = data.get("tracked_investors", data)
    names = []
    for group in tracked.values():
        if isinstance(group, list):
            names.extend(group)
    return names


_INVESTOR_WATCHLIST_CACHE: list[str] | None = None

def _get_investor_watchlist() -> list[str]:
    global _INVESTOR_WATCHLIST_CACHE
    if _INVESTOR_WATCHLIST_CACHE is None:
        _INVESTOR_WATCHLIST_CACHE = _load_investor_watchlist()
    return _INVESTOR_WATCHLIST_CACHE


def _match_individual_holders_against_watchlist(text: str) -> list[str]:
    """Scan Individual Holder Detail sections and match against investor_watchlist.yaml."""
    tracked = _get_investor_watchlist()
    if not tracked:
        return []
    norm_map = {_normalize_investor_name_valuation(n): n for n in tracked}
    from difflib import SequenceMatcher

    def _fuzzy_match_val(inv_norm: str) -> str | None:
        for norm, orig in norm_map.items():
            if norm in inv_norm or inv_norm in norm:
                return orig
        for norm, orig in norm_map.items():
            if SequenceMatcher(None, inv_norm, norm).ratio() >= 0.85:
                return orig
        return None

    found = []
    # Parse all investor rows from Individual Holder Detail tables
    in_individual = False
    for line in text.split("\n"):
        if "## Individual Holder Detail" in line:
            in_individual = True
            continue
        if in_individual and line.startswith("## ") and "Individual" not in line:
            break
        if in_individual and line.startswith("|") and not line.startswith("|---") and not line.startswith("| Investor"):
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if parts:
                inv_name = parts[0]
                inv_norm = _normalize_investor_name_valuation(inv_name)
                matched = _fuzzy_match_val(inv_norm)
                if matched and inv_name not in found:
                    found.append(inv_name)
    return found


def _parse_shareholding(text: str) -> dict:
    """Extract shareholding data."""
    result = {}
    lines = text.split("\n")
    
    # Extract quarters covered
    for line in lines:
        if line.startswith("## Quarters Covered"):
            continue
        if "Quarters Covered" in lines[lines.index(line)-1] if lines.index(line) > 0 else False:
            result["quarters"] = line.strip()
            break
    
    # Parse main table
    in_table = False
    for line in lines:
        if "| Category |" in line:
            in_table = True
            # count quarters
            header_parts = [p.strip() for p in line.split("|") if p.strip()]
            result["n_quarters_sh"] = len(header_parts) - 1  # minus Category col
            continue
        if in_table and line.startswith("|---"):
            continue
        if in_table and line.startswith("|"):
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if not parts:
                continue
            label = parts[0].replace("+", "").strip()
            values = [p.replace("%", "").strip() for p in parts[1:]]
            
            if label == "Promoters":
                if values:
                    result["promoter_latest"] = _parse_number(values[-1])
                    result["promoter_first"] = _parse_number(values[0])
                    if len(values) >= 2:
                        result["promoter_prev"] = _parse_number(values[-2])
            elif label == "FIIs":
                if values:
                    result["fii_latest"] = _parse_number(values[-1])
                    result["fii_first"] = _parse_number(values[0])
            elif label == "DIIs":
                if values:
                    result["dii_latest"] = _parse_number(values[-1])
                    result["dii_first"] = _parse_number(values[0])
            elif label == "Public":
                if values:
                    result["public_latest"] = _parse_number(values[-1])
            elif label == "No. of Shareholders":
                if values:
                    result["shareholders"] = values[-1]
        elif in_table and not line.startswith("|"):
            in_table = False
    
    # Extract tracked investor alerts from the Tracked Investor Alerts section
    alerts = []
    in_alerts = False
    for line in lines:
        if "## Tracked Investor Alerts" in line:
            in_alerts = True
            continue
        if in_alerts and line.startswith("- "):
            # Extract investor name
            m = re.search(r"\*\*(.+?)\*\*", line)
            if m:
                alerts.append(m.group(1))
    
    # If no alerts from the dedicated section, scan Individual Holder Detail
    # directly against investor_watchlist.yaml (handles name mismatch cases)
    if not alerts:
        alerts = _match_individual_holders_against_watchlist(text)
    
    result["tracked_investors"] = alerts
    
    # Extract key DII holders
    dii_holders = []
    in_dii = False
    for line in lines:
        if "### DIIs (Individual)" in line:
            in_dii = True
            continue
        if in_dii and line.startswith("###"):
            break
        if in_dii and line.startswith("|") and not line.startswith("|---") and not line.startswith("| Investor"):
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if parts:
                dii_holders.append(parts[0])
    result["dii_holders"] = dii_holders[:5]  # top 5
    
    return result


def _compute_revenue_cagr(rev_history: list, periods: list) -> float | None:
    """Compute 3-year revenue CAGR from annual data."""
    if not rev_history or len(rev_history) < 4:
        return None
    # last annual figure (before TTM if present)
    has_ttm = "TTM" in periods if periods else False
    idx_latest = -2 if has_ttm and len(rev_history) >= 2 else -1
    idx_3y_back = idx_latest - 3
    if abs(idx_3y_back) > len(rev_history):
        return None
    latest = _parse_number(rev_history[idx_latest])
    early = _parse_number(rev_history[idx_3y_back])
    if not latest or not early or early <= 0:
        return None
    try:
        cagr = ((latest / early) ** (1/3) - 1) * 100
        return round(cagr, 1)
    except:
        return None


def _compute_profit_cagr(np_history: list, periods: list) -> float | None:
    """Compute 3-year profit CAGR."""
    if not np_history or len(np_history) < 4:
        return None
    has_ttm = "TTM" in periods if periods else False
    idx_latest = -2 if has_ttm and len(np_history) >= 2 else -1
    idx_3y_back = idx_latest - 3
    if abs(idx_3y_back) > len(np_history):
        return None
    latest = _parse_number(np_history[idx_latest])
    early = _parse_number(np_history[idx_3y_back])
    if not latest or not early or early <= 0 or latest <= 0:
        return None
    try:
        cagr = ((latest / early) ** (1/3) - 1) * 100
        return round(cagr, 1)
    except:
        return None


def _qtr_trend(values: list) -> str:
    """Determine if last 3-4 quarters show improving or declining trend."""
    nums = []
    for v in (values or [])[-4:]:
        n = _parse_number(v)
        if n is not None:
            nums.append(n)
    if len(nums) < 3:
        return "insufficient_data"
    if nums[-1] > nums[-2] > nums[-3]:
        return "improving"
    if nums[-1] < nums[-2] < nums[-3]:
        return "declining"
    if nums[-1] > nums[0]:
        return "mixed_up"
    return "mixed_down"


# ── Scoring System ────────────────────────────────────────────────────────────

def _score_company(ratios: dict, pl: dict, qtr: dict, bs: dict, cf: dict, hr: dict, sh: dict,
                    rev_cagr_3y: float | None = None, profit_cagr_3y: float | None = None) -> dict:
    """Score a company on multiple dimensions (0-5 each). Returns scores dict."""
    scores = {}
    
    # 1. Growth Score (revenue + profit trajectory)
    growth = 0
    # Use Key Ratio values first, then computed CAGRs as fallback
    sg3 = ratios.get("sales_growth_3y") or rev_cagr_3y
    sg5 = ratios.get("sales_growth_5y")
    pv3 = ratios.get("profit_var_3y") or profit_cagr_3y
    if sg3 is not None:
        if sg3 > 30: growth += 2.5
        elif sg3 > 20: growth += 2
        elif sg3 > 10: growth += 1.5
        elif sg3 > 0: growth += 0.5
    if pv3 is not None:
        if pv3 > 30: growth += 2.5
        elif pv3 > 15: growth += 2
        elif pv3 > 0: growth += 1
        elif pv3 < -10: growth -= 0.5
    qtr_rev_trend = _qtr_trend(qtr.get("qtr_revenue"))
    if qtr_rev_trend == "improving": growth += 0.5
    elif qtr_rev_trend == "declining": growth -= 0.5
    scores["growth"] = max(0, min(5, growth))
    
    # 2. Profitability Score (ROCE, ROE, OPM)
    prof = 0
    roce = ratios.get("roce")
    roe = ratios.get("roe")
    if roce is not None:
        if roce > 25: prof += 2
        elif roce > 15: prof += 1.5
        elif roce > 10: prof += 1
        elif roce > 0: prof += 0.5
    if roe is not None:
        if roe > 20: prof += 2
        elif roe > 15: prof += 1.5
        elif roe > 10: prof += 1
        elif roe > 0: prof += 0.5
    # OPM trend
    opm_vals = pl.get("opm_history", [])
    if opm_vals:
        latest_opm = opm_vals[-1].replace("%", "").strip()
        try:
            opm_num = float(latest_opm)
            if opm_num > 20: prof += 1
            elif opm_num > 10: prof += 0.5
            elif opm_num < 0: prof -= 1
        except: pass
    scores["profitability"] = max(0, min(5, prof))
    
    # 3. Valuation Score (P/E, PEG, P/BV, EV/EBITDA)
    val = 2.5  # neutral start
    pe = ratios.get("pe")
    peg = ratios.get("peg")
    bv = ratios.get("book_value")
    cmp = ratios.get("cmp")
    if pe is not None:
        if pe < 0: val -= 1  # loss making
        elif pe < 10: val += 1.5
        elif pe < 15: val += 1
        elif pe < 20: val += 0.5
        elif pe > 50: val -= 1
        elif pe > 30: val -= 0.5
    if peg is not None and peg > 0:
        if peg < 0.5: val += 1
        elif peg < 1: val += 0.5
        elif peg > 2: val -= 0.5
    if bv and cmp and bv > 0:
        pb = cmp / bv
        if pb < 1: val += 0.5
        elif pb > 5: val -= 0.5
    scores["valuation"] = max(0, min(5, val))
    
    # 4. Balance Sheet Score (debt, cash flow)
    bss = 2.5
    de = ratios.get("de_ratio")
    cr = ratios.get("current_ratio")
    pledged = ratios.get("pledged")
    if de is not None:
        if de == 0: bss += 1.5
        elif de < 0.3: bss += 1
        elif de < 0.5: bss += 0.5
        elif de > 1.5: bss -= 1
        elif de > 1: bss -= 0.5
    if cr is not None:
        if cr > 2: bss += 0.5
        elif cr < 1: bss -= 0.5
    if pledged is not None and pledged > 10: bss -= 1
    ocf = cf.get("ocf")
    fcf = cf.get("fcf")
    if ocf is not None:
        if ocf > 0: bss += 0.5
        else: bss -= 0.5
    if fcf is not None:
        if fcf > 0: bss += 0.5
        else: bss -= 0.25
    scores["balance_sheet"] = max(0, min(5, bss))
    
    # 5. Shareholding Score
    shs = 2.5
    prom = sh.get("promoter_latest")
    prom_first = sh.get("promoter_first")
    fii = sh.get("fii_latest")
    fii_first = sh.get("fii_first")
    tracked = sh.get("tracked_investors", [])
    if prom is not None:
        if prom > 60: shs += 0.5
        elif prom < 30: shs -= 0.5
    if prom and prom_first:
        if prom > prom_first: shs += 0.5  # promoter increasing
        elif prom < prom_first - 5: shs -= 1  # significant reduction
    if fii and fii_first:
        if fii > fii_first: shs += 0.5  # FII increasing
    if tracked:
        shs += min(1, len(tracked) * 0.5)  # smart money presence
    scores["shareholding"] = max(0, min(5, shs))
    
    # Composite
    weights = {"growth": 0.25, "profitability": 0.20, "valuation": 0.20, "balance_sheet": 0.15, "shareholding": 0.20}
    composite = sum(scores[k] * weights[k] for k in weights)
    scores["composite"] = round(composite, 2)
    
    return scores


def _rating_label(composite: float) -> str:
    if composite >= 4.0: return "STRONG BUY"
    if composite >= 3.5: return "BUY"
    if composite >= 3.0: return "ACCUMULATE"
    if composite >= 2.5: return "HOLD"
    if composite >= 2.0: return "WATCH"
    if composite >= 1.5: return "REDUCE"
    return "AVOID"


def _stars(score: float) -> str:
    full = int(score)
    half = 1 if (score - full) >= 0.4 else 0
    return "⭐" * full + ("½" if half else "")


# ── Main Processing ───────────────────────────────────────────────────────────

def analyze_company(sym_dir: Path) -> dict | None:
    """Analyze a single company. Returns analysis dict or None if insufficient data."""
    symbol = sym_dir.name
    
    # Load meta
    meta_path = sym_dir / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    else:
        meta = {"symbol": symbol, "name": symbol, "sector": "Unknown"}
    
    # Load financials
    fin_file = _latest_file(sym_dir / "financials")
    if not fin_file:
        return None  # can't analyze without financials
    fin_text = fin_file.read_text(encoding="utf-8")
    
    # Load shareholding
    sh_file = _latest_file(sym_dir / "shareholding")
    sh_text = sh_file.read_text(encoding="utf-8") if sh_file else ""
    
    # Parse all sections
    ratios = _parse_key_ratios(fin_text)
    pl = _parse_annual_pl(fin_text)
    qtr = _parse_quarterly(fin_text)
    bs = _parse_balance_sheet(fin_text)
    cf = _parse_cash_flow(fin_text)
    hr = _parse_historical_ratios(fin_text)
    sh = _parse_shareholding(sh_text) if sh_text else {}
    
    # Skip if no market cap data
    if not ratios.get("mcap") and not ratios.get("cmp"):
        return None
    
    # Compute derived metrics
    rev_cagr_3y = _compute_revenue_cagr(pl.get("revenue_history"), pl.get("annual_periods"))
    profit_cagr_3y = _compute_profit_cagr(pl.get("np_history"), pl.get("annual_periods"))
    
    # Score
    scores = _score_company(ratios, pl, qtr, bs, cf, hr, sh,
                            rev_cagr_3y=rev_cagr_3y, profit_cagr_3y=profit_cagr_3y)
    rating = _rating_label(scores["composite"])
    
    # Quarterly trend
    rev_trend = _qtr_trend(qtr.get("qtr_revenue"))
    np_trend = _qtr_trend(qtr.get("qtr_np"))
    
    # Promoter trend
    prom_trend = "→"
    if sh.get("promoter_latest") and sh.get("promoter_first"):
        diff = sh["promoter_latest"] - sh["promoter_first"]
        if diff > 1: prom_trend = "⬆"
        elif diff < -1: prom_trend = "⬇"
    
    # FII trend
    fii_trend = "→"
    if sh.get("fii_latest") and sh.get("fii_first"):
        diff = sh["fii_latest"] - sh["fii_first"]
        if diff > 1: fii_trend = "⬆"
        elif diff < -1: fii_trend = "⬇"
    
    # Price vs 52w
    price_position = ""
    if ratios.get("cmp") and ratios.get("52w_high") and ratios.get("52w_low"):
        high = ratios["52w_high"]
        low = ratios["52w_low"]
        cmp = ratios["cmp"]
        if high > low:
            pct = (cmp - low) / (high - low) * 100
            if pct > 80: price_position = "Near 52w High"
            elif pct < 20: price_position = "Near 52w Low"
            else: price_position = f"{pct:.0f}% range"
    
    return {
        "symbol": symbol,
        "name": meta.get("name", symbol),
        "sector": meta.get("sector", ""),
        "mcap": ratios.get("mcap"),
        "cmp": ratios.get("cmp"),
        "pe": ratios.get("pe"),
        "peg": ratios.get("peg"),
        "roce": ratios.get("roce"),
        "roe": ratios.get("roe"),
        "de_ratio": ratios.get("de_ratio"),
        "div_yield": ratios.get("div_yield"),
        "ev_ebitda": ratios.get("ev_ebitda"),
        "sales_growth_3y": ratios.get("sales_growth_3y"),
        "profit_var_3y": ratios.get("profit_var_3y"),
        "current_ratio": ratios.get("current_ratio"),
        "pledged": ratios.get("pledged"),
        "book_value": ratios.get("book_value"),
        "rev_cagr_3y": rev_cagr_3y,
        "profit_cagr_3y": profit_cagr_3y,
        "revenue_ttm": pl.get("revenue_ttm") or pl.get("revenue_latest_annual"),
        "np_ttm": pl.get("np_ttm") or pl.get("np_latest_annual"),
        "eps_ttm": pl.get("eps_ttm"),
        "opm_latest": pl.get("opm_latest"),
        "borrowings": bs.get("borrowings"),
        "reserves": bs.get("reserves"),
        "total_assets": bs.get("total_assets"),
        "ocf": cf.get("ocf"),
        "fcf": cf.get("fcf"),
        "cfo_op": cf.get("cfo_op"),
        "ccc": hr.get("ccc_latest"),
        "wc_days": hr.get("wc_days_latest"),
        "promoter": sh.get("promoter_latest"),
        "promoter_trend": prom_trend,
        "fii": sh.get("fii_latest"),
        "fii_trend": fii_trend,
        "dii": sh.get("dii_latest"),
        "public": sh.get("public_latest"),
        "shareholders": sh.get("shareholders"),
        "tracked_investors": sh.get("tracked_investors", []),
        "dii_holders": sh.get("dii_holders", []),
        "price_position": price_position,
        "qtr_rev_trend": rev_trend,
        "qtr_np_trend": np_trend,
        "n_quarters": qtr.get("n_quarters", 0),
        "scores": scores,
        "rating": rating,
    }


def _fmt(val, fmt=".1f", suffix="", prefix="", na="—"):
    if val is None:
        return na
    try:
        return f"{prefix}{val:{fmt}}{suffix}"
    except:
        return str(val)


def _fmt_cr(val):
    if val is None: return "—"
    if abs(val) >= 1000:
        return f"₹{val:,.0f} Cr"
    return f"₹{val:,.1f} Cr"


def generate_individual_report(c: dict, sym_dir: Path):
    """Generate individual watchlist_analysis.md for a company."""
    reports_dir = sym_dir / "reports"
    reports_dir.mkdir(exist_ok=True)
    out_path = reports_dir / "valuation_summary.md"
    
    s = c["scores"]
    lines = [
        f"# Valuation Summary — {c['name']} ({c['symbol']})",
        f"*Generated: {datetime.now().strftime('%d %b %Y')} | Automated Analysis*\n",
        f"## Snapshot",
        f"- **Sector:** {c['sector'] or 'Unknown'}",
        f"- **Market Cap:** {_fmt_cr(c['mcap'])} | **CMP:** ₹{_fmt(c['cmp'], '.1f')} | **P/E:** {_fmt(c['pe'], '.1f')}",
        f"- **ROCE:** {_fmt(c['roce'], '.1f', '%')} | **ROE:** {_fmt(c['roe'], '.1f', '%')} | **D/E:** {_fmt(c['de_ratio'], '.2f')}",
        f"- **PEG:** {_fmt(c['peg'], '.2f')} | **EV/EBITDA:** {_fmt(c['ev_ebitda'], '.1f')} | **Div Yield:** {_fmt(c['div_yield'], '.2f', '%')}",
        f"- **Revenue TTM:** {_fmt_cr(c['revenue_ttm'])} | **Net Profit TTM:** {_fmt_cr(c['np_ttm'])}",
        f"- **Sales Growth 3Y:** {_fmt(c['sales_growth_3y'], '.1f', '%')} | **Profit Var 3Y:** {_fmt(c['profit_var_3y'], '.1f', '%')}",
        "",
        f"## Balance Sheet",
        f"- **Borrowings:** {_fmt_cr(c['borrowings'])} | **Reserves:** {_fmt_cr(c['reserves'])}",
        f"- **OCF:** {_fmt_cr(c['ocf'])} | **FCF:** {_fmt_cr(c['fcf'])} | **CFO/OP:** {c.get('cfo_op') or '—'}",
        f"- **Current Ratio:** {_fmt(c['current_ratio'], '.2f')} | **CCC:** {_fmt(c['ccc'], '.0f', ' days')}",
        "",
        f"## Shareholding",
        f"- **Promoter:** {_fmt(c['promoter'], '.2f', '%')} {c['promoter_trend']} | **FII:** {_fmt(c['fii'], '.2f', '%')} {c['fii_trend']}",
        f"- **DII:** {_fmt(c['dii'], '.2f', '%')} | **Public:** {_fmt(c['public'], '.2f', '%')}",
        f"- **Shareholders:** {c.get('shareholders') or '—'}",
    ]
    if c["tracked_investors"]:
        lines.append(f"- **Smart Money:** {', '.join(c['tracked_investors'])}")
    if c["dii_holders"]:
        lines.append(f"- **Key DIIs:** {', '.join(c['dii_holders'][:3])}")
    
    lines += [
        "",
        f"## Quarterly Trend",
        f"- **Revenue Trend:** {c['qtr_rev_trend']} | **Profit Trend:** {c['qtr_np_trend']}",
        f"- **Price Position:** {c['price_position'] or '—'}",
        "",
        f"## Scores",
        f"| Dimension | Score | Stars |",
        f"|---|---|---|",
        f"| Growth | {s['growth']:.1f}/5 | {_stars(s['growth'])} |",
        f"| Profitability | {s['profitability']:.1f}/5 | {_stars(s['profitability'])} |",
        f"| Valuation | {s['valuation']:.1f}/5 | {_stars(s['valuation'])} |",
        f"| Balance Sheet | {s['balance_sheet']:.1f}/5 | {_stars(s['balance_sheet'])} |",
        f"| Shareholding | {s['shareholding']:.1f}/5 | {_stars(s['shareholding'])} |",
        f"| **Composite** | **{s['composite']:.2f}/5** | |",
        "",
        f"## Rating: **{c['rating']}**",
    ]
    
    out_path.write_text("\n".join(lines), encoding="utf-8")


def generate_master_ranking(companies: list[dict]):
    """Generate the master ranking report."""
    # Sort by composite score descending
    ranked = sorted(companies, key=lambda c: c["scores"]["composite"], reverse=True)
    
    lines = [
        "# Master Valuation Ranking — All Portfolio & Watchlist Companies",
        f"*Generated: {datetime.now().strftime('%d %b %Y %H:%M')} | {len(ranked)} companies analyzed*\n",
        "---\n",
        "## Scoring Methodology",
        "Each company scored on 5 dimensions (0-5 scale):",
        "- **Growth (25%):** Sales/profit growth rates, quarterly trend",
        "- **Profitability (20%):** ROCE, ROE, OPM",
        "- **Valuation (20%):** P/E, PEG, P/BV, EV/EBITDA",
        "- **Balance Sheet (15%):** Debt/equity, current ratio, cash flows, pledging",
        "- **Shareholding (20%):** Promoter stability, FII/DII trends, smart-money presence",
        "",
        "---\n",
    ]
    
    # ── Tier breakdown ──
    tiers = {
        "STRONG BUY": [],
        "BUY": [],
        "ACCUMULATE": [],
        "HOLD": [],
        "WATCH": [],
        "REDUCE": [],
        "AVOID": [],
    }
    for c in ranked:
        tiers[c["rating"]].append(c)
    
    # ── Summary table ──
    lines.append("## Complete Ranking\n")
    lines.append("| Rank | Symbol | Company | Sector | Mcap (Cr) | P/E | ROCE% | ROE% | D/E | Growth 3Y | Prom% | FII Trend | Score | Rating |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    
    for i, c in enumerate(ranked, 1):
        s = c["scores"]
        sector_short = (c["sector"] or "—")[:30]
        mcap_str = f"{c['mcap']:,.0f}" if c['mcap'] else "—"
        pe_str = f"{c['pe']:.1f}" if c['pe'] is not None else "—"
        roce_str = f"{c['roce']:.1f}" if c['roce'] is not None else "—"
        roe_str = f"{c['roe']:.1f}" if c['roe'] is not None else "—"
        de_str = f"{c['de_ratio']:.2f}" if c['de_ratio'] is not None else "—"
        sg3_str = f"{c['sales_growth_3y']:.1f}%" if c['sales_growth_3y'] is not None else "—"
        prom_str = f"{c['promoter']:.1f}" if c['promoter'] is not None else "—"
        fii_str = c['fii_trend']
        lines.append(f"| {i} | {c['symbol']} | {c['name'][:35]} | {sector_short} | {mcap_str} | {pe_str} | {roce_str} | {roe_str} | {de_str} | {sg3_str} | {prom_str} | {fii_str} | **{s['composite']:.2f}** | {c['rating']} |")
    
    lines.append("")
    
    # ── Tier summaries ──
    lines.append("---\n")
    lines.append("## Tier Breakdown\n")
    
    tier_emoji = {"STRONG BUY": "🟢", "BUY": "🔵", "ACCUMULATE": "🟡", "HOLD": "⚪", "WATCH": "🟠", "REDUCE": "🔴", "AVOID": "⛔"}
    
    for tier_name, tier_companies in tiers.items():
        if not tier_companies:
            continue
        emoji = tier_emoji.get(tier_name, "")
        lines.append(f"### {emoji} {tier_name} ({len(tier_companies)} companies)\n")
        for c in tier_companies:
            s = c["scores"]
            smart = ", ".join(c["tracked_investors"][:3]) if c["tracked_investors"] else "—"
            lines.append(f"**{c['name']} ({c['symbol']})** — Score: {s['composite']:.2f}")
            lines.append(f"  - Mcap: {_fmt_cr(c['mcap'])} | P/E: {_fmt(c['pe'],'.1f')} | ROCE: {_fmt(c['roce'],'.1f','%')} | D/E: {_fmt(c['de_ratio'],'.2f')}")
            lines.append(f"  - Growth: {s['growth']:.1f} | Prof: {s['profitability']:.1f} | Val: {s['valuation']:.1f} | BS: {s['balance_sheet']:.1f} | SH: {s['shareholding']:.1f}")
            lines.append(f"  - Promoter: {_fmt(c['promoter'],'.1f','%')} {c['promoter_trend']} | FII: {_fmt(c['fii'],'.1f','%')} {c['fii_trend']} | Smart Money: {smart}")
            lines.append(f"  - Qtr Trend: Rev {c['qtr_rev_trend']} / NP {c['qtr_np_trend']} | Price: {c['price_position'] or '—'}")
            lines.append("")
    
    # ── Statistics ──
    lines.append("---\n")
    lines.append("## Portfolio Statistics\n")
    
    scores_list = [c["scores"]["composite"] for c in ranked]
    avg_score = sum(scores_list) / len(scores_list) if scores_list else 0
    median_score = sorted(scores_list)[len(scores_list)//2] if scores_list else 0
    
    lines.append(f"- **Total Companies Analyzed:** {len(ranked)}")
    lines.append(f"- **Average Score:** {avg_score:.2f}")
    lines.append(f"- **Median Score:** {median_score:.2f}")
    lines.append(f"- **Strong Buy + Buy:** {len(tiers['STRONG BUY']) + len(tiers['BUY'])}")
    lines.append(f"- **Avoid + Reduce:** {len(tiers['AVOID']) + len(tiers['REDUCE'])}")
    
    # Sector distribution of top-rated
    top_sectors = {}
    for c in ranked[:20]:
        sec = c["sector"] or "Unknown"
        top_sectors[sec] = top_sectors.get(sec, 0) + 1
    if top_sectors:
        lines.append("\n### Top 20 — Sector Distribution")
        for sec, count in sorted(top_sectors.items(), key=lambda x: -x[1]):
            lines.append(f"- **{sec}:** {count}")
    
    lines.append(f"\n---\n*Disclaimer: Automated scoring based on quantitative data. Not investment advice.*")
    
    # Write
    out_path = VALUATION_DIR / "master_ranking.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ Master ranking saved to {out_path}")
    
    # Also save JSON for programmatic use
    json_data = []
    for c in ranked:
        entry = {k: v for k, v in c.items() if k not in ("scores",)}
        entry["scores"] = c["scores"]
        json_data.append(entry)
    json_path = VALUATION_DIR / "master_ranking.json"
    json_path.write_text(json.dumps(json_data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"✅ JSON data saved to {json_path}")
    
    return ranked


# ── Entry Point ───────────────────────────────────────────────────────────────

def main():
    print(f"🔍 Scanning {COMPANIES_DIR} ...")
    sym_dirs = sorted([d for d in COMPANIES_DIR.iterdir() if d.is_dir()])
    print(f"   Found {len(sym_dirs)} company directories\n")
    
    results = []
    skipped = []
    errors = []
    
    for sym_dir in sym_dirs:
        symbol = sym_dir.name
        try:
            analysis = analyze_company(sym_dir)
            if analysis:
                results.append(analysis)
                generate_individual_report(analysis, sym_dir)
                print(f"  ✅ {symbol:20s} — {analysis['rating']:12s} (score: {analysis['scores']['composite']:.2f})")
            else:
                skipped.append(symbol)
                print(f"  ⏭️  {symbol:20s} — skipped (no financials)")
        except Exception as e:
            errors.append((symbol, str(e)))
            print(f"  ❌ {symbol:20s} — error: {e}")
    
    print(f"\n📊 Analyzed: {len(results)} | Skipped: {len(skipped)} | Errors: {len(errors)}")
    
    if errors:
        print("\n⚠️ Errors:")
        for sym, err in errors:
            print(f"   {sym}: {err}")
    
    # Deduplicate: if two symbols share the exact same company name, keep higher Mcap
    seen_names: dict[str, int] = {}
    deduped = []
    for c in results:
        name = c["name"]
        if name in seen_names:
            idx = seen_names[name]
            existing = deduped[idx]
            if (c.get("mcap") or 0) > (existing.get("mcap") or 0):
                print(f"  ⚠️  Dedup: {c['symbol']} replaces {existing['symbol']} (same name: {name})")
                deduped[idx] = c
            else:
                print(f"  ⚠️  Dedup: {c['symbol']} dropped (same name as {existing['symbol']}: {name})")
        else:
            seen_names[name] = len(deduped)
            deduped.append(c)
    if len(deduped) < len(results):
        print(f"  ℹ️  Deduplicated: {len(results)} → {len(deduped)} companies")
    results = deduped
    
    if results:
        ranked = generate_master_ranking(results)
        print(f"\n🏆 Top 10:")
        for i, c in enumerate(ranked[:10], 1):
            print(f"   {i:2d}. {c['symbol']:15s} {c['name'][:30]:30s} Score: {c['scores']['composite']:.2f}  {c['rating']}")
    
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
