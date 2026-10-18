"""
BSE-only stocks (no NSE listing → no NSE RSS, SHP or results XBRL): a gentle Screener fallback.

BSE's own announcement API refuses non-browser clients (HTTP 403), so filings are found on the
Screener company page — loaded through the logged-in Chrome debug session (skills/cdp_helper),
never with httpx — while the PDFs themselves are downloaded straight from bseindia.com, which
serves them to plain clients, and go through the normal document pipeline (P2).

Rules:
  • never launches Chrome — skipped (and shown on the status page) when no debug session runs
  • ≥ min_gap_s between Screener requests; ≤ daily_cap companies a day, spread across the window
    (at most one company per window ÷ cap minutes)
  • a company is fetched only when due: results season and its latest quarter missing (weekly),
    the shareholding window after a quarter end (once), or not refreshed for stale_days
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


def due(conn: sqlite3.Connection, s: sqlite3.Row, today: date, cfg: dict) -> str | None:
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
                     "last": kv_get(conn, f"scr_fetched:{s['sid']}")})
    return {"enabled": cfg["enabled"], "chrome": cdp_helper.is_available(),
            "blocked_on": kv_get(conn, "screener_blocked_on"), "block_reason": kv_get(conn, "screener_block_reason"),
            "today": kv_get(conn, f"scr_count:{today.isoformat()}") or 0, "cap": cfg["daily_cap"],
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
        raise Blocked("page without the financial sections")
    return html


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


def _doc_date(text: str, today: date) -> date | None:
    m = _DATE_RX.search(text or "")
    if not m:
        return None
    try:
        if m.group(1):
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y").date()
        return datetime.strptime(f"15 {m.group(4)} {m.group(5)}", "%d %b %Y").date()
    except ValueError:
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
        when = _doc_date(text, today) or today
        if when < since:
            continue
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
        if kind is None or c.type == "routine":
            continue
        filed = f"{when.isoformat()}T00:00:00+05:30"
        eid = None
        if "announcement" in heading or kind in ("order_win", "capacity", "acquisition"):
            eid = add_event(conn, sid=s["sid"], ts=filed, kind="filing", type=c.type, severity=c.severity, title=c.label,
                            detail=text[:400], value_cr=c.value_cr if c.value_cr is not None else money_to_crore(text, usd),
                            url=doc["url"], source="BSE via Screener", data=c.data, dedupe=f"bse:{doc['url']}")
            n_events += 1 if eid else 0
        new = _insert(conn, ukey=doc["url"], sid=s["sid"], kind=kind, origin="screener", url=doc["url"],
                      title=text[:300], filed_at=filed, period=quarter_before(when),
                      priority=100 + KIND_WEIGHT.get(kind, 0), backfill=0, event_id=eid)
        n_docs += 1 if new else 0
    out.update(documents=n_docs, events=n_events, requests=requests)
    return out


def run(conn: sqlite3.Connection, settings: dict) -> dict:
    """At most one due company per call (the scheduler calls it every tick)."""
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
    count_key = f"scr_count:{today}"
    count = kv_get(conn, count_key) or 0
    if count >= cfg["daily_cap"]:
        return {"skipped": f"daily cap of {cfg['daily_cap']} reached"}
    window_min = (h1 * 60 + m1) - (h0 * 60 + m0)
    spread = window_min / cfg["daily_cap"]
    last = kv_get(conn, "scr_last_company_at")
    if last and (now - datetime.fromisoformat(last)).total_seconds() < spread * 60:
        return {"skipped": "spacing companies across the day"}
    due_list = [(due(conn, s, now.date(), cfg), s) for s in candidates(conn)]
    due_list = [(r, s) for r, s in due_list if r]
    if not due_list:
        return {"skipped": "nothing due"}
    if not cdp_helper.is_available():
        return {"skipped": "Chrome debug session not running", "due": len(due_list)}
    due_list.sort(key=lambda rs: kv_get(conn, f"scr_fetched:{rs[1]['sid']}") or "")
    reason, s = due_list[0]
    kv_set(conn, "scr_last_company_at", now.isoformat(timespec="seconds"))
    try:
        res = fetch_one(conn, s, settings)
    except Blocked as exc:
        kv_set(conn, "screener_blocked_on", today)
        kv_set(conn, "screener_block_reason", str(exc))
        add_event(conn, sid=None, ts=now.isoformat(timespec="seconds"), kind="system", type="screener_blocked",
                  severity="high", title=f"Screener fallback paused for today — {exc}",
                  detail=f"While fetching {s['sid']} for BSE-only coverage. Nothing retries until tomorrow.",
                  source="tracker", dedupe=f"scr_block:{today}")
        return {"blocked": str(exc), "sid": s["sid"]}
    kv_set(conn, f"scr_fetched:{s['sid']}", today)
    kv_set(conn, count_key, count + 1)
    res["reason"] = reason
    return res
