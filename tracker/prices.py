"""
End-of-day prices from the exchanges' official bhavcopy files (UDiFF format).

  NSE  https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip
  BSE  https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_YYYYMMDD_F_0000.CSV

One file per exchange per day covers every listed stock, SME boards included.
Equity rows are cached gzipped under data/tracker/raw/bhavcopy/<exch>/<year>/.
Bhavcopy closes are unadjusted; split/bonus factors are derived from the exchange's
own adjusted previous close (PrvsClsgPric) — see `adjusted_series`.
"""
from __future__ import annotations

import csv
import gzip
import io
import sqlite3
import zipfile
from datetime import date, datetime

import pandas as pd

from . import refdata
from .config import RAW_DIR
from .util import Http, days_back, now_ist, to_float

FIELDS = ["d", "exch", "code", "symbol", "isin", "series", "name",
          "open", "high", "low", "close", "prev_close", "volume", "turnover"]


def _url(exch: str, d: date) -> str:
    ymd = d.strftime("%Y%m%d")
    if exch == "NSE":
        return f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip"
    return f"https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{ymd}_F_0000.CSV"


def _cache_path(exch: str, d: date):
    return RAW_DIR / "bhavcopy" / exch.lower() / str(d.year) / f"{d.strftime('%Y%m%d')}.csv.gz"


# Regular-market equity series. Excludes e.g. BL (block-deal window), which shares the
# symbol and ISIN but trades at negotiated prices with its own "previous close".
NSE_SERIES = {"EQ", "BE", "BZ", "BT", "SM", "ST", "SZ"}


def _is_equity_isin(isin: str) -> bool:
    # INE + issuer(4) + security type "01" (equity shares) + serial + check digit
    return len(isin) == 12 and isin.startswith("INE") and isin[7:9] == "01"


def parse_bhavcopy(text: str, exch: str) -> list[dict]:
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        if r.get("FinInstrmTp", "STK") != "STK":
            continue
        isin = (r.get("ISIN") or "").strip()
        if not _is_equity_isin(isin):
            continue
        close = to_float(r.get("ClsPric"))
        if not close:
            continue
        if exch == "NSE" and (r.get("SctySrs") or "").strip() not in NSE_SERIES:
            continue
        code = (r.get("TckrSymb") if exch == "NSE" else r.get("FinInstrmId")) or ""
        out.append({
            "d": r.get("TradDt", "")[:10],
            "exch": exch,
            "code": code.strip(),
            "symbol": (r.get("TckrSymb") or "").strip(),
            "isin": isin,
            "series": (r.get("SctySrs") or "").strip(),
            "name": (r.get("FinInstrmNm") or "").strip(),
            "open": to_float(r.get("OpnPric")),
            "high": to_float(r.get("HghPric")),
            "low": to_float(r.get("LwPric")),
            "close": close,
            "prev_close": to_float(r.get("PrvsClsgPric")),
            "volume": to_float(r.get("TtlTradgVol")),
            "turnover": to_float(r.get("TtlTrfVal")),
        })
    return out


def _write_cache(exch: str, d: date, rows: list[dict]) -> None:
    path = _cache_path(exch, d)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS)
    w.writeheader()
    w.writerows(rows)
    path.write_bytes(gzip.compress(buf.getvalue().encode()))


def read_cache(exch: str, d: date) -> list[dict] | None:
    path = _cache_path(exch, d)
    if not path.exists():
        return None
    rows = list(csv.DictReader(io.StringIO(gzip.decompress(path.read_bytes()).decode())))
    if exch == "NSE":
        rows = [r for r in rows if r["series"] in NSE_SERIES]
    for r in rows:
        for k in ("open", "high", "low", "close", "prev_close", "volume", "turnover"):
            r[k] = to_float(r[k])
    return rows


