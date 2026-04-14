"""
Integration: Zerodha Kite Connect
===================================
Fetches your portfolio holdings and positions from Zerodha Kite.

https://kite.zerodha.com/connect/login?api_key=<your_api_key>&v=3


Authentication flow (required once per day):
  1. Run: python -m integrations.kite_connect --login
  2. Browser opens Kite login page
  3. After login, Kite redirects to your redirect URL with ?request_token=XXX
  4. Paste the full redirect URL (or just the request_token) when prompted
  5. Access token is saved to .env as KITE_ACCESS_TOKEN

Then run normally:
  python -m integrations.kite_connect --holdings
  python -m integrations.kite_connect --positions
  python -m integrations.kite_connect --summary

Environment variables needed in .env:
  KITE_API_KEY=your_api_key
  KITE_API_SECRET=your_api_secret
  KITE_ACCESS_TOKEN=your_daily_access_token  (set after login)

For multiple Kite accounts, add suffixes per user ID, e.g.:
  KITE_API_KEY_WN5759, KITE_API_SECRET_WN5759, KITE_ACCESS_TOKEN_WN5759
and pass --user WN5759 to the CLI/workflows.
"""

from __future__ import annotations

import json
import os
import sys
import webbrowser
from pathlib import Path

from dotenv import load_dotenv, set_key

PROJECT_ROOT = Path(__file__).parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(ENV_FILE)


def _normalize_user_id(user_id: str | None) -> str | None:
    if not user_id:
        return None
    return user_id.strip().upper()


def _env_key(base: str, user_id: str | None = None) -> str:
    suffix = f"_{_normalize_user_id(user_id)}" if user_id else ""
    return f"{base}{suffix}"


def _env_value(base: str, user_id: str | None = None) -> str:
    key = _env_key(base, user_id)
    value = os.getenv(key, "")
    if value:
        return value
    # If user_id was specified but user-specific key doesn't exist, FAIL
    # Never fall back to default credentials - that would mix user data
    if user_id:
        raise RuntimeError(
            f"{key} not found in .env\n"
            f"When --user {user_id} is specified, you must set user-specific credentials.\n"
            f"Add to .env: {key}=your_value"
        )
    # Only use default keys if no user_id was specified
    return os.getenv(base, "")


def _get_kite_client(user_id: str | None = None):
    """Return an authenticated KiteConnect client. Raises if not configured."""
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        raise RuntimeError(
            "kiteconnect not installed. Run: pip install kiteconnect"
        )

    api_key = _env_value("KITE_API_KEY", user_id)
    access_token = _env_value("KITE_ACCESS_TOKEN", user_id)

    if not api_key:
        suffix = f"_{_normalize_user_id(user_id)}" if user_id else ""
        raise RuntimeError(
            "KITE_API_KEY{suffix} not set in .env\n"
            "Get your API key from: https://developers.kite.trade/apps".format(suffix=suffix)
        )
    if not access_token:
        suffix = f"_{_normalize_user_id(user_id)}" if user_id else ""
        raise RuntimeError(
            "KITE_ACCESS_TOKEN{suffix} not set in .env\n"
            "Run: python -m integrations.kite_connect --login{user_hint}".format(
                suffix=suffix, user_hint=f" --user {user_id}" if user_id else ""
            )
        )

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


_LOGIN_PORT = 8765  # local port to catch Kite redirect; set redirect URL to http://127.0.0.1:8765/


