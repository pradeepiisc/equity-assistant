"""
BSE-only stocks (no NSE listing → no NSE RSS, SHP or results XBRL): a gentle Screener fallback.

BSE's own announcement API refuses non-browser clients (HTTP 403), so filings are found on the
Screener company page — loaded through the logged-in Chrome debug session (skills/cdp_helper),
never with httpx — while the PDFs themselves are downloaded straight from bseindia.com, which
serves them to plain clients, and go through the normal document pipeline (P2).

Rules:
  • the dedicated debug profile (~/.config/chrome_screener_debug, port 9222) is started when it isn't running
    (launch_chrome; at most every 30 min, never touching your own Chrome windows)
  • ≥ min_gap_s between Screener requests; ≤ daily_pages page loads a day; ≤ per_tick companies a tick
  • a held / watched company is checked every check_hours.meeting on its board-meeting day and the day after
    (the date its own BSE intimation gives), check_hours.season in results season, else check_hours.quiet;
    a new period column in the results table becomes a results event at once (the PDF read refines it)
  • the first sign of a block (rate-limit / captcha / access-denied page, or a page without the
    expected sections) stops the fallback for the rest of the day with one high alert — no retries
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import date, datetime, timedelta

from . import classify
from .config import COMPANIES_DIR
from .db import add_event, kv_get, kv_set
from .util import money_to_crore, now_ist

BLOCK_RX = re.compile(r"too many requests|captcha|are you (a )?(human|robot)|access denied|unusual traffic|"
                      r"rate[- ]limit|cf-chl|challenge-platform|please wait while we verify", re.I)
SCREENER_HOLDER_CLASSES = {"foreign_institutions": "FIIs (Individual)", "domestic_institutions": "DIIs (Individual)",
                           "public": "Public Anchor Investors", "promoters": "Promoters (Individual)"}


def _cfg(settings: dict) -> dict:
    return settings["screener_fallback"]


CHROME_APP = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_PROFILE = "~/.config/chrome_screener_debug"


def launch_chrome(conn: sqlite3.Connection) -> bool:
    """Start the dedicated debug profile in the background (a second Chrome instance; yours is left alone)."""
    import os
    import subprocess

    last = kv_get(conn, "chrome_launched_at")
    if last and (now_ist() - datetime.fromisoformat(last)).total_seconds() < 1800:
        return False
    kv_set(conn, "chrome_launched_at", now_ist().isoformat(timespec="seconds"))
    if not os.path.exists(CHROME_APP):
        return False
    profile = os.path.expanduser(CHROME_PROFILE)
    os.makedirs(profile, exist_ok=True)
    subprocess.Popen([CHROME_APP, "--remote-debugging-port=9222", "--remote-allow-origins=*", "--no-first-run",
                      "--no-default-browser-check", f"--user-data-dir={profile}", "about:blank"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return True


def candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM stocks WHERE nse_symbol IS NULL AND bse_code IS NOT NULL AND "
                        "(in_portfolio=1 OR in_watchlist=1 OR in_notes=1)").fetchall()


def _latest_file(folder: str | None, sub: str, kind: str):
    if not folder:
        return None
    files = sorted((COMPANIES_DIR / folder / sub).glob(f"*_{kind}_*.txt"))
    return files[-1] if files else None


def _file_date(path) -> date | None:
    m = re.search(r"_(\d{4})(\d{2})(\d{2})\.txt$", path.name) if path else None
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _quarter_end_before(d: date) -> date:
    for y, m, dd in ((d.year, 9, 30), (d.year, 6, 30), (d.year, 3, 31), (d.year - 1, 12, 31)):
        if date(y, m, dd) < d:
            return date(y, m, dd)
    return date(d.year - 1, 9, 30)


_MEETING_RX = re.compile(r"(?:held|scheduled|convened)\s+on\s+(?:\w+,\s*)?(\d{1,2})(?:st|nd|rd|th)?[\s\-/.]+"
                         r"([A-Za-z]{3,9}|\d{1,2})[\s\-/.,]+(\d{4})", re.I)


_MEETING_RX2 = re.compile(r"(?:held|scheduled|convened)\s+on\s+(?:\w+,\s*)?([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", re.I)


def meeting_date(text: str) -> date | None:
    """The date a board-meeting intimation names ("held on 02-Nov-2026 / 2nd November, 2026 / November 2, 2026")."""
    m = _MEETING_RX.search(text or "")
    m2 = None if m else _MEETING_RX2.search(text or "")
    if not m and not m2:
        return None
    d, mon, y = m.groups() if m else (m2.group(2), m2.group(1), m2.group(3))
    for fmt in ("%d %b %Y", "%d %B %Y", "%d %m %Y"):
        try:
            return datetime.strptime(f"{d} {mon[:3] if fmt == '%d %b %Y' else mon} {y}", fmt).date()
        except ValueError:
            continue
    return None


def check_every_h(conn: sqlite3.Connection, s: sqlite3.Row, today: date, cfg: dict) -> float:
    ch = cfg["check_hours"]
    bm = kv_get(conn, f"bse_meeting:{s['sid']}")
    if bm and 0 <= (today - date.fromisoformat(bm)).days <= 1:
        return float(ch["meeting"])
    since_q = (today - _quarter_end_before(today)).days
    return float(ch["season"] if 10 <= since_q <= 62 else ch["quiet"])


def due(conn: sqlite3.Connection, s: sqlite3.Row, today: date, cfg: dict) -> str | None:
    checked = kv_get(conn, f"scr_checked:{s['sid']}")
    if s["in_portfolio"] or s["in_watchlist"]:
        every = check_every_h(conn, s, today, cfg)
        if not checked or (now_ist() - datetime.fromisoformat(checked)).total_seconds() >= every * 3600:
            return f"check every {every:g} h" + (" (board meeting)" if every == cfg["check_hours"]["meeting"] else "")
        return None
    last_s = kv_get(conn, f"scr_fetched:{s['sid']}")
    fin = _latest_file(s["folder"], "financials", "financials")
    sh = _latest_file(s["folder"], "shareholding", "shareholding")
    last = date.fromisoformat(last_s) if last_s else max(filter(None, (_file_date(fin), _file_date(sh))), default=None)
    age = (today - last).days if last else None
    q_end = _quarter_end_before(today)
    since_q = (today - q_end).days
    # results season: ~2 weeks after quarter end until the filing deadline (45 days, 60 for March)
    in_results = 14 <= since_q <= (62 if q_end.month == 3 else 48)
    if in_results and (age is None or age >= cfg["results_refresh_days"]):
        exp = q_end.strftime("%b %Y")                          # the quarter being reported now
        if not fin or exp not in fin.read_text(errors="replace").split("## Quarterly Results", 1)[-1][:400]:
            return f"results season ({exp} not in the latest file)"
    # shareholding is due within 21 days of quarter end
    if 21 <= since_q <= 45 and (last is None or last < q_end + timedelta(days=21)):
        return f"shareholding for {q_end.strftime('%b %Y')}"
    if age is None or age >= cfg["stale_days"]:
        return "never fetched" if age is None else f"not refreshed in {age} days"
    return None


def _hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def state(conn: sqlite3.Connection, settings: dict) -> dict:
    from skills import cdp_helper

    cfg = _cfg(settings)
    today = now_ist().date()
    rows = []
    for s in candidates(conn):
        rows.append({"sid": s["sid"], "name": s["name"], "due": due(conn, s, today, cfg),
                     "last": kv_get(conn, f"scr_checked:{s['sid']}") or kv_get(conn, f"scr_fetched:{s['sid']}"),
                     "meeting": kv_get(conn, f"bse_meeting:{s['sid']}")})
    return {"enabled": cfg["enabled"], "chrome": cdp_helper.is_available(),
            "blocked_on": kv_get(conn, "screener_blocked_on"), "block_reason": kv_get(conn, "screener_block_reason"),
            "today": kv_get(conn, f"scr_pages:{today.isoformat()}") or 0, "cap": cfg["daily_pages"],
            "last_request": kv_get(conn, "scr_last_request"), "stocks": rows,
            "due": sum(1 for r in rows if r["due"])}


class Blocked(Exception):
    pass


def _gap(conn: sqlite3.Connection, cfg: dict) -> None:
    last = kv_get(conn, "scr_last_request")
    if last:
        wait = cfg["min_gap_s"] - (now_ist() - datetime.fromisoformat(last)).total_seconds()
        if wait > 0:
            time.sleep(wait)
    kv_set(conn, "scr_last_request", now_ist().isoformat(timespec="seconds"))
    key = f"scr_pages:{now_ist().date().isoformat()}"       # every Screener load counts (pages and holder lists)
    kv_set(conn, key, (kv_get(conn, key) or 0) + 1)
    conn.commit()


def _page(conn: sqlite3.Connection, url: str, cfg: dict) -> str:
    from skills import cdp_helper

    _gap(conn, cfg)
    html = cdp_helper.fetch_page_html(url)
    if not html:
        raise Blocked("Chrome returned nothing")
    if BLOCK_RX.search(html[:20000]) and 'id="quarters"' not in html:
        raise Blocked("rate-limit / verification page")
    if 'id="quarters"' not in html and 'id="shareholding"' not in html:
        raise NoPage("page without the financial sections (wrong URL, or Screener has no page)")
    return html


class NoPage(Exception):
    """One company's page is unusable — skip it for now; not a sign of being blocked."""


