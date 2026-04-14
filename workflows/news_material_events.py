"""
Workflow: News Material Events
==============================
End-to-end workflow for latest portfolio news monitoring:
1) Runs daily news digest fetch (Google News RSS)
2) Filters potentially material headlines using deterministic keyword rules
3) Saves a compact material-events report for quick review

Usage:
    python -m workflows.news_material_events --user ZV3899 --top 200
    python -m workflows.news_material_events --user ZV3899 --skip-fetch --date 2026-03-02
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from workflows import news_digest

PROJECT_ROOT = Path(__file__).parent.parent
PORTFOLIO_DIR = PROJECT_ROOT / "portfolio"


@dataclass
class EventHit:
    symbol: str
    company_name: str
    title: str
    link: str
    source_age: str
    categories: list[str]
    severity: str


CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "Earnings/Results": [
        "results",
        "earnings",
        "q1",
        "q2",
        "q3",
        "q4",
        "board meeting",
        "guidance",
    ],
    "Order/Contract": [
        "order",
        "orders",
        "contract",
        "loi",
        "wins",
        "deal",
        "awarded",
        "secures",
        "order book",
    ],
    "Corporate Action": [
        "dividend",
        "buyback",
        "bonus",
        "split",
        "merger",
        "demerger",
        "acquisition",
        "acquire",
        "stake",
        "bulk deal",
        "block deal",
    ],
    "Regulatory/Legal": [
        "sebi",
        "regulatory",
        "notice",
        "penalty",
        "raid",
        "lawsuit",
        "court",
        "fined",
        "compliance",
    ],
    "Management": [
        "ceo",
        "cfo",
        "resign",
        "resignation",
        "appoint",
        "appointment",
        "director",
    ],
    "Balance Sheet Risk": [
        "debt",
        "default",
        "downgrade",
        "insolvency",
        "bankruptcy",
        "pledge",
    ],
}

HIGH_SEVERITY_TERMS = {
    "default",
    "insolvency",
    "bankruptcy",
    "raid",
    "penalty",
    "lawsuit",
    "fined",
    "sebi",
    "resignation",
    "merger",
    "demerger",
    "acquisition",
    "buyback",
    "bulk deal",
    "block deal",
}

SECTION_RE = re.compile(r"^##\s+(.+?)\s+—\s+(.+)$")
HEADLINE_RE = re.compile(r"^-\s+\[(.+?)\]\((.+?)\)\s+\*(.+?)\*$")


def _find_user_dir(user_id: str | None = None) -> Path:
    dirs = [d for d in PORTFOLIO_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if not dirs:
        raise FileNotFoundError("No portfolio user directories found.")
    if user_id:
        matches = [d for d in dirs if d.name.upper() == user_id.upper()]
        if not matches:
            raise FileNotFoundError(f"User '{user_id}' not found under portfolio/.")
        return matches[0]
    if len(dirs) == 1:
        return dirs[0]
    raise ValueError(f"Multiple users found: {[d.name for d in dirs]}. Pass --user <ID>.")


def _latest_snapshot_date(user_dir: Path) -> str:
    holdings_dir = user_dir / "holdings"
    if not holdings_dir.exists():
        raise FileNotFoundError(f"No holdings directory found under {user_dir}")
    date_dirs = sorted([d for d in holdings_dir.iterdir() if d.is_dir()], key=lambda d: d.name, reverse=True)
    if not date_dirs:
        raise FileNotFoundError(f"No dated holdings snapshots found under {holdings_dir}")
    return date_dirs[0].name


def _contains_term(text: str, term: str) -> bool:
    if " " in term:
        return term in text
    return bool(re.search(rf"\b{re.escape(term)}\b", text))


def _classify_headline(title: str) -> tuple[list[str], str]:
    text = title.lower()
    categories: list[str] = []
    matched_terms: set[str] = set()

    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if _contains_term(text, kw):
                categories.append(category)
                matched_terms.add(kw)
                break

    if not categories:
        return [], ""

    severity = "high" if any(t in HIGH_SEVERITY_TERMS for t in matched_terms) else "medium"
    return categories, severity


def _extract_events(digest_path: Path) -> tuple[list[EventHit], int, int]:
    lines = digest_path.read_text(encoding="utf-8").splitlines()

    current_symbol = ""
    current_name = ""
    all_headlines = 0
    all_symbols: set[str] = set()
    hits: list[EventHit] = []

    for line in lines:
        section_match = SECTION_RE.match(line.strip())
        if section_match:
            current_symbol = section_match.group(1).strip()
            current_name = section_match.group(2).strip()
            all_symbols.add(current_symbol)
            continue

        head_match = HEADLINE_RE.match(line.strip())
        if not head_match:
            continue

        title, link, source_age = head_match.groups()
        all_headlines += 1

        if not current_symbol:
            continue

        categories, severity = _classify_headline(title)
        if not categories:
            continue

        hits.append(
            EventHit(
                symbol=current_symbol,
                company_name=current_name,
                title=title,
                link=link,
                source_age=source_age,
                categories=categories,
                severity=severity,
            )
        )

    return hits, all_headlines, len(all_symbols)


def _build_report(user_id: str, snapshot_date: str, hits: list[EventHit], headline_count: int, symbol_count: int) -> str:
    lines: list[str] = [
        "# Material News Events",
        f"**{user_id}  ·  {snapshot_date}**",
        "",
        f"Scanned **{headline_count}** headlines across **{symbol_count}** stocks from `news_digest.md`.",
        f"Detected **{len(hits)}** potentially material headlines (keyword-based).",
        "",
    ]

    if not hits:
        lines += [
            "No material headlines detected by current keyword rules.",
            "",
            "*Tip: broaden keywords in `workflows/news_material_events.py` if you want a more sensitive scan.*",
        ]
        return "\n".join(lines)

    high_hits = [h for h in hits if h.severity == "high"]
    if high_hits:
        lines += ["## High Priority", ""]
        for h in high_hits:
            cat = ", ".join(h.categories)
            lines.append(f"- **{h.symbol}** [{cat}] [{h.title}]({h.link})  *{h.source_age}*")
        lines.append("")

    by_symbol: dict[str, list[EventHit]] = {}
    for h in hits:
        by_symbol.setdefault(h.symbol, []).append(h)

    lines += ["## By Symbol", ""]
    for symbol in sorted(by_symbol.keys()):
        entries = by_symbol[symbol]
        company_name = entries[0].company_name
        lines.append(f"### {symbol}  —  {company_name} ({len(entries)} event(s))")
        for h in entries:
            cat = ", ".join(h.categories)
            sev = "HIGH" if h.severity == "high" else "MED"
            lines.append(f"- [{sev}] **[{cat}]** [{h.title}]({h.link})  *{h.source_age}*")
        lines.append("")

    lines += [
        "## Notes",
        "- Deterministic filter only (no LLM sentiment in this report).",
        "- Treat this as an alerting layer; manually open links for confirmation/context.",
    ]

    return "\n".join(lines)


def run(
    user_id: str | None = None,
    top_n: int = 200,
    snapshot_date: str | None = None,
    skip_fetch: bool = False,
) -> Path:
    user_dir = _find_user_dir(user_id)

    if skip_fetch:
        snap = snapshot_date or _latest_snapshot_date(user_dir)
        digest_path = user_dir / "holdings" / snap / "news_digest.md"
        if not digest_path.exists():
            raise FileNotFoundError(f"Digest not found: {digest_path}. Run without --skip-fetch first.")
    else:
        digest_path = news_digest.run(user_id=user_dir.name, top_n=top_n)
        if digest_path is None:
            raise RuntimeError("news_digest.run() did not return an output file path.")
        snap = digest_path.parent.name

    hits, headline_count, symbol_count = _extract_events(digest_path)
    report = _build_report(user_dir.name, snap, hits, headline_count, symbol_count)

    out_file = digest_path.parent / "news_material_events.md"
    out_file.write_text(report, encoding="utf-8")

    print(f"\n✓ Material events report saved → {out_file}")
    print(f"  Headlines scanned: {headline_count}")
    print(f"  Material hits: {len(hits)}")

    return out_file


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="End-to-end material news event scan for portfolio holdings")
    parser.add_argument("--user", help="Kite user ID (auto-detected if only one)")
    parser.add_argument("--top", type=int, default=200, help="Top holdings to include when fetching digest")
    parser.add_argument("--date", help="Snapshot date (YYYY-MM-DD). Used with --skip-fetch or for reference")
    parser.add_argument("--skip-fetch", action="store_true", help="Do not fetch news again; analyze existing digest")
    args = parser.parse_args()

    run(user_id=args.user, top_n=args.top, snapshot_date=args.date, skip_fetch=args.skip_fetch)
