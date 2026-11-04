"""
NSE corporate-filing RSS feeds (https://www.nseindia.com/rss-feed) → events.

The feeds are rolling windows (20–60 latest items), so they are polled every few
minutes; every item is stored once (feed_items) and items for tracked stocks become
events. BSE-only stocks are not covered by these feeds.

A rolling window loses whatever was filed while the laptop slept or the server was off,
and can overflow in a results-season burst. `catch_up` therefore reads NSE's own
date-range APIs (announcements and integrated results filings, main board and SME) from
the last sync to now — every `catch_up_every_min`, and on the first tick after a gap.
An item is stored once whichever path sees it first (matched by its attachment URL).
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from . import classify
from .db import add_event, kv_get, kv_set
from .util import IST, Http, norm_name, now_ist, to_float

BASE = "https://nsearchives.nseindia.com/content/RSS/"
FEEDS = {
    "announcements": "Online_announcements.xml",
    "sast29": "Sast_Regulation29.xml",
    "sast31": "Sast_Regulation31.xml",
    "encumbrance": "Sast_ReasonForEncumbrance.xml",
    "insider": "InsiderTrading.xml",
    "shareholding": "Shareholding_Pattern.xml",
    "board": "Board_Meetings.xml",
    "results": "Integrated_Filing_Financials.xml",
    "corp_action": "Corporate_action.xml",
}
_DATE_FORMATS = ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%d-%b-%Y")   # also NSE API stamps


def parse_ts(s: str) -> str:
    s = (s or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s.title() if "%b" in fmt else s, fmt).replace(tzinfo=IST).isoformat()
        except ValueError:
            continue
    return now_ist().isoformat(timespec="seconds")


def fix_link(link: str) -> str:
    """NSE's RSS sometimes drops the '/corporate/' path ('…nseindia.comSYMBOL_…pdf'): put it back."""
    return re.sub(r"^(https://nsearchives\.nseindia\.com)(?=[^/])", r"\1/corporate/", link or "")


def parse_rss(content: bytes) -> list[dict]:
    root = ET.fromstring(content)
    items = []
    for it in root.iter("item"):
        items.append({
            "title": (it.findtext("title") or "").strip(),
            "link": fix_link((it.findtext("link") or "").strip()),
            "description": (it.findtext("description") or "").strip(),
            "pubDate": (it.findtext("pubDate") or "").strip(),
        })
    return items


class Matcher:
    """Maps a filing's company name / link / text to a tracked stock."""

    def __init__(self, conn: sqlite3.Connection):
        self.by_norm: dict[str, sqlite3.Row] = {}
        self.by_code: dict[str, sqlite3.Row] = {}
        for r in conn.execute("SELECT * FROM refdata WHERE exch='NSE'"):
            self.by_norm.setdefault(r["norm_name"], r)
            self.by_code[r["code"]] = r
        self.tracked = {r["isin"]: r for r in conn.execute("SELECT * FROM stocks") if r["isin"]}
        self.tracked_norm = {norm_name(r["name"]): r for r in self.tracked.values() if r["name"]}

    def match(self, title: str, link: str, description: str) -> tuple[sqlite3.Row | None, sqlite3.Row | None]:
        """(refdata row, tracked stock row) — either may be None."""
        company = re.split(r"\s+-\s+Ex-Date", title)[0].strip()
        ref = self.by_norm.get(norm_name(company))
        if ref is None:
            m = re.search(r"\((?:NSE|NSE Emerge|NSE SME)\s*:\s*([A-Z0-9&\-]+)\)", description)
            if m:
                ref = self.by_code.get(m.group(1))
        if ref is None and link:
            token = link.rsplit("/", 1)[-1].split("_", 1)[0].upper()
            ref = self.by_code.get(token)
        stock = self.tracked.get(ref["isin"]) if ref is not None else None
        if stock is None and ref is None:
            # by name only when the exchange master doesn't know the company: a known ISIN that isn't tracked
            # is another company (SIYSIL's filings once matched 544047 by a wrong stored name)
            stock = self.tracked_norm.get(norm_name(company))
        return ref, stock


