"""
Named shareholders per quarter — anchor (non-institutional public ≥1%), FII, DII and promoter
holders — from two sources, no LLM:

  • NSE shareholding-pattern XBRL, linked from the Shareholding_Pattern RSS item the moment a
    company files (NSE-listed stocks; official, preferred for any quarter it covers)
  • Screener shareholding files written by the existing fetcher (all stocks; gives history, and
    the only source for BSE-only stocks)

Tables: shareholding_holders (one row per holder per quarter) and holder_coverage (which
quarters/groups each source reported, so "exited" can be told apart from "no data").
Events: anchor entry / exit / move ≥ shareholding.anchor_change_pp, and tracked-investor
(investor_watchlist.yaml) entry / exit / change in any non-promoter group.
"""
from __future__ import annotations

import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

from .config import COMPANIES_DIR
from .db import add_event, kv_get, kv_set
from .shareholding import _tracked_investors, match_investor, parse_file, quarter_end
from .util import Http, norm_name, now_ist

GROUP_LABEL = {"anchor": "Anchor investors", "fii": "FIIs", "dii": "DIIs", "promoter": "Promoters", "other": "Other"}
SCREENER_GROUP = {"Public Anchor Investors": "anchor", "FIIs (Individual)": "fii", "DIIs (Individual)": "dii",
                  "Promoters (Individual)": "promoter"}

# NSE SHP XBRL typed-member domains → group. Promoter tables use the plain individual / "any other"
# domains; public named holders (≥1%) use the "DetailsOfSharesHeldBy…" domains.
_SKIP = re.compile(r"PersonsInConcert|SignificantBeneficialOwner", re.I)
_PROMOTER = re.compile(r"^(IndividualsOrHUF|OthersIndianShareholders|OtherForeignShareholders|"
                       r"NonResidentIndividualsOrForeignIndividuals|GovernmentOrStateGovernment|"
                       r"FinancialInstitutionsOrBanks)Domain$|Promoter", re.I)
_FII = re.compile(r"Foreign(Portfolio|VentureCapital|Institutional|Bank)|InstitutionsForeign|SovereignWealthFundsForeign"
                  r"|OverseasDepositor", re.I)
_DII = re.compile(r"MutualFunds|AlternativeInvestment|Insurance|Banks|ProvidentFund|PensionFund|NBFC|"
                  r"FinancialInstitution|AssetReconstruction|InstitutionsDomestic|SovereignWealthFundsDomestic|"
                  r"VentureCapitalFunds", re.I)
_ANCHOR = re.compile(r"ResidentIndividual|BodiesCorporate|NonResidentIndians|ForeignCompanies|OtherNonInstitutions|"
                     r"Directors|KeyManagerial|ForeignNationals|Trusts", re.I)
# category rows that Screener (and some filings) list as if they were holders
NOT_A_HOLDER = re.compile(r"^(key managerial personnel|investor education and protection fund|iepf|clearing members?|huf|"
                          r"llp|trusts?|bodies corporate|employees|others?|unclaimed.*|overseas corporate bodies|"
                          r"directors? (and|&) (their )?relatives|non[- ]resident indians?|nris?|"
                          r"resident individuals?.*|public|foreign nationals?)\.?$", re.I)
TOTALS = {"ShareholdingOfPromoterAndPromoterGroupMember": "promoters", "InstitutionsDomesticMember": "diis",
          "InstitutionsForeignMember": "fiis", "NonInstitutionsMember": "public",
          "CentralGovernmentOrStateGovernmentOrPresidentOfIndiaMember": "govt"}


def _group_for_domain(domain: str) -> str | None:
    if _SKIP.search(domain):
        return None
    if _PROMOTER.search(domain):
        return "promoter"
    if _FII.search(domain):
        return "fii"
    if _DII.search(domain):
        return "dii"
    if _ANCHOR.search(domain):
        return "anchor"
    return "other"


def nkey(name: str) -> str:
    return norm_name(re.sub(r"\s+", " ", name or "")) or (name or "").strip().lower()


# ── NSE XBRL ────────────────────────────────────────────────────────────────

