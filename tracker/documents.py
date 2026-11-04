"""
Document pipeline — the only place the tracker spends LLM tokens.

  1. discover  NSE announcement items of tracked stocks (presentations, transcripts, results,
               order wins, capacity, acquisitions, press releases) → `documents` (queued);
               and, once, the PDFs already under data/companies/<folder>/{ppt,transcripts}/
               (back-fill, newest first).
  2. download  the attachment → data/companies/<folder>/filings/ (or data/tracker/filings/<sid>/),
               deduplicated by URL and SHA-256.
  3. prepare   text per page (pdfplumber, cached by hash) + keyword hits per page. No relevant
               pages → `irrelevant`, never sent to the LLM. Same hash as a document already
               extracted → `duplicate` (the cache: a PDF is never extracted twice).
  4. extract   relevant pages only, trimmed to llm.max_input_tokens, within the daily budget
               (extract.py). Every figure keeps its verbatim quote, page and source link.

Order-win filings also produce an order row straight from the NSE announcement text (no LLM),
so the Orders views fill even when the budget is spent.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import COMPANIES_DIR, ROOT, TRACKER_DIR
from .util import Http, money_to_crore, now_ist

TEXT_DIR = TRACKER_DIR / "text"
FILINGS_DIR = TRACKER_DIR / "filings"
MAX_SCAN_PAGES = 120

KIND_FOR_TYPE = {"results": "results", "concall_transcript": "transcript", "concall": "presentation",
                 "order_win": "order_win", "capacity": "capacity", "acquisition": "acquisition", "update": "press_release"}
KIND_WEIGHT = {"order_win": 30, "presentation": 25, "transcript": 20, "results": 15, "capacity": 12,
               "acquisition": 10, "press_release": 8}

# keyword groups → hits per page. "strong" groups decide whether a document is worth an LLM call.
KEYWORDS = {
    "order_book": re.compile(r"order\s*book|order\s*backlog|un-?executed order|outstanding orders?|order\s*position|"
                             r"orders? in hand|pending orders?|executable orders?", re.I),
    "order_inflow": re.compile(r"order\s*(inflow|intake)|new orders?|orders? (won|received|secured|bagged|worth)|"
                               r"book[- ]to[- ]bill|letter of (award|intent)|\bL-?1\b|work orders?|purchase orders?", re.I),
    "guidance": re.compile(r"guidance|we (expect|target|aim|anticipate|are confident|plan)|outlook|going forward|"
                           r"visibility|targeting|aspir|CAGR|next (year|fiscal|two years)", re.I),
    "capex": re.compile(r"\bcapex\b|capital expenditure|capacity (addition|expansion|utili[sz]ation)|greenfield|"
                        r"brownfield|commission(ed|ing)|new (plant|facility|unit|line)", re.I),
    "macro": re.compile(r"tailwind|headwind|government|policy|\bPLI\b|union budget|tariff|interest rates?|repo rate|"
                        r"demand environment|slowdown|geopolitic", re.I),
    "financials": re.compile(r"revenue|ebitda|margin|profit after tax|\bPAT\b|\bEPS\b", re.I),
}
STRONG = ("order_book", "order_inflow", "guidance", "capex", "macro")


def _rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve()))


def file_link(path: str | None) -> str | None:
    return f"/api/t/file?path={path}" if path else None


def quarter_before(d: date) -> str:
    """Quarter that a document filed on `d` reports on (the last completed quarter), e.g. Q2 FY27."""
    m, y = d.month, d.year
    if m <= 3:
        end_m, end_y = 12, y - 1
    elif m <= 6:
        end_m, end_y = 3, y
    elif m <= 9:
        end_m, end_y = 6, y
    else:
        end_m, end_y = 9, y
    return quarter_label(end_y, end_m)


def quarter_label(year: int, month: int) -> str:
    fy = year + 1 if month >= 4 else year
    q = {3: 4, 6: 1, 9: 2, 12: 3}.get(month) or ((month - 4) % 12) // 3 + 1
    return f"Q{q}FY{str(fy)[2:]}"


def settle_event(conn: sqlite3.Connection, event_id: int | None, *, severity: str | None = None, title: str | None = None,
                 detail: str | None = None) -> None:
    """Finish a filing event that waited for the AI read: restore its severity (or set one) and attach the summary.

    Filings that go to the LLM are created at low severity so the inbox gets one informative alert after
    the read instead of a bare "Results filed" first."""
    if not event_id:
        return
    row = conn.execute("SELECT severity, title, detail, data FROM events WHERE id=?", (event_id,)).fetchone()
    if not row:
        return
    data = json.loads(row["data"] or "{}") if row["data"] else {}
    sev0 = data.pop("sev0", None)
    data.pop("pending_ai", None)
    conn.execute("UPDATE events SET severity=?, title=?, detail=?, data=? WHERE id=?",
                 (severity or sev0 or row["severity"], title or row["title"], detail or row["detail"],
                  json.dumps(data, default=str), event_id))


# ── 1. discover ─────────────────────────────────────────────────────────────

def _insert(conn: sqlite3.Connection, **f) -> int | None:
    stamp = now_ist().isoformat(timespec="seconds")
    cols = ["ukey", "sid", "kind", "origin", "url", "path", "title", "filed_at", "period", "status", "priority",
            "backfill", "event_id", "created_at", "updated_at"]
    f.setdefault("status", "queued")
    f.setdefault("created_at", stamp)
    f.setdefault("updated_at", stamp)
    cur = conn.execute(f"INSERT OR IGNORE INTO documents({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                       [f.get(c) for c in cols])
    return cur.lastrowid if cur.rowcount else None


def discover_nse(conn: sqlite3.Connection, settings: dict) -> int:
    kinds = set(settings["documents"]["kinds"])
    rows = conn.execute(
        "SELECT f.*, e.id AS event_id, e.type AS etype, e.title AS etitle FROM feed_items f "
        "JOIN events e ON e.dedupe = 'feed:' || f.feed || ':' || f.guid "
        "WHERE f.feed IN ('announcements') AND f.sid IS NOT NULL AND f.url LIKE '%.pdf' "
        "AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.ukey = f.url)").fetchall()
    n = 0
    for r in rows:
        if r["etype"] not in kinds:
            continue
        kind = KIND_FOR_TYPE.get(r["etype"], "other")
        subj = f"{r['subject'] or ''} {r['description'] or ''}"
        if kind == "presentation" and not re.search(r"presentation", subj, re.I):
            kind = "meet"            # analyst-meet intimations: kept, but usually irrelevant
        if kind == "press_release" and re.search(r"presentation", subj, re.I):
            kind = "presentation"
        filed = r["ts"][:10]
        n += 1 if _insert(conn, ukey=r["url"], sid=r["sid"], kind=kind, origin="nse", url=r["url"],
                          title=(r["description"] or "").split("|SUBJECT")[0][:300], filed_at=r["ts"],
                          period=quarter_before(date.fromisoformat(filed)),
                          priority=100 + KIND_WEIGHT.get(kind, 0), backfill=0, event_id=r["event_id"]) else 0
    return n


_DISK_NAME = re.compile(r"_(PPT|Transcript)_([A-Za-z]{3})(\d{4})", re.I)
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def discover_disk(conn: sqlite3.Connection, settings: dict) -> int:
    since = settings["documents"].get("backfill_since") or "2000-01-01"
    n = 0
    for s in conn.execute("SELECT sid, folder FROM stocks WHERE folder IS NOT NULL").fetchall():
        base = COMPANIES_DIR / s["folder"]
        for sub, kind in (("ppt", "presentation"), ("transcripts", "transcript")):
            for f in (base / sub).glob("*.pdf"):
                m = _DISK_NAME.search(f.name)
                if m and m.group(2).lower() in _MONTHS:
                    filed = date(int(m.group(3)), _MONTHS[m.group(2).lower()], 15)
                else:
                    filed = datetime.fromtimestamp(f.stat().st_mtime).date()
                if filed.isoformat() < since:
                    continue
                rel = _rel(f)
                n += 1 if _insert(conn, ukey=f"file:{rel}", sid=s["sid"], kind=kind, origin="disk", path=rel,
                                  title=f.name, filed_at=f"{filed.isoformat()}T00:00:00+05:30",
                                  period=quarter_before(filed), priority=0, backfill=1) else 0
    return n


# ── 2. download ─────────────────────────────────────────────────────────────

def _target_dir(conn: sqlite3.Connection, sid: str) -> Path:
    row = conn.execute("SELECT folder FROM stocks WHERE sid=?", (sid,)).fetchone()
    if row and row["folder"]:
        return COMPANIES_DIR / row["folder"] / "filings"
    return FILINGS_DIR / re.sub(r"[^A-Za-z0-9&_-]", "_", sid)


def download(conn: sqlite3.Connection, http: Http, doc: sqlite3.Row) -> str:
    r = http.get(doc["url"])
    if r.status_code != 200:
        return f"HTTP {r.status_code}"
    if not r.content.startswith(b"%PDF"):
        return "not a PDF"
    sha = hashlib.sha256(r.content).hexdigest()
    folder = _target_dir(conn, doc["sid"])
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{doc['filed_at'][:10].replace('-', '')}_{doc['kind']}_{doc['url'].rsplit('/', 1)[-1]}"[:180]
    path = folder / name
    path.write_bytes(r.content)
    conn.execute("UPDATE documents SET path=?, sha256=?, updated_at=? WHERE id=?",
                 (_rel(path), sha, now_ist().isoformat(timespec="seconds"), doc["id"]))
    return "ok"


# ── 3. prepare ──────────────────────────────────────────────────────────────

def page_texts(path: Path, sha: str) -> list[str]:
    """Text of each page (cached by content hash under data/tracker/text/).

    layout.py: scanned pages OCR'd again locally, tables one row per line (`label | Q | PQ | YQ …`), the rest in
    reading order. If that fails, PDFium's plain text (pypdfium2) as before."""
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    cache = TEXT_DIR / f"{sha}.l2.json"
    if cache.exists():
        return json.loads(cache.read_text())["pages"]
    try:
        from . import layout
        pages, how = layout.page_texts(path, MAX_SCAN_PAGES)
        cache.write_text(json.dumps({"pages": pages, "how": how}))
        return pages
    except Exception:
        pass
    return plain_page_texts(path, sha)


