"""
Page text that keeps tables readable — what the LLM, the quote checks and the number check (numcheck.py) read.

  • scanned pages — a page-sized image whose only text is an invisible OCR layer, or none at all: OCR'd again
    here with tesseract at 300 dpi. The scanner's own layer is ignored: it reads 1 as I and 5 as s, splits numbers
    ("I 8,899.76", "8 1 0.82") and lists a table column by column with the empty cells dropped, so a model can't
    tell which number belongs to which row or quarter (AEROENTER Q2 FY27).
  • table pages of digital PDFs (many numbers): rebuilt from character positions.
  • everything else: the PDF's text in reading order, as before.

A rebuilt table has one row per line, cells separated by " | ", in the column order of the header lines above it:
    Revenue from operations | 1 | 24,990.49 | 18,899.76 | 17,234.97 | 43,890.25 | 30,682.88 | 69,783.06

No LLM and no network: tesseract runs locally (about 2 s a page, four pages at a time). Without tesseract a scanned
page keeps its embedded text, as before.
"""
from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from statistics import median

OCR_DPI = 300
OCR_WORKERS = 4
NUMBER = re.compile(r"^\(?-?[\d,]*\d(?:\.\d+)?\)?%?$")
CELL_NUM = re.compile(r"^[(\[]?[-–]?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:[.,]\d{1,4})?[)\]]?%?[,;]?$")
CELL_NIL = re.compile(r"^[-–—=~_.']{1,3}$")
OCR_DECIMAL_COMMA = re.compile(r"^([(\[]?[-–]?\d{1,3}(?:,\d{2,3})*),(\d{2})([)\]]?)$")   # "659,96" → "659.96"