def parse_shp_xbrl(content: bytes) -> dict:
    """SHP XBRL → {qdate, isin, symbol, totals{promoters,fiis,diis,public,govt,shareholders}, holders[]}."""
    root = ET.fromstring(content)
    contexts: dict[str, tuple[tuple, str | None]] = {}
    for c in root:
        if not c.tag.endswith("}context"):
            continue
        key: list[str] = []
        domain = None
        for m in c.iter():
            tag = m.tag.rsplit("}", 1)[-1]
            if tag == "explicitMember":
                key.append((m.text or "").strip().split(":")[-1])
            elif tag == "typedMember":
                for x in m:
                    domain = x.tag.rsplit("}", 1)[-1]
                    key.append((x.text or "").strip())
        contexts[c.get("id")] = (tuple(key), domain)
    groups: dict[tuple, dict] = {}
    general: dict[str, str] = {}
    for el in root:
        ref = el.get("contextRef")
        if not ref or ref not in contexts:
            continue
        key, domain = contexts[ref]
        tag = el.tag.rsplit("}", 1)[-1]
        val = (el.text or "").strip()
        if not key:
            general.setdefault(tag, val)
            continue
        g = groups.setdefault(key, {"_domain": None})
        g[tag] = val
        if domain:
            g["_domain"] = domain

    def f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    totals: dict[str, float | None] = {}
    for key, g in groups.items():
        if len(key) == 1 and key[0] in TOTALS and g.get("ShareholdingAsAPercentageOfTotalNumberOfShares"):
            totals[TOTALS[key[0]]] = round(f(g["ShareholdingAsAPercentageOfTotalNumberOfShares"]) * 100, 2)
        if key == ("ShareholdingPatternMember",):
            totals["shareholders"] = f(g.get("NumberOfShareholders"))
    holders = []
    for key, g in groups.items():
        name = re.sub(r"\s+", " ", g.get("NameOfTheShareholder") or "").strip()
        dom = g.get("_domain") or ""
        pct = f(g.get("ShareholdingAsAPercentageOfTotalNumberOfShares"))
        if not name or not dom or not pct or NOT_A_HOLDER.match(name):
            continue
        grp = _group_for_domain(dom)
        if grp is None:
            continue
        holders.append({"name": name, "grp": grp, "pct": round(pct * 100, 2), "category": dom.removesuffix("Domain"),
                        "shares": f(g.get("NumberOfShares") or g.get("NumberOfFullyPaidUpEquityShares"))})
    qdate = general.get("DateOfReport") or general.get("ShareholdingPatternAsOnDate")
    return {"qdate": qdate, "isin": general.get("ISIN"), "symbol": general.get("Symbol"),
            "company": general.get("NameOfTheCompany"), "totals": totals, "holders": holders}


def _store(conn: sqlite3.Connection, sid: str, qdate: str, grp: str, source: str, rows: list[dict],
           url: str | None = None) -> int:
    conn.execute("INSERT INTO holder_coverage(sid, qdate, grp, source) VALUES(?,?,?,?) "
                 "ON CONFLICT(sid, qdate, grp) DO UPDATE SET source=excluded.source "
                 "WHERE holder_coverage.source != 'nse_xbrl' OR excluded.source = 'nse_xbrl'",
                 (sid, qdate, grp, source))
    merged: dict[str, dict] = {}
    for r in rows:   # the same holder can appear twice (two folios / categories) → add up
        k = nkey(r["name"])
        if k in merged:
            merged[k]["pct"] = round(merged[k]["pct"] + r["pct"], 2)
        else:
            merged[k] = dict(r)
    for k, r in merged.items():
        conn.execute(
            "INSERT INTO shareholding_holders(sid, qdate, grp, nkey, name, pct, shares, category, source, url) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(sid, qdate, grp, nkey) DO UPDATE SET name=excluded.name, "
            "pct=excluded.pct, shares=excluded.shares, category=excluded.category, source=excluded.source, "
            "url=excluded.url WHERE shareholding_holders.source != 'nse_xbrl' OR excluded.source = 'nse_xbrl'",
            (sid, qdate, grp, k, r["name"], r["pct"], r.get("shares"), r.get("category"), source, url))
    return len(merged)