def _guid(feed: str, it: dict) -> str:
    raw = "|".join((feed, it["title"], it["link"], it["description"], it["pubDate"]))
    return hashlib.sha1(raw.encode()).hexdigest()


def _event_for(feed: str, it: dict, stock: sqlite3.Row, settings: dict) -> dict:
    """Event fields for one feed item of a tracked stock."""
    desc = it["description"]
    orders = settings["orders"]
    if feed == "announcements":
        c = classify.classify_announcement(desc, sales_ttm_cr=stock["sales_ttm_cr"],
                                           high_pct=orders["high_pct_of_revenue"], usd_inr=orders["usd_inr"])
        text, subject = classify.split_subject(desc)
        return {"kind": "filing", "type": c.type, "severity": c.severity, "title": c.label,
                "detail": text or subject, "value_cr": c.value_cr, "data": c.data}
    if feed == "sast29":
        who = desc.split(":", 1)[-1].strip()
        return {"kind": "sast", "type": "sast_reg29", "severity": "high",
                "title": f"Substantial stake change (SAST Reg 29): {who}",
                "detail": "Acquirer/seller crossed a 5% threshold or moved ≥2% — see filing for direction.",
                "data": {"party": who}}
    if feed == "sast31":
        who = desc.split(":", 1)[-1].strip()
        return {"kind": "pledge", "type": "pledge_reg31", "severity": "high",
                "title": f"Promoter encumbrance disclosure (Reg 31): {who}",
                "detail": "Creation, invocation or release of pledge/encumbrance — see filing.",
                "data": {"party": who}}
    if feed == "encumbrance":
        who = desc.split(":", 1)[-1].strip()
        return {"kind": "pledge", "type": "pledge_reason", "severity": "medium",
                "title": f"Reason for encumbrance disclosed: {who}", "detail": desc, "data": {"party": who}}
    if feed == "insider":
        return {"kind": "insider", "type": "insider_trade", "severity": "high",
                "title": "Insider trading disclosure (PIT)", "detail": desc, "data": {}}
    if feed == "shareholding":
        return {"kind": "shareholding", "type": "shp_filed", "severity": "low",
                "title": "Shareholding pattern filed", "detail": desc, "data": _shp_fields(desc)}
    if feed == "board":
        c = classify.classify_announcement(desc + " |SUBJECT: Board Meeting Intimation", sales_ttm_cr=None,
                                           high_pct=orders["high_pct_of_revenue"], usd_inr=orders["usd_inr"])
        return {"kind": "filing", "type": "board_meeting", "severity": c.severity, "title": c.label,
                "detail": desc.split("|")[0].strip(), "data": c.data}
    if feed == "results":
        return {"kind": "filing", "type": "results", "severity": "medium", "title": "Results filed",
                "detail": desc.replace("|", " · "), "data": {}}
    if feed == "corp_action":
        purpose = re.search(r"PURPOSE:([^|]+)", desc)
        p = purpose.group(1).strip() if purpose else desc
        big = bool(re.search(r"bonus|split|sub-?division|buy\s*-?back|rights", p, re.I))
        ex = re.search(r"Ex-Date:\s*([0-9A-Za-z\-]+)", it["title"])
        return {"kind": "filing", "type": "corporate_action", "severity": "medium" if big else "low",
                "title": f"{p.title()}{' · ex ' + ex.group(1) if ex else ''}", "detail": desc.replace("|", " · "),
                "data": {"ex_date": ex.group(1) if ex else None, "purpose": p}}
    return {"kind": "filing", "type": feed, "severity": "low", "title": feed, "detail": desc, "data": {}}


