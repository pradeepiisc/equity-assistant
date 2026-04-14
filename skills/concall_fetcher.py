"""
Skill: Concall Fetcher
=======================
Fetches earnings call transcript PDFs from Screener.in for a company.
Downloads only PDFs not already present in data/companies/{symbol}/transcripts/.
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from llm.client import get_config
from skills.cdp_helper import fetch_page_html as _cdp_fetch

PROJECT_ROOT = Path(__file__).parent.parent

SKILL_NAME = "concall_fetcher"
DOWNLOAD_DELAY_SECONDS = 1
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
CDP_URL = "http://127.0.0.1:9222"

# ── Shared Screener session ───────────────────────────────────────────────
_SCREENER_CLIENT: httpx.Client | None = None


def _get_screener_client() -> httpx.Client:
    global _SCREENER_CLIENT
    if _SCREENER_CLIENT is None:
        c = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-IN,en;q=0.9",
                "Referer": "https://www.screener.in/",
            },
            follow_redirects=True,
            timeout=30,
        )
        try:
            c.get("https://www.screener.in/", timeout=10)  # establish session cookies
        except Exception:
            pass
        _SCREENER_CLIENT = c
    return _SCREENER_CLIENT


def _fetch_page_via_cdp(url: str) -> str | None:
    """Navigate Chrome to URL and return full page HTML. None if CDP unavailable."""
    return _cdp_fetch(url)


def _get_transcripts_dir(symbol: str) -> Path:
    config = get_config()
    data_root = PROJECT_ROOT / config["paths"]["data_root"]
    transcripts_dir = data_root / symbol / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    return transcripts_dir


def _get_ppt_dir(symbol: str) -> Path:
    config = get_config()
    data_root = PROJECT_ROOT / config["paths"]["data_root"]
    ppt_dir = data_root / symbol / "ppt"
    ppt_dir.mkdir(parents=True, exist_ok=True)
    return ppt_dir


def _already_downloaded(transcripts_dir: Path, filename: str) -> bool:
    return (transcripts_dir / filename).exists()


def _clean_label(symbol: str, anchor_text: str) -> str:
    """
    Convert Screener anchor text to a clean filename label.
    Examples:
        "Q3 FY2026" -> "DCAL_Q3FY26.pdf"
        "Q2 FY26"   -> "DCAL_Q2FY26.pdf"
        "Transcript" -> "DCAL_Transcript.pdf"
    """
    import re
    label = anchor_text.strip()
    # Normalise "FY2026" -> "FY26", "FY2025" -> "FY25"
    label = re.sub(r"FY20(\d{2})", r"FY\1", label)
    # Remove spaces
    label = label.replace(" ", "")
    if not label:
        label = "doc"
    return f"{symbol}_{label}.pdf"


def _classify_doc_type(label_text: str, url: str) -> int:
    """
    Return a preference priority for a concall document (lower = more preferred).
      0 = transcript
      1 = AI / auto summary
      2 = investor presentation / PPT
      3 = unknown
    """
    ll = label_text.lower()
    ul = url.lower()
    if "transcript" in ll or "transcript" in ul:
        return 0
    if "summary" in ll or "summary" in ul:
        return 1
    if "ppt" in ll or "presentation" in ll or "ppt" in ul or "presentation" in ul:
        return 2
    return 3


def _extract_quarter_key(label_text: str, nearby_text: str, url: str) -> str:
    """
    Extract a canonical quarter identifier (e.g. 'Q3FY26') from any combination
    of label text, nearby DOM text, and URL path. Falls back to a month-year key,
    then to a URL slug.
    """
    import re as _re
    combined = f"{nearby_text} {label_text} {url}"

    # Q<n>FY<yy|yyyy>  e.g. "Q3 FY2026", "Q3FY26"
    m = _re.search(r"Q([1-4])\s*FY\s*(\d{2,4})", combined, _re.IGNORECASE)
    if m:
        fy = m.group(2)
        if len(fy) == 4:
            fy = fy[2:]          # 2026 → 26
        return f"Q{m.group(1)}FY{fy}".upper()

    # Month+Year  e.g. "Nov 2025", "May2025"
    m = _re.search(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{4})",
        combined,
        _re.IGNORECASE,
    )
    if m:
        return f"{m.group(1).capitalize()}{m.group(2)}"

    # Last resort: a short slug from the URL
    slug = url.rstrip("/").rsplit("/", 1)[-1][:30]
    return slug or url[-20:]


def _fetch_all_concall_docs(screener_url: str, symbol: str) -> dict:
    """
    Scrape the main Screener company page and extract ALL concall PDF links
    from the Concalls subsection, separately bucketed as transcripts and PPTs.

    Returns:
        {
            "transcripts": [ {url, filename, label, quarter_key}, ... ],  # newest first
            "ppts":        [ {url, filename, label, quarter_key}, ... ],  # newest first
        }
    """
    import re as _re

    base_url = screener_url.rstrip("/")

    # Primary: Chrome CDP (real browser, bypasses Screener tarpit)
    html = _fetch_page_via_cdp(base_url)
    if html:
        soup = BeautifulSoup(html, "lxml")
    else:
        # Fallback: shared session with homepage cookie warmup
        client = _get_screener_client()
        try:
            response = client.get(base_url)
            response.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            print(f"[concall_fetcher][{symbol}] Network error: {exc}")
            return {"transcripts": [], "ppts": []}
        soup = BeautifulSoup(response.text, "lxml")

    seen_urls: set[str] = set()

    # ── Locate the Concalls subsection inside #documents ─────────────────────
    docs_section = soup.find(id="documents")
    concalls_root = None

    if docs_section:
        for tag in docs_section.find_all(True):
            text = tag.get_text(strip=True)
            if text in ("Concalls", "Con Calls", "Earnings Call"):
                node = tag
                for _ in range(6):
                    node = node.parent
                    if node is None:
                        break
                    if node.find("a", href=lambda h: h and ".pdf" in h.lower()):
                        concalls_root = node
                        break
                if concalls_root:
                    break

    if concalls_root is None:
        print(f"    [concall_fetcher] 'Concalls' heading not isolated — scanning full #documents")
        concalls_root = docs_section if docs_section else soup

    # ── Collect ALL concall PDFs ──────────────────────────────────────────────
    _MAX_SCAN = 200  # safety cap — scan all available concalls
    raw_transcripts: list[dict] = []
    raw_ppts: list[dict] = []
    seen_transcript_quarters: set[str] = set()
    seen_ppt_quarters: set[str] = set()

    for anchor in concalls_root.find_all("a", href=True):
        if len(raw_transcripts) + len(raw_ppts) >= _MAX_SCAN:
            break
        href: str = anchor["href"]
        if ".pdf" not in href.lower():
            continue
        full_url = href if href.startswith("http") else f"https://www.screener.in{href}"
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        label_text = anchor.get_text(strip=True) or "doc"

        parent_li = anchor.find_parent(["li", "div", "tr"])
        nearby_text = ""
        if parent_li:
            all_text = parent_li.get_text(" ", strip=True)
            nearby_text = all_text.replace(label_text, "").strip()

        quarter_key = _extract_quarter_key(label_text, nearby_text, full_url)
        doc_priority = _classify_doc_type(label_text, full_url)

        entry = {
            "url": full_url,
            "label": label_text,
            "nearby_text": nearby_text,
            "quarter_key": quarter_key,
            "doc_priority": doc_priority,
        }

        # Bucket by type: transcript (0/1) vs PPT (2/3)
        if doc_priority <= 1:  # transcript or AI summary
            if quarter_key not in seen_transcript_quarters:
                seen_transcript_quarters.add(quarter_key)
                raw_transcripts.append(entry)
        elif doc_priority == 2:  # PPT / investor presentation
            if quarter_key not in seen_ppt_quarters:
                seen_ppt_quarters.add(quarter_key)
                raw_ppts.append(entry)
        # doc_priority == 3 (unknown) — skip

    def _build_file_list(entries: list[dict], label_prefix: str) -> list[dict]:
        result = []
        seen_labels: dict[str, int] = {}
        for entry in entries:
            ltext = entry["label"]
            nearby = entry["nearby_text"]
            qk = entry["quarter_key"]
            _type_names = {0: "transcript", 1: "ai-summary", 2: "ppt", 3: "unknown"}
            dtype = _type_names.get(entry["doc_priority"], "unknown")
            print(f"    [concall_fetcher] {label_prefix}/{qk}: {dtype} — '{ltext}'")

            if nearby and nearby != ltext:
                composite = f"{ltext}_{nearby[:20].replace(' ', '')}"
            else:
                composite = ltext
            if composite in seen_labels:
                seen_labels[composite] += 1
                composite = f"{composite}_{seen_labels[composite]:02d}"
            else:
                seen_labels[composite] = 0

            filename = _clean_label(symbol, composite)
            result.append({"url": entry["url"], "filename": filename,
                           "label": ltext, "quarter_key": qk})
        return result

    transcripts = _build_file_list(raw_transcripts, "transcripts")
    ppts = _build_file_list(raw_ppts, "ppt")

    print(
        f"    [concall_fetcher] found {len(transcripts)} transcript(s) "
        f"and {len(ppts)} PPT(s) on Screener"
    )
    return {"transcripts": transcripts, "ppts": ppts}


def _download_pdf(url: str, dest_path: Path) -> None:
    """Download a single PDF with proper headers."""
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        dest_path.write_bytes(response.content)


def run(company: dict, config: dict, limit: int = 999) -> dict:
    """
    Fetch concall PDFs from Screener for a company.
    Downloads up to `limit` transcripts → transcripts/ and up to `limit` PPTs → ppt/.

    Args:
        company: Entry from watchlist/portfolio YAML (must have screener_url)
        config:  Loaded config.yaml dict
        limit:   Max PDFs to download per type (default 999 = all available)

    Returns:
        dict with: skill_name, symbol, status, data, error
        data keys: transcripts_downloaded, transcripts_skipped, ppts_downloaded,
                   ppts_skipped, failed
    """
    symbol = company["symbol"]
    screener_url = company.get("screener_url", "")

    result = {
        "skill_name": SKILL_NAME,
        "symbol": symbol,
        "status": "success",
        "data": {
            "transcripts_downloaded": [], "transcripts_skipped": [],
            "ppts_downloaded": [], "ppts_skipped": [], "failed": [],
        },
        "error": None,
    }

    if not screener_url:
        result["status"] = "error"
        result["error"] = "No screener_url configured for this company."
        print(f"[{SKILL_NAME}][{symbol}] No screener_url — cannot fetch.")
        return result

    try:
        transcripts_dir = _get_transcripts_dir(symbol)
        ppt_dir = _get_ppt_dir(symbol)

        print(f"[{SKILL_NAME}][{symbol}] Fetching concall docs from: {screener_url}")
        docs = _fetch_all_concall_docs(screener_url, symbol)

        transcripts = docs["transcripts"][:limit]
        ppts = docs["ppts"][:limit]

        if not transcripts and not ppts:
            result["status"] = "no_data"
            result["error"] = "No concall PDFs found on Screener."
            print(f"[{SKILL_NAME}][{symbol}] No PDF links found.")
            return result

        # ── Download transcripts ───────────────────────────────────────────
        for pdf in transcripts:
            filename = pdf["filename"]
            dest_path = transcripts_dir / filename
            if _already_downloaded(transcripts_dir, filename):
                print(f"[{SKILL_NAME}][{symbol}] Skip (exists): transcripts/{filename}")
                result["data"]["transcripts_skipped"].append(str(dest_path))
                continue
            try:
                print(f"[{SKILL_NAME}][{symbol}] Downloading transcript: {filename}")
                _download_pdf(pdf["url"], dest_path)
                result["data"]["transcripts_downloaded"].append(str(dest_path))
                time.sleep(DOWNLOAD_DELAY_SECONDS)
            except Exception as exc:
                print(f"[{SKILL_NAME}][{symbol}] Failed {filename}: {exc}")
                result["data"]["failed"].append({"filename": filename, "error": str(exc)})

        # ── Download PPTs ──────────────────────────────────────────────────
        for pdf in ppts:
            filename = pdf["filename"]
            dest_path = ppt_dir / filename
            if _already_downloaded(ppt_dir, filename):
                print(f"[{SKILL_NAME}][{symbol}] Skip (exists): ppt/{filename}")
                result["data"]["ppts_skipped"].append(str(dest_path))
                continue
            try:
                print(f"[{SKILL_NAME}][{symbol}] Downloading PPT: {filename}")
                _download_pdf(pdf["url"], dest_path)
                result["data"]["ppts_downloaded"].append(str(dest_path))
                time.sleep(DOWNLOAD_DELAY_SECONDS)
            except Exception as exc:
                print(f"[{SKILL_NAME}][{symbol}] Failed {filename}: {exc}")
                result["data"]["failed"].append({"filename": filename, "error": str(exc)})

        n_t = len(result["data"]["transcripts_downloaded"])
        n_p = len(result["data"]["ppts_downloaded"])
        n_sk = len(result["data"]["transcripts_skipped"]) + len(result["data"]["ppts_skipped"])
        n_f = len(result["data"]["failed"])
        print(
            f"[{SKILL_NAME}][{symbol}] Done. "
            f"Transcripts={n_t}, PPTs={n_p}, Skipped={n_sk}, Failed={n_f}"
        )

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        print(f"[{SKILL_NAME}][{symbol}] ERROR: {exc}")
        traceback.print_exc()

    return result


if __name__ == "__main__":
    import sys
    import yaml
    from pathlib import Path

    if len(sys.argv) < 2:
        print("Usage: python -m skills.concall_fetcher <SYMBOL>")
        print("Example: python -m skills.concall_fetcher QPOWER")
        sys.exit(1)

    target_symbol = sys.argv[1].upper()
    watchlist_path = Path(__file__).parent.parent / "watchlist.yaml"
    with open(watchlist_path, "r") as f:
        wl_data = yaml.safe_load(f)

    companies = wl_data.get("stocks", [])
    target = next((c for c in companies if c.get("symbol", "").upper() == target_symbol), None)

    if target is None:
        print(f"[ERROR] Symbol '{target_symbol}' not found in watchlist.yaml.")
        sys.exit(1)

    cfg = get_config()
    fetch_result = run(target, cfg)
    print(f"\nResult: {fetch_result['status']}")
    if fetch_result.get("data"):
        d = fetch_result["data"]
        print(f"  Transcripts downloaded: {len(d.get('transcripts_downloaded', []))}")
        print(f"  Transcripts skipped:    {len(d.get('transcripts_skipped', []))}")
        print(f"  PPTs downloaded:        {len(d.get('ppts_downloaded', []))}")
        print(f"  PPTs skipped:           {len(d.get('ppts_skipped', []))}")
        print(f"  Failed:                 {len(d.get('failed', []))}")
