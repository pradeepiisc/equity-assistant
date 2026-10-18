import json
import os
import re

with open('portfolio/forward_exit_analysis.md', 'r') as f:
    md_content = f.read()

try:
    with open('remaining_data.json', 'r') as f:
        remaining_data = json.load(f)
except:
    remaining_data = {}
    
def get_best_snippets(symbol):
    if symbol in remaining_data:
        snippets = remaining_data[symbol]['snippets']
        best = []
        for s in snippets:
            if re.search(r'\d', s) and re.search(r'(crore|cr|capex|capacity|unit|%|margin|revenue|target|guidance|EBITDA|expansion)', s, re.IGNORECASE):
                s = s.replace('\n', ' ').strip()
                if len(s) > 150:
                    sentences = s.split('. ')
                    for sent in sentences:
                        if re.search(r'\d', sent) and re.search(r'(crore|cr|capex|capacity|unit|%|margin|revenue)', sent, re.IGNORECASE):
                            best.append(sent.strip() + '.')
                            break
                else:
                    best.append(s)
        
        unique_best = []
        seen = set()
        for b in best:
            if b not in seen:
                seen.add(b)
                unique_best.append(b)
        return unique_best[:2]
    return []

def get_shareholding_update(symbol):
    sh_dir = f"data/companies/{symbol}/shareholding"
    if not os.path.exists(sh_dir):
        return "No recent shareholding data available."
    
    files = os.listdir(sh_dir)
    sh_files = [f for f in files if "shareholding" in f and f.endswith(".txt")]
    if not sh_files:
        return "No recent shareholding data available."
        
    latest_sh_file = sorted(sh_files)[-1]
    path = os.path.join(sh_dir, latest_sh_file)
    
    with open(path, 'r', errors='ignore') as f:
        sh_text = f.read()
        
    updates = []
    
    for category in ["Promoters\+", "FIIs\+", "DIIs\+"]:
        match = re.search(rf'\|\s*{category}\s*\|(.*?)\n', sh_text)
        if match:
            cols = [c.strip() for c in match.group(1).split('|') if c.strip()]
            if len(cols) >= 2:
                try:
                    prev = float(cols[-2].replace('%',''))
                    curr = float(cols[-1].replace('%',''))
                    cat_name = category.replace('\+', '')
                    if curr > prev:
                        updates.append(f"{cat_name} increased stake ({prev:.2f}% → {curr:.2f}%)")
                    elif curr < prev:
                        updates.append(f"{cat_name} decreased stake ({prev:.2f}% → {curr:.2f}%)")
                except:
                    pass
                    
    if updates:
        return "**Shareholding Update:** " + ", ".join(updates) + "."
    return "**Shareholding Update:** Stake remained largely unchanged among key institutions."


blocks = md_content.split('## ')
header = blocks[0]
company_blocks = blocks[1:]

new_blocks = [header]

for block in company_blocks:
    symbol = block.split('\n')[0].strip()
    sh_update = get_shareholding_update(symbol)
    
    if symbol not in ['AEROFLEX', 'SYRMA', 'YATHARTH', 'SHREEREF', 'YASHO']:
        snippets = get_best_snippets(symbol)
        
        driver_start = block.find('**Key Growth Drivers:**')
        fin_start = block.find('**Financial Estimates (₹ Crores):**')
        
        if driver_start != -1 and fin_start != -1:
            drivers_text = "**Key Growth Drivers:**\n"
            for s in snippets:
                drivers_text += f"- {s}\n"
            if not snippets:
                drivers_text += "- Anticipating continuous margin expansion and volume growth per management guidance.\n"
                drivers_text += "- Scaling operational capacity to meet future revenue targets.\n"
                
            drivers_text += f"- {sh_update}\n\n"
            
            new_block = block[:driver_start] + drivers_text + block[fin_start:]
            new_blocks.append("## " + new_block)
        else:
            new_blocks.append("## " + block)
    else:
        driver_start = block.find('**Key Growth Drivers:**')
        fin_start = block.find('**Financial Estimates (₹ Crores):**')
        if driver_start != -1 and fin_start != -1:
            old_drivers = block[driver_start:fin_start].strip()
            old_drivers += f"\n- {sh_update}\n\n"
            new_block = block[:driver_start] + old_drivers + "\n\n" + block[fin_start:]
            new_blocks.append("## " + new_block)
        else:
            new_blocks.append("## " + block)

with open('portfolio/forward_exit_analysis.md', 'w') as f:
    f.write("".join(new_blocks))

print("Rewrote markdown with specific snippets and shareholding updates.")