def page_modes(sha: str) -> list[str]:
    """How each page was read: 'ocr' | 'rows' | 'text' (empty before the layout read)."""
    cache = TEXT_DIR / f"{sha}.l2.json"
    return json.loads(cache.read_text()).get("how", []) if cache.exists() else []


def plain_page_texts(path: Path, sha: str) -> list[str]:
    """The PDF's own text layer per page, as the tracker read it before layout.py (cached as data/tracker/text/<sha>.json).

    PDFium (pypdfium2, already installed with pdfplumber) — ~30× faster than pdfplumber, same text."""
    cache = TEXT_DIR / f"{sha}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    import pypdfium2 as pdfium

    pages: list[str] = []
    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception:            # PDFium rejects some malformed files that pdfminer still reads
        import logging

        import pdfplumber
        logging.getLogger("pdfminer").setLevel(logging.ERROR)
        with pdfplumber.open(path) as doc:
            pages = [(pg.extract_text() or "") for pg in doc.pages[:MAX_SCAN_PAGES]]
        cache.write_text(json.dumps(pages))
        return pages
    try:
        for i in range(min(len(pdf), MAX_SCAN_PAGES)):
            page = pdf[i]
            try:
                tp = page.get_textpage()
                pages.append(tp.get_text_range().replace("\r\n", "\n").replace("\r", "\n"))
                tp.close()
            except Exception:
                pages.append("")
            finally:
                page.close()
    finally:
        pdf.close()
    cache.write_text(json.dumps(pages))
    return pages