def _json(conn: sqlite3.Connection, url: str, cfg: dict) -> dict | None:
    from skills import cdp_helper

    _gap(conn, cfg)
    raw = cdp_helper.navigate_and_evaluate(url, "document.body ? document.body.innerText : ''", wait=2.0)
    if raw and BLOCK_RX.search(raw[:2000]):
        raise Blocked("rate-limit / verification page on the holders list")
    try:
        data = json.loads(raw or "")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _screener_url(s: sqlite3.Row) -> str:
    meta = COMPANIES_DIR / (s["folder"] or "") / "meta.json"
    if s["folder"] and meta.exists():
        try:
            u = json.loads(meta.read_text()).get("screener_url")
            if u:
                return u.rstrip("/") + "/"
        except ValueError:
            pass
    return f"https://www.screener.in/company/{s['bse_code']}/"


def _ensure_folder(conn: sqlite3.Connection, s: sqlite3.Row) -> str:
    if s["folder"]:
        return s["folder"]
    from skills.company_meta import store_company_meta

    folder = s["bse_code"]
    store_company_meta(symbol=folder, name=s["name"] or "", screener_url=_screener_url(s), sector=s["sector"] or "")
    conn.execute("UPDATE stocks SET folder=? WHERE sid=?", (folder, s["sid"]))
    return folder


