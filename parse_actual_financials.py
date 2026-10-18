import os
import re
import json

eligible = ['AEROFLEX', 'SYRMA', 'YATHARTH', 'SHREEREF', 'YASHO', 'APEX', 'XPROINDIA', 'BETA', 'NUVAMA', 'HFCL', 'ADVAIT', 'ICIL', 'WPIL', 'GREAVESCOT', 'MANINDS', 'INDOSMC', 'TANFACIND', 'DEEPAKFERT', 'STARHEALTH', 'MBEL', 'BPLPHARMA', 'VAIBHAVGBL', 'VIKRAN', 'FCL', 'COSMICCRF', 'SHAILY', 'SUDEEPPHRM', 'HSCL', 'DEEPINDS', 'DHABRIYA', 'KRN', 'GAUDIUMIVF', 'CARYSIL', 'STOVEKRAFT', 'HCC', 'CAPACITE', 'SGFIN', 'GANESHCP']

actuals = {}

for symbol in eligible:
    company_dir = f"data/companies/{symbol}"
    fin_dir = os.path.join(company_dir, 'financials')
    
    sales, opm, pat, eps = None, None, None, None
    
    if os.path.exists(fin_dir):
        files = os.listdir(fin_dir)
        fin_files = sorted([f for f in files if "financials" in f])
        if fin_files:
            latest_file = os.path.join(fin_dir, fin_files[-1])
            with open(latest_file, 'r', errors='ignore') as f:
                content = f.read()
                
            # Find the Annual Profit & Loss section
            pnl_section = content.split("## Annual Profit & Loss")
            if len(pnl_section) > 1:
                annual_data = pnl_section[1].split("---")[0]
                lines = annual_data.strip().split('\n')
                
                # Determine which column is Mar 2026 / FY26
                header_index = -1
                for line in lines:
                    if "Mar 2026" in line or "FY26" in line or "Mar 25" in line: # if 2026 doesn't exist maybe TTM
                        cols = [c.strip() for c in line.split('|')]
                        # find the last valid column (sometimes it's TTM)
                        for idx, c in enumerate(cols):
                            if "Mar 2026" in c or "Mar 26" in c:
                                header_index = idx
                                break
                        if header_index == -1 and len(cols) > 2:
                            header_index = len(cols) - 1 # Just pick the last
                        break
                        
                for line in lines:
                    cols = [c.strip() for c in line.split('|')]
                    if not cols or len(cols) <= 1:
                        continue
                        
                    key = cols[0].lower()
                    if header_index != -1 and len(cols) > header_index:
                        val_str = cols[header_index].replace(',', '').replace('%', '').strip()
                        try:
                            val = float(val_str)
                        except:
                            val = None
                            
                        if "sales" in key and sales is None:
                            sales = val
                        elif "opm" in key and opm is None:
                            opm = val / 100.0 if val else None
                        elif "net profit" in key and pat is None:
                            pat = val
                        elif "eps in rs" in key and eps is None:
                            eps = val

    actuals[symbol] = {
        "fy26_rev": sales,
        "fy26_ebitda_margin": opm,
        "fy26_pat": pat,
        "fy26_eps": eps
    }

with open('actual_financials.json', 'w') as f:
    json.dump(actuals, f, indent=2)

print("Parsed true FY26 financials.")
