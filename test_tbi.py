import json

def get_tbi_details(user_id):
    try:
        with open(f"portfolio/{user_id}/holdings/holdings.json") as f:
            holdings = json.load(f)
            for h in holdings:
                sym = h.get("tradingsymbol", "") or h.get("symbol", "")
                if sym.replace("-SM", "").replace("-BE", "").replace("-SME", "") == "TBI":
                    val = h.get("current_value", 0)
                    inv = h.get("invested_value", 0)
                    qty = h.get("quantity", 0)
                    ltp = h.get("last_price", 0)
                    pnl = h.get("pnl", 0)
                    alloc = val / sum(x.get("current_value", 0) for x in holdings) * 100
                    
                    print(f"[{user_id}] TBI: Qty {qty} | LTP: ₹{ltp} | Inv: ₹{inv:,.0f} | Val: ₹{val:,.0f} | P&L: ₹{pnl:,.0f} | Alloc: {alloc:.2f}%")
    except Exception as e:
        print(f"[{user_id}] TBI: Not held or data missing")

get_tbi_details("ZV3899")
get_tbi_details("WN5759")
# Let's see if there's a third account mentioned, like "across 3 accounts"