def _shareholding(soup) -> dict:
    """The quarterly shareholding table on the company page (same shape skills.shareholding_fetcher builds)."""
    section = soup.find(id="shareholding")
    table = section.find("table") if section else None
    if not table:
        return {}
    quarters, rows, lines = [], [], []
    thead = table.find("thead")
    if thead:
        quarters = [c.get_text(strip=True) for c in thead.find_all(["th", "td"])[1:]]
        lines += ["| Category | " + " | ".join(quarters) + " |", "|" + "---|" * (len(quarters) + 1)]
    tbody = table.find("tbody")
    for tr in tbody.find_all("tr") if tbody else []:
        cells = tr.find_all(["td", "th"])
        if cells:
            cat, vals = cells[0].get_text(strip=True), [c.get_text(strip=True) for c in cells[1:]]
            rows.append({"category": cat, "values": vals})
            lines.append(f"| {cat} | " + " | ".join(vals) + " |")
    return {"quarters": quarters, "rows": rows, "raw_text": "\n".join(lines), "individual": {}}


def _documents(soup) -> list[dict]:
    """PDF links in the page's Documents section that live on bseindia.com, with their label and nearby text."""
    docs_section = soup.find(id="documents") or soup
    out, seen = [], set()
    for a in docs_section.find_all("a", href=True):
        href = a["href"]
        if "bseindia.com" not in href or ".pdf" not in href.lower() or href in seen:
            continue
        seen.add(href)
        label = a.get_text(" ", strip=True)
        parent = a.find_parent(["li", "div", "tr"])
        nearby = parent.get_text(" ", strip=True).replace(label, "").strip() if parent else ""
        heading = ""
        block = a.find_parent(lambda t: t.name in ("div", "section") and t.find(["h3", "h2"]))
        if block and block.find(["h3", "h2"]):
            heading = block.find(["h3", "h2"]).get_text(strip=True)
        out.append({"url": href, "label": label, "nearby": nearby, "heading": heading})
    return out


