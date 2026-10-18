"""
NSE bulk and block deals (published daily after market close).

  https://nsearchives.nseindia.com/content/equities/bulk.csv
  https://nsearchives.nseindia.com/content/equities/block.csv

Deals in tracked stocks become events; deals by investors in investor_watchlist.yaml
are kept for every stock (a discovery signal) and raise an event even when the stock
isn't tracked yet.
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
from datetime import datetime

from .config import RAW_DIR
from .db import add_event, kv_get, kv_set
from .shareholding import _tracked_investors, match_investor, name_tokens
from .util import Http, now_ist, to_float

URLS = {
    "bulk": "https://nsearchives.nseindia.com/content/equities/bulk.csv",
    "block": "https://nsearchives.nseindia.com/content/equities/block.csv",
}

PROP = re.compile(
    r"QE SECURITIES|HRTI|MICROCURVES|JUNOMONETA|JUMP TRADING|ALPHAGREP|NK SECURITIES|IRAGE|MUSIGMA|"
    r"MATHISYS|BLITZQUANT|GRAVITON|TOWER RESEARCH|SILVERLEAF|DOLAT|ELIXIR WEALTH|SHARE INDIA SECURITIES|"
    r"PLUTUS WEALTH|WATERFRONT|QUANTUM SECURITIES|BULLPULSE|PRIJAL TRADEFIN|J4S FINANCIAL|NEO APEX|"
    r"PUMA SECURITIES|SSPL SECURITIES|ARIHANT CAPITAL|BHAVISHYA ECOMMERCE|HI GROWTH CORPORATE", re.I)
FII = re.compile(
    r"\b(LLC|L\.?P\.?|PTE|PLC|INC|INCORPORATED|VCC|SICAV|S\.A\.|GMBH|B\.V\.)\b|MAURITIUS|SINGAPORE|CAYMAN|"
    r"LUXEMBOURG|NORGES|GOVERNMENT PENSION|ABU DHABI|GOLDMAN|MORGAN STANLEY|CITIGROUP|BNP PARIBAS|NOMURA|"
    r"MERRILL|J\.? ?P\.? MORGAN|\bUBS\b|SOCIETE GENERALE|BOFA|VANGUARD|ISHARES|BLACKROCK|FIDELITY|"
    r"MASSACHUSETTS|PEAK XV|SMALLCAP WORLD|GOVERNMENT OF SINGAPORE|MONETARY AUTHORITY|SOVEREIGN", re.I)
DII = re.compile(
    r"MUTUAL FUND|\bMF\b|ASSET MANAGEMENT|\bAMC\b|LIFE INSURANCE|INSURANCE|\bLIC\b|PENSION FUND|PROVIDENT|"
    r"BANK LIMITED|\bBANK\b", re.I)
FUND = re.compile(r"\bFUND\b|\bAIF\b|CAPITAL ADVISORS|INVESTMENT TRUST|VENTURES|OPPORTUNITIES", re.I)


def client_type(client: str) -> str:
    if PROP.search(client):
        return "prop"
    if FII.search(client):
        return "fii"
    if DII.search(client):
        return "dii"
    if FUND.search(client):
        return "fund"
    return "other"


def _parse(text: str, kind: str) -> list[dict]:
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        if r.get("Date", "").upper().startswith("NO RECORDS") or not r.get("Symbol"):
            continue
        try:
            d = datetime.strptime(r["Date"].title(), "%d-%b-%Y").date().isoformat()
        except (KeyError, ValueError):
            continue
        qty = to_float(r.get("Quantity Traded"))
        price = to_float(r.get("Trade Price / Wght. Avg. Price"))
        if not qty or not price:
            continue
        out.append({"d": d, "kind": kind, "symbol": r["Symbol"].upper(), "security": r.get("Security Name", ""),
                    "client": r.get("Client Name", "").upper(), "side": r.get("Buy/Sell", "").upper(),
                    "qty": qty, "price": price, "remarks": r.get("Remarks", "")})
    return out


def sync(conn: sqlite3.Connection, http: Http, settings: dict) -> dict:
    investors = _tracked_investors()
    stocks = {r["nse_symbol"]: r for r in conn.execute("SELECT * FROM stocks WHERE nse_symbol IS NOT NULL")}
    isin_by_code = {r["code"]: r["isin"] for r in conn.execute("SELECT code, isin FROM refdata WHERE exch='NSE'")}
    out: dict[str, object] = {}
    latest_date = None
    for kind, url in URLS.items():
        r = http.get(url)
        if r.status_code != 200:
            out[kind] = f"HTTP {r.status_code}"
            continue
        rows = _parse(r.text, kind)
        if rows:
            latest_date = max(latest_date or "", rows[0]["d"])
            path = RAW_DIR / "deals" / f"{rows[0]['d']}_{kind}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(r.text)
        new = 0
        for d in rows:
            stock = stocks.get(d["symbol"])
            ctype = client_type(d["client"])
            inv = match_investor(d["client"], investors)
            promoter = None
            if stock is not None:
                for pname in kv_get(conn, f"promoters:{stock['sid']}") or []:
                    pt = name_tokens(pname)
                    if pt and pt <= name_tokens(d["client"]):
                        promoter = pname
                        break
            if inv:
                ctype = "ace"
            if promoter:
                ctype = "promoter"
            value_cr = round(d["qty"] * d["price"] / 1e7, 2)
            if stock is None and not inv:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO deals(d, exch, kind, symbol, security, isin, sid, client, side, qty, price, "
                "value_cr, client_type, investor, remarks) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (d["d"], "NSE", kind, d["symbol"], d["security"], isin_by_code.get(d["symbol"]),
                 stock["sid"] if stock is not None else None, d["client"], d["side"], d["qty"], d["price"],
                 value_cr, ctype, inv or promoter, d["remarks"]),
            )
            if not cur.rowcount:
                continue
            new += 1
            if ctype == "prop":
                severity = "info"
            elif ctype in ("ace", "promoter"):
                severity = "high"
            elif ctype in ("fii", "dii", "fund"):
                severity = "high" if value_cr >= 10 else "medium"
            else:   # unknown individuals / corporates: visible on the Deals page, alert only when large
                severity = "medium" if value_cr >= 25 else "low"
            who = inv or promoter or d["client"].title()
            verb = "bought" if d["side"].startswith("B") else "sold"
            label = {"ace": "Tracked investor", "promoter": "Promoter", "fii": "FII", "dii": "DII",
                     "fund": "", "prop": "Prop/HFT desk", "other": ""}[ctype]
            add_event(
                conn, sid=stock["sid"] if stock is not None else None, ts=f"{d['d']}T17:00:00+05:30",
                kind="deal", type=f"{kind}_deal_{'buy' if verb == 'bought' else 'sell'}", severity=severity,
                title=f"{(label + ' ') if label else ''}{who} {verb} {d['qty']:,.0f} shares @ ₹{d['price']:,.2f} "
                      f"({kind} deal, ₹{value_cr:,.1f} Cr)",
                detail=f"{d['security']} ({d['symbol']}) · client: {d['client']}"
                       + ("" if stock is not None else " · not in your universe"),
                value_cr=value_cr, url=url, source=f"NSE {kind} deals",
                dedupe=f"deal:{d['d']}:{kind}:{d['symbol']}:{d['client']}:{d['side']}:{d['qty']}:{d['price']}",
                data={"client_type": ctype, "investor": inv, "promoter": promoter, "symbol": d["symbol"],
                      "side": d["side"], "qty": d["qty"], "price": d["price"]},
            )
        out[kind] = {"rows": len(rows), "new": new}
    if latest_date:
        kv_set(conn, "deals_latest", latest_date)
        if latest_date == now_ist().date().isoformat():
            kv_set(conn, "deals_done_for", latest_date)
    return out