def tesseract_bin() -> str | None:
    """The LaunchAgent's PATH has no Homebrew directories, so look there too."""
    return shutil.which("tesseract") or next(
        (p for p in ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract") if os.path.exists(p)), None)


# ── which pages need what ───────────────────────────────────────────────────

def is_scanned(page, textpage) -> bool:
    """A page that is mostly one image, with no visible text of its own."""
    import pypdfium2.raw as R

    w, h = page.get_size()
    img = 0.0
    txt = inv = 0
    for o in page.get_objects(max_depth=2):
        if o.type == R.FPDF_PAGEOBJ_IMAGE:
            l, b, r, t = o.get_bounds()
            img += max(0.0, r - l) * max(0.0, t - b)
        elif o.type == R.FPDF_PAGEOBJ_TEXT:
            txt += 1
            inv += R.FPDFTextObj_GetTextRenderMode(o.raw) == R.FPDF_TEXTRENDERMODE_INVISIBLE
    if img < 0.5 * w * h:
        return False
    return textpage.count_chars() < 50 or (txt and inv / txt >= 0.5)


def numeric_dense(text: str) -> bool:
    toks = text.split()
    nums = sum(1 for t in toks if NUMBER.match(t) and any(ch.isdigit() for ch in t))
    return nums >= 40 and nums >= 0.25 * max(1, len(toks))


# ── rows from word boxes ────────────────────────────────────────────────────

def _kind(text: str) -> str:
    """'num' (a figure), 'nil' (a printed dash) or 'text'."""
    if CELL_NIL.match(text):
        return "nil"
    return "num" if CELL_NUM.match(text) and any(c.isdigit() for c in text) else "text"


def _serial(text: str) -> bool:
    return bool(re.fullmatch(r"[(\[]?\d{1,2}[)\].]?|[ivx]{1,4}[).]?", text, re.I))


def rows_from_words(words: list[tuple[float, float, float, float, str]], ocr: bool = False) -> str:
    """words: (x0, top, x1, bottom, text), y growing downwards → one line per table row.

    Lines are grouped by height; in a run of lines with figures (a table) every figure is placed under its column
    (figures are right-aligned, so columns are found from the right edges), a missing cell is left empty, and
    serial numbers ("1", "2", "(iv)") are dropped — so the n-th cell after the label is always the n-th column."""
    if not words:
        return ""
    hs = [w[3] - w[1] for w in words if w[3] > w[1]]
    h = median(hs) if hs else 10.0
    ytol, gap = 0.55 * h, 1.6 * h
    lines: list[list] = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        y = (w[1] + w[3]) / 2
        if lines and abs(y - lines[-1][0]) <= ytol:
            ln = lines[-1]
            ln[1].append(w)
            ln[0] = sum((x[1] + x[3]) / 2 for x in ln[1]) / len(ln[1])
        else:
            lines.append([y, [w]])

    # cells: words closer than the gap belong together
    rows = []
    for _, ws in lines:
        ws.sort(key=lambda w: w[0])
        cells: list[list] = []
        for w in ws:
            t = w[4]
            if ocr:
                t = OCR_DECIMAL_COMMA.sub(r"\1.\2\3", t)
            if cells and w[0] - cells[-1][1] <= gap and _kind(t) == "text" and _kind(cells[-1][2]) == "text":
                cells[-1][1] = w[2]
                cells[-1][2] += " " + t
            else:
                cells.append([w[0], w[2], t])
        rows.append(cells)

    def figures(cells):
        return [c for c in cells if _kind(c[2]) != "text"]

    # tables: runs of lines with at least two figures (a couple of label-only lines may sit inside)
    blocks, cur, idle = [], [], 0
    for i, cells in enumerate(rows):
        if len(figures(cells)) >= 2:
            cur.append(i)
            idle = 0
        elif cur:
            idle += 1
            if idle > 3:
                blocks.append(cur)
                cur, idle = [], 0
    if cur:
        blocks.append(cur)

    placed: dict[int, str] = {}
    for blk in blocks:
        edges = sorted(c[1] for i in blk for c in figures(rows[i]) if not _serial(c[2]))
        groups: list[list[float]] = []
        for x in edges:
            if groups and x - groups[-1][-1] <= 2.0 * h:
                groups[-1].append(x)
            else:
                groups.append([x])
        anchors = [median(g) for g in groups if len(g) >= max(2, 0.15 * len(blk))]
        if len(anchors) < 2:
            continue
        for i in blk:
            cells = rows[i]
            figs = [c for c in figures(cells) if not (_serial(c[2]) and min(abs(c[1] - a) for a in anchors) > 2.0 * h)]
            if len(figs) < 2:
                continue
            slots: list[str | None] = [None] * len(anchors)
            ok = True
            for c in figs:
                j = min(range(len(anchors)), key=lambda k: abs(c[1] - anchors[k]))
                if abs(c[1] - anchors[j]) > 3.0 * h or slots[j] is not None:
                    ok = False
                    break
                slots[j] = c[2]
            first, last = min(c[0] for c in figs), max(c[1] for c in figs)
            inside = [c for c in cells if _kind(c[2]) == "text" and c[0] > first and c[1] < last
                      and not re.fullmatch(r"[%x×]|cr\.?|crores?|lakhs?|mn|bn", c[2], re.I)]
            if not ok or inside:
                continue             # words between the figures: a sentence, not a table row
            label = " ".join(c[2] for c in cells if _kind(c[2]) == "text" and c[1] <= first and not _serial(c[2]))
            tail = " ".join(c[2] for c in cells if _kind(c[2]) == "text" and c[1] > first)
            placed[i] = " | ".join([label] + [s or "" for s in slots]) + (f" | {tail}" if tail else "")

    out = []
    for i, cells in enumerate(rows):
        if i in placed:
            out.append(placed[i])
            continue
        s = ""
        for j, c in enumerate(cells):      # not a table row: words stay a sentence, wide gaps keep a separator
            s += c[2] if j == 0 else (" | " if c[0] - cells[j - 1][1] > gap else " ") + c[2]
        out.append(s)
    return "\n".join(out)


def digital_rows(textpage, page_height: float) -> str:
    """Rows rebuilt from the character boxes of a digital page."""
    words, cur = [], None
    n = textpage.count_chars()
    for i in range(n):
        ch = textpage.get_text_range(i, 1)
        if not ch or ch.isspace() or ch in "￾\x02":
            if cur:
                words.append(cur)
                cur = None
            continue
        l, b, r, t = textpage.get_charbox(i, loose=True)    # advance-width boxes: tight glyph boxes split "13,013"
        top, bot = page_height - t, page_height - b
        if cur and abs((top + bot) / 2 - (cur[1] + cur[3]) / 2) < 0.5 * max(1.0, bot - top) \
                and l - cur[2] < 0.3 * max(1.0, bot - top):
            cur = (cur[0], min(cur[1], top), r, max(cur[3], bot), cur[4] + ch)
        else:
            if cur:
                words.append(cur)
            cur = (l, top, r, bot, ch)
    if cur:
        words.append(cur)
    return rows_from_words(words)


def ocr_rows(png: bytes, exe: str) -> str:
    """tesseract word boxes (automatic layout, so ruled 'Total' rows aren't lost) → rows."""
    env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    res = subprocess.run([exe, "stdin", "stdout", "--psm", "3", "--dpi", str(OCR_DPI), "tsv"], input=png,
                         capture_output=True, env=env, timeout=120)
    words, lines = [], {}
    for r in csv.DictReader(io.StringIO(res.stdout.decode("utf-8", "replace")), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (r.get("text") or "").strip()
        if r.get("level") == "5" and text:
            x, y, w, h = (int(r[k]) for k in ("left", "top", "width", "height"))
            words.append((x, y, x + w, y + h, text))
            lines.setdefault((r["block_num"], r["par_num"], r["line_num"]), []).append((x + w / 2, y + h / 2))
    # a scan tilted by half a degree puts a label and its figures a row apart across the page: straighten it, using
    # the slope of tesseract's own text lines (TRACXN Q2 FY27 was -0.7°)
    slopes = []
    for pts in lines.values():
        xs = [p[0] for p in pts]
        if len(pts) >= 3 and max(xs) - min(xs) >= 400:
            mx, my = sum(xs) / len(xs), sum(p[1] for p in pts) / len(pts)
            den = sum((x - mx) ** 2 for x in xs)
            slopes.append(sum((p[0] - mx) * (p[1] - my) for p in pts) / den)
    slope = median(slopes) if len(slopes) >= 5 else 0.0
    if 0.001 < abs(slope) < 0.05:
        words = [(x0, y0 - slope * (x0 + x1) / 2, x1, y1 - slope * (x0 + x1) / 2, t) for x0, y0, x1, y1, t in words]
    return rows_from_words(words, ocr=True)


# ── a whole document ────────────────────────────────────────────────────────

def page_texts(path, max_pages: int) -> tuple[list[str], list[str]]:
    """(text per page, how each page was read: 'ocr' | 'rows' | 'text')."""
    import pypdfium2 as pdfium

    exe = tesseract_bin()
    pdf = pdfium.PdfDocument(str(path))
    texts: list[str] = []
    how: list[str] = []
    jobs: dict[int, bytes] = {}
    try:
        for i in range(min(len(pdf), max_pages)):
            page = pdf[i]
            try:
                tp = page.get_textpage()
                plain = tp.get_text_range().replace("\r\n", "\n").replace("\r", "\n")
                if exe and is_scanned(page, tp):
                    buf = io.BytesIO()
                    page.render(scale=OCR_DPI / 72, grayscale=True).to_pil().save(buf, format="PNG")
                    jobs[i] = buf.getvalue()
                    texts.append(plain)
                    how.append("ocr")
                elif numeric_dense(plain):
                    texts.append(digital_rows(tp, page.get_size()[1]))
                    how.append("rows")
                else:
                    texts.append(plain)
                    how.append("text")
                tp.close()
            except Exception:
                texts.append("")
                how.append("text")
            finally:
                page.close()
    finally:
        pdf.close()
    if jobs:
        with ThreadPoolExecutor(OCR_WORKERS) as pool:
            done = dict(zip(jobs, pool.map(lambda png: _safe_ocr(png, exe), jobs.values())))
        for i, text in done.items():
            if text is not None and len(text.strip()) >= 20:
                texts[i] = text
            else:
                how[i] = "text"          # OCR failed: keep whatever the scanner embedded
    return texts, how


def _safe_ocr(png: bytes, exe: str) -> str | None:
    try:
        return ocr_rows(png, exe)
    except Exception:
        return None