def score_pages(pages: list[str]) -> dict[int, dict[str, int]]:
    hits: dict[int, dict[str, int]] = {}
    for i, text in enumerate(pages, 1):
        h = {k: len(rx.findall(text)) for k, rx in KEYWORDS.items()}
        h = {k: v for k, v in h.items() if v}
        if h:
            hits[i] = h
    return hits


def prepare(conn: sqlite3.Connection, doc: sqlite3.Row) -> str:
    path = ROOT / doc["path"]
    if not path.exists():
        return "missing file"
    sha = doc["sha256"]
    if not sha:
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
    twin = conn.execute("SELECT id FROM documents WHERE sha256=? AND id != ? AND status='extracted' LIMIT 1",
                        (sha, doc["id"])).fetchone()
    stamp = now_ist().isoformat(timespec="seconds")
    if twin:
        conn.execute("UPDATE documents SET sha256=?, status='duplicate', dup_of=?, updated_at=? WHERE id=?",
                     (sha, twin["id"], stamp, doc["id"]))
        settle_event(conn, doc["event_id"], severity="low", detail="Same document as one already read — see the earlier filing.")
        return "duplicate"
    pages = page_texts(path, sha)
    chars = sum(len(p) for p in pages)
    hits = score_pages(pages)
    strong = sum(v for h in hits.values() for k, v in h.items() if k in STRONG)
    fin = sum(h.get("financials", 0) for h in hits.values())
    if chars < 40 * max(1, len(pages)):
        status = "no_text"          # scanned images that OCR couldn't read either (or tesseract is missing)
    elif strong >= 2 or (not doc["backfill"] and (doc["kind"] == "results" or (reads_financials(doc["kind"]) and fin >= 3))):
        status = "prepared"         # a plain results statement has only financial tables — still worth the read
    else:
        status = "irrelevant"
    keep = relevant_pages(pages, hits, 10 ** 9, include_financials=reads_financials(doc["kind"]) and not doc["backfill"])
    if not keep and status == "prepared":
        keep = list(range(1, len(pages) + 1))
    est = sum(len(pages[i - 1]) for i in keep) // 4
    priority = doc["priority"]
    if doc["backfill"] and any("order_book" in h for h in hits.values()):
        priority += 20              # back-fill: documents that state an order book go first
    conn.execute("UPDATE documents SET sha256=?, pages=?, chars=?, hits=?, est_tokens=?, status=?, priority=?, "
                 "updated_at=? WHERE id=?", (sha, len(pages), chars, json.dumps(hits), est, status, priority, stamp,
                                             doc["id"]))
    if status in ("irrelevant", "no_text"):
        settle_unread(conn, doc)                     # nothing for the AI to read: show the filing as it came
    return status


