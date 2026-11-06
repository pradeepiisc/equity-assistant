"""
Tracker settings: code defaults, overridden by `tracker.yaml` at the project root.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
TRACKER_DIR = ROOT / "data" / "tracker"
RAW_DIR = TRACKER_DIR / "raw"
DB_PATH = TRACKER_DIR / "tracker.db"
TICK_LOCK = TRACKER_DIR / "tick.lock"
LAUNCHD_LABEL = "com.equity-assistant.tracker"
NOTES_DIR = ROOT / "notes"
COMPANIES_DIR = ROOT / "data" / "companies"
PORTFOLIO_DIR = ROOT / "portfolio"
WEB_DIR = Path(__file__).resolve().parent / "web"
SETTINGS_PATH = ROOT / "tracker.yaml"

USER_AGENT = "equity-assistant-tracker/0.1 (personal portfolio tracker)"

DEFAULTS: dict[str, Any] = {
    "accounts": ["ZV3899", "WN5759", "CQN676"],
    "technicals": {
        # Phase classification — same rule as workflows/shared_sections.classify_trend
        "phase_fast": 40,
        "phase_slow": 100,
        "neutral_band_pct": 3.0,
        # Lines whose price-crossing raises an event
        "cross_lines": [40, 200],
        # Golden / death cross pair (fast DMA crossing slow DMA)
        "golden_cross": [50, 200],
        # Close must clear the line by this much before a cross counts (stops whipsaw)
        "cross_confirm_pct": 0.5,
        "volume_spike_x": 3.0,
        "volume_spike_min_move_pct": 3.0,
        "history_days": 400,
    },
    "alerts": {
        "notify_macos": True,
        "min_push_severity": "high",       # pushed as macOS notifications
        "min_inbox_severity": "medium",    # shown in the dashboard inbox
        "quiet_hours": ["23:00", "07:00"],
        "max_push_per_run": 4,             # more than this → one summary notification
    },
    "orders": {
        # An order win at/above this share of trailing-12M revenue is flagged high
        "high_pct_of_revenue": 5.0,
        "usd_inr": 88.0,
    },
    "shareholding": {
        "promoter_change_pp": 1.0,
        "institution_change_pp": 1.5,
        "anchor_change_pp": 0.5,           # a named anchor investor moving this much raises an event
    },
    "schedule": {
        "feeds_from": "07:00",
        "feeds_to": "23:30",
        "feeds_every_min": 10,             # all NSE feeds, inside feeds_from–feeds_to
        "market_from": "09:00",            # weekdays in this window: announcements polled faster
        "market_to": "18:00",
        "announcements_every_min": 3,
        "catch_up_every_min": 30,          # NSE date-range APIs: whatever the rolling RSS windows missed
        "catch_up_max_days": 14,           # how far back the first catch-up after a long gap reaches
        "eod_after": "18:15",
        "deals_after": "17:15",
        "server_poll": True,               # `serve` runs a tick at start-up, then every server_every_min
        "server_every_min": 3,
        "launchd_every_min": 3,            # LaunchAgent interval written by scripts/tracker_schedule.sh
    },
    "dashboard": {"port": 7788},
    # ── phase 2: document pipeline ──
    "documents": {
        "enabled": True,
        # NSE announcement types whose attachment is downloaded for tracked stocks
        "kinds": ["results", "concall_transcript", "concall", "order_win", "capacity", "acquisition", "update"],
        "max_seconds_per_tick": 60,        # PDF text extraction + LLM time a single tick may spend
        "backfill": True,                  # also read PDFs already under data/companies/*/ (newest first)
        "backfill_since": "2023-01-01",
    },
    "llm": {
        "enabled": True,
        "client": "client_gpt41_itam",     # a config name from llm/client.py
        "daily_budget_inr": 1000,          # all LLM calls per IST day (≈ 3.9M tokens on gpt-4.1)
        "daily_token_budget": 500000,      # deprecated: only read by a server still running pre-2026-10-20 code
        "backfill_share": 0.6,             # back-fill (old PDFs, notes from old reports) may use at most this share;
                                           # the rest always stays free for new filings
        "max_input_tokens": 14000,         # per document, after keeping only the relevant pages
        "max_output_tokens": 4000,
        "request_timeout_s": 120,          # one LLM call; the SDK retries once
        "usd_per_m_input": 2.0,            # for the cost estimate on the status page
        "usd_per_m_output": 8.0,
        "local_model": "qwen3.8:27b",      # ollama model on this Mac (localllm.py): free; company profiles, policy matching
        "local_url": "http://127.0.0.1:11434",
        "local_timeout_s": 600,
    },
    # ── the evening priority list (priority.py): held and watched stocks with new filings, best first ──
    "priority": {
        "days": 7,                         # filings from the last 7 days
        "client": "client_gpt52_azure",    # the grader
        "batch": 12,                       # companies per call
        "workers": 4,
    },
    # ── the local-model comparison week (llm_eval.py): every filing also read by the local models in the evening,
    #    shown beside gpt-4.1's read and graded by another model; then choose one ──
    "llm_eval": {
        "from": "2026-11-05",              # filings from this day…
        "until": "2026-11-13",             # …to this one (7 weekdays); then eval-final decides once
        "window": ["17:00", "21:00"],      # local reads only inside this window (fans)
        "models": ["gemma4:26b-a4b", "qwen3.8:27b"],
        "kinds": ["results", "press_release", "presentation", "transcript"],
        "judge_client": "client_gpt52_azure",
        "weekdays_needed": 7,
    },
    "valuation": {
        "auto": True,                      # rewrite a memo when the company's numbers change (2 per tick)
        "max_age_days": 120,
        "fallback_client": "client_gpt52_azure",   # when gpt-4.1's memo is broken JSON twice (repetition loop)
    },
    "notes": {
        "backfill": True,                  # condense the existing reports into quarterly notes (once per company)
    },
    # ── phase 2: BSE-only stocks via Screener (Chrome debug session only; gentle) ──
    "screener_fallback": {
        "enabled": True,
        "daily_pages": 300,                # Screener page loads a day, at most
        "per_tick": 2,                     # companies per 3-minute tick
        "check_hours": {"meeting": 0.75, "season": 3, "quiet": 12},   # held / watched BSE-only companies
        "launch_chrome": True,             # start the dedicated debug profile when it isn't running
        "min_gap_s": 9,                    # seconds between any two Screener requests
        "window": ["08:00", "22:00"],      # only inside this window
        "stale_days": 30,                  # refresh a company not fetched for this long
        "results_refresh_days": 7,         # in results season, re-check weekly until the quarter appears
    },
    # ── phase 2: Add / Hold / Trim / Exit / Watch (rule-based; no LLM) ──
    "score": {
        "weights": {                       # each component scores −2…+2, multiplied by its weight
            "order_book_growth": 1.0, "book_to_revenue": 0.75, "guidance_delivery": 1.0, "margin_trend": 0.75,
            "eps_vs_pe": 1.0, "technical": 1.0, "promoter_flow": 1.0, "institution_flow": 0.5,
            "anchor_flow": 0.5, "pledge": 1.0, "valuation": 0.75,
        },
        "add_at": 4.0,                     # held: ≥ add_at → Add, > trim_at → Hold, > exit_at → Trim, else Exit
        "trim_at": -1.5,
        "exit_at": -4.0,
        "watch_add_at": 3.0,               # not held: ≥ this → Add (start a position), else Watch
        "min_components": 4,               # fewer components with data → Hold / Watch ("not enough data")
        "default_execution_months": 24,    # order-book execution period when the company doesn't state one
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if v is None:            # a section left with only commented-out lines
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings() -> dict[str, Any]:
    user: dict = {}
    if SETTINGS_PATH.exists():
        user = yaml.safe_load(SETTINGS_PATH.read_text()) or {}
    return _merge(DEFAULTS, user)


SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}