def store_xbrl(conn: sqlite3.Connection, sid: str, parsed: dict, url: str) -> int:
    qdate = parsed["qdate"]
    # the exchange filing replaces whatever Screener said about this quarter (names are spelt differently)
    conn.execute("DELETE FROM shareholding_holders WHERE sid=? AND qdate=? AND source != 'nse_xbrl'", (sid, qdate))
    n = 0
    for grp in ("promoter", "fii", "dii", "anchor", "other"):
        n += _store(conn, sid, qdate, grp, "nse_xbrl", [h for h in parsed["holders"] if h["grp"] == grp], url)
    t = parsed["totals"]
    if t.get("promoters") is not None:
        conn.execute(
            "INSERT INTO shareholding(sid, qdate, quarter, promoters, fiis, diis, public, govt, shareholders, source) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(sid, qdate) DO UPDATE SET promoters=excluded.promoters, "
            "fiis=excluded.fiis, diis=excluded.diis, public=excluded.public, govt=COALESCE(excluded.govt, shareholding.govt), "
            "shareholders=COALESCE(excluded.shareholders, shareholding.shareholders), source=excluded.source, "
            "quarter=COALESCE(shareholding.quarter, excluded.quarter)",
            (sid, qdate, datetime.fromisoformat(qdate).strftime("%b %Y"), t.get("promoters"), t.get("fiis"),
             t.get("diis"), t.get("public"), t.get("govt"), t.get("shareholders"), "nse_xbrl"))
    return n


def fetch_nse(conn: sqlite3.Connection, http: Http, limit: int = 25) -> dict:
    """Download the SHP XBRL of every tracked stock's new Shareholding_Pattern feed item."""
    pending = conn.execute(
        "SELECT f.sid, f.url, f.ts FROM feed_items f LEFT JOIN shp_filings p ON p.url = f.url "
        "WHERE f.feed='shareholding' AND f.sid IS NOT NULL AND f.url LIKE '%.xml' AND p.url IS NULL "
        "ORDER BY f.ts DESC LIMIT ?", (limit,)).fetchall()
    stamp = now_ist().isoformat(timespec="seconds")
    done = failed = 0
    touched: set[str] = set()
    for p in pending:
        status, qdate, n = "ok", None, 0
        try:
            r = http.get(p["url"])
            if r.status_code != 200:
                status = f"HTTP {r.status_code}"
            else:
                parsed = parse_shp_xbrl(r.content)
                qdate = parsed["qdate"]
                stock = conn.execute("SELECT sid FROM stocks WHERE isin=?", (parsed["isin"],)).fetchone()
                sid = stock["sid"] if stock else p["sid"]
                if not qdate:
                    status = "no report date"
                else:
                    n = store_xbrl(conn, sid, parsed, p["url"])
                    touched.add(sid)
        except (ET.ParseError, ValueError) as exc:
            status = f"parse error: {exc}"
        except Exception as exc:  # network
            status = f"error: {exc.__class__.__name__}"
        conn.execute("INSERT OR REPLACE INTO shp_filings(url, sid, qdate, holders, status, fetched_at) "
                     "VALUES(?,?,?,?,?,?)", (p["url"], p["sid"], qdate, n, status, stamp))
        done += status == "ok"
        failed += status != "ok"
    events = sum(holder_events(conn, sid) for sid in touched)
    return {"filings": done, "failed": failed, "stocks": sorted(touched), "new_events": events}


# ── Screener ────────────────────────────────────────────────────────────────

def from_screener(conn: sqlite3.Connection, sid: str, path: Path) -> int:
    parsed = parse_file(path)
    n = 0
    for section, holders in parsed["holders"].items():
        grp = SCREENER_GROUP.get(section)
        if not grp:
            continue
        by_q: dict[str, list[dict]] = {}
        quarters: set[str] = set()
        for h in holders:
            for q, v in h["values"].items():
                qd = quarter_end(q)
                if not qd:
                    continue
                quarters.add(qd)
                if v and not NOT_A_HOLDER.match(h["name"].strip()):
                    by_q.setdefault(qd, []).append({"name": h["name"].rstrip(" ."), "pct": v, "category": section})
        for qd in quarters:
            n += _store(conn, sid, qd, grp, "screener", by_q.get(qd, []))
    return n