def _catch_request_token_via_server(port: int = _LOGIN_PORT, timeout: int = 120) -> str | None:
    """
    Start a one-shot HTTP server on localhost:{port} that captures the
    Kite redirect URL containing request_token.  Returns the token or None.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs

    captured = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = parse_qs(urlparse(self.path).query)
            tokens = qs.get("request_token", [])
            if tokens:
                captured["token"] = tokens[0]
                body = (
                    b"<html><body><h2 style='font-family:sans-serif;color:green'>"
                    b"Login successful! You can close this tab.</h2></body></html>"
                )
            else:
                body = b"<html><body>Waiting...</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass  # silence server logs

    try:
        server = HTTPServer(("127.0.0.1", port), _Handler)
        server.timeout = 1
    except OSError:
        return None  # port already in use — fall back to manual

    def _serve():
        deadline = __import__("time").time() + timeout
        while not captured and __import__("time").time() < deadline:
            server.handle_request()
        server.server_close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    t.join(timeout + 2)
    return captured.get("token")


def login(user_id: str | None = None) -> str:
    """
    Interactive login flow. Opens browser, auto-catches request_token via
    a local HTTP server, exchanges it for access_token and saves to .env.

    For auto-catch to work, set your Kite app's redirect URL to:
        http://127.0.0.1:8765/

    Falls back to manual paste if the port is unavailable.
    Returns the access_token string.
    """
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        raise RuntimeError("kiteconnect not installed. Run: pip install kiteconnect")

    api_key = _env_value("KITE_API_KEY", user_id)
    api_secret = _env_value("KITE_API_SECRET", user_id)

    if not api_key or not api_secret:
        print(
            "\n[ERROR] KITE_API_KEY and KITE_API_SECRET (optionally suffixed per user) must be set in .env\n"
            "Get them from: https://developers.kite.trade/apps\n"
        )
        sys.exit(1)

    kite = KiteConnect(api_key=api_key)
    login_url = kite.login_url()

    print(f"\nOpening Kite login in browser...")
    print(f"  {login_url}\n")
    print(f"  Auto-catch server listening on http://127.0.0.1:{_LOGIN_PORT}/")
    print(f"  (Set your Kite app redirect URL to http://127.0.0.1:{_LOGIN_PORT}/ for auto mode)\n")
    webbrowser.open(login_url)

    # Try auto-catch first
    import threading
    request_token = None
    spinner_stop = threading.Event()

    def _spin():
        import itertools, time
        for c in itertools.cycle("|/-\\"):
            if spinner_stop.is_set():
                break
            print(f"\r  Waiting for browser login... {c}", end="", flush=True)
            time.sleep(0.2)
        print("\r" + " " * 40 + "\r", end="", flush=True)

    spin_t = threading.Thread(target=_spin, daemon=True)
    spin_t.start()

    request_token = _catch_request_token_via_server(_LOGIN_PORT)
    spinner_stop.set()
    spin_t.join()

    if not request_token:
        # fallback: manual paste
        print(
            "  Auto-catch did not receive token (redirect URL may not be set to "
            f"http://127.0.0.1:{_LOGIN_PORT}/).\n"
        )
        raw = input(
            "  After login, paste the full redirect URL (or just the request_token): "
        ).strip()
        if "request_token=" in raw:
            request_token = raw.split("request_token=")[1].split("&")[0]
        else:
            request_token = raw

    print(f"  Token captured. Exchanging for access_token...")
    data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = data["access_token"]

    # Write directly to .env (set_key can silently fail on some dotenv versions)
    _write_env_key("KITE_ACCESS_TOKEN", access_token, user_id=user_id)
    print(f"\n✓ Logged in as: {data.get('user_name', '')}  [{data.get('user_id', '')}]")
    print(f"✓ Access token saved to .env")
    print(f"\nRun: python -m integrations.kite_connect --save-portfolio\n")
    return access_token


def _write_env_key(key: str, value: str, user_id: str | None = None) -> None:
    """Reliably write/update a key in the .env file."""
    env_path = ENV_FILE
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    found = False
    new_lines = []
    env_key = _env_key(key, user_id)
    for line in lines:
        if line.startswith(f"{env_key}=") or line.startswith(f"{env_key} ="):
            new_lines.append(f"{env_key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{env_key}={value}")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def get_holdings(user_id: str | None = None) -> list[dict]:
    """
    Fetch all equity holdings from Kite.
    Returns a list of dicts with symbol, quantity, avg_price, last_price, pnl, etc.
    """
    kite = _get_kite_client(user_id=user_id)
    raw = kite.holdings()

    holdings = []
    for h in raw:
        holdings.append({
            "symbol": h.get("tradingsymbol", ""),
            "exchange": h.get("exchange", "NSE"),
            "quantity": h.get("quantity", 0),
            "avg_price": round(h.get("average_price", 0), 2),
            "last_price": round(h.get("last_price", 0), 2),
            "pnl": round(h.get("pnl", 0), 2),
            "pnl_pct": round(
                ((h.get("last_price", 0) - h.get("average_price", 1)) / h.get("average_price", 1)) * 100, 2
            ),
            "current_value": round(h.get("quantity", 0) * h.get("last_price", 0), 2),
            "invested_value": round(h.get("quantity", 0) * h.get("average_price", 0), 2),
            "isin": h.get("isin", ""),
        })

    return sorted(holdings, key=lambda x: x["current_value"], reverse=True)


def get_positions(user_id: str | None = None) -> dict:
    """
    Fetch current day and net positions.
    Returns dict with 'day' and 'net' lists.
    """
    kite = _get_kite_client(user_id=user_id)
    raw = kite.positions()

    def _clean(pos_list):
        out = []
        for p in pos_list:
            out.append({
                "symbol": p.get("tradingsymbol", ""),
                "exchange": p.get("exchange", "NSE"),
                "quantity": p.get("quantity", 0),
                "buy_price": round(p.get("buy_price", 0), 2),
                "sell_price": round(p.get("sell_price", 0), 2),
                "pnl": round(p.get("pnl", 0), 2),
                "product": p.get("product", ""),
            })
        return out

    return {
        "day": _clean(raw.get("day", [])),
        "net": _clean(raw.get("net", [])),
    }


def get_user_profile(user_id: str | None = None) -> dict:
    """
    Fetch the logged-in user's profile (includes user_id, user_name, etc.).
    """
    kite = _get_kite_client(user_id=user_id)
    return kite.profile()


def save_portfolio(
    folder_name: str | None = None,
    date_str: str | None = None,
    user_id: str | None = None,
) -> Path:
    """
    Fetch holdings and positions and save to:
        portfolio/{folder_name}/{YYYY-MM-DD}/holdings.json
        portfolio/{folder_name}/{YYYY-MM-DD}/holdings.csv
        portfolio/{folder_name}/{YYYY-MM-DD}/positions.json
        portfolio/{folder_name}/{YYYY-MM-DD}/summary.txt

    folder_name defaults to the Kite user_id (e.g. ZU1234).
    date_str defaults to today (YYYY-MM-DD).
    """
    import csv

    if not date_str:
        from datetime import date
        date_str = date.today().isoformat()

    if not folder_name:
        try:
            profile = get_user_profile(user_id=user_id)
            folder_name = profile.get("user_id", "pradeep")
        except Exception:
            folder_name = "pradeep"
    if user_id and not folder_name:
        folder_name = _normalize_user_id(user_id)

    save_dir = PROJECT_ROOT / "portfolio" / folder_name / "holdings" / date_str
    save_dir.mkdir(parents=True, exist_ok=True)

    holdings = get_holdings(user_id=user_id)
    positions = get_positions(user_id=user_id)
    summary = get_portfolio_summary(user_id=user_id)

    # JSON
    (save_dir / "holdings.json").write_text(
        json.dumps(holdings, indent=2, default=str), encoding="utf-8"
    )
    (save_dir / "positions.json").write_text(
        json.dumps(positions, indent=2, default=str), encoding="utf-8"
    )

    # CSV
    if holdings:
        csv_path = save_dir / "holdings.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=holdings[0].keys())
            writer.writeheader()
            writer.writerows(holdings)

    # Summary txt
    lines = [
        f"Portfolio Summary — {folder_name} — {date_str}",
        "=" * 55,
        f"Invested : ₹{summary['total_invested']:>12,.0f}",
        f"Current  : ₹{summary['total_current_value']:>12,.0f}",
        f"P&L      : {'+' if summary['total_pnl'] >= 0 else ''}₹{summary['total_pnl']:,.0f}  "
        f"({'+' if summary['total_pnl_pct'] >= 0 else ''}{summary['total_pnl_pct']:.1f}%)",
        f"Holdings : {summary['num_holdings']}",
        "",
    ]
    if summary["watchlist_stocks_held"]:
        lines.append("Watchlist stocks held:")
        for h in summary["watchlist_stocks_held"]:
            lines.append(
                f"  {h['symbol']:<10} qty={h['quantity']:<6} "
                f"avg=₹{h['avg_price']:.1f}  ltp=₹{h['last_price']:.1f}  "
                f"pnl={'+' if h['pnl'] >= 0 else ''}₹{h['pnl']:,.0f}"
            )
    (save_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")

    print(f"\n✓ Portfolio saved → {save_dir}")
    for f in sorted(save_dir.iterdir()):
        print(f"  {f.name}  ({f.stat().st_size:,} bytes)")
    return save_dir


def get_portfolio_summary(user_id: str | None = None) -> dict:
    """
    Returns a high-level summary of the portfolio:
    total invested, current value, total P&L, top holdings,
    and which watchlist stocks are held.
    """
    import yaml

    holdings = get_holdings(user_id=user_id)

    total_invested = sum(h["invested_value"] for h in holdings)
    total_current = sum(h["current_value"] for h in holdings)
    total_pnl = total_current - total_invested
    total_pnl_pct = (total_pnl / total_invested * 100) if total_invested else 0

    # Cross-reference with watchlist
    watchlist_path = PROJECT_ROOT / "watchlist.yaml"
    watchlist_symbols = set()
    if watchlist_path.exists():
        with open(watchlist_path) as f:
            wl = yaml.safe_load(f)
        watchlist_symbols = {s["symbol"].upper() for s in wl.get("stocks", [])}

    held_watchlist = [
        h for h in holdings if h["symbol"].upper() in watchlist_symbols
    ]

    return {
        "total_invested": round(total_invested, 2),
        "total_current_value": round(total_current, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "num_holdings": len(holdings),
        "top_5_by_value": holdings[:5],
        "watchlist_stocks_held": held_watchlist,
        "all_holdings": holdings,
    }


def print_summary(summary: dict) -> None:
    print(f"\n{'='*55}")
    print(f"  PORTFOLIO SUMMARY  (Zerodha Kite)")
    print(f"{'='*55}")
    print(f"  Invested : ₹{summary['total_invested']:>12,.0f}")
    print(f"  Current  : ₹{summary['total_current_value']:>12,.0f}")
    pnl_sign = "+" if summary["total_pnl"] >= 0 else ""
    print(f"  P&L      : {pnl_sign}₹{summary['total_pnl']:,.0f}  ({pnl_sign}{summary['total_pnl_pct']:.1f}%)")
    print(f"  Holdings : {summary['num_holdings']}")

    if summary["watchlist_stocks_held"]:
        print(f"\n  {'─'*50}")
        print(f"  Watchlist stocks in portfolio:")
        for h in summary["watchlist_stocks_held"]:
            pnl_sign = "+" if h["pnl"] >= 0 else ""
            print(f"    {h['symbol']:<10} qty={h['quantity']:<6} "
                  f"avg=₹{h['avg_price']:.1f}  ltp=₹{h['last_price']:.1f}  "
                  f"pnl={pnl_sign}₹{h['pnl']:,.0f} ({pnl_sign}{h['pnl_pct']:.1f}%)")

    if summary["top_5_by_value"]:
        print(f"\n  {'─'*50}")
        print(f"  Top holdings by value:")
        for h in summary["top_5_by_value"]:
            print(f"    {h['symbol']:<12} ₹{h['current_value']:>10,.0f}  ({h['pnl_pct']:+.1f}%)")
    print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Zerodha Kite portfolio integration")
    parser.add_argument("--user", help="Kite user ID (for multi-account support, e.g., WN5759)")
    parser.add_argument("--login", action="store_true", help="Run daily login flow")
    parser.add_argument("--holdings", action="store_true", help="Print all holdings")
    parser.add_argument("--positions", action="store_true", help="Print today's positions")
    parser.add_argument("--summary", action="store_true", help="Print portfolio summary")
    parser.add_argument("--save-portfolio", action="store_true", help="Save holdings+positions to portfolio/{user_id}/{date}/")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    if args.login:
        login(user_id=args.user)

    elif args.holdings:
        data = get_holdings(user_id=args.user)
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(f"\n{'Symbol':<12} {'Qty':<6} {'Avg':>8} {'LTP':>8} {'P&L':>10} {'%':>7}")
            print("─" * 55)
            for h in data:
                sign = "+" if h["pnl"] >= 0 else ""
                print(f"{h['symbol']:<12} {h['quantity']:<6} {h['avg_price']:>8.1f} "
                      f"{h['last_price']:>8.1f} {sign}₹{h['pnl']:>8,.0f} {sign}{h['pnl_pct']:>6.1f}%")

    elif args.positions:
        data = get_positions(user_id=args.user)
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(f"\nDay positions: {len(data['day'])}")
            print(f"Net positions: {len(data['net'])}")

    elif args.summary:
        summary = get_portfolio_summary(user_id=args.user)
        if args.json:
            print(json.dumps(summary, indent=2, default=str))
        else:
            print_summary(summary)

    elif getattr(args, "save_portfolio", False):
        save_portfolio(user_id=args.user)

    else:
        parser.print_help()