_DATE_RX = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})|([A-Za-z]{3})\s+(\d{4})")


_REL_RX = re.compile(r"(?<![\w.])(\d{1,3})\s*(m|min|h|hr|d)\b", re.I)
_DM_RX = re.compile(r"(?<!\d)(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b(?!\s+\d{4})")


def _doc_date(text: str, today: date) -> date | None:
    """The filing's date from Screener's label: "5 Oct 2026", "Oct 2026", "5 Oct" (this year, or last year if that
    would be in the future), or an age like "9h" / "2d". None when the label carries no date."""
    m = _DATE_RX.search(text or "")
    try:
        if m and m.group(1):
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y").date()
        dm = _DM_RX.search(text or "")
        if dm:
            d = datetime.strptime(f"{dm.group(1)} {dm.group(2)} {today.year}", "%d %b %Y").date()
            return d if d <= today else d.replace(year=today.year - 1)
        rel = _REL_RX.search(text or "")
        if rel:
            n, unit = int(rel.group(1)), rel.group(2).lower()
            return today - timedelta(days=n) if unit == "d" else today
        if m:
            return datetime.strptime(f"15 {m.group(4)} {m.group(5)}", "%d %b %Y").date()
    except ValueError:
        return None
    return None


def fetch_one(conn: sqlite3.Connection, s: sqlite3.Row, settings: dict) -> dict:
    from bs4 import BeautifulSoup
    from skills import financial_fetcher, shareholding_fetcher

    from .documents import KIND_WEIGHT, _insert, quarter_before
    from .universe import _financial_summary

    cfg = _cfg(settings)
    today = now_ist().date()
    stamp = today.strftime("%Y%m%d")
    url = _screener_url(s)
    html = _page(conn, url, cfg)
    requests = 1
    folder = _ensure_folder(conn, s)
    soup = BeautifulSoup(html, "lxml")
    out: dict = {"sid": s["sid"]}

    fin_text = financial_fetcher._parse_financials(html)
    if financial_fetcher._looks_like_useful_financial_text(fin_text):
        prev_fin = _latest_file(folder, "financials", "financials")
        quick = screener_results(prev_fin.read_text(errors="replace") if prev_fin else "", fin_text)
        if quick:
            from . import results as results_mod
            quick.update(url=url, basis="consolidated" if "/consolidated" in url else "standalone")
            results_mod.upsert(conn, s["sid"], quick.pop("period"), numbers=quick, url=url,
                               ts=now_ist().isoformat(timespec="seconds"))
            out["results"] = True
        d = COMPANIES_DIR / folder / "financials"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{folder}_financials_{stamp}.txt").write_text(fin_text, encoding="utf-8")
        mcap, sales = _financial_summary(folder)
        conn.execute("UPDATE stocks SET mcap_cr=COALESCE(?, mcap_cr), sales_ttm_cr=COALESCE(?, sales_ttm_cr) WHERE sid=?",
                     (mcap, sales, s["sid"]))
        out["financials"] = True

    data = _shareholding(soup)
    if data.get("quarters"):
        prev = _latest_file(folder, "shareholding", "shareholding")
        prev_text = prev.read_text(errors="replace") if prev else ""
        new_quarter = data["quarters"][-1] not in prev_text.split("## Individual Holder Detail")[0]
        tag = soup.find(attrs={"data-url": re.compile(r"/trades/company-\d+/")})
        cid = re.search(r"/trades/company-(\d+)/", tag["data-url"]).group(1) if tag else None
        if cid and (new_quarter or not prev):
            for cls, label in SCREENER_HOLDER_CLASSES.items():   # the named-holder lists, paced like the page
                raw = _json(conn, f"https://www.screener.in/api/3/{cid}/investors/{cls}/quarterly/", cfg)
                requests += 1
                if not raw:
                    continue
                inv = [{"name": n, "values": {q: v[q] for q in data["quarters"] if q in v}}
                       for n, v in raw.items() if isinstance(v, dict)]
                inv = [i for i in inv if i["values"]]
                if inv:
                    data["individual"][label] = shareholding_fetcher._merge_similar_investors(inv)
        if new_quarter or not prev or data["individual"]:
            d = COMPANIES_DIR / folder / "shareholding"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{folder}_shareholding_{stamp}.txt").write_text(
                shareholding_fetcher._format_report(folder, s["name"] or folder, data), encoding="utf-8")
            out["shareholding"] = data["quarters"][-1]

    usd = settings["orders"]["usd_inr"]
    since = today - timedelta(days=120)
    n_docs = n_events = 0
    for doc in _documents(soup):
        text = f"{doc['label']} {doc['nearby']}"
        when = _doc_date(text, today)
        if when is None or when < since:
            continue                        # undated (e.g. an old annual report) or old: not news
        heading = doc["heading"].lower()
        c = classify.classify_announcement(f"{text} |SUBJECT: {doc['label']}", sales_ttm_cr=s["sales_ttm_cr"],
                                           high_pct=settings["orders"]["high_pct_of_revenue"], usd_inr=usd)
        if "concall" in heading or re.search(r"transcript", text, re.I):
            kind = "transcript"
        elif re.search(r"\bppt\b|presentation", text, re.I):
            kind = "presentation"
        else:
            kind = {"order_win": "order_win", "capacity": "capacity", "acquisition": "acquisition",
                    "results": "results"}.get(c.type, "press_release" if c.type == "update" else None)
        if c.type == "board_meeting":
            md = meeting_date(text)
            if md and md >= today - timedelta(days=1):
                kv_set(conn, f"bse_meeting:{s['sid']}", md.isoformat())
        if kind is None or c.type == "routine":
            continue
        filed = f"{when.isoformat()}T00:00:00+05:30"
        eid = None
        if "announcement" in heading or kind in ("order_win", "capacity", "acquisition"):
            # a results filing stays low: the quarter's results event (from the table above, then the AI read) carries it
            eid = add_event(conn, sid=s["sid"], ts=filed, kind="filing", type=c.type,
                            severity="low" if c.type == "results" else c.severity, title=c.label,
                            detail=text[:400], value_cr=c.value_cr if c.value_cr is not None else money_to_crore(text, usd),
                            url=doc["url"], source="BSE via Screener", data=c.data, dedupe=f"bse:{doc['url']}")
            n_events += 1 if eid else 0
        new = _insert(conn, ukey=doc["url"], sid=s["sid"], kind=kind, origin="screener", url=doc["url"],
                      title=text[:300], filed_at=filed, period=quarter_before(when),
                      priority=100 + KIND_WEIGHT.get(kind, 0), backfill=0, event_id=eid)
        n_docs += 1 if new else 0
    out.update(documents=n_docs, events=n_events, requests=requests)
    return out


