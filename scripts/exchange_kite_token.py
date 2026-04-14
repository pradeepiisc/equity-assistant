#!/usr/bin/env python3
"""
Helper script to manually exchange Kite request token for access token.
Usage: python scripts/exchange_kite_token.py --user WN5759 --token <request_token>
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(ENV_FILE)


def exchange_token(user_id: str, request_token: str) -> None:
    """Exchange request token for access token and save to .env"""
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        print("ERROR: kiteconnect not installed. Run: pip install kiteconnect")
        sys.exit(1)

    # Get credentials
    user_suffix = f"_{user_id.strip().upper()}" if user_id else ""
    api_key = os.getenv(f"KITE_API_KEY{user_suffix}", "")
    api_secret = os.getenv(f"KITE_API_SECRET{user_suffix}", "")

    if not api_key or not api_secret:
        print(f"\nERROR: KITE_API_KEY{user_suffix} and KITE_API_SECRET{user_suffix} must be set in .env")
        print("Get them from: https://developers.kite.trade/apps\n")
        sys.exit(1)

    print(f"\nExchanging request token for access token...")
    print(f"  API Key: {api_key}")
    print(f"  Request Token: {request_token}\n")

    # Exchange for access token
    kite = KiteConnect(api_key=api_key)
    try:
        data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = data["access_token"]
    except Exception as e:
        print(f"ERROR: Failed to exchange token: {e}")
        sys.exit(1)

    print(f"✓ Logged in as: {data.get('user_name', '')} [{data.get('user_id', '')}]")
    print(f"✓ Access token: {access_token[:30]}...\n")

    # Write to .env
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    env_key = f"KITE_ACCESS_TOKEN{user_suffix}"
    found = False
    new_lines = []
    for line in lines:
        if line.startswith(f"{env_key}=") or line.startswith(f"{env_key} ="):
            new_lines.append(f"{env_key}={access_token}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{env_key}={access_token}")
    ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    print(f"✓ Access token saved to .env as {env_key}\n")
    print("You can now run:")
    user_flag = f" --user {user_id}" if user_id else ""
    print(f"  python -m integrations.kite_connect --save-portfolio{user_flag}")
    print(f"  python -m workflows.portfolio_daily_report{user_flag}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Exchange Kite request token for access token")
    parser.add_argument("--user", help="Kite user ID (e.g., WN5759)")
    parser.add_argument("--token", required=True, help="Request token from Kite redirect URL")
    args = parser.parse_args()

    exchange_token(user_id=args.user, request_token=args.token)