def settle_unread(conn: sqlite3.Connection, doc: sqlite3.Row) -> None:
    """A filing the AI will not read. Show it as it came — except a results filing whose quarter already has
    its own results event (XBRL numbers), which stays low so the inbox doesn't get a bare duplicate."""
    from .results import norm_period, period_end

    if doc["kind"] == "results" and doc["sid"]:
        per = norm_period(doc["period"])
        has = conn.execute("SELECT 1 FROM results_reviews WHERE sid=? AND period=?", (doc["sid"], per)).fetchone() or \
            conn.execute("SELECT 1 FROM financials_q WHERE sid=? AND period_end=? AND months=3",
                         (doc["sid"], period_end(per))).fetchone()
        if has:
            settle_event(conn, doc["event_id"], severity="low",
                         detail="Not read by the AI; the quarter's numbers are in its results event.")
            return
    settle_event(conn, doc["event_id"])


FINANCIAL_KINDS = ("results", "press_release", "presentation")


def reads_financials(kind: str | None) -> bool:
    """Documents whose results tables matter even without order-book / guidance words."""
    return kind in FINANCIAL_KINDS


def relevant_pages(pages: list[str], hits: dict, max_tokens: int, include_financials: bool = False) -> list[int]:
    """Pages worth sending, best first until the token cap, returned in page order."""
    def weight(h: dict) -> float:
        return (4 * h.get("order_book", 0) + 3 * h.get("order_inflow", 0) + 2 * h.get("guidance", 0)
                + 1.5 * h.get("capex", 0) + h.get("macro", 0) + (1.0 if include_financials else 0.3) * h.get("financials", 0))
    ranked = sorted(((weight(h), p) for p, h in hits.items()
                     if any(k in STRONG for k in h) or (include_financials and h.get("financials"))), reverse=True)
    out, used = [], 0
    for _, p in ranked:
        cost = len(pages[p - 1]) // 4 + 10
        if used + cost > max_tokens and out:
            continue
        out.append(p)
        used += cost
    return sorted(out)