_MON = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _table(text: str) -> tuple[list[str], dict[str, list]]:
    part = text.split("## Quarterly Results", 1)
    if len(part) < 2:
        return [], {}
    lines = [ln for ln in part[1].split("\n---", 1)[0].splitlines() if "|" in ln]
    if not lines:
        return [], {}
    cols = [c.strip() for c in lines[0].split("|") if c.strip()]
    rows = {}
    for ln in lines[1:]:
        cells = [c.strip() for c in ln.split("|")]
        name = cells[0].rstrip(" +").replace(" %", "").strip()
        vals = []
        for c in cells[1:1 + len(cols)]:
            try:
                vals.append(float(c.replace(",", "").rstrip("%")))
            except ValueError:
                vals.append(None)
        rows[name] = vals
    return cols, rows


def screener_results(prev_text: str, new_text: str) -> dict | None:
    """A results-table column that wasn't in the previous file → that period's numbers, with YoY / sequential change."""
    cols, rows = _table(new_text)
    pcols, _ = _table(prev_text)
    if not cols or not pcols or cols[-1] in pcols or not re.match(r"[A-Z][a-z]{2} \d{4}$", cols[-1]):
        return None
    mon, yr = cols[-1].split()
    m, y = _MON.get(mon), int(yr)
    if not m:
        return None
    half = len(cols) >= 2 and re.match(r"[A-Z][a-z]{2} \d{4}$", cols[-2]) and \
        ((y * 12 + m) - (int(cols[-2].split()[1]) * 12 + _MON.get(cols[-2].split()[0], 0))) == 6
    fy = y + 1 if m >= 4 else y
    period = (f"H{1 if m == 9 else 2} FY{fy % 100:02d}" if half else f"Q{((m - 4) % 12) // 3 + 1} FY{fy % 100:02d}")
    back = 2 if half else 4

    def at(name, i):
        v = rows.get(name) or []
        return v[i] if -len(v) <= i < len(v) else None

    def pct(a, b):
        return round((a / b - 1) * 100, 1) if a is not None and b not in (None, 0) and b > 0 else None
    rev, pat = at("Sales", -1) if "Sales" in rows else at("Revenue", -1), at("Net Profit", -1)
    if rev is None:
        return None
    rev_y = at("Sales", -1 - back) if "Sales" in rows else at("Revenue", -1 - back)
    rev_q = at("Sales", -2) if "Sales" in rows else at("Revenue", -2)
    opm, opm_y = at("OPM", -1), at("OPM", -1 - back)
    return {"period": period, "source": "screener", "revenue_cr": rev, "pat_cr": pat, "eps": at("EPS in Rs", -1),
            "ebitda_cr": at("Operating Profit", -1), "ebitda_margin_pct": opm,
            "ebitda_margin_yoy_bp": round((opm - opm_y) * 100) if opm is not None and opm_y is not None else None,
            "revenue_yoy_pct": pct(rev, rev_y), "revenue_qoq_pct": None if half else pct(rev, rev_q),
            "pat_yoy_pct": pct(pat, at("Net Profit", -1 - back)), "pat_qoq_pct": None if half else pct(pat, at("Net Profit", -2)),
            "verified": 1.0}


