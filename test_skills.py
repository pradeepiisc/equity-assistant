"""
Skills Test Suite
=================
Tests each skill independently for a given company (default: DCAL).
Validates that each skill either returns a successful result with expected
output keys, or gracefully returns no_data when input files are absent.

Usage:
    python test_skills.py              # tests DCAL
    python test_skills.py QPOWER       # tests a different symbol
"""

import sys
import time
import yaml
from pathlib import Path

from llm.client import get_config
from skills import (
    transcript_analysis,
    shareholding_analysis,
    news_sentiment,
    financial_snapshot,
    peer_comparison,
)

# ─── Expected output keys per skill ──────────────────────────────────────────

SKILL_EXPECTED_KEYS = {
    "transcript_analysis": [
        "executive_summary",
        "management_tone",
        "guidance_vs_delivery",
        "margin_commentary",
        "revenue_growth_narrative",
        "key_risks_flagged_by_management",
        "positive_signals",
        "red_flags",
        "overall_investment_signal",
    ],
    "shareholding_analysis": [
        "promoter_trend",
        "fii_trend",
        "dii_trend",
        "notable_changes",
        "summary",
    ],
    "news_sentiment": [
        "overall_sentiment",
        "key_themes",
        "sector_tailwinds",
        "sector_headwinds",
        "summary",
    ],
    "financial_snapshot": [
        "revenue_trend",
        "margin_trend",
        "debt_situation",
        "cash_flow_quality",
        "key_ratios",
        "summary",
    ],
    "peer_comparison": [
        "relative_valuation",
        "relative_growth",
        "relative_margins",
        "competitive_position",
        "summary",
    ],
}

OPTIONAL_SKILLS = {
    "shareholding_analysis",
    "news_sentiment",
    "financial_snapshot",
    "peer_comparison",
}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _sep(title: str) -> None:
    print(f"\n{'─' * 58}")
    print(f"  {title}")
    print(f"{'─' * 58}")


def _load_company(symbol: str) -> dict:
    watchlist_path = Path(__file__).parent / "watchlist.yaml"
    with open(watchlist_path) as f:
        wl = yaml.safe_load(f)
    company = next(
        (c for c in wl.get("stocks", []) if c.get("symbol", "").upper() == symbol.upper()),
        None,
    )
    if company is None:
        raise ValueError(f"Symbol '{symbol}' not found in watchlist.yaml")
    return company


def _validate_result(skill_name: str, result: dict) -> tuple[bool, str]:
    """
    Returns (passed: bool, message: str).
    - success status → checks all expected output keys are present
    - no_data status → acceptable for optional skills, fail for transcript_analysis
    - error status   → always fail
    """
    status = result.get("status")

    if status == "error":
        return False, f"skill raised error: {result.get('error')}"

    if status == "no_data":
        if skill_name in OPTIONAL_SKILLS:
            return True, "no_data (optional — acceptable, add input files to test fully)"
        else:
            return False, f"no_data on required skill: {result.get('error')}"

    if status == "success":
        data = result.get("data", {})
        if not data:
            return False, "status=success but data is empty"
        expected = SKILL_EXPECTED_KEYS.get(skill_name, [])
        missing = [k for k in expected if k not in data]
        if missing:
            return False, f"missing output keys: {missing}"
        return True, f"all {len(expected)} expected keys present"

    return False, f"unknown status: {status!r}"


# ─── Main Test Runner ─────────────────────────────────────────────────────────

def run_tests(symbol: str) -> None:
    print(f"\n{'=' * 58}")
    print(f"  SKILLS TEST SUITE — {symbol.upper()}")
    print(f"{'=' * 58}")

    company = _load_company(symbol)
    config = get_config()
    print(f"\n  Company : {company.get('name', symbol)}")
    print(f"  Sector  : {company.get('sector', 'N/A')}")

    skills_to_test = [
        ("transcript_analysis",   transcript_analysis),
        ("shareholding_analysis",  shareholding_analysis),
        ("news_sentiment",         news_sentiment),
        ("financial_snapshot",     financial_snapshot),
        ("peer_comparison",        peer_comparison),
    ]

    results = []

    for skill_name, skill_module in skills_to_test:
        _sep(f"SKILL: {skill_name}")
        t0 = time.time()
        try:
            result = skill_module.run(company, config)
            elapsed = time.time() - t0
            passed, msg = _validate_result(skill_name, result)
            tag = "PASS" if passed else "FAIL"
            print(f"\n  [{tag}] status={result['status']}  ({elapsed:.1f}s)")
            print(f"  {msg}")
            results.append((skill_name, passed, result["status"], msg))
        except Exception as exc:
            elapsed = time.time() - t0
            print(f"\n  [FAIL] unhandled exception ({elapsed:.1f}s): {exc}")
            results.append((skill_name, False, "exception", str(exc)))

    # ── Summary ───────────────────────────────────────────────────────────────
    _sep("TEST SUMMARY")
    passed_count = sum(1 for _, p, _, _ in results if p)
    for skill_name, passed, status, msg in results:
        icon = "✓" if passed else "✗"
        print(f"  {icon}  {skill_name:<28}  [{status}]")
    print(f"\n  Result: {passed_count}/{len(results)} passed")
    print()

    sys.exit(0 if passed_count == len(results) else 1)


if __name__ == "__main__":
    target = sys.argv[1].upper() if len(sys.argv) > 1 else "DCAL"
    run_tests(target)