def fetch_day(http: Http, exch: str, d: date) -> tuple[str, list[dict]]:
    """Returns (status, rows). status: loaded | cached | missing (holiday / not yet published)."""
    cached = read_cache(exch, d)
    if cached is not None:
        return "cached", cached
    r = http.get(_url(exch, d))
    if r.status_code == 404:
        return "missing", []
    if r.status_code != 200:
        raise RuntimeError(f"{exch} bhavcopy {d}: HTTP {r.status_code}")
    if exch == "NSE":
        if r.content[:2] != b"PK":
            return "missing", []
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            text = z.read(z.namelist()[0]).decode("utf-8", errors="replace")
    else:
        text = r.text
        if not text.lstrip().startswith("TradDt"):
            return "missing", []          # BSE serves its HTML site for non-trading days
    rows = parse_bhavcopy(text, exch)
    if not rows:
        return "missing", []
    _write_cache(exch, d, rows)
    return "loaded", rows


# ── loading into the DB ────────────────────────────────────────────────────

def tracked_keys(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """{'NSE': {code_or_isin: sid}, 'BSE': {...}} for stocks priced on each exchange."""
    keys: dict[str, dict[str, str]] = {"NSE": {}, "BSE": {}}
    for s in conn.execute("SELECT sid, isin, nse_symbol, bse_code, price_exch FROM stocks"):
        exch = s["price_exch"]
        if exch not in keys:
            continue
        code = s["nse_symbol"] if exch == "NSE" else s["bse_code"]
        if code:
            keys[exch][code] = s["sid"]
        if s["isin"]:
            keys[exch][s["isin"]] = s["sid"]
    return keys


def store_rows(conn: sqlite3.Connection, rows: list[dict], keys: dict[str, str]) -> int:
    n = 0
    for r in rows:
        if r["code"] not in keys and r["isin"] not in keys:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO prices(exch, code, d, isin, series, open, high, low, close, "
            "prev_close, volume, turnover) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["exch"], r["code"], r["d"], r["isin"], r["series"], r["open"], r["high"], r["low"],
             r["close"], r["prev_close"], r["volume"], r["turnover"]),
        )
        n += 1
    return n


def _mark_day(conn: sqlite3.Connection, exch: str, d: date, rows: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO trading_days(exch, d, rows, loaded_at) VALUES(?,?,?,?)",
        (exch, d.isoformat(), rows, now_ist().isoformat(timespec="seconds")),
    )


def sync(conn: sqlite3.Connection, http: Http, days: int = 400, log=print, until: date | None = None) -> dict:
    """Load every day in the last `days` calendar days that isn't loaded yet.

    Weekends are tried too: exchanges occasionally hold special sessions (Budget day,
    Muhurat trading) and skipping them corrupts the split/bonus detection. A missing
    file for a past date is recorded as a holiday (rows=0); today's missing file just
    means it isn't published yet and is retried on the next run.
    """
    today = now_ist().date()
    end = min(until or today, today)
    done = {(r["exch"], r["d"]) for r in conn.execute("SELECT exch, d FROM trading_days")}
    keys = tracked_keys(conn)
    summary = {"NSE": 0, "BSE": 0, "holidays": 0, "pending_today": []}
    latest_bse_rows: list[dict] = []
    for d in days_back(end, days):
        for exch in ("NSE", "BSE"):
            if (exch, d.isoformat()) in done:
                continue
            try:
                status, rows = fetch_day(http, exch, d)
            except Exception as exc:          # network trouble: retry on the next run
                log(f"  ! {exch} {d}: {exc}")
                continue
            if status == "missing":
                if d < today:
                    _mark_day(conn, exch, d, 0)
                    summary["holidays"] += 1
                else:
                    summary["pending_today"].append(exch)
                continue
            n = store_rows(conn, rows, keys[exch])
            _mark_day(conn, exch, d, len(rows))
            summary[exch] += 1
            if exch == "BSE":
                latest_bse_rows = rows
            if status == "loaded":
                log(f"  {exch} {d}: {len(rows)} equities, {n} tracked")
        conn.commit()
    if latest_bse_rows:
        refdata.refresh_bse_from_rows(conn, latest_bse_rows)
    elif not conn.execute("SELECT 1 FROM refdata WHERE exch='BSE' LIMIT 1").fetchone():
        last = latest_day(conn, "BSE")
        if last:
            refdata.refresh_bse_from_rows(conn, read_cache("BSE", last) or [])
    return summary


