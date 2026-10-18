import json

holdings = {}
for pf in ['ZV3899', 'WN5759']:
    with open(f'portfolio/{pf}/holdings/holdings.json', 'r') as f:
        data = json.load(f)
        for item in data:
            sym = item['symbol']
            val = item.get('current_value', 0)
            if sym in holdings:
                holdings[sym]['current_value'] += val
            else:
                holdings[sym] = item

sorted_holdings = sorted(holdings.values(), key=lambda x: x['current_value'], reverse=True)
top100 = [x['symbol'] for x in sorted_holdings[:100]]

print(f"Top 100 symbols: {top100}")
