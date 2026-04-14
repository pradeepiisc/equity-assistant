#!/bin/bash
# Daily News Digest
# Fetches recent headlines for your top 20 holdings + all watchlist stocks via Google News RSS.
# No API key required. Use daily or evening to stay on top of company news.
# Output: portfolio/ZV3899/{today}/news_digest.md
#
# Usage: bash scripts/run_news_digest.sh [--top 30]

set -e
cd "$(dirname "$0")/.."

/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.news_digest "$@"
