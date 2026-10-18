"""
Stock Tracker — single source of truth for portfolio, watchlist and note-driven ideas.

Pulls official exchange data (NSE/BSE bhavcopy, NSE corporate-filing RSS feeds,
NSE bulk/block deals), detects technical milestones (DMA crossovers, golden/death
cross, phase changes) and corporate events (order wins, fund raising, SAST,
pledges, shareholding changes), and raises alerts.

Run `python -m tracker --help` for the CLI and `python -m tracker serve` for the dashboard.
"""
