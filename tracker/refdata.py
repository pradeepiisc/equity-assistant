"""
Exchange master data: every listed equity on NSE (mainboard + Emerge SME) and BSE,
used to resolve symbols, ISINs, scrip codes and company names.

Sources (public files, no login):
  NSE mainboard  https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv
  NSE Emerge     https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv
  BSE            the latest BSE bhavcopy (scrip code, ticker, ISIN, name)
"""
from __future__ import annotations

import csv
import io
import sqlite3

from .config import RAW_DIR
from .util import Http, norm_name, now_ist

NSE_EQUITY_L = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
NSE_SME_L = "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv"


def _fetch_csv(http: Http, url: str, cache_name: str) -> list[dict]:
    r = http.get(url)
    path = RAW_DIR / "master" / cache_name
    path.parent.mkdir(parents=True, exist_ok=True)
    if r.status_code == 200 and r.text.lstrip().upper().startswith("SYMBOL"):
        path.write_text(r.text)
        text = r.text
    elif path.exists():
        text = path.read_text()
    else:
        raise RuntimeError(f"{url} → HTTP {r.status_code}")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        rows.append({(k or "").strip().upper(): (v or "").strip() for k, v in row.items()})
    return rows


def refresh_nse(conn: sqlite3.Connection, http: Http) -> int:
    stamp = now_ist().isoformat(timespec="seconds")
    n = 0
    for url, name in ((NSE_EQUITY_L, "nse_equity_l.csv"), (NSE_SME_L, "nse_sme_equity_l.csv")):
        for r in _fetch_csv(http, url, name):
            sym = r.get("SYMBOL")
            if not sym:
                continue
            company = r.get("NAME OF COMPANY") or r.get("NAME_OF_COMPANY") or ""
            isin = r.get("ISIN NUMBER") or r.get("ISIN_NUMBER") or ""
            conn.execute(
                "INSERT INTO refdata(exch, code, isin, symbol, series, name, norm_name, updated_at) "
                "VALUES('NSE',?,?,?,?,?,?,?) ON CONFLICT(exch, code) DO UPDATE SET "
                "isin=excluded.isin, series=excluded.series, name=excluded.name, "
                "norm_name=excluded.norm_name, updated_at=excluded.updated_at",
                (sym, isin, sym, r.get("SERIES", ""), company, norm_name(company), stamp),
            )
            n += 1
    return n


def refresh_bse_from_rows(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """Upsert BSE scrips from parsed bhavcopy rows (see prices.parse_bhavcopy)."""
    stamp = now_ist().isoformat(timespec="seconds")
    for r in rows:
        conn.execute(
            "INSERT INTO refdata(exch, code, isin, symbol, series, name, norm_name, updated_at) "
            "VALUES('BSE',?,?,?,?,?,?,?) ON CONFLICT(exch, code) DO UPDATE SET "
            "isin=excluded.isin, symbol=excluded.symbol, series=excluded.series, "
            "name=excluded.name, norm_name=excluded.norm_name, updated_at=excluded.updated_at",
            (r["code"], r["isin"], r["symbol"], r["series"], r["name"], norm_name(r["name"]), stamp),
        )
    return len(rows)


def lookup(conn: sqlite3.Connection, exch: str, code: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM refdata WHERE exch=? AND code=?", (exch, code)).fetchone()


def by_isin(conn: sqlite3.Connection, isin: str) -> dict[str, sqlite3.Row]:
    return {r["exch"]: r for r in conn.execute("SELECT * FROM refdata WHERE isin=?", (isin,))}


def by_name(conn: sqlite3.Connection, name: str, exch: str | None = None) -> list[sqlite3.Row]:
    q = "SELECT * FROM refdata WHERE norm_name=?"
    args: list = [norm_name(name)]
    if exch:
        q += " AND exch=?"
        args.append(exch)
    return conn.execute(q, args).fetchall()
