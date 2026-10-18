import json
import os

with open('estimated_parameters.json', 'r') as f:
    companies = json.load(f)

md_lines = []

for c in companies:
    md_lines.append(f"## {c['symbol']}")
    
    rev27 = c['fy26_rev'] * (1 + c['growth_guidance_fy27'])
    margin27 = c['fy26_ebitda_margin'] + c['margin_expansion']
    pat_margin27 = c['fy26_pat_margin'] + (c['margin_expansion'] * 0.5)
    pat27 = rev27 * pat_margin27
    
    # Safe division for price target
    if c['fy26_rev'] > 0 and c['fy26_pat_margin'] > 0:
        base_pat = c['fy26_rev'] * c['fy26_pat_margin']
        price27 = c['current_price'] * (pat27 / base_pat)
    else:
        price27 = c['current_price'] * (1 + c['growth_guidance_fy27'])
    
    rev28 = rev27 * (1 + c['growth_guidance_long'])
    margin28 = margin27 + c['margin_expansion']
    pat_margin28 = pat_margin27 + (c['margin_expansion'] * 0.5)
    pat28 = rev28 * pat_margin28
    
    if c['fy26_rev'] > 0 and c['fy26_pat_margin'] > 0:
        price28 = c['current_price'] * (pat28 / base_pat)
    else:
        price28 = price27 * (1 + c['growth_guidance_long'])
    
    rev29 = rev28 * (1 + c['growth_guidance_long'])
    margin29 = margin28 
    pat_margin29 = pat_margin28
    pat29 = rev29 * pat_margin29
    
    if c['fy26_rev'] > 0 and c['fy26_pat_margin'] > 0:
        price29 = c['current_price'] * (pat29 / base_pat)
    else:
        price29 = price28 * (1 + c['growth_guidance_long'])
    
    md_lines.append(f"**Key Growth Drivers:**")
    for driver in c['key_drivers']:
        md_lines.append(f"- {driver}")

    md_lines.append("\n**Financial Estimates (₹ Crores):**")
    md_lines.append("| Metric | FY26 (Base) | FY27E | FY28E | FY29E |")
    md_lines.append("|---|---|---|---|---|")
    md_lines.append(f"| **Revenue** | {c['fy26_rev']:.1f} | {rev27:.1f} | {rev28:.1f} | {rev29:.1f} |")
    md_lines.append(f"| **EBITDA Margin** | {c['fy26_ebitda_margin']*100:.1f}% | {margin27*100:.1f}% | {margin28*100:.1f}% | {margin29*100:.1f}% |")
    md_lines.append(f"| **PAT Margin** | {c['fy26_pat_margin']*100:.1f}% | {pat_margin27*100:.1f}% | {pat_margin28*100:.1f}% | {pat_margin29*100:.1f}% |")
    md_lines.append(f"| **Est. PAT** | {(c['fy26_rev']*c['fy26_pat_margin']):.1f} | {pat27:.1f} | {pat28:.1f} | {pat29:.1f} |")
    
    md_lines.append("\n**Expected Stock Price Target Range (assuming constant P/E valuation multiples):**")
    md_lines.append(f"- **Current Price:** ₹{c['current_price']:.1f}")
    md_lines.append(f"- **FY27 Target:** ₹{price27*0.9:.0f} - ₹{price27*1.1:.0f}")
    md_lines.append(f"- **FY28 Target:** ₹{price28*0.9:.0f} - ₹{price28*1.1:.0f}")
    md_lines.append(f"- **FY29 Target:** ₹{price29*0.9:.0f} - ₹{price29*1.1:.0f}")
    md_lines.append("\n---\n")

with open('portfolio/forward_exit_analysis.md', 'a') as f:
    f.write("\n".join(md_lines))

print("Appended remaining companies to forward_exit_analysis.md")
