from workflows.shared_sections import EXPLICIT_SMES, is_sme
import json

def report_smes(user_id):
    print(f"--- {user_id} ---")
    with open(f"portfolio/{user_id}/holdings/holdings.json") as f:
        holdings = json.load(f)
        
    sme_val = 0
    main_val = 0
    smes = []
    
    for h in holdings:
        sym = h.get("tradingsymbol", "") or h.get("symbol", "")
        val = h.get("current_value", 0)
        
        if is_sme(sym, h.get("exchange", "")):
            smes.append(sym)
            sme_val += val
        else:
            main_val += val
            
    print(f"SMEs Held: {sorted(smes)}")
    print(f"SME Value: ₹{sme_val:,.0f}")
    print(f"Mainboard Value: ₹{main_val:,.0f}")
    print(f"SME Allocation: {sme_val/(sme_val+main_val)*100:.2f}%")

report_smes("ZV3899")
report_smes("WN5759")

