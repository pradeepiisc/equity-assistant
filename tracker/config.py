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
    "accounts": ["ZV3899", "WN5759"],
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
        "daily_token_budget": 500000,      # prompt + completion tokens per IST day, all calls
        "backfill_share": 0.6,             # back-fill may use at most this share; the rest stays for new filings
        "max_input_tokens": 12000,         # per document, after keeping only the relevant pages
        "max_output_tokens": 3000,
        "usd_per_m_input": 2.0,            # for the cost estimate on the status page
        "usd_per_m_output": 8.0,
    },
    # ── phase 2: BSE-only stocks via Screener (Chrome debug session only; gentle) ──
    "screener_fallback": {
        "enabled": True,
        "daily_cap": 30,                   # companies a day, at most
        "min_gap_s": 9,                    # seconds between any two Screener requests
        "window": ["08:00", "22:00"],      # spread across this window: one company per (window ÷ cap)
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