# ── order rows straight from the announcement text (no LLM) ─────────────────

def orders_from_filings(conn: sqlite3.Connection, usd_inr: float) -> int:
    rows = conn.execute(
        "SELECT e.* FROM events e WHERE e.type='order_win' AND e.sid IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM orders o WHERE o.event_id = e.id AND o.method='filing_text')").fetchall()
    n = 0
    for e in rows:
        text = e["detail"] or e["title"] or ""
        value = e["value_cr"] if e["value_cr"] is not None else money_to_crore(text, usd_inr)
        sentence = _sentence_with_value(text)
        conn.execute(
            "INSERT OR IGNORE INTO orders(sid, event_id, announced_on, customer, order_type, value_cr, duration_months, "
            "annual_value_cr, description, quote, page, verified, url, method, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (e["sid"], e["id"], e["d"], None, None, value, None, None, text[:400], sentence, None,
             1.0 if value is not None else 0.5, e["url"], "filing_text", now_ist().isoformat(timespec="seconds")))
        n += 1
    return n


def _sentence_with_value(text: str) -> str:
    parts = re.split(r"(?<=[.;])\s+", text or "")
    for p in parts:
        if re.search(r"(rs\.?|inr|₹|usd|\$|crore|cr\b|lakh|million|mn\b|billion)", p, re.I):
            return p.strip()[:400]
    return (text or "").strip()[:400]


# ── queue runner ────────────────────────────────────────────────────────────

def run(conn: sqlite3.Connection, http: Http, settings: dict, max_seconds: float | None = None) -> dict:
    """Discover → download → prepare → extract, bounded by time and the LLM budget."""
    from . import company_notes, extract

    cfg = settings["documents"]
    t0 = time.monotonic()
    budget_s = max_seconds if max_seconds is not None else cfg["max_seconds_per_tick"]
    out = {"discovered": discover_nse(conn, settings), "orders_text": orders_from_filings(conn, settings["orders"]["usd_inr"])}
    if cfg.get("backfill") and not conn.execute("SELECT 1 FROM kv WHERE k='docs_disk_scanned_on' AND v=?",
                                                (json.dumps(now_ist().date().isoformat()),)).fetchone():
        out["discovered_disk"] = discover_disk(conn, settings)
        conn.execute("INSERT OR REPLACE INTO kv(k, v, updated_at) VALUES('docs_disk_scanned_on', ?, ?)",
                     (json.dumps(now_ist().date().isoformat()), now_ist().isoformat(timespec="seconds")))
    conn.commit()

    def left() -> float:
        return budget_s - (time.monotonic() - t0)

    # download (live filings only — disk documents are already local)
    dl = 0
    for doc in conn.execute("SELECT * FROM documents WHERE status='queued' AND path IS NULL AND url IS NOT NULL "
                            "ORDER BY priority DESC, filed_at DESC LIMIT 20").fetchall():
        if left() <= 0:
            break
        try:
            res = download(conn, http, doc)
        except Exception as exc:
            res = f"error: {exc.__class__.__name__}"
        if res != "ok":
            conn.execute("UPDATE documents SET status='failed', error=?, updated_at=? WHERE id=?",
                         (res, now_ist().isoformat(timespec="seconds"), doc["id"]))
            settle_unread(conn, doc)
        dl += res == "ok"
        conn.commit()
    out["downloaded"] = dl

    # order books the earlier workflows already found (no LLM)
    if cfg.get("backfill") and left() > 0:
        out["insights"] = backfill_insights(conn, settings, deadline=lambda: left() > budget_s * 0.5)

    # prepare (text + keywords), live first; back-fill gets at most half of what is left
    prep: dict[str, int] = {}
    reserve = left() * 0.5
    for doc in conn.execute("SELECT * FROM documents WHERE status='queued' AND path IS NOT NULL "
                            "ORDER BY backfill, priority DESC, filed_at DESC LIMIT 400").fetchall():
        if left() <= (reserve if doc["backfill"] else 0):
            break
        try:
            res = prepare(conn, doc)
        except Exception as exc:
            res = "failed"
            conn.execute("UPDATE documents SET status='failed', error=?, updated_at=? WHERE id=?",
                         (f"{exc.__class__.__name__}: {exc}"[:300], now_ist().isoformat(timespec="seconds"), doc["id"]))
            settle_unread(conn, doc)
        prep[res] = prep.get(res, 0) + 1
        conn.commit()
    out["prepared"] = prep

    # extract within the budget: new filings first, then a couple of companies' notes from earlier reports,
    # then old PDFs with whatever time is left (so the notes back-fill is never starved by old PDFs)
    llm_on = settings["llm"].get("enabled", True)
    if llm_on:
        out["extracted"] = extract.run_queue(conn, settings, deadline=lambda: left() > 0, which="live")
    if llm_on and settings.get("notes", {}).get("backfill", True) and left() > 0:
        # notes first: while companies still wait for theirs, old PDFs are not read (both share the back-fill cap)
        try:        # never let the notes back-fill stop the old-PDF back-fill and the pending-event settle below
            out["notes_backfill"] = company_notes.backfill(conn, settings, deadline=lambda: left() > 0, limit=2)
            out["notes_backfill"]["views"] = company_notes.views_missing(conn, settings, deadline=lambda: left() > 0)
        except Exception as exc:
            conn.rollback()
            out["notes_backfill"] = {"error": f"{exc.__class__.__name__}: {exc}"}
    notes_waiting = (llm_on and settings.get("notes", {}).get("backfill", True) and company_notes.pending(conn) > 0
                     and not out.get("notes_backfill", {}).get("deferred_budget"))
    if llm_on and left() > 0 and not notes_waiting:
        out["extracted_backfill"] = extract.run_queue(conn, settings, deadline=lambda: left() > 0, which="backfill")

    # any filing event still waiting for the AI read after 45 minutes — whatever happened to its document
    # (budget spent, LLM down, read by an older server, never became a document) — is shown as it came
    cutoff = (now_ist() - timedelta(minutes=45)).isoformat(timespec="seconds")
    for ev in conn.execute("SELECT id FROM events WHERE backfill=0 AND data LIKE '%\"pending_ai\"%' AND created_at < ?",
                           (cutoff,)).fetchall():
        doc = conn.execute("SELECT * FROM documents WHERE event_id=?", (ev["id"],)).fetchone()
        if doc is not None:
            settle_unread(conn, doc)
        else:
            settle_event(conn, ev["id"])
    conn.commit()

    out["secs"] = round(time.monotonic() - t0, 1)
    return out


