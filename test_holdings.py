import json
from workflows.portfolio_daily_report import _load_latest_holdings
from pathlib import Path
h, date = _load_latest_holdings(Path("portfolio/ZV3899"))
jsfb = [x for x in h if x['symbol'] == 'JSFB']
print(f"Total: {len(h)}")
print(f"JSFB: {jsfb}")