def sync_screener(conn: sqlite3.Connection) -> dict:
    """Every Screener shareholding file (oldest first, so newer files win) → holders; then events."""
    rows = files = 0
    touched = []
    for s in conn.execute("SELECT sid, folder FROM stocks WHERE folder IS NOT NULL").fetchall():
        paths = sorted((COMPANIES_DIR / s["folder"] / "shareholding").glob("*_shareholding_*.txt"))
        if not paths:
            continue
        sig = ";".join(f"{p.name}:{int(p.stat().st_mtime)}" for p in paths)
        if kv_get(conn, f"holders_sig:{s['sid']}") == sig:
            continue
        for p in paths:
            rows += from_screener(conn, s["sid"], p)
            files += 1
        kv_set(conn, f"holders_sig:{s['sid']}", sig)
        touched.append(s["sid"])
    events = sum(holder_events(conn, sid) for sid in touched)
    return {"files": files, "rows": rows, "stocks": len(touched), "new_events": events}


# ── events ──────────────────────────────────────────────────────────────────

def _is_history(qdate: str) -> bool:
    """Changes for quarters that ended long ago are history, not news (no inbox / push)."""
    return (now_ist().date() - date.fromisoformat(qdate)).days > 75


def holder_events(conn: sqlite3.Connection, sid: str, settings: dict | None = None) -> int:
    from .config import load_settings

    th = (settings or load_settings())["shareholding"]
    anchor_pp = float(th.get("anchor_change_pp", 0.5))
    investors = _tracked_investors()
    n = 0
    for grp in ("anchor", "fii", "dii", "other"):
        qs = [r["qdate"] for r in conn.execute(
            "SELECT qdate FROM holder_coverage WHERE sid=? AND grp=? ORDER BY qdate DESC LIMIT 2", (sid, grp))]
        if len(qs) < 2:
            continue
        q1, q0 = qs
        cur = {r["nkey"]: r for r in conn.execute(
            "SELECT * FROM shareholding_holders WHERE sid=? AND grp=? AND qdate=?", (sid, grp, q1))}
        prev = {r["nkey"]: r for r in conn.execute(
            "SELECT * FROM shareholding_holders WHERE sid=? AND grp=? AND qdate=?", (sid, grp, q0))}
        src = "NSE shareholding pattern" if any(r["source"] == "nse_xbrl" for r in cur.values()) else "Screener shareholding"
        url = next((r["url"] for r in cur.values() if r["url"]), "")
        q1l, q0l = datetime.fromisoformat(q1).strftime("%b %Y"), datetime.fromisoformat(q0).strftime("%b %Y")
        for k in set(cur) | set(prev):
            a = prev[k]["pct"] if k in prev else None
            b = cur[k]["pct"] if k in cur else None
            name = (cur.get(k) or prev.get(k))["name"]
            inv = match_investor(name, investors)
            if inv:   # same dedupe key the phase-1 code used, so nothing is raised twice
                if a is None:
                    kind, title = "investor_entry", f"{inv} entered with {b:.2f}%"
                elif b is None:
                    kind, title = "investor_exit", f"{inv} exited or fell below 1% (was {a:.2f}%)"
                elif abs(b - a) >= 0.01:
                    kind = "investor_up" if b > a else "investor_down"
                    title = f"{inv} {'raised' if b > a else 'cut'} stake {a:.2f}% → {b:.2f}%"
                else:
                    continue
                sev = "high" if kind in ("investor_entry", "investor_exit") else "medium"
                dedupe = f"inv:{sid}:{norm_name(name)}:{q1}"
                data = {"investor": inv, "holder": name, "from": a, "to": b, "group": grp}
            elif grp == "anchor":
                if a is None:
                    kind, title = "anchor_entry", f"New anchor investor: {name} {b:.2f}%"
                elif b is None:
                    kind, title = "anchor_exit", f"Anchor investor exited or fell below 1%: {name} (was {a:.2f}%)"
                elif abs(b - a) >= anchor_pp:
                    kind = "anchor_up" if b > a else "anchor_down"
                    title = f"{name} {'raised' if b > a else 'cut'} stake {a:.2f}% → {b:.2f}%"
                else:
                    continue
                sev, dedupe = "medium", f"anc:{sid}:{k}:{q1}"
                data = {"holder": name, "from": a, "to": b, "group": grp}
            else:
                continue
            eid = add_event(conn, sid=sid, ts=f"{q1}T18:00:00+05:30", kind="shareholding", type=kind, severity=sev,
                            title=title, detail=f"{q0l} → {q1l} · {GROUP_LABEL[grp]}", source=src, url=url,
                            dedupe=dedupe, backfill=_is_history(q1), data=data)
            n += 1 if eid else 0
    return n
