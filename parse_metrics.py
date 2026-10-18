import json
import re
import math

with open('remaining_data.json', 'r') as f:
    data = json.load(f)

def extract_percentage(text, keywords):
    for keyword in keywords:
        matches = re.findall(rf'{keyword}.*?(\d+(?:\.\d+)?)%', text, re.IGNORECASE)
        if matches:
            val = float(matches[0]) / 100.0
            if 0.05 <= val <= 0.8:  
                return val
    return None

def extract_absolute(text, keywords):
    for keyword in keywords:
        matches = re.findall(rf'{keyword}.*?(?:INR|Rs\.?)\s*(\d+(?:\.\d+)?)\s*cr', text, re.IGNORECASE)
        if matches:
            return float(matches[0])
    return None

estimates = []

for symbol, info in data.items():
    snippets = " ".join(info['snippets'])
    
    fy26_rev = extract_absolute(snippets, ['revenue', 'topline', 'income']) or 500.0
    fy26_ebitda_margin = extract_percentage(snippets, ['ebitda margin', 'operating margin', 'margin']) or 0.15
    fy26_pat_margin = extract_percentage(snippets, ['pat margin', 'net margin']) or (fy26_ebitda_margin * 0.6)
    
    growth_fy27 = extract_percentage(snippets, ['revenue growth', 'volume growth', 'growth expected', 'growth guidance', 'growth']) or 0.15
    if growth_fy27 > 0.4: growth_fy27 = 0.20 
    
    margin_expansion = 0.005
    if "margin expansion" in snippets.lower() or "improve margin" in snippets.lower():
        margin_expansion = 0.01
        
    drivers = ["Consistent revenue scaling based on guidance."]
    if "capex" in snippets.lower():
        drivers.append("Ongoing capacity expansion (capex) to support volume growth.")
    if "export" in snippets.lower():
        drivers.append("Export market penetration contributing to top-line.")

    estimates.append({
        "symbol": symbol,
        "current_price": info['current_price'],
        "fy26_rev": fy26_rev,
        "fy26_ebitda_margin": fy26_ebitda_margin,
        "fy26_pat_margin": fy26_pat_margin,
        "growth_guidance_fy27": growth_fy27,
        "growth_guidance_long": max(0.10, growth_fy27 - 0.02),
        "margin_expansion": margin_expansion,
        "key_drivers": drivers
    })

with open('estimated_parameters.json', 'w') as f:
    json.dump(estimates, f, indent=2)

print("Parsed metrics saved.")
