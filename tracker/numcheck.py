"""
Number check for what the AI wrote about a filing — no LLM, run on every read before anything is stored.

The page text (layout.py) has one table row per line — `label | Q | PQ | YQ | YTD | YTD-1 | FY` in a results
statement — so the figures the AI quoted can be found in their row and column:

  • a ₹ figure found in a row's quarter column fixes the row it talks about; the same row then gives the
    previous-quarter and year-ago figures, so "up x% YoY", "down y% QoQ", "7.7x", "from ₹z Cr" and "vs ₹z" are
    recomputed, and up/down follow the sign;
  • a figure named for a metric (revenue, PAT, EPS, PBT, other income) that no row has is replaced by that metric's
    figure from the statement (consolidated when the read says so);
  • a figure outside the tables must appear somewhere in the document (order books, capex, guidance …);
  • a line whose figure the document can't support is dropped, and the summary says so ("check the PDF");
  • the results figures (revenue, PAT, EPS, P&L lines, YoY / QoQ) are replaced by the statement's, and
    the previous quarters are compared with NSE's XBRL figures already stored (financials_q).

Column order of a results statement is the SEBI format (quarter, preceding quarter, year-ago quarter, then
year-to-date and the previous year), unless a header line names the periods (Q2 FY27 | Q2 FY26 | …), as
presentations do. XBRL disagreeing with the order → the order is not trusted.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

CELL_SPLIT = " | "
UNITS = [(re.compile(r"\blakh\s+crores?\b", re.I), 1e5),
         (re.compile(r"\b(lakhs?|lacs?)\b", re.I), 0.01),
         (re.compile(r"\b(crores?|crs?\.?)\b", re.I), 1.0),
         (re.compile(r"\b(millions?|mn)\b|(?:inr|rs\.?|₹)\s?m\b", re.I), 0.1),
         (re.compile(r"\b(thousands?|'000)\b", re.I), 1e-4)]
UNIT_PHRASE = re.compile(r"(?:amount|amounts|figures|rs\.?|₹|inr|rupees|\bin\b)[^\n]{0,30}?\b(lakhs?|lacs?|crores?|crs?|millions?|"
                         r"mn|thousands?|'000)\b|(?:inr|rs\.?|₹)\s?m\b", re.I)
VALUE = re.compile(r"^[(\[]?\s*[-–−]?\s*(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?\s*[)\]]?%?$")
NIL = re.compile(r"^[-–—=~_.']{1,3}$")

METRICS = {
    "revenue": re.compile(r"^\W*(?:\(?[a-z0-9]{1,3}[).]\s*)?(?:net\s+)?(?:revenue|income)s?\s+from\s+operations|^\W*(?:net\s+)?sales\b|"
                          r"^\W*(?:\(?[a-z0-9]{1,3}[).]\s*)?revenue\b(?!.*segment)|^\W*total revenue from operations", re.I),
    "total_income": re.compile(r"^\W*total\s+(?:income|revenue)\b(?!\s+from)", re.I),
    "other_income": re.compile(r"^\W*(?:\(?[a-z0-9]{1,3}[).]\s*)?other\s+income\b", re.I),
    "finance": re.compile(r"financ(?:e|ial)\s+(?:costs?|charges?|expenses?)|interest\s+(?:expenses?|costs?)", re.I),
    "depreciation": re.compile(r"depreciation", re.I),
    "exceptional": re.compile(r"^\W*(?:\(?[a-z0-9]{1,3}[).]\s*)?exceptional", re.I),
    "pbt": re.compile(r"profit\W*(?:\(loss\))?\W*before\s+tax(?!.*exceptional)", re.I),
    "pat": re.compile(r"(?:net\s+)?profit\W*(?:\(?loss\)?)?\W*(?:for\s+the\s+(?:period|quarter|year)|after\s+tax)|"
                      r"^\W*net\s+profit\b", re.I),
    "pat_owners": re.compile(r"owners|equity\s+holders\s+of\s+the\s+(?:parent|company)", re.I),
    "ebitda": re.compile(r"\bebitda\b", re.I),
}
NOT_PAT = re.compile(r"discontinued|comprehensive|before|exceptional|share of|non-?controlling|margin|%", re.I)
EPS_HEAD = re.compile(r"earnings?\s+per\s+(?:equity\s+)?share|\bEPS\b", re.I)

# what a summary line says
MONEY = re.compile(r"(?P<cur>₹|Rs\.?\s?|INR\s?)\s?(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>lakh\s+crores?|crores?|Cr\b|cr\b|"
                   r"lakhs?|mn\b|million|bn\b|billion)?", re.I)
PCT = re.compile(r"(?P<sign>[+\-−])?(?P<num>\d+(?:\.\d+)?)\s?%")
BPS = re.compile(r"(?P<sign>[+\-−])?(?P<num>\d[\d,]*)\s?(?:bps?|basis points)\b", re.I)
MULT = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s?(?:x|×|times|-fold|fold)(?![a-z])", re.I)
YOY = re.compile(r"\b(?:YoY|y-?o-?y|year[- ]on[- ]year|year[- ]ago|from a year|over last year|vs\.?\s+Q\d\s*FY\s*\d\d)", re.I)
QOQ = re.compile(r"\b(?:QoQ|q-?o-?q|quarter[- ]on[- ]quarter|sequential(?:ly)?|over (?:the )?(?:previous|last|preceding) quarter)", re.I)
BASE_WORD = re.compile(r"\b(?:from|vs\.?|versus|against|compared (?:with|to)|over)\s*$", re.I)
METRIC_WORDS = [("eps", re.compile(r"\bEPS\b|earnings per share", re.I)),
                ("pat", re.compile(r"\bPAT\b|net profit|profit after tax|\bprofit\b(?! before)", re.I)),
                ("pbt", re.compile(r"\bPBT\b|profit before tax", re.I)),
                ("ebitda", re.compile(r"\bEBITDA\b", re.I)),
                ("other_income", re.compile(r"other income", re.I)),
                ("finance", re.compile(r"financ(?:e|ial) (?:cost|expense|charge)s?|interest (?:cost|expense)s?", re.I)),
                ("depreciation", re.compile(r"depreciation", re.I)),
                ("total_income", re.compile(r"total income", re.I)),
                ("revenue", re.compile(r"\brevenue\b|\bsales\b|top ?line|income from operations", re.I))]
UP_DOWN = [("up", "down"), ("rose", "fell"), ("grew", "declined"), ("increase", "decrease"), ("increased", "decreased"),
           ("higher", "lower"), ("growth", "decline"), ("jump", "drop"), ("jumped", "dropped"), ("rise", "fall"),
           ("gain", "loss"), ("improved", "worsened"), ("expanded", "contracted")]
FLIP = {**{a: b for a, b in UP_DOWN}, **{b: a for a, b in UP_DOWN}}
DIRECTION = re.compile(r"\b(" + "|".join(sorted(FLIP, key=len, reverse=True)) + r")\b", re.I)


@dataclass
class Row:
    label: str
    cells: list            # float | None (blank) per column
    page: int
    unit: float | None     # factor to ₹ crore (None: per-share figures, or unknown)
    basis: str | None      # consolidated | standalone | None
    cols: list = field(default_factory=list)   # 'Q' | 'PQ' | 'YQ' | None per column
    eps: bool = False
    segment: bool = False
    raw: list = field(default_factory=list)    # the cells as printed
    fv: float | None = None                    # face value per share printed on the page
    positional: bool = False                   # roles assumed from the SEBI column order, not read off a header
    inherited: bool = False                    # roles taken from another statement's header in the same filing

    def at(self, col: str) -> float | None:
        if col not in self.cols:
            return None
        v = self.cells[self.cols.index(col)]
        return None if v is None else v

    def cr(self, col: str) -> float | None:
        v = self.at(col)
        if v is None:
            return None
        return v if self.eps or self.unit is None else v * self.unit


def _num(s: str) -> float | None:
    s = re.sub(r"[!|:;'’\"]+$", "", s.strip())              # OCR specks after a figure: "(187.69)!"
    s = re.sub(r"^([(\[]?[-–−]?\d[\d,]*)\s(\d{2})([)\]]?)$", r"\1.\2\3", s)
    if NIL.match(s):
        return 0.0
    if not VALUE.match(s):
        return None
    neg = s.startswith(("(", "[")) or re.match(r"^[-–−]", s)
    v = float(re.sub(r"[^\d.]", "", s))
    return -v if neg else v


def _unit_of(text: str) -> float | None:
    m = UNIT_PHRASE.search(text)
    if not m:
        return None
    for rx, f in UNITS:
        if rx.search(m.group(0)):
            return f
    return None


def _basis_of(text: str) -> str | None:
    # the statement's own title, wherever the text order put it (DANISH's comes after the table)
    titles = {m.group(1).lower() for m in re.finditer(r"statement of (?:the )?(?:un-?audited |audited )?(standalone|consolidated)",
                                                      text, re.I)}
    if len(titles) == 1:
        return titles.pop()
    head = "\n".join(text.splitlines()[:18]).lower()
    if "consolidated" in head and "standalone" not in head:
        return "consolidated"
    if "standalone" in head and "consolidated" not in head:
        return "standalone"
    if re.search(r"non[- ]?controlling|attributable to\W+(?:\(a\)\s*)?owners", text, re.I):
        return "consolidated"
    return None


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DATE_TOKEN = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b"
                        r"|\b([A-Za-z]{3,9})\.?\s*(\d{1,2}),?\s*(\d{4})\b"
                        r"|\b(\d{1,2})(?:st|nd|rd|th)?[\s-]*([A-Za-z]{3,9})[,\s-]*(\d{2,4})\b"
                        r"|\b([A-Za-z]{3,9})[\s'’-]+(\d{2})\b(?![\d,.])")
PERIOD_TOKEN = re.compile(r"\b([QH])\s*([1-4])\s*'?\s*FY\s*'?(\d{2,4})\b|\b(9M|nine months)\s*FY\s*'?(\d{2,4})\b|"
                          r"\bFY\s*'?(\d{2,4})\b|\b(Y-?o-?Y|Q-?o-?Q|growth|change|var(?:iance)?)\b|%", re.I)
YEAR_TOKEN = re.compile(r"\b(20\d{2})\b")
MONTH_TOKEN = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*", re.I)


def _ym(month: str, year: str) -> tuple[int, int] | None:
    m = MONTHS.get((month or "")[:3].lower())
    if not m:
        return None
    y = int(year)
    return (y + 2000 if y < 100 else y, m)


def _dates_in(line: str) -> list[tuple[int, int]]:
    out = []
    for m in DATE_TOKEN.finditer(line):
        g = m.groups()
        if g[0]:
            mm, yy = int(g[1]), int(g[2])
            if 1 <= mm <= 12:
                out.append((yy + 2000 if yy < 100 else yy, mm))
        elif g[3]:
            ym = _ym(g[3], g[5])
            if ym:
                out.append(ym)
        elif g[6]:
            ym = _ym(g[7], g[8])
            if ym:
                out.append(ym)
        elif g[9]:
            ym = _ym(g[9], g[10])
            if ym:
                out.append(ym)
    return out


def _ends(per: tuple) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    """(this period's end, the preceding period's end, the year-ago period's end) as (year, month)."""
    kind, n, fy = per
    if kind == "H":
        cur = (2000 + fy - 1, 9) if n == 1 else (2000 + fy, 3)
        step = 6
    else:
        cur = {1: (2000 + fy - 1, 6), 2: (2000 + fy - 1, 9), 3: (2000 + fy - 1, 12), 4: (2000 + fy, 3)}[n]
        step = 3
    months = cur[0] * 12 + cur[1] - 1 - step
    return cur, (months // 12, months % 12 + 1), (cur[0] - 1, cur[1])


def _roles_from_dates(dates: list[tuple[int, int]], per: tuple) -> list | None:
    """Columns dated by period end → Q / PQ / YQ / YTD / YTDp / FY for the document's quarter or half-year
    ("Q" is the document's own period either way)."""
    kind, q, _ = per
    cur, pq, yq = _ends(per)
    if kind == "H":
        q = 0                       # a half-year statement has no year-to-date columns of its own
    roles, seen = [], {}
    for d in dates:
        k = seen.get(d, 0)
        seen[d] = k + 1
        if d == cur:
            roles.append("Q" if k == 0 else "YTD" if q in (2, 3) else None)
        elif d == yq:
            roles.append("YQ" if k == 0 else "YTDp" if q in (2, 3) else None)
        elif d == pq:
            roles.append("PQ" if k == 0 else "FY" if pq[1] == 3 else None)
        elif d[1] == 3 and d[0] in (cur[0], cur[0] - 1, cur[0] + 1):
            roles.append("FY" if "FY" not in roles else None)        # the year-end column
        else:
            return None     # a date that is no period of this statement — an OCR misread ("30-09-2625"): not trusted
    return roles if "Q" in roles else None


def _roles_from_periods(tokens: list, per: tuple) -> list | None:
    kind, q, fy = per
    roles = []
    if kind == "H":
        for t in tokens:
            g = t.groups()
            k = (g[0].upper(), int(g[1]), int(g[2]) % 100) if g[0] else None
            roles.append("Q" if k == ("H", q, fy) else "YQ" if k == ("H", q, fy - 1) else
                         "PQ" if k == ("H", 2, fy - 1) and q == 1 or k == ("H", 1, fy) and q == 2 else None)
        return roles if "Q" in roles else None
    pq = (q - 1, fy) if q > 1 else (4, fy - 1)
    for t in tokens:
        g = t.groups()
        if g[0] and g[0].upper() == "Q":
            k = (int(g[1]), int(g[2]) % 100)
            roles.append("Q" if k == (q, fy) else "PQ" if k == pq else "YQ" if k == (q, fy - 1) else None)
        elif g[0] and g[0].upper() == "H" and q in (2, 4):
            k = (int(g[1]), int(g[2]) % 100)
            h = 1 if q == 2 else 2
            roles.append("YTD" if k == (h, fy) and q == 2 else "YTDp" if k == (h, fy - 1) and q == 2 else None)
        else:
            roles.append(None)
    return roles if "Q" in roles else None


def _header_cols(lines: list[str], n: int, period: tuple[int, int] | None) -> list | None:
    """Column roles from the header lines above a table row, if they name the n columns' periods:
    dates (Sept 30, 2026 | 30.06.2026 | …, in any order), labels (Q2 FY27 | Q2 FY26 | YoY | …), or a line of month
    names over a line of years (headers wrapped by OCR)."""
    if not period:
        return None
    for ln in reversed(lines):
        ds = _dates_in(ln)
        if len(ds) == n:
            r = _roles_from_dates(ds, period)
            if r:
                return r
        toks = list(PERIOD_TOKEN.finditer(ln))
        if len(toks) == n and sum(1 for t in toks if t.group(1)) >= 2:
            r = _roles_from_periods(toks, period)
            if r:
                return r
    for i, ln in enumerate(lines):
        years = YEAR_TOKEN.findall(ln)
        if len(years) != n:
            continue
        for up in lines[max(0, i - 3):i + 1][::-1]:
            months = MONTH_TOKEN.findall(up)
            if len(months) == n:
                ds = [_ym(m, y) for m, y in zip(months, years)]
                if all(ds):
                    r = _roles_from_dates(ds, period)
                    if r:
                        return r
    return None


def parse(pages: list[str], period: str | None = None) -> list[Row]:
    """Table rows of every page, with units, basis and column roles where they can be told."""
    per = None
    m = re.search(r"\b([QH])\s*([1-4])\s*FY\s*'?(\d{2})", period or "", re.I)
    if m and not (m.group(1).upper() == "H" and m.group(2) not in "12"):
        per = (m.group(1).upper(), int(m.group(2)), int(m.group(3)))
    units = [_unit_of(p) for p in pages]
    doc_unit = max(set(u for u in units if u), key=lambda u: units.count(u)) if any(units) else None
    rows: list[Row] = []
    for pno, text in enumerate(pages, 1):
        unit = units[pno - 1] or doc_unit
        basis = _basis_of(text)
        lines = text.splitlines()
        standard = bool(re.search(r"preceding", text, re.I)) and \
            bool(re.search(r"corresponding|previous\s+year|year\s+ago", text, re.I))
        segment = False
        eps_ctx = 0
        header_cache: dict[int, list] = {}
        fvm = re.search(r"face\s+value[^\d\n]{0,40}?(\d+(?:\.\d+)?)\s*/?-?", text, re.I)
        fv = float(fvm.group(1)) if fvm and 0 < float(fvm.group(1)) <= 1000 else None
        for i, ln in enumerate(lines):
            if re.search(r"segment", ln, re.I) and CELL_SPLIT not in ln:
                segment = True
            if EPS_HEAD.search(ln):
                eps_ctx = 8
            elif eps_ctx:
                eps_ctx -= 1
            if CELL_SPLIT not in ln:
                toks = ln.split()
                j = len(toks)
                while j > 0 and (_num(toks[j - 1]) is not None):
                    j -= 1
                if len(toks) - j < 2 or not re.search(r"[A-Za-z]{3}", " ".join(toks[:j])):
                    continue
                ln = " ".join(toks[:j]) + CELL_SPLIT + CELL_SPLIT.join(toks[j:])   # a digital page's text row
            parts = ln.split(CELL_SPLIT)
            k = 0                     # the label: every leading text cell ("rl | Profit / (Loss) for the period")
            while k < len(parts) - 1 and parts[k].strip() and _num(parts[k]) is None:
                k += 1
            label, vals = " ".join(x.strip() for x in parts[:max(k, 1)]), parts[max(k, 1):]
            nums = [_num(v) if v.strip() else None for v in vals]
            junk = sum(1 for v in vals if v.strip() and _num(v) is None)    # an OCR speck in a cell: that cell unknown
            if sum(1 for v in nums if v is not None) < 2 or junk * 3 > len(vals):
                continue
            n = len(nums)
            cols = header_cache.get(n) if n in header_cache else _header_cols(lines[max(0, i - 16):i], n, per)
            if cols is not None:
                header_cache[n] = cols
            positional = cols is None and standard          # assumed, not read off a header
            if positional:
                cols = {6: ["Q", "PQ", "YQ", "YTD", "YTDp", "FY"], 4: ["Q", "PQ", "YQ", "FY"],
                        5: ["Q", "PQ", "YQ", None, None], 3: ["Q", "PQ", "YQ"]}.get(n, [None] * n)
            is_eps = bool(eps_ctx and re.search(r"basic|diluted", label, re.I)) or bool(EPS_HEAD.search(label))
            rows.append(Row(label, nums, pno, None if is_eps else unit, basis, cols or [None] * n, eps=is_eps,
                            segment=segment and not METRICS["revenue"].search(label), raw=[v.strip() for v in vals], fv=fv,
                            positional=positional))
    # a statement whose header OCR couldn't read takes the column roles of another statement of the filing with as
    # many columns (the standalone and consolidated statements share their format) — STOVEKRAFT Q2 FY27 p.8
    known: dict[int, list] = {}
    for r in rows:
        if "Q" in r.cols and not r.positional:
            known.setdefault(len(r.cols), list(r.cols))
    for r in rows:
        if "Q" not in r.cols and len(r.cols) in known:
            r.cols = list(known[len(r.cols)])
            r.positional = r.inherited = True
    # a statement page that doesn't say its basis (a garbled title), when the other statements all say one → the other
    stated = {r.basis for r in rows if r.basis and "Q" in r.cols}
    if len(stated) == 1:
        other = "consolidated" if stated == {"standalone"} else "standalone"
        for r in rows:
            if r.basis is None and "Q" in r.cols:
                r.basis = other
    return rows


def _eps_expected(rows: list[Row], basis: str) -> float | None:
    """Quarter EPS from the owners' profit and the paid-up capital ÷ face value printed in the same statement."""
    stmt = [r for r in rows if r.basis == basis and "Q" in r.cols and not r.eps]
    owners = next((r for r in stmt if METRICS["pat_owners"].search(r.label) and r.at("Q")), None)
    if owners is None and basis == "standalone":
        owners = next((r for r in stmt if METRICS["pat"].search(r.label) and not NOT_PAT.search(r.label) and r.at("Q")), None)
    cap = next((r for r in stmt if re.search(r"paid[- ]?up", r.label, re.I) and r.at("Q")), None)
    if not owners or not cap:
        return None
    if not cap.fv:
        return None
    return owners.at("Q") / (cap.at("Q") / cap.fv)


def identities(rows: list[Row], basis: str | None) -> dict:
    """Arithmetic the statement must satisfy, per column — a misread figure breaks one (OCR check).

    revenue + other income = total income; profit before exceptional items + exceptional items = profit before tax."""
    want = basis or "consolidated"
    out = {"ok": [], "broken": []}
    get = {k: metric_row(rows, k, want) for k in ("revenue", "other_income", "total_income", "pbt", "exceptional")}
    pbe = next((r for r in rows if r.basis == want and "Q" in r.cols and re.search(r"before\s+exceptional", r.label, re.I)), None)
    r, t = get["revenue"], get["total_income"]
    income = []                       # revenue … total income: other income, other gains, and so on
    if r and t and r.page == t.page and r in rows and t in rows and rows.index(r) < rows.index(t):
        income = [x for x in rows[rows.index(r):rows.index(t)] if x.page == r.page and x.cols == r.cols]
    for col in ("Q", "PQ", "YQ"):
        if income and t.at(col) is not None and all(x.at(col) is not None for x in income):
            total = sum(x.at(col) for x in income)
            (out["ok"] if abs(total - t.at(col)) <= 0.011 * max(1, abs(t.at(col))) else out["broken"]).append(
                f"income lines add up to total income ({col})")
        p, e = get["pbt"], get["exceptional"]
        if pbe and p and e and pbe.page == p.page == e.page and None not in (pbe.at(col), e.at(col), p.at(col)):
            sign = -1 if e.at(col) > 0 and p.at(col) < pbe.at(col) else 1
            (out["ok"] if abs(pbe.at(col) + sign * e.at(col) - p.at(col)) <= 0.011 * max(1, abs(p.at(col))) else
             out["broken"]).append(f"pre-exceptional ± exceptional = PBT ({col})")
    return out


def _completeness(rows: list[Row]) -> dict[int, int]:
    """Per page: how many statement lines it has — the full statement beats a notes table or a segment page."""
    out: dict[int, int] = {}
    for p in {r.page for r in rows}:
        labels = [r.label for r in rows if r.page == p and "Q" in r.cols]
        out[p] = sum(1 for k in ("revenue", "other_income", "finance", "depreciation", "pbt", "pat", "exceptional")
                     if any(METRICS[k].search(x) for x in labels)) + sum(1 for r in rows if r.page == p and r.eps)
    return out


def metric_row(rows: list[Row], metric: str, basis: str | None) -> Row | None:
    rx = METRICS.get(metric)
    if metric == "eps":
        cands = [r for r in rows if r.eps and "Q" in r.cols and re.search(r"basic", r.label, re.I)] or \
            [r for r in rows if r.eps and "Q" in r.cols]
        if basis and any(r.basis == basis for r in rows):
            cands = [r for r in cands if r.basis in (basis, None)]
        if not cands:
            return None
        want = basis or "consolidated"
        expect = _eps_expected(rows, want)

        def eps_rank(r: Row):
            q = r.raw[r.cols.index("Q")] if r.raw else ""
            near = -abs(r.at("Q") - expect) / max(abs(expect), 0.01) if expect is not None and r.at("Q") is not None else 0
            return (r.basis == want, "." in q, near, -rows.index(r))      # OCR sometimes drops the point: "227"
        return max(cands, key=eps_rank)
    elif rx is None:
        return None
    else:
        cands = [r for r in rows if "Q" in r.cols and not r.eps and not r.segment and rx.search(r.label)
                 and not (metric == "pat" and NOT_PAT.search(r.label))
                 and not (metric == "revenue" and re.search(r"other income|total income", r.label, re.I))]
    if basis and any(r.basis == basis for r in rows):     # the read's statement is in the filing: only that one
        cands = [r for r in cands if r.basis in (basis, None)]
    elif basis and len({r.page for r in cands if r.basis is None and _completeness(rows).get(r.page, 0) >= 4}) >= 2:
        return None          # two statements whose titles don't say which is which: can't tell the read's one
    if metric == "pat":
        best = [r for r in cands if re.search(r"for the (period|quarter|year)", r.label, re.I)]
        cands = best or cands
    if not cands:
        return None
    full = _completeness(rows)
    order = {r_id: i for i, r_id in enumerate(map(id, cands))}
    want = basis or "consolidated"
    return max(cands, key=lambda r: (r.basis == want, r.basis is not None, full.get(r.page, 0), -order[id(r)]))


# ── the check ───────────────────────────────────────────────────────────────

def _decimals(s: str) -> int:
    return len(s.split(".", 1)[1]) if "." in s else 0


def _fmt(v: float, like: str) -> str:
    d = _decimals(like)
    s = f"{abs(v):,.{d}f}" if "," in like or abs(v) >= 1000 else f"{abs(v):.{d}f}"
    return ("-" if v < 0 else "") + s


def _close(claimed: float, true: float, text: str) -> bool:
    d = _decimals(text)
    return abs(claimed - true) <= max(0.51 * 10 ** -d, 0.006 * abs(true))


def _to_cr(num: float, unit: str | None, metric: str | None) -> float:
    u = (unit or "").lower()
    if metric == "eps" and not u:
        return num
    if "lakh crore" in u:
        return num * 1e5
    if u.startswith("lakh"):
        return num / 100
    if u in ("mn", "million"):
        return num / 10
    if u in ("bn", "billion"):
        return num * 100
    return num


def _from_cr(v: float, unit: str | None) -> float:
    u = (unit or "").lower()
    if "lakh crore" in u:
        return v / 1e5
    if u.startswith("lakh"):
        return v * 100
    if u in ("mn", "million"):
        return v * 10
    if u in ("bn", "billion"):
        return v / 100
    return v


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b <= 0 or a < 0:
        return None
    return (a / b - 1) * 100


TEXT_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
NEAR_UNIT = [(re.compile(r"^\W{0,3}(?:lakh\s+crores?|lac\s+crores?)", re.I), 1e5),
             (re.compile(r"^\W{0,3}(?:crores?|crs?\b|cr\.)", re.I), 1.0),
             (re.compile(r"^\W{0,3}(?:lakhs?|lacs?)\b", re.I), 0.01),
             (re.compile(r"^\W{0,3}(?:millions?|mn\b|mio\b|m\b)", re.I), 0.1),
             (re.compile(r"^\W{0,3}(?:billions?|bn\b)", re.I), 100.0)]


def _in_text(v_cr: float, raw: str, pages: list[str], usd_inr: float) -> bool:
    """Is the figure printed somewhere — in ₹ crore, or in the unit written next to the number (lakh, million,
    billion, lakh crore, rupees, USD) or in the page's "amount in …" header?"""
    if v_cr == 0:
        return True
    claimed = float(raw.replace(",", "")) or 1e-9
    per_unit = abs(v_cr) / claimed                      # ₹ crore per unit of the figure as written
    tol = max(0.002 * abs(v_cr), 0.51 * 10 ** -_decimals(raw) * per_unit)
    for p in pages:
        page_unit = _unit_of(p) or 1.0
        for m in TEXT_NUM.finditer(p):
            try:
                n = float(m.group(0).replace(",", ""))
            except ValueError:
                continue
            if not n:
                continue
            after, before = p[m.end():m.end() + 14], p[max(0, m.start() - 6):m.start()]
            line_start = p.rfind("\n", 0, m.start()) + 1
            in_table = CELL_SPLIT in p[line_start:p.find("\n", m.end()) if p.find("\n", m.end()) >= 0 else len(p)]
            factors = {page_unit} if in_table and page_unit != 1.0 else {1.0, page_unit}
            for rx, f in NEAR_UNIT:
                if rx.search(after):
                    factors = {f}
                    break
            if re.search(r"(?:usd|us\$|\$)\s*$", before, re.I):
                factors = {usd_inr / 10 if re.match(r"\W{0,3}(?:millions?|mn)", after, re.I) else
                           usd_inr * 100 if re.match(r"\W{0,3}(?:billions?|bn)", after, re.I) else usd_inr / 1e7}
            elif n >= 1e5 and re.search(r"(?:rs\.?|₹|inr)\s*$", before, re.I) and not re.match(r"\W{0,3}(?:cr|lakh|mn|bn|mil|bil)", after, re.I):
                factors.add(1e-7)                       # "Rs. 1,91,75,000/-"
            if any(abs(n * f - abs(v_cr)) <= tol for f in factors):
                return True
    return False


@dataclass
class Result:
    summary: list
    results: dict | None
    fixes: list
    dropped: list
    source: str          # table | xbrl | text | none


def _xbrl(conn: sqlite3.Connection | None, sid: str | None, period: str | None, basis: str | None) -> dict:
    """Stored NSE XBRL figures for the previous and year-ago quarters (₹ Cr)."""
    if conn is None or not sid or not period:
        return {}
    from .results import period_end
    from datetime import date
    end = period_end(period)
    if not end or not re.match(r"Q", period or ""):
        return {}
    d = date.fromisoformat(end)
    yq = date(d.year - 1, d.month, d.day).isoformat()
    out = {}
    for b in [basis] if basis else []:
        pq = conn.execute("SELECT revenue_cr, pat_cr FROM financials_q WHERE sid=? AND months=3 AND basis=? AND period_end<? "
                          "ORDER BY period_end DESC LIMIT 1", (sid, b, end)).fetchone()
        y = conn.execute("SELECT revenue_cr, pat_cr FROM financials_q WHERE sid=? AND months=3 AND basis=? AND period_end=?",
                         (sid, b, yq)).fetchone()
        if pq or y:
            out = {"basis": b, "PQ": pq["revenue_cr"] if pq else None, "YQ": y["revenue_cr"] if y else None}
            break
    return out


def check(summary: list[str], results: dict | None, pages: list[str], *, period: str | None = None,
          conn: sqlite3.Connection | None = None, sid: str | None = None, usd_inr: float = 88.0,
          context: str = "", company: str = "") -> Result:
    """`context`: the earlier-quarter context the prompt carried — a figure taken from it is not invented."""
    rows = parse(pages, period or (results or {}).get("period"))
    present = pages + ([context] if context else [])
    basis = (results or {}).get("basis")
    fixes: list[str] = []
    source = "none"

    # trust the column order only when the stored XBRL quarters agree with it
    xb = _xbrl(conn, sid, period or (results or {}).get("period"), basis)
    rev_row = metric_row(rows, "revenue", basis)
    confirmed_order = False
    if rev_row and xb and rev_row.positional:     # a header that names the periods outranks stored XBRL
        pq, yq = rev_row.cr("PQ"), rev_row.cr("YQ")
        agree = [abs(t - x) <= 0.02 * x for t, x in ((pq, xb.get("PQ")), (yq, xb.get("YQ"))) if t and x]
        confirmed_order = bool(agree) and all(agree)
        if agree and not all(agree):
            swapped = [abs(t - x) <= 0.02 * x for t, x in ((pq, xb.get("YQ")), (yq, xb.get("PQ"))) if t and x]
            for r in rows:
                if r.page == rev_row.page and r.positional and "PQ" in r.cols and "YQ" in r.cols:
                    if swapped and all(swapped):
                        i, j = r.cols.index("PQ"), r.cols.index("YQ")
                        r.cols[i], r.cols[j] = "YQ", "PQ"
                    else:
                        r.cols = [c if c == "Q" else None for c in r.cols]
            fixes.append("column order of the statement disagreed with NSE XBRL — " +
                         ("swapped" if swapped and all(swapped) else "previous quarters not used"))

    if rev_row and rev_row.positional and not rev_row.inherited and not confirmed_order:
        for r in rows:              # no header and no XBRL to confirm which column is which quarter
            if r.positional and not r.inherited:
                r.cols = [c if c == "Q" else None for c in r.cols]
    # change a figure only off a statement the check has confirmed: its unit known and its quarter column adding up
    # (revenue + other income = total income, or pre-exceptional ± exceptional = PBT). Otherwise — a presentation
    # table, a page whose unit isn't printed, columns of unknown order — figures are only checked for presence.
    ids = identities(rows, basis or (rev_row.basis if rev_row else None)) if rev_row else {"ok": [], "broken": []}
    trusted = bool(rev_row and rev_row.unit is not None and any(x.endswith("(Q)") for x in ids["ok"])
                   and not any(b.endswith("(Q)") for b in ids["broken"]))
    if rev_row is not None and not trusted:
        fixes.append("statement not confirmed (unit or arithmetic): figures only checked for presence")
        rev_row = None
    use_rows = rows if trusted else []
    out_res = dict(results) if isinstance(results, dict) else None
    if out_res is not None and rev_row is not None:
        source = "table"
        out_res = _fix_results(out_res, rows, basis, fixes, xb)
        broken = [b for b in identities(rows, out_res.get("basis"))["broken"] if b.endswith("(Q)")]
        if broken:            # a misread figure in the quarter's column (OCR): the table can't vouch for the numbers
            out_res["verified"] = 0.5
            source = "table_unconfirmed"
            fixes.append("statement arithmetic doesn't add up: " + "; ".join(broken))

    xb_lines: dict = {}       # stored XBRL only confirms a statement's columns; it never rewrites a figure
    new_lines, dropped = [], []
    for line in summary:
        fixed, ok, gone = _fix_line(line, use_rows, basis, present, usd_inr, fixes, xb_lines, company, str(period or (results or {}).get('period') or ''))
        if ok:
            new_lines.append(fixed)
        dropped += gone
    if out_res is not None:
        if out_res.get("headline"):
            n0 = len(fixes)
            fixed, ok, _ = _fix_line(str(out_res["headline"]), use_rows, basis, present, usd_inr, fixes, xb_lines, company, str(period or (results or {}).get('period') or ''))
            out_res["headline"] = fixed if ok else None
            if len(fixes) > n0:
                fixes.append("headline fixed")
        kept = []
        for a in out_res.get("anomalies") or []:       # an anomaly read off a wrong figure is not an anomaly
            text = a.get("text") if isinstance(a, dict) else str(a)
            n0 = len(fixes)
            fixed, ok, gone = _fix_line(str(text or ""), use_rows, basis, present, usd_inr, fixes, xb_lines, company, str(period or (results or {}).get('period') or ''))
            if ok and not gone and all(f.startswith("unit ") for f in fixes[n0:]):
                kept.append({**a, "text": fixed} if isinstance(a, dict) else fixed)
            else:
                del fixes[n0:]
                fixes.append(f"anomaly dropped (its figure is not the filing's): {str(text)[:80]}")
        out_res["anomalies"] = kept
        out_res["numbers_check"] = source
    if rows and source == "none":
        source = "text"
    return Result(new_lines, out_res, fixes, dropped, source)


def _f(v):
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else None
    except ValueError:
        return None


def _fix_results(res: dict, rows: list[Row], basis: str | None, fixes: list, xb: dict) -> dict:
    """The quarter's figures from the statement; YoY / QoQ recomputed from it."""
    def put(key, v):
        if v is None:
            return
        old = _f(res.get(key))
        if old is None or abs(old - v) > max(0.011, 0.006 * abs(v)):
            fixes.append(f"{key} {old} → {round(v, 2)}")
        res[key] = round(v, 2)

    rev = metric_row(rows, "revenue", basis)
    res["basis"] = rev.basis or basis or res.get("basis")
    put("revenue_cr", rev.cr("Q"))
    def put_pct(key, row, col):
        rng = pct_range(row, "Q", col)
        if rng is None:
            return False
        old = _f(res.get(key))
        if old is not None and rng[0] - 0.6 <= old <= rng[1] + 0.6:
            return True                    # what the AI wrote fits the printed figures
        put(key, round((rng[0] + rng[1]) / 2, 1))
        return True

    for key, col in (("revenue_yoy_pct", "YQ"), ("revenue_qoq_pct", "PQ")):
        put_pct(key, rev, col)
    # PAT: keep the AI's choice (total or owners' share) when a row has it, else the period's total
    pat_rows = [r for r in rows if r.basis == rev.basis and "Q" in r.cols and not r.eps and
                (METRICS["pat"].search(r.label) or METRICS["pat_owners"].search(r.label)) and
                not re.search(r"comprehensive|discontinued|before", r.label, re.I)]
    claimed = _f(res.get("pat_cr"))
    pat = next((r for r in pat_rows if claimed is not None and r.cr("Q") is not None and
                abs(r.cr("Q") - claimed) <= max(0.06, 0.006 * abs(claimed))), None) or metric_row(rows, "pat", rev.basis)
    if pat:
        put("pat_cr", pat.cr("Q"))
        for key, col in (("pat_yoy_pct", "YQ"), ("pat_qoq_pct", "PQ")):
            if not put_pct(key, pat, col) and pat.cr(col) is not None and res.get(key) is not None:
                fixes.append(f"{key} {res.get(key)} → None (a loss on one side: no % change)")
                res[key] = None
    eps = metric_row(rows, "eps", rev.basis)
    if eps and eps.basis == rev.basis or (eps and eps.page == rev.page):
        put("eps", eps.at("Q"))
    # EBITDA, when printed: the AI sometimes keeps the statement's unit (₹ million as ₹ crore)
    claimed_e = _f(res.get("ebitda_cr"))
    if claimed_e is not None:
        cands = [r for r in rows if "Q" in r.cols and not r.eps and r.unit and r.unit != 1 and r.at("Q") is not None and
                 re.match(r"^\W*(?:adjusted\s+|operational\s+|operating\s+|reported\s+)?ebitda\b(?!.*margin)", r.label, re.I)
                 and abs(r.at("Q") - claimed_e) <= max(0.011, 0.006 * abs(claimed_e))]
        if cands:                       # the closest: "EBITDA 1,557" over "Adjusted EBITDA 1,549"
            put("ebitda_cr", min(cands, key=lambda r: abs(r.at("Q") - claimed_e)).cr("Q"))
    prev = res.get("prev_year") if isinstance(res.get("prev_year"), dict) else {}
    prev = dict(prev)
    for key, metric in (("pbt_cr", "pbt"), ("finance_cost_cr", "finance"), ("depreciation_cr", "depreciation"),
                        ("other_income_cr", "other_income"), ("exceptional_cr", "exceptional")):
        r = metric_row(rows, metric, rev.basis)
        if r is None or r.page != rev.page:
            continue
        put(key, r.cr("Q") if r.cr("Q") is not None else None)
        if r.cr("YQ") is not None:
            prev[key] = round(r.cr("YQ"), 2)
    if rev.cr("YQ") is not None:
        prev["revenue_cr"] = round(rev.cr("YQ"), 2)
    if prev:
        res["prev_year"] = prev
    res["verified"] = 1.0
    return res


BASES = {"Q": {"yoy": "YQ", "qoq": "PQ"}, "YTD": {"yoy": "YTDp"}}
LEAD_OK = re.compile(r"^(?:[\W\d]|\b(?:(?:Q[1-4]|H[12]|9M)\s*(?:FY\s*'?\d{2,4})?|FY\s*'?\d{2,4}(?:\s*[-–]\s*\d{2})?|"
                     r"consolidated|standalone|total|net|overall|company'?s?|group|the|"
                     r"its|quarterly|quarter|reported|headline|record|highest|ever|all-time|strong|robust|operating|operational)\b)*$",
                     re.I)
GENERIC = {"segment", "segments", "business", "businesses", "division", "revenue", "revenues", "sales", "income", "profit",
           "from", "operations", "limited", "ltd", "india", "private", "subsidiary", "the", "and"}


def _qualifier(clause: str, metric_start: int, company: str) -> str:
    """Words naming something other than the company in front of a metric ("Flexible Flow Solution segment")."""
    lead = re.split(r"[,;:(]", clause[:metric_start])[-1]
    for w in re.findall(r"[A-Za-z]{3,}", company or ""):
        lead = re.sub(rf"\b{re.escape(w)}\b", " ", lead, flags=re.I)
    return "" if LEAD_OK.match(lead) else lead.strip()


def _same_thing(qualifier: str, label: str) -> bool:
    words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", qualifier)} - GENERIC
    return bool(words) and any(w in label.lower() for w in words)
BREAK = re.compile(r",|\b(?:exceptional|tax|capex|order|inflow|book|debt|cash|dividend|loss|"
                   r"gain|expenses?|costs?|segment|subsidiar|acqui|stake|consideration|borrowing|receivable|inventor)", re.I)
NOT_QUARTER = re.compile(r"\b(?:H[12]|9M|nine months|half[- ]?year|first half|year[- ]to[- ]date|YTD|full[- ]year|year ended|"
                         r"FY\s?'?\d{2}(?:\s*[-–]\s*\d{2})?\s+(?:revenue|guidance|target|order|capex|ebitda|pat|profit))\b", re.I)


def pct_range(row: Row, col: str, base_col: str) -> tuple[float, float] | None:
    """The % change between two cells of a row, as a range allowing for the rounding of the printed figures
    ("48 | 51" can be −7.8 % … −4.0 %)."""
    a, b = row.cr(col), row.cr(base_col)
    if a is None or b is None or b <= 0 or a < 0:
        return None

    def half(c):
        i = row.cols.index(c)
        raw = row.raw[i] if row.raw and i < len(row.raw) else ""
        d = _decimals(re.sub(r"[^\d.]", "", raw)) if raw else 2
        return 0.5 * 10 ** -d * (row.unit if row.unit and not row.eps else 1)
    ha, hb = half(col), half(base_col)
    lo, hi = ((a - ha) / (b + hb) - 1) * 100, ((a + ha) / max(b - hb, 1e-9) - 1) * 100
    return (lo, hi)


def _row_for(value_cr: float, raw: str, rows: list[Row], metric: str | None, basis: str | None,
             cols=("Q",)) -> tuple[Row, str] | None:
    """(row, column) holding this figure, preferring the named metric, the read's basis and a full statement."""
    if basis and any(r.basis == basis for r in rows):
        rows = [r for r in rows if r.basis in (basis, None)]     # never "correct" a figure from the other statement
    hits = [(r, c) for r in rows for c in cols if c in r.cols and r.cr(c) is not None
            and _close(value_cr, abs(r.cr(c)), raw) and (r.eps == (metric == "eps"))]
    if not hits:
        return None
    rx = METRICS.get(metric or "")
    full = _completeness(rows)

    def score(h) -> tuple:
        r, c = h
        return (c == cols[0], bool(rx and rx.search(r.label)), r.basis == (basis or "consolidated"), not r.segment,
                bool(r.eps and re.search(r"basic", r.label, re.I)),
                full.get(r.page, 0))
    return max(hits, key=score)


def _fix_line(line: str, rows: list[Row], basis: str | None, pages: list[str], usd_inr: float, fixes: list,
              xb: dict, company: str = "", period: str = ""):
    """(the line with its figures fixed, keep it?, the clauses left out — a figure the filing doesn't have)."""
    hm = re.match(r"\s*H\s*([12])\b", period or "", re.I)
    half = hm.group(1) if hm else None
    edits: list[tuple[int, int, str]] = []
    gone: list[tuple[int, int]] = []
    line_tag = None                    # "QoQ revenue grew 56%; PAT rose 83% from ₹17.51 Cr" — QoQ for both
    line_basis = basis
    for clause_m in re.finditer(r"[^;]+", line):
        clause, off = clause_m.group(0), clause_m.start()
        keep = True
        quarter = not (NOT_QUARTER.search(clause) and not re.search(r"\bQ[1-4]\b|quarter", clause, re.I))
        if half and re.search(rf"\bH\s*{half}\b|half[- ]?year|six months|first half", clause, re.I) and \
                not re.search(r"\bQ[1-4]\b|full[- ]year|year ended|9M", clause, re.I):
            quarter = True          # a half-year filing: its own half is the "current period" column
        named_basis = re.findall(r"\b(standalone|consolidated)\b", clause, re.I)
        basis_here = named_basis[-1].lower() if named_basis and len({b.lower() for b in named_basis}) == 1 else line_basis
        if named_basis and len({b.lower() for b in named_basis}) == 1:
            line_basis = basis_here
        cols = ("Q", "YTD") if quarter else ("YTD", "FY", "Q")
        subject: Row | None = None
        scol = "Q"
        subject_val = None
        subject_end = -1
        metric = None
        tokens = sorted([("m", m) for m in MONEY.finditer(clause)] + [("p", m) for m in PCT.finditer(clause)] +
                        [("x", m) for m in MULT.finditer(clause)] + [("b", m) for m in BPS.finditer(clause)],
                        key=lambda t: t[1].start())
        margin_ctx = None
        for kind, m in tokens:
            before = clause[:m.start()]
            named = None
            for name, rx in METRIC_WORDS:
                hit = list(rx.finditer(before))
                if hit and (named is None or hit[-1].end() > named[1]):
                    named = (name, hit[-1].end(), hit[-1].start())
            qual = _qualifier(clause, named[2], company) if named else ""
            if kind == "m":
                raw = m.group("num")
                unit = m.group("unit")
                met = named[0] if named and named[1] > len(before) - 60 and not BREAK.search(before[named[1]:]) else None
                if met == "eps" and unit:
                    met = None
                is_base = bool(BASE_WORD.search(before)) or bool(re.search(r"%\s*(?:YoY|QoQ|y-?o-?y|q-?o-?q)?\s*\(\s*$", before, re.I)) \
                    or (subject is not None and met in (None, metric) and bool(
                    re.match(r"\s*(?:Cr\b|crores?)?\s*(?:YoY|QoQ|y-?o-?y|q-?o-?q)\b", clause[m.end():], re.I)))
                v = _to_cr(float(raw.replace(",", "")), unit, met)
                if re.search(r"\b(?:by|of)\s*[-−]?\s*$", before[-12:], re.I) and re.search(
                        r"\b(?:improv|widen|narrow|rise|rose|fell|declin|increas|decreas|grew|up|down|lower|higher)\w*\s+(?:by|of)\s*[-−]?\s*$",
                        before[-40:], re.I):
                    continue        # a change ("narrowed by ₹1.1 Cr"): not checkable against one cell
                if is_base and subject is not None:
                    own = _tag_after(clause, m.end()) or _tag_before(before) or _any_tag(clause)
                    tag = own or line_tag or "yoy"
                    col = BASES.get(scol, {}).get(tag)
                    true = subject.cr(col) if col else None
                    twins = [r for r in rows if r.eps == subject.eps and r.cr(scol) is not None and subject_val is not None
                             and abs(abs(r.cr(scol)) - abs(subject_val)) <= 0.006 * max(abs(subject_val), 1e-9)]
                    comparisons = [r.cr(c) for r in twins for c in (BASES.get(scol, {}).values() if not own else [col])
                                   if c and r.cr(c) is not None]
                    if true is not None and not any(_close(abs(v), abs(x), raw) for x in comparisons):
                        new = _fmt(abs(_from_cr(true, unit)) if not subject.eps else abs(true), raw)
                        edits.append((off + m.start("num"), off + m.end("num"), new))
                        fixes.append(f"base {raw} → {new} ({tag})")
                    continue
                hit = _row_for(v, raw, rows, met, basis_here, cols)
                if hit is not None and met and qual and not _same_thing(qual, hit[0].label):
                    hit = None             # a segment / subsidiary figure that only coincides with some other row
                if hit is not None and met and not qual and METRICS.get(met) and not METRICS[met].search(hit[0].label) \
                        and not (met == "pat" and METRICS["pat_owners"].search(hit[0].label)) and metric_row(rows, met, basis_here):
                    hit = None             # "revenue ₹x" that equals an unrelated row: judged against the revenue row
                if hit is None and quarter and not qual and met in ("revenue", "pat", "eps", "pbt", "other_income", "total_income", "finance", "depreciation"):
                    row = metric_row(rows, met, basis_here)
                    elsewhere = row is not None and any(
                        x is not None and _close(v, abs(x), raw)
                        for x in (row.cr(c) for c in row.cols if c and c != "Q"))
                    if row is not None and row.cr("Q") is not None and not _close(v, row.cr("Q"), raw) \
                            and not elsewhere and not (met == "pat" and _owners_or_total(rows, row, v, raw)):
                        true = row.cr("Q")
                        new = _fmt(_from_cr(true, unit) if not row.eps else true, raw)
                        edits.append((off + m.start("num"), off + m.end("num"), new))
                        fixes.append(f"{met} {raw} → {new}")
                    hit = (row, "Q") if row is not None and row.cr("Q") is not None and not elsewhere else None
                    if hit and met == "pat":
                        alt = _owners_or_total(rows, row, v, raw)
                        hit = (alt, "Q") if alt else hit
                if hit is None and not (unit or "").lower().startswith(("mn", "million", "lakh", "bn", "billion")):
                    slip = None if _in_text(v, raw, pages, usd_inr) else \
                        _unit_slip(float(raw.replace(",", "")), raw, rows, basis_here, met, clause)
                    if slip is not None:       # "₹69.7 Cr" for a figure printed as 69.72 on a page in ₹ million
                        new = _fmt(slip[0] if v >= 0 else -abs(slip[0]), raw) if slip[0] >= 0.1 else f"{slip[0]:.2f}"
                        edits.append((off + m.start("num"), off + m.end("num"), new.lstrip("-")))
                        fixes.append(f"unit {raw} → {new.lstrip('-')} (printed in {slip[1]})")
                        continue
                if hit is None:
                    if not _in_text(v, raw, pages, usd_inr) and not _in_text_raw(raw, pages):
                        keep = False
                        gone.append((off, off + len(clause)))
                    subject, subject_val, metric, subject_end = None, None, met, m.end()
                    continue
                subject, scol = hit
                subject_val, metric, subject_end = subject.cr(scol), met, m.end()
            elif kind == "p" and quarter and re.search(r"\bEBITDA\s+margins?\s*(?:of|at|was|is|stood at)?\s*"
                                                       r"(?:(?:in\s+)?Q[1-4]\s*FY\s*'?\d{2}\s*)?(?:of|at|was|is)?\s*$",
                                                       before[-45:], re.I):
                ms = margins(rows, basis_here)
                claimed = float(m.group("num")) * (-1 if (m.group("sign") or re.search(r"[-−]\s*$", before)) else 1)
                fit = [k for k, v in ms.items() if v.get("Q") is not None and abs(v["Q"] - claimed) <= 0.6]
                if fit:
                    margin_ctx = (ms[fit[0]], fit[0])
                elif ms:
                    use = "printed" if "printed" in ms else "printed_ratio" if "printed_ratio" in ms else "excl_other_income"
                    if ms[use].get("Q") is not None and (ms[use]["Q"] < 0) == (claimed < 0):
                        new = f"{abs(ms[use]['Q']):.{max(0, min(1, _decimals(m.group('num'))))}f}"
                        edits.append((off + m.start("num"), off + m.end("num"), new))
                        fixes.append(f"EBITDA margin {m.group(0)} → {new}% ({use.replace('_', ' ')})")
                        margin_ctx = (ms[use], use)
            elif kind == "b":
                if margin_ctx is None:
                    continue
                mv, _ = margin_ctx
                own = _tag_after(clause, m.end()) or _tag_before(before[-25:])
                col = {"yoy": "YQ", "qoq": "PQ"}.get(own or line_tag or "yoy")
                if mv.get("Q") is None or mv.get(col) is None:
                    continue
                true = (mv["Q"] - mv[col]) * 100
                claimed = float(m.group("num").replace(",", "")) * (-1 if m.group("sign") in ("-", "−") else 1)
                dword = _direction(before)
                if dword and dword.group(1).lower() in {b for _, b in UP_DOWN}:
                    claimed = -abs(claimed)
                if abs(claimed - true) <= max(60, 0.15 * abs(true)):
                    continue
                edits.append((off + m.start("num"), off + m.end("num"), f"{abs(true):.0f}"))
                if m.group("sign"):
                    edits.append((off + m.start("sign"), off + m.end("sign"), "+" if true >= 0 else "-"))
                if dword and (true >= 0) != (dword.group(1).lower() in {a for a, _ in UP_DOWN}):
                    w = dword.group(1)
                    edits.append((off + dword.start(1), off + dword.end(1), _case(FLIP[w.lower()], w)))
                fixes.append(f"EBITDA margin change {m.group(0).strip()} → {true:+.0f} bps")
            elif kind == "p" and re.search(r"\b(?:PAT|net(?: profit)?)\s+margin\s*(?:of|at|was|stood at)?\s*$", before[-30:], re.I):
                pat, rev = metric_row(rows, "pat", basis_here), metric_row(rows, "revenue", basis_here)
                if quarter and pat and rev and pat.cr("Q") is not None and rev.cr("Q"):
                    if subject is not None and metric == "pat" and scol == "Q":
                        pat = subject
                    true = pat.cr("Q") / rev.cr("Q") * 100
                    if abs(float(m.group("num")) - true) > max(0.3, 0.02 * abs(true)):
                        new = f"{true:.{max(0, min(1, _decimals(m.group('num'))))}f}"
                        edits.append((off + m.start("num"), off + m.end("num"), new))
                        fixes.append(f"PAT margin {m.group(0)} → {new}%")
            elif kind in ("p", "x"):
                after = clause[m.end():m.end() + 40]
                if re.match(r"\s*(?:margin|of (?:revenue|sales)|share|stake|tax|contribution|utili[sz]ation)", after, re.I) \
                        or re.search(r"margin\s*(?:of|at|was|stood at)?\s*$", before[-30:], re.I):
                    continue        # a margin or a share, not a change
                if named and named[1] > subject_end and quarter and not qual and \
                        named[0] in ("revenue", "pat", "eps", "pbt", "total_income"):
                    row = metric_row(rows, named[0], basis_here)     # "PAT up 7.7x YoY" — named, without its figure
                    if row is not None and row.cr("Q") is not None:
                        subject, scol, subject_val, metric, subject_end = row, "Q", row.cr("Q"), named[0], named[1]
                elif named and named[1] > subject_end and qual:
                    subject = None                              # "Engineering Services revenue fell 94%": not checkable
                if subject is None:
                    continue
                own = _tag_after(clause, m.end()) or _tag_before(before[-25:])
                tag = own or line_tag
                if own:
                    line_tag = own
                col = BASES.get(scol, {}).get(tag or "")
                if col is None:
                    continue
                rng = pct_range(subject, scol, col)
                if rng is None:
                    continue
                true = (rng[0] + rng[1]) / 2
                if not own:            # the period is only implied: either comparison may be meant
                    other = [pct_range(subject, scol, c) for c in BASES.get(scol, {}).values() if c != col]
                    alt = [r for r in other if r]
                else:
                    alt = []
                dword = _direction(before)
                if kind == "p":
                    claimed = float(m.group("num")) * (-1 if m.group("sign") in ("-", "−") else 1)
                    if dword and dword.group(1).lower() in {b for _, b in UP_DOWN}:
                        claimed = -abs(claimed)
                    if any(lo - 0.6 <= claimed <= hi + 0.6 for lo, hi in [rng] + alt):
                        continue
                    new = f"{abs(true):.{max(0, min(1, _decimals(m.group('num'))))}f}"
                    edits.append((off + m.start("num"), off + m.end("num"), new))
                    if m.group("sign"):
                        edits.append((off + m.start("sign"), off + m.end("sign"), "+" if true >= 0 else "-"))
                    fixes.append(f"{metric or 'figure'} {tag} {m.group(0).strip()} → {abs(true):.1f}%")
                else:
                    claimed = float(m.group("num"))
                    ratio = 1 + true / 100
                    if any(1 + lo / 100 - 0.1 * claimed <= claimed <= 1 + hi / 100 + 0.1 * claimed for lo, hi in [rng] + alt):
                        continue
                    new = f"{ratio:.1f}x" if ratio >= 2 else f"{abs(true):.0f}%"
                    edits.append((off + m.start(), off + m.end(), new))
                    fixes.append(f"{metric or 'figure'} {tag} {m.group(0).strip()} → {new}")
                if dword and (true >= 0) != (dword.group(1).lower() in {a for a, _ in UP_DOWN}):
                    w = dword.group(1)
                    edits.append((off + dword.start(1), off + dword.end(1), _case(FLIP[w.lower()], w)))
    for s_, e in gone:
        fixes.append(f"left out (figure not in the filing): {line[s_:e].strip()[:90]}")
    dropped = [line[s_:e].strip() for s_, e in gone]
    spans = sorted(set(edits) | {(s_, e, "") for s_, e in gone}, reverse=True)
    for s_, e, new in spans:
        if any(gs <= s_ and e <= ge and new for gs, ge in gone):
            continue                       # an edit inside a clause that is left out anyway
        line = line[:s_] + new + line[e:]
    line = re.sub(r"\s*;\s*(?=;|$)", "", re.sub(r"^\s*;\s*", "", line)).strip(" ;")
    return line, bool(line), dropped


def margins(rows: list[Row], basis: str | None) -> dict[str, dict[str, float]]:
    """EBITDA margin % per column, every way it is commonly stated: printed in the filing, printed EBITDA ÷ revenue,
    and computed from the P&L lines (PBT − exceptional + finance cost + depreciation, without / with other income)."""
    out: dict[str, dict[str, float]] = {}
    rev = metric_row(rows, "revenue", basis)
    if rev is None:
        return out
    for r in rows:
        if r.page == rev.page or r.basis == rev.basis:
            if re.match(r"^\W*(?:operational\s+|operating\s+)?ebitda\s+margin", r.label, re.I) and "Q" in r.cols:
                out.setdefault("printed", {c: r.at(c) for c in ("Q", "PQ", "YQ") if r.at(c) is not None})
            elif re.match(r"^\W*(?:operational\s+|operating\s+)?ebitda\b(?!.*margin)", r.label, re.I) and "Q" in r.cols:
                out.setdefault("printed_ratio", {c: r.cr(c) / rev.cr(c) * 100 for c in ("Q", "PQ", "YQ")
                                                 if r.cr(c) is not None and rev.cr(c)})
    get = {k: metric_row(rows, k, rev.basis) for k in ("pbt", "exceptional", "finance", "depreciation", "other_income")}
    if all(get[k] is not None and get[k].page == rev.page for k in ("pbt", "finance", "depreciation")):
        ex, inc = {}, {}
        for c in ("Q", "PQ", "YQ"):
            vals = {k: (get[k].cr(c) if get[k] is not None and get[k].page == rev.page else None) for k in get}
            if None in (vals["pbt"], vals["finance"], vals["depreciation"], rev.cr(c)) or not rev.cr(c):
                continue
            e = vals["pbt"] - (vals["exceptional"] or 0) + vals["finance"] + vals["depreciation"]
            inc[c] = e / rev.cr(c) * 100
            ex[c] = (e - (vals["other_income"] or 0)) / rev.cr(c) * 100
        if ex:
            out["excl_other_income"], out["incl_other_income"] = ex, inc
    return out


def _unit_slip(n: float, raw: str, rows: list[Row], basis: str | None, metric: str | None = None,
               clause: str = "") -> tuple[float, str] | None:
    """A figure written as ₹ crore that is, unconverted, the printed value of the row the line talks about (its
    metric, or a row whose name the clause uses) on a page in another unit — and isn't in the filing as crore."""
    names = {0.01: "₹ lakh", 0.1: "₹ million", 1e-4: "₹ thousand"}
    words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", clause)} - GENERIC
    for r in rows:
        if r.eps or not r.unit or r.unit == 1 or (basis and r.basis not in (basis, None)):
            continue
        rx = METRICS.get(metric or "")
        about = rx.search(r.label) if rx else bool(words & {w.lower() for w in re.findall(r"[A-Za-z]{4,}", r.label)})
        if not about:
            continue
        for x in r.cells:
            if x is not None and x != 0 and abs(abs(x) - n) <= 0.51 * 10 ** -_decimals(raw):
                return abs(x) * r.unit, names.get(r.unit, "another unit")
    return None


def _owners_or_total(rows: list[Row], row: Row, v: float, raw: str) -> Row | None:
    """The owners' share or the total PAT of the same statement, if the stated figure is that one."""
    for r in rows:
        if r.page == row.page and r.basis == row.basis and "Q" in r.cols and r.cr("Q") is not None and \
                (METRICS["pat_owners"].search(r.label) or METRICS["pat"].search(r.label)) and \
                not re.search(r"comprehensive|discontinued|before", r.label, re.I) and _close(v, r.cr("Q"), raw):
            return r
    return None


def _any_tag(clause: str) -> str | None:
    y, q = YOY.search(clause), QOQ.search(clause)
    return "yoy" if y and not q else "qoq" if q and not y else None


def _in_text_raw(raw: str, pages: list[str]) -> bool:
    return any(raw in p for p in pages)


def _direction(before: str):
    hits = list(DIRECTION.finditer(before[-30:]))
    if not hits:
        return None
    h = hits[-1]

    class M:            # positions relative to `before`
        def __init__(self, h, shift):
            self._h, self._s = h, shift

        def group(self, i):
            return self._h.group(i)

        def start(self, i):
            return self._h.start(i) + self._s

        def end(self, i):
            return self._h.end(i) + self._s
    return M(h, max(0, len(before) - 30))


def _case(word: str, like: str) -> str:
    return word.capitalize() if like[:1].isupper() else word


def _tag_after(clause: str, pos: int) -> str | None:
    nxt = clause[pos:pos + 45]
    stop = re.search(r"[,;]|\band\b|₹|Rs\.?", nxt)
    window = nxt[:stop.start()] if stop else nxt
    if YOY.search(window):
        return "yoy"
    if QOQ.search(window):
        return "qoq"
    # "from ₹z Cr YoY": the tag after the base
    m = re.match(r"\s*(?:from|vs\.?|versus|against)\s*(?:₹|Rs\.?|INR)?\s?[\d,.]+\s*(?:Cr|crores?|lakhs?|mn|million)?\s*", nxt, re.I)
    if m:
        rest = nxt[m.end():m.end() + 12]
        if YOY.match(rest):
            return "yoy"
        if QOQ.match(rest):
            return "qoq"
    return None


def _tag_before(before: str) -> str | None:
    tail = before[-25:]
    y, q = list(YOY.finditer(tail)), list(QOQ.finditer(tail))
    if y and (not q or y[-1].end() > q[-1].end()):
        return "yoy"
    if q:
        return "qoq"
    return None
