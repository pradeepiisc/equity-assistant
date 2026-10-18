"""
Shared helpers: IST clock, polite HTTP, company-name normalisation, money parsing.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    return datetime.now(IST)


def today_ist() -> date:
    return now_ist().date()


def iso(dt: datetime) -> str:
    return dt.astimezone(IST).isoformat(timespec="seconds")


def days_back(end: date, n_days: int) -> list[date]:
    """Calendar days from (end - n_days) to end, oldest first."""
    return [end - timedelta(days=i) for i in range(n_days, -1, -1)]


# ── HTTP ────────────────────────────────────────────────────────────────────

class Http:
    """Small polite client: one request per `min_interval` seconds per host, retries on 5xx."""

    def __init__(self, user_agent: str, min_interval: float = 1.0, timeout: float = 30.0):
        self.client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "*/*"},
            timeout=timeout,
            follow_redirects=True,
        )
        self.min_interval = min_interval
        self._last: dict[str, float] = {}

    def get(self, url: str, retries: int = 2) -> httpx.Response:
        host = httpx.URL(url).host
        for attempt in range(retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()
            try:
                r = self.client.get(url)
            except httpx.HTTPError:
                if attempt == retries:
                    raise
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 500 and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return r
        raise RuntimeError("unreachable")

    def close(self) -> None:
        self.client.close()


# ── names ───────────────────────────────────────────────────────────────────

_NAME_STOP = {"limited", "ltd", "the", "pvt", "private", "co", "company", "corp", "corporation", "inc"}


def norm_name(name: str) -> str:
    """'Rossell Techsys Ltd.' and 'ROSSELL TECHSYS LIMITED' → 'rossell techsys'."""
    s = (name or "").lower().replace("&", " and ")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(t for t in s.split() if t not in _NAME_STOP)


# ── numbers & money ─────────────────────────────────────────────────────────

def to_float(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).replace(",", "").replace("₹", "").replace("%", "").strip()
    if t in ("", "-", "—", "NA", "N/A"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


_MONEY = re.compile(
    r"(?P<cur>rs\.?|inr|₹|usd|us\$|\$|eur|€)\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>lakh\s*crores?|crores?|cr\b|crs\b|lakhs?|lacs?|million|mn\b|mio\b|billion|bn\b)?",
    re.I,
)
_MONEY_SUFFIX = re.compile(
    r"(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>crores?|cr\b|crs\b|lakhs?|lacs?)\b", re.I
)


def money_to_crore(text: str, usd_inr: float = 88.0) -> float | None:
    """Largest monetary amount mentioned in `text`, in ₹ crore (None if none found)."""
    best: float | None = None
    for m in _MONEY.finditer(text or ""):
        cur = m.group("cur").lower()
        num = to_float(m.group("num"))
        unit = (m.group("unit") or "").lower().replace(" ", "")
        if num is None:
            continue
        fx = usd_inr if cur in ("usd", "us$", "$") else (usd_inr * 1.08 if cur in ("eur", "€") else 1.0)
        if unit.startswith("lakhcrore"):
            cr = num * 1e5
        elif unit.startswith(("crore", "cr")):
            cr = num
        elif unit.startswith(("lakh", "lac")):
            cr = num / 100
        elif unit in ("million", "mn", "mio"):
            cr = num * 1e6 / 1e7
        elif unit in ("billion", "bn"):
            cr = num * 1e9 / 1e7
        else:
            # bare amount, e.g. "Rs. 12,50,00,000" or "USD 5,000,000"; small ones are per-share prices
            if num * fx < 1e5:
                continue
            cr = num / 1e7
        cr *= fx
        if best is None or cr > best:
            best = cr
    if best is None:
        for m in _MONEY_SUFFIX.finditer(text or ""):
            num = to_float(m.group("num"))
            if num is None:
                continue
            unit = m.group("unit").lower()
            cr = num if unit.startswith("cr") else num / 100
            if best is None or cr > best:
                best = cr
    return round(best, 2) if best is not None else None


def fmt_cr(v: float | None) -> str:
    if v is None:
        return "—"
    if v >= 100:
        return f"₹{v:,.0f} Cr"
    if v >= 1:
        return f"₹{v:,.1f} Cr"
    return f"₹{v * 100:,.0f} lakh"