def _goes_to_ai(feed: str, ev: dict, it: dict, settings: dict) -> bool:
    docs = settings.get("documents", {})
    return (feed == "announcements" and docs.get("enabled", True) and settings.get("llm", {}).get("enabled", True)
            and ev["type"] in set(docs.get("kinds") or []) and (it.get("link") or "").lower().endswith(".pdf"))


def _shp_fields(desc: str) -> dict:
    out = {}
    for key, name in (("AS ON DATE", "as_on"), ("PR_AND_PRGRP", "promoters"), ("PUBLIC_VAL", "public")):
        m = re.search(re.escape(key) + r"\s*:\s*([^|]+)", desc)
        if m:
            v = m.group(1).strip()
            out[name] = v if name == "as_on" else to_float(v)
    return out


def _record_shareholding(conn: sqlite3.Connection, sid: str, fields: dict) -> None:
    if fields.get("promoters") is None or not fields.get("as_on"):
        return
    try:
        qdate = datetime.strptime(fields["as_on"].title(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return
    conn.execute(
        "INSERT INTO shareholding(sid, qdate, quarter, promoters, public, source) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(sid, qdate) DO UPDATE SET promoters=COALESCE(shareholding.promoters, excluded.promoters), "
        "public=COALESCE(shareholding.public, excluded.public)",
        (sid, qdate, None, fields["promoters"], fields.get("public"), "nse_rss"),
    )


def _ingest(conn: sqlite3.Connection, feed: str, it: dict, matcher: Matcher, settings: dict, stamp: str,
            source: str) -> tuple[bool, bool]:
    """Store one feed item (once) and turn it into an event when it belongs to a tracked stock → (new, tracked)."""
    guid = _guid(feed, it)
    seen = conn.execute("SELECT guid, sid FROM feed_items WHERE feed=? AND guid=?", (feed, guid)).fetchone() or \
        (conn.execute("SELECT guid, sid FROM feed_items WHERE feed=? AND url=?", (feed, it["link"])).fetchone()
         if it["link"] else None)   # the same filing, already seen by the other path (RSS ↔ API)
    if seen is not None:
        if seen["sid"] is not None:
            return False, False
        # stored while the company wasn't tracked: if it is now (added to the watchlist), it becomes an event
        ref, stock = matcher.match(it["title"], it["link"], it["description"])
        if stock is None:
            return False, False
        conn.execute("UPDATE feed_items SET sid=? WHERE feed=? AND guid=?", (stock["sid"], feed, seen["guid"]))
        guid = seen["guid"]
        ts = parse_ts(it["pubDate"])
        ev = _event_for(feed, it, stock, settings)
        if _goes_to_ai(feed, ev, it, settings):
            ev["data"] = {**(ev.get("data") or {}), "sev0": ev["severity"], "pending_ai": True}
            ev["severity"] = "low"
        elif feed == "results":
            ev["severity"] = "low"
        add_event(conn, sid=stock["sid"], ts=ts, kind=ev["kind"], type=ev["type"], severity=ev["severity"],
                  title=ev["title"], detail=ev.get("detail", ""), value_cr=ev.get("value_cr"),
                  url=it["link"], source=source, data=ev.get("data"), dedupe=f"feed:{feed}:{guid}")
        return False, True
    ref, stock = matcher.match(it["title"], it["link"], it["description"])
    ts = parse_ts(it["pubDate"])
    text, subject = classify.split_subject(it["description"])
    conn.execute(
        "INSERT INTO feed_items(feed, guid, ts, company, isin, sid, subject, description, url, "
        "category, fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (feed, guid, ts, it["title"], ref["isin"] if ref is not None else None,
         stock["sid"] if stock is not None else None, subject, it["description"], it["link"],
         None, stamp),
    )
    if stock is None:
        return True, False
    ev = _event_for(feed, it, stock, settings)
    if feed == "shareholding":
        _record_shareholding(conn, stock["sid"], ev["data"])
    if _goes_to_ai(feed, ev, it, settings):
        # the AI read (documents.py → extract.py) finishes this event with a summary and its real
        # severity, so the inbox gets one informative alert instead of a bare "Results filed" first
        ev["data"] = {**(ev.get("data") or {}), "sev0": ev["severity"], "pending_ai": True}
        ev["severity"] = "low"
    elif feed == "results":
        ev["severity"] = "low"          # the quarter's results event (results.py) carries the numbers
    elif feed == "announcements" and re.search(r"/xbrl/.*\.xml$", it.get("link") or "", re.I):
        # the machine-readable twin of a PDF announcement filed minutes earlier — never a second alert
        ev["severity"] = "low"
        ev["title"] = f"{ev['title']} (XBRL copy)"
    conn.execute("UPDATE feed_items SET category=? WHERE feed=? AND guid=?", (ev["type"], feed, guid))
    add_event(conn, sid=stock["sid"], ts=ts, kind=ev["kind"], type=ev["type"], severity=ev["severity"],
              title=ev["title"], detail=ev.get("detail", ""), value_cr=ev.get("value_cr"),
              url=it["link"], source=source, data=ev.get("data"), dedupe=f"feed:{feed}:{guid}")
    return True, True


def poll(conn: sqlite3.Connection, http: Http, settings: dict, only: list[str] | None = None) -> dict:
    """Poll the NSE feeds (all, or just the ones in `only`); returns per-feed counts."""
    matcher = Matcher(conn)
    stamp = now_ist().isoformat(timespec="seconds")
    out: dict[str, object] = {}
    for feed, fname in FEEDS.items():
        if only is not None and feed not in only:
            continue
        kv_set(conn, f"feed_polled:{feed}", stamp)   # stamped even on failure: no retry storm
        try:
            r = http.get(BASE + fname)
        except Exception as exc:
            out[feed] = f"error: {exc}"
            continue
        if r.status_code != 200:
            out[feed] = f"HTTP {r.status_code}"
            continue
        try:
            items = parse_rss(r.content)
        except ET.ParseError as exc:
            out[feed] = f"parse error: {exc}"
            continue
        new = tracked = 0
        for it in items:
            n, t = _ingest(conn, feed, it, matcher, settings, stamp, f"NSE · {fname.removesuffix('.xml')}")
            new, tracked = new + n, tracked + t
        out[feed] = {"items": len(items), "new": new, "tracked": tracked}
        if new or items:
            kv_set(conn, f"feed_ok:{feed}", stamp)
    cutoff = (now_ist() - timedelta(days=45)).isoformat()
    conn.execute("DELETE FROM feed_items WHERE ts < ?", (cutoff,))
    return out


# ── catch-up: NSE's date-range APIs ─────────────────────────────────────────

API = "https://www.nseindia.com/api/"
API_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"   # sets the session cookies


class ApiError(Exception):
    pass


def _api(http: Http, path: str):
    if not getattr(http, "_nse_session", False):
        http.get(API_PAGE)
        http._nse_session = True
    r = http.get(API + path, headers={"Referer": API_PAGE, "Accept": "application/json, text/plain, */*"})
    if r.status_code != 200:
        raise ApiError(f"HTTP {r.status_code} for {path.split('?')[0]}")
    try:
        return r.json()
    except ValueError as exc:
        raise ApiError(f"not JSON for {path.split('?')[0]}") from exc


def _ann_item(x: dict) -> dict:
    text, subj = (x.get("attchmntText") or "").strip(), (x.get("desc") or "").strip()
    return {"title": (x.get("sm_name") or x.get("symbol") or "").strip(), "link": (x.get("attchmntFile") or "").strip(),
            "description": f"{text} |SUBJECT: {subj}" if subj else text, "pubDate": x.get("an_dt") or ""}


def _res_item(x: dict) -> dict:
    return {"title": (x.get("smName") or x.get("cmName") or x.get("symbol") or "").strip(),
            "link": (x.get("xbrl") or "").strip(),
            "description": f"{x.get('type') or 'Integrated Filing- Financials'}|{x.get('type_Sub') or ''}|",
            "pubDate": x.get("creation_Date") or ""}


def catch_up(conn: sqlite3.Connection, http: Http, settings: dict, force: bool = False) -> dict:
    """Everything NSE published since the last sync (both boards), through the date-range APIs."""
    sch = settings["schedule"]
    now = now_ist()
    last_s = kv_get(conn, "nse_api_synced_to")
    if last_s and not force and (now - datetime.fromisoformat(last_s)) < timedelta(minutes=int(sch["catch_up_every_min"])):
        return {"skipped": "synced recently"}
    # the first run reaches the full window back: gaps before it are unknown (the RSS may have resumed since)
    since = datetime.fromisoformat(last_s) - timedelta(hours=2) if last_s else now     # overlap: filings lag
    since = max(since if last_s else now - timedelta(days=int(sch["catch_up_max_days"])),
                now - timedelta(days=int(sch["catch_up_max_days"])))
    cut = since.isoformat(timespec="seconds")
    fd, td = since.strftime("%d-%m-%Y"), now.strftime("%d-%m-%Y")
    matcher = Matcher(conn)
    stamp = now.isoformat(timespec="seconds")
    out: dict = {"from": since.isoformat(timespec="minutes")}
    try:
        for index in ("equities", "sme"):
            board = "SME" if index == "sme" else "main board"
            ann = _api(http, f"corporate-announcements?index={index}&from_date={fd}&to_date={td}")
            new = tracked = 0
            for x in ann if isinstance(ann, list) else []:
                it = _ann_item(x)
                if not it["link"] or parse_ts(it["pubDate"]) < cut:
                    continue
                n, t = _ingest(conn, "announcements", it, matcher, settings, stamp, f"NSE · announcements ({board}, catch-up)")
                new, tracked = new + n, tracked + t
            res = _api(http, f"integrated-filing-results?index={index}&from_date={fd}&to_date={td}"
                             "&type=Integrated%20Filing-%20Financials&size=1000")
            rn = rt = 0
            for x in (res.get("data") if isinstance(res, dict) else None) or []:
                it = _res_item(x)
                if not it["link"].lower().endswith(".xml") or parse_ts(it["pubDate"]) < cut:
                    continue
                n, t = _ingest(conn, "results", it, matcher, settings, stamp, f"NSE · results filings ({board}, catch-up)")
                rn, rt = rn + n, rt + t
            out[index] = {"announcements": len(ann) if isinstance(ann, list) else 0, "new": new, "tracked": tracked,
                          "results_new": rn, "results_tracked": rt}
        # substantial stake changes (SAST Reg 29) — high importance, so they are not lost to a sleep gap either
        sast = _api(http, f"corporate-sast-reg29?index=equities&from_date={fd}&to_date={td}")
        sn = st_ = 0
        for x in (sast.get("data") if isinstance(sast, dict) else None) or []:
            it = {"title": (x.get("company") or x.get("symbol") or "").strip(), "link": (x.get("attachement") or "").strip(),
                  "description": f"{x.get('acqSaleType') or 'Change'} by {x.get('regType') or 'Reg 29'}: {(x.get('acquirerName') or '').strip()}",
                  "pubDate": x.get("timestamp") or x.get("sysTime") or ""}
            if not it["link"] or parse_ts(it["pubDate"]) < cut:
                continue
            n, t = _ingest(conn, "sast29", it, matcher, settings, stamp, "NSE · SAST Reg 29 (catch-up)")
            sn, st_ = sn + n, st_ + t
        out["sast29"] = {"new": sn, "tracked": st_}
    except Exception as exc:                  # network / NSE hiccup: the next tick tries again from the same point
        out["error"] = f"{exc.__class__.__name__}: {exc}"
        return out
    kv_set(conn, "nse_api_synced_to", stamp)
    return out