def queue_counts(conn: sqlite3.Connection) -> dict:
    out: dict = {}
    for r in conn.execute("SELECT status, backfill, COUNT(*) n, SUM(est_tokens) t FROM documents GROUP BY status, backfill"):
        key = ("backfill" if r["backfill"] else "live")
        out.setdefault(key, {})[r["status"]] = {"n": r["n"], "tokens": r["t"] or 0}
    return out


# ── back-fill: order books the earlier workflows already found (no LLM) ─────

ORDER_BOOK_METRIC = re.compile(r"order\s*book|order\s*backlog|un-?executed|outstanding orders?|orders? in hand", re.I)
_QLABEL = re.compile(r"Q([1-4])\s*FY\s*'?(\d{2})", re.I)


def _q_end(label: str) -> str | None:
    m = _QLABEL.search(label or "")
    if not m:
        return None
    q, fy = int(m.group(1)), 2000 + int(m.group(2))
    return {1: f"{fy - 1}-06-30", 2: f"{fy - 1}-09-30", 3: f"{fy - 1}-12-31", 4: f"{fy}-03-31"}[q]


def _value_cr(raw, metric: str, usd_inr: float) -> float | None:
    if raw is None:
        return None
    text = str(raw)
    v = money_to_crore(text, usd_inr)
    if v is None:
        m = re.search(r"\d[\d,]*(?:\.\d+)?", text)
        if not m:
            return None
        v = float(m.group(0).replace(",", ""))
        unit = f"{metric} {text}".lower()
        if re.search(r"\bmn\b|million", unit):
            v /= 10
        elif re.search(r"\bbn\b|billion", unit):
            v *= 100
        elif re.search(r"lakh", unit) and "crore" not in unit:
            v /= 100
    return round(v, 2) if v and v > 0 else None


