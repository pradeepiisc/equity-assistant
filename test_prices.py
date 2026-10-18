from workflows.portfolio_daily_report import _load_latest_holdings, _load_multiday_prices
from pathlib import Path
user_dir = Path("portfolio/ZV3899")
h, date = _load_latest_holdings(user_dir)
prices = _load_multiday_prices(user_dir, date)
import pprint
print("Prices loaded for:", date)
# Look at MAHALIFE and JSFB across dates
dates = sorted(prices.keys())
for d in dates[-5:]:
    print(f"{d} MAHALIFE: {prices[d].get('MAHALIFE')} JSFB: {prices[d].get('JSFB')}")