def replay(conn: sqlite3.Connection, sids: list[str] | None = None) -> int:
    """Re-load cached bhavcopy rows for (newly added) stocks."""
    keys_all = tracked_keys(conn)
    if sids is not None:
        wanted = set(sids)
        keys_all = {ex: {k: v for k, v in m.items() if v in wanted} for ex, m in keys_all.items()}
    n = 0
    for row in conn.execute("SELECT exch, d FROM trading_days WHERE rows > 0 ORDER BY d").fetchall():
        keys = keys_all.get(row["exch"]) or {}
        if not keys:
            continue
        rows = read_cache(row["exch"], date.fromisoformat(row["d"]))
        if rows:
            n += store_rows(conn, rows, keys)
    return n


def latest_day(conn: sqlite3.Connection, exch: str = "NSE") -> date | None:
    r = conn.execute("SELECT MAX(d) AS d FROM trading_days WHERE exch=? AND rows>0", (exch,)).fetchone()
    return date.fromisoformat(r["d"]) if r and r["d"] else None


# ── series for one stock ───────────────────────────────────────────────────

def adjusted_series(conn: sqlite3.Connection, stock: sqlite3.Row | dict) -> pd.DataFrame:
    """Daily OHLCV for one stock, adjusted for splits / bonuses / consolidations.

    Bhavcopy prices are unadjusted. Factors come from NSE's corporate-action file
    (see corpactions.py); a gap that no circuit band allows (>25% overnight) and that
    no nearby NSE record explains is matched to a standard split/bonus ratio.
    """
    from . import corpactions

    exch = stock["price_exch"]
    code = stock["nse_symbol"] if exch == "NSE" else stock["bse_code"]
    rows = conn.execute(
        "SELECT d, open, high, low, close, prev_close, volume, turnover FROM prices "
        "WHERE exch=? AND (code=? OR isin=?) ORDER BY d",
        (exch, code or "", stock["isin"] or ""),
    ).fetchall()
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "prev_close", "volume", "factor", "ca_note"])
    df = pd.DataFrame([dict(r) for r in rows]).drop_duplicates("d", keep="last")
    df["d"] = pd.to_datetime(df["d"])
    df = df.set_index("d").sort_index()

    n = len(df)
    factor = [1.0] * n
    note = [""] * n
    recorded: list[int] = []
    for ex_date, f, purpose in corpactions.factors_for(conn, stock["nse_symbol"] if exch == "NSE" else None):
        pos = int(df.index.searchsorted(pd.Timestamp(ex_date)))
        if 0 < pos < n:
            factor[pos] *= f
            note[pos] = purpose
            recorded.append(pos)
    opens, closes = df["open"].tolist(), df["close"].tolist()
    for i in range(1, n):
        if factor[i] != 1.0 or any(abs(i - p) <= 5 for p in recorded):
            continue
        c0 = closes[i - 1]
        s = corpactions.infer(c0, opens[i], closes[i])
        if s:
            factor[i] = s
            note[i] = f"inferred from {closes[i] / c0 - 1:+.0%} move"
    df["factor"] = factor
    df["ca_note"] = note
    # cumulative multiplier for each bar = product of factors of all LATER bars
    mult = []
    running = 1.0
    for f in reversed(factor):
        mult.append(running)
        running *= f
    mult.reverse()
    m = pd.Series(mult, index=df.index)
    for col in ("open", "high", "low", "close"):
        df[col] = df[col] * m
    df["volume"] = df["volume"] / m
    return df


def adjustment_events(df: pd.DataFrame) -> list[tuple[datetime, float, str]]:
    return [(idx.to_pydatetime(), f, note) for (idx, f), note in zip(df["factor"].items(), df["ca_note"])
            if f != 1.0]
