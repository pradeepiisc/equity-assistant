import json
import re

try:
    with open('top5_data.json', 'r') as f:
        data = json.load(f)
except:
    data = {}

companies = [
    {
        "symbol": "AEROFLEX",
        "current_price": 458.95,
        "fy26_rev": 443.3,
        "fy26_ebitda_margin": 0.226,
        "fy26_pat_margin": 0.125,
        "growth_guidance_fy27": 0.25, 
        "growth_guidance_long": 0.20,
        "margin_expansion": 0.005 
    },
    {
        "symbol": "SYRMA",
        "current_price": 1394.9,
        "fy26_rev": 4840,
        "fy26_ebitda_margin": 0.12,
        "fy26_pat_margin": 0.06,
        "growth_guidance_fy27": 0.32, 
        "growth_guidance_long": 0.30,
        "margin_expansion": -0.01 
    },
    {
        "symbol": "YATHARTH",
        "current_price": 842.55,
        "fy26_rev": 2100, 
        "fy26_ebitda_margin": 0.25,
        "fy26_pat_margin": 0.12,
        "growth_guidance_fy27": 0.20,
        "growth_guidance_long": 0.18,
        "margin_expansion": 0.01 
    },
    {
        "symbol": "SHREEREF",
        "current_price": 348.9,
        "fy26_rev": 300, 
        "fy26_ebitda_margin": 0.22,
        "fy26_pat_margin": 0.11,
        "growth_guidance_fy27": 0.40,
        "growth_guidance_long": 0.30,
        "margin_expansion": 0.02
    },
    {
        "symbol": "YASHO",
        "current_price": 2867.4,
        "fy26_rev": 830, 
        "fy26_ebitda_margin": 0.174,
        "fy26_pat_margin": 0.09,
        "growth_guidance_fy27": 0.35, 
        "growth_guidance_long": 0.25, 
        "margin_expansion": 0.01
    }
]

md_lines = []
md_lines.append("# Exit Strategy & Forward Projections (FY27 - FY29)")
md_lines.append("Based on Q4 FY26 / Mar 2026 Earnings Transcripts and Guidelines\n")

for c in companies:
    md_lines.append(f"## {c['symbol']}")
    
    rev27 = c['fy26_rev'] * (1 + c['growth_guidance_fy27'])
    margin27 = c['fy26_ebitda_margin'] + c['margin_expansion']
    pat_margin27 = c['fy26_pat_margin'] + (c['margin_expansion'] * 0.5)
    pat27 = rev27 * pat_margin27
    price27 = c['current_price'] * (pat27 / (c['fy26_rev'] * c['fy26_pat_margin']))
    
    if c['symbol'] == 'YASHO':
        rev28 = 1500.0
    else:
        rev28 = rev27 * (1 + c['growth_guidance_long'])
        
    margin28 = margin27 + c['margin_expansion']
    pat_margin28 = pat_margin27 + (c['margin_expansion'] * 0.5)
    pat28 = rev28 * pat_margin28
    price28 = c['current_price'] * (pat28 / (c['fy26_rev'] * c['fy26_pat_margin']))
    
    rev29 = rev28 * (1 + c['growth_guidance_long'])
    margin29 = margin28 
    pat_margin29 = pat_margin28
    pat29 = rev29 * pat_margin29
    price29 = c['current_price'] * (pat29 / (c['fy26_rev'] * c['fy26_pat_margin']))
    
    md_lines.append(f"**Key Growth Drivers:**")
    if c['symbol'] == 'AEROFLEX':
        md_lines.append("- Expanding Skid Assemblies capacity from 6,000 to 15,000 units.")
        md_lines.append("- Peak utilization expected at 75-80%. Targeting 20-25% sustainable growth.")
    elif c['symbol'] == 'SYRMA':
        md_lines.append("- ₹800 cr multi-year capex for PCB lines.")
        md_lines.append("- Blended growth guidance of 30-35%. MedTech segment to cross ₹500 cr.")
        md_lines.append("- Margin guidance cautious at 10.5-11% for FY27 due to external turmoil.")
    elif c['symbol'] == 'YATHARTH':
        md_lines.append("- New hospitals generating double-digit EBITDA and ~11% of Q4 revenue.")
        md_lines.append("- Expanded to 2550+ beds with strong ARPOB (₹33,744).")
    elif c['symbol'] == 'SHREEREF':
        md_lines.append("- 4 out of 8 new projects to complete in FY27. Capacity building underway.")
        md_lines.append("- Order book of ₹327 cr ensures strong near-term execution.")
    elif c['symbol'] == 'YASHO':
        md_lines.append("- Target ₹1500 cr revenue by FY28.")
        md_lines.append("- ₹125 cr capex planned in FY27 purely from internal accruals.")
        md_lines.append("- Anticipating 35-45% volume growth in FY27 with capacity utilization over 75%.")

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

with open('portfolio/forward_exit_analysis.md', 'w') as f:
    f.write("\n".join(md_lines))

print("Created forward_exit_analysis.md")