def run(conn: sqlite3.Connection, settings: dict) -> dict:
    """Up to per_tick due companies per call (the scheduler calls it every tick)."""
    from skills import cdp_helper

    cfg = _cfg(settings)
    if not cfg["enabled"]:
        return {"skipped": "disabled"}
    now = now_ist()
    today = now.date().isoformat()
    (h0, m0), (h1, m1) = _hm(cfg["window"][0]), _hm(cfg["window"][1])
    if not ((now.hour, now.minute) >= (h0, m0) and (now.hour, now.minute) < (h1, m1)):
        return {"skipped": "outside the fetch window"}
    if kv_get(conn, "screener_blocked_on") == today:
        return {"skipped": f"paused for today: {kv_get(conn, 'screener_block_reason')}"}
    if (kv_get(conn, f"scr_pages:{today}") or 0) >= cfg["daily_pages"]:
        return {"skipped": f"daily cap of {cfg['daily_pages']} Screener pages reached"}
    due_list = [(due(conn, s, now.date(), cfg), s) for s in candidates(conn)]
    due_list = [(r, s) for r, s in due_list if r]
    if not due_list:
        return {"skipped": "nothing due"}
    if not cdp_helper.is_available():
        started = cfg.get("launch_chrome", True) and launch_chrome(conn)
        return {"skipped": "Chrome debug session not running" + (" — starting it" if started else ""), "due": len(due_list)}
    due_list.sort(key=lambda rs: kv_get(conn, f"scr_checked:{rs[1]['sid']}") or "")
    done = []
    for reason, s in due_list[: int(cfg["per_tick"])]:
        if (kv_get(conn, f"scr_pages:{today}") or 0) >= cfg["daily_pages"]:
            break
        try:
            res = fetch_one(conn, s, settings)
        except NoPage as exc:
            kv_set(conn, f"scr_checked:{s['sid']}", now_ist().isoformat(timespec="seconds"))
            done.append({"sid": s["sid"], "error": str(exc)})
            conn.commit()
            continue
        except Blocked as exc:
            kv_set(conn, "screener_blocked_on", today)
            kv_set(conn, "screener_block_reason", str(exc))
            add_event(conn, sid=None, ts=now.isoformat(timespec="seconds"), kind="system", type="screener_blocked",
                      severity="high", title=f"Screener fallback paused for today — {exc}",
                      detail=f"While fetching {s['sid']} for BSE-only coverage. Nothing retries until tomorrow.",
                      source="tracker", dedupe=f"scr_block:{today}")
            return {"blocked": str(exc), "sid": s["sid"], "done": done}
        kv_set(conn, f"scr_fetched:{s['sid']}", today)
        kv_set(conn, f"scr_checked:{s['sid']}", now_ist().isoformat(timespec="seconds"))
        res["reason"] = reason
        done.append(res)
        conn.commit()
    return {"sid": ",".join(d["sid"] for d in done), "done": done}
