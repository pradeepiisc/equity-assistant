"""
The tracked universe: Kite holdings (both accounts) ∪ watchlist.yaml ∪ stocks named in notes.

Every stock is resolved against exchange master data to an ISIN, NSE symbol and
BSE scrip code. `sid` is the NSE symbol when the stock trades on NSE, otherwise
its BSE scrip code (the same convention watchlist.yaml already uses).
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import yaml

from . import refdata
from .config import COMPANIES_DIR, PORTFOLIO_DIR, ROOT
from .util import IST, norm_name, now_ist

SUFFIXES = ("-SM", "-ST", "-BE", "-BZ", "-SME")
SME_SERIES = {"NSE": {"SM", "ST", "SZ"}, "BSE": {"M", "MT", "MS"}}


def strip_suffix(sym) -> str:
    s = str(sym or "").strip().upper()
    for sfx in SUFFIXES:
        if s.endswith(sfx):
            return s[: -len(sfx)]
    return s


def resolve(
    conn: sqlite3.Connection,
    symbol: str = "",
    exchange: str = "",
    isin: str = "",
    bse_code: str = "",
    name: str = "",
) -> dict | None:
    """Resolve any identifier we have to {isin, nse, bse} refdata rows."""
    sym = strip_suffix(symbol)
    rows: dict[str, sqlite3.Row] = {}
    if isin:
        rows = refdata.by_isin(conn, isin)
    if not rows and sym and not sym.isdigit():
        r = refdata.lookup(conn, "NSE", sym)
        if r is None and exchange.upper() == "BSE":
            r = conn.execute("SELECT * FROM refdata WHERE exch='BSE' AND symbol=?", (sym,)).fetchone()
        if r is not None:
            rows = refdata.by_isin(conn, r["isin"]) or {r["exch"]: r}
    if not rows and (sym.isdigit() or bse_code):
        r = refdata.lookup(conn, "BSE", sym if sym.isdigit() else str(bse_code))
        if r is not None:
            rows = refdata.by_isin(conn, r["isin"]) or {"BSE": r}
    if not rows and name:
        found = refdata.by_name(conn, name)
        if found:
            rows = refdata.by_isin(conn, found[0]["isin"]) or {found[0]["exch"]: found[0]}
    if not rows:
        return None
    nse, bse = rows.get("NSE"), rows.get("BSE")
    any_row = nse or bse
    sme = bool(
        (nse is not None and nse["series"] in SME_SERIES["NSE"])
        or (nse is None and bse is not None and bse["series"] in SME_SERIES["BSE"])
    )
    return {
        "sid": nse["code"] if nse is not None else bse["code"],
        "isin": any_row["isin"],
        "name": (nse or bse)["name"],
        "nse_symbol": nse["code"] if nse is not None else None,
        "nse_series": nse["series"] if nse is not None else None,
        "bse_code": bse["code"] if bse is not None else None,
        "bse_symbol": bse["symbol"] if bse is not None else None,
        "price_exch": "NSE" if nse is not None else "BSE",
        "is_sme": 1 if sme else 0,
    }


def find_folder(*candidates: str) -> str | None:
    seen = []
    for c in candidates:
        c = str(c or "").strip()
        if not c:
            continue
        base = strip_suffix(c)
        for cand in (c, base, *[base + s for s in SUFFIXES]):
            if cand and cand not in seen:
                seen.append(cand)
    for cand in seen:
        if (COMPANIES_DIR / cand).is_dir():
            return cand
    return None


def _sector_map() -> dict[str, str]:
    p = ROOT / "sector_map.yaml"
    if not p.exists():
        return {}
    return {str(k).upper(): str(v) for k, v in (yaml.safe_load(p.read_text()) or {}).items()}


def _yaml_stocks(name: str) -> list[dict]:
    p = ROOT / name
    if not p.exists():
        return []
    return (yaml.safe_load(p.read_text()) or {}).get("stocks", []) or []


def upsert_stock(conn: sqlite3.Connection, res: dict, **extra) -> str:
    stamp = now_ist().isoformat(timespec="seconds")
    sid = res["sid"]
    existing = conn.execute("SELECT * FROM stocks WHERE sid=?", (sid,)).fetchone()
    fields = {k: res.get(k) for k in ("isin", "name", "nse_symbol", "nse_series", "bse_code",
                                      "bse_symbol", "price_exch", "is_sme")}
    fields.update({k: v for k, v in extra.items() if v is not None})
    if existing is None:
        fields.update({"sid": sid, "added_at": stamp, "updated_at": stamp})
        cols = ",".join(fields)
        conn.execute(f"INSERT INTO stocks({cols}) VALUES({','.join('?' * len(fields))})",
                     list(fields.values()))
    else:
        fields["updated_at"] = stamp
        conn.execute(f"UPDATE stocks SET {','.join(f'{k}=?' for k in fields)} WHERE sid=?",
                     [*fields.values(), sid])
    return sid


def _financial_summary(folder: str | None) -> tuple[float | None, float | None]:
    """(market cap ₹Cr, trailing-4-quarter sales ₹Cr) from the latest Screener financials file."""
    if not folder:
        return None, None
    fin_dir = COMPANIES_DIR / folder / "financials"
    files = sorted(fin_dir.glob("*_financials_*.txt")) if fin_dir.is_dir() else []
    if not files:
        return None, None
    text = files[-1].read_text(errors="replace")
    mcap = None
    m = re.search(r"Market Cap\s*₹?\s*([\d,\.]+)\s*Cr", text)
    if m:
        mcap = float(m.group(1).replace(",", ""))
    sales_ttm = None
    q = text.split("## Quarterly Results", 1)
    if len(q) == 2:
        for line in q[1].splitlines():
            if line.strip().startswith(("Sales", "Revenue")) and "|" in line:
                vals = []
                for cell in line.split("|")[1:]:
                    cell = cell.strip().replace(",", "")
                    try:
                        vals.append(float(cell))
                    except ValueError:
                        pass
                if len(vals) >= 4:
                    sales_ttm = sum(vals[-4:])
                break
            if line.startswith("---"):
                break
    return mcap, sales_ttm


def sync_universe(conn: sqlite3.Connection, accounts: list[str], log=print) -> dict:
    """Rebuild membership flags from holdings, watchlist and notes; resolve identifiers."""
    stamp = now_ist().isoformat(timespec="seconds")
    sectors = _sector_map()
    registry = {strip_suffix(s.get("symbol", "")): s for s in _yaml_stocks("portfolio_companies.yaml")}
    unresolved: list[str] = []

    # ── holdings ──
    conn.execute("UPDATE stocks SET in_portfolio=0, in_watchlist=0")
    for acct in accounts:
        hf = PORTFOLIO_DIR / acct / "holdings" / "holdings.json"
        if not hf.exists():
            continue
        as_of = datetime.fromtimestamp(hf.stat().st_mtime, IST).date().isoformat()
        conn.execute("DELETE FROM holdings WHERE account=?", (acct,))
        for h in json.loads(hf.read_text()):
            res = resolve(conn, h.get("symbol", ""), h.get("exchange", ""), h.get("isin", ""))
            if res is None:
                unresolved.append(f"{acct}:{h.get('symbol')}")
                continue
            sym = h.get("symbol", "")
            reg = registry.get(strip_suffix(sym), {})
            folder = find_folder(sym, res["sid"], res.get("bse_code"), reg.get("screener_symbol"))
            meta = _meta(folder)
            mcap, sales = _financial_summary(folder)
            sid = upsert_stock(
                conn, res,
                name=meta.get("name") or reg.get("name") or res["name"],
                sector=meta.get("sector") or sectors.get(strip_suffix(sym)) or reg.get("sector"),
                folder=folder, mcap_cr=mcap, sales_ttm_cr=sales,
            )
            qty = float(h.get("quantity") or 0)
            conn.execute(
                "INSERT OR REPLACE INTO holdings(account, isin, sid, kite_symbol, exchange, quantity, "
                "avg_price, invested, last_price, as_of) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (acct, res["isin"], sid, sym, h.get("exchange"), qty, h.get("avg_price"),
                 h.get("invested_value"), h.get("last_price"), as_of),
            )
    conn.execute(
        "UPDATE stocks SET in_portfolio=1 WHERE sid IN (SELECT sid FROM holdings WHERE quantity > 0)"
    )

    # ── watchlist ──
    for w in _yaml_stocks("watchlist.yaml"):
        res = resolve(conn, str(w.get("symbol", "")), w.get("exchange", ""), "",
                      str(w.get("bse_code") or ""), w.get("name", ""))
        if res is None:
            unresolved.append(f"watchlist:{w.get('symbol')}")
            continue
        folder = find_folder(str(w.get("symbol", "")), res["sid"], res.get("bse_code") or "")
        meta = _meta(folder)
        mcap, sales = _financial_summary(folder)
        upsert_stock(
            conn, res,
            name=w.get("name") or meta.get("name") or res["name"],
            sector=w.get("sector") or meta.get("sector"),
            folder=folder, watch_reason=w.get("watch_reason") or None,
            mcap_cr=mcap, sales_ttm_cr=sales,
        )
        conn.execute("UPDATE stocks SET in_watchlist=1 WHERE sid=?", (res["sid"],))

    # ── notes ── (mentions are resolved by notes.index; flag is derived here)
    conn.execute("UPDATE stocks SET in_notes = CASE WHEN sid IN "
                 "(SELECT sid FROM note_mentions WHERE sid IS NOT NULL) THEN 1 ELSE 0 END")
    conn.execute("UPDATE stocks SET updated_at=? WHERE updated_at IS NULL", (stamp,))

    counts = dict(conn.execute(
        "SELECT SUM(in_portfolio), SUM(in_watchlist), SUM(in_notes), COUNT(*) FROM stocks"
    ).fetchone())
    vals = list(counts.values())
    summary = {"portfolio": vals[0] or 0, "watchlist": vals[1] or 0, "notes": vals[2] or 0,
               "total": vals[3] or 0, "unresolved": unresolved}
    if unresolved:
        log(f"  unresolved: {', '.join(unresolved)}")
    return summary


def _meta(folder: str | None) -> dict:
    if not folder:
        return {}
    p = COMPANIES_DIR / folder / "meta.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except ValueError:
        return {}


def add_to_watchlist_yaml(stock: dict, reason: str = "") -> bool:
    """Append a stock to watchlist.yaml in its existing format. Returns False if present."""
    p = ROOT / "watchlist.yaml"
    current = {str(s.get("symbol", "")).upper() for s in _yaml_stocks("watchlist.yaml")}
    sym = stock["sid"]
    if sym.upper() in current:
        return False
    entry = {
        "symbol": sym,
        "name": stock.get("name") or sym,
        "exchange": stock.get("price_exch") or "NSE",
        "sector": stock.get("sector") or "",
        "screener_url": f"https://www.screener.in/company/{stock.get('nse_symbol') or stock.get('bse_code') or sym}/",
        "bse_code": str(stock.get("bse_code") or ""),
        "watch_reason": reason,
        "peers": [],
    }
    text = p.read_text() if p.exists() else "stocks:\n"
    if not text.endswith("\n"):
        text += "\n"
    text += yaml.safe_dump([entry], sort_keys=False, allow_unicode=True)
    p.write_text(text)
    return True


def name_index(conn: sqlite3.Connection) -> dict[str, str]:
    """norm_name → sid for the tracked universe (used to spot company names in notes)."""
    return {norm_name(r["name"]): r["sid"] for r in conn.execute("SELECT sid, name FROM stocks") if r["name"]}


def holdings_value(conn: sqlite3.Connection) -> dict[str, dict]:
    """Merged holdings per sid across accounts."""
    out: dict[str, dict] = {}
    for h in conn.execute("SELECT * FROM holdings WHERE quantity > 0"):
        o = out.setdefault(h["sid"], {"quantity": 0.0, "invested": 0.0, "accounts": [], "kite_last": h["last_price"]})
        o["quantity"] += h["quantity"] or 0
        o["invested"] += h["invested"] or 0
        o["accounts"].append(h["account"])
    for o in out.values():
        o["avg_price"] = o["invested"] / o["quantity"] if o["quantity"] else None
    return out


def stock_folder_path(folder: str | None) -> Path | None:
    return COMPANIES_DIR / folder if folder else None