def backfill_insights(conn: sqlite3.Connection, settings: dict, deadline=lambda: True) -> dict:
    """insights_values.json order-book series → order_book_history, each excerpt re-located in its PDF."""
    from .extract import ORDER_BOOK_TERM, anchored, number_in_quote, stated_date, verify

    usd = settings["orders"]["usd_inr"]
    out = {"stocks": 0, "points": 0}
    for s in conn.execute("SELECT sid, folder FROM stocks WHERE folder IS NOT NULL").fetchall():
        if not deadline():
            break
        f = COMPANIES_DIR / s["folder"] / "insights" / "insights_values.json"
        if not f.exists():
            continue
        sig = f"{int(f.stat().st_mtime)}"
        key = f"insights_ob:{s['sid']}"
        if conn.execute("SELECT 1 FROM kv WHERE k=? AND v=?", (key, json.dumps(sig))).fetchone():
            continue
        try:
            data = json.loads(f.read_text(errors="replace"))
        except ValueError:
            data = {}
        sources = data.get("sources") or {}
        for metric, series in (data.get("metrics") or {}).items():
            if not ORDER_BOOK_METRIC.search(metric or "") or not isinstance(series, dict):
                continue
            if re.search(r"inflow|intake|pipeline|bid", metric, re.I):
                continue
            seg = re.search(r"order\s*(?:book|backlog)\s*[-–:(]\s*([A-Za-z][^()]*?)\)?\s*(?:INR|₹|Rs|$)", metric, re.I)
            scope = seg.group(1).strip()[:60] if seg else "total"
            for q, raw in series.items():
                v, as_of = _value_cr(raw, metric, usd), _q_end(q)
                src = sources.get(f"{metric}|{q}") or {}
                if not v or not as_of or not src.get("file"):
                    continue
                pdfs = list((COMPANIES_DIR / s["folder"]).glob(f"*/{src['file']}"))
                quote, page, score, rel, doc_id = str(src.get("excerpt") or "")[:600], None, 0.0, None, None
                if pdfs:
                    rel = _rel(pdfs[0])
                    row = conn.execute("SELECT id, sha256 FROM documents WHERE ukey=?", (f"file:{rel}",)).fetchone()
                    doc_id = row["id"] if row else None
                    sha = (row["sha256"] if row and row["sha256"] else None) or hashlib.sha256(pdfs[0].read_bytes()).hexdigest()
                    try:
                        pages = page_texts(pdfs[0], sha)
                        score, page = verify(quote, None, pages)
                        if score and not number_in_quote(v, quote, usd):
                            score = min(score, 0.5)
                        if score == 0:
                            p2 = anchored(v, pages, None, usd)
                            if p2:
                                score, page = 0.5, p2
                    except Exception:
                        pass
                if not ORDER_BOOK_TERM.search(quote):
                    score = 0.0          # e.g. "bids of ₹700 Cr" filed under order book by the earlier run
                filed = None
                if doc_id:
                    filed = conn.execute("SELECT substr(filed_at,1,10) f FROM documents WHERE id=?", (doc_id,)).fetchone()["f"]
                if stated_date(quote):
                    as_of = stated_date(quote)
                elif filed and re.search(r"as on date|as of date|as of today|currently|current order book|right now",
                                         quote, re.I):
                    as_of = filed        # "as on date" = the day of the call / filing
                cur = conn.execute(
                    "INSERT OR IGNORE INTO order_book_history(sid, as_of, value_cr, scope, doc_id, method, quote, page, "
                    "verified, url, path, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (s["sid"], as_of, v, scope, doc_id, "insights", quote, page, score, file_link(rel), rel,
                     now_ist().isoformat(timespec="seconds")))
                out["points"] += cur.rowcount
        conn.execute("INSERT OR REPLACE INTO kv(k, v, updated_at) VALUES(?,?,?)",
                     (key, json.dumps(sig), now_ist().isoformat(timespec="seconds")))
        conn.commit()
        out["stocks"] += 1
    return out
