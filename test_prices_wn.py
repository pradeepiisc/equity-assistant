from workflows.portfolio_daily_report import _load_latest_holdings, _load_multiday_prices
from pathlib import Path
user_dir = Path("portfolio/WN5759")
h, date = _load_latest_holdings(user_dir)
prices = _load_multiday_prices(user_dir, date)
dates = sorted(prices.keys())
for d in dates[-5:]:
    print(f"{d} TRUECOLORS: {prices[d].get('TRUECOLORS')}")
