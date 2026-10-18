import os
import pdfplumber
import json
import re

eligible = ['AEROFLEX', 'SYRMA', 'YATHARTH', 'SHREEREF', 'YASHO', 'APEX', 'XPROINDIA', 'BETA', 'NUVAMA', 'HFCL', 'ADVAIT', 'ICIL', 'WPIL', 'GREAVESCOT', 'MANINDS', 'INDOSMC', 'TANFACIND', 'DEEPAKFERT', 'STARHEALTH', 'MBEL', 'BPLPHARMA', 'VAIBHAVGBL', 'VIKRAN', 'FCL', 'COSMICCRF', 'SHAILY', 'SUDEEPPHRM', 'HSCL', 'DEEPINDS', 'DHABRIYA', 'KRN', 'GAUDIUMIVF', 'CARYSIL', 'STOVEKRAFT', 'HCC', 'CAPACITE', 'SGFIN', 'GANESHCP']

def extract_pdf_text(pdf_path):
    text = ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text += page.extract_text() + "\n"
    except Exception as e:
        print(f"Error reading {pdf_path}: {e}")
    return text

results = {}

for symbol in eligible:
    company_dir = f"data/companies/{symbol}"
    tr_dir = os.path.join(company_dir, 'transcripts')
    
    latest_tr = None
    if os.path.exists(tr_dir):
        files = os.listdir(tr_dir)
        for f in sorted(files, reverse=True):
            if any(x in f for x in ["May2026", "Apr2026", "Mar2026", "Jun2026", "Q4_FY26", "Q4FY26"]):
                latest_tr = os.path.join(tr_dir, f)
                break
                
    text = ""
    if latest_tr:
        if latest_tr.endswith('.pdf'):
            text = extract_pdf_text(latest_tr)
        else:
            with open(latest_tr, 'r', errors='ignore') as f:
                text = f.read()
    
    insights_path = os.path.join(company_dir, 'insights', 'insights_values.txt')
    insights_text = ""
    if os.path.exists(insights_path):
        with open(insights_path, 'r', errors='ignore') as f:
            insights_text = f.read()
            
    guidance = []
    lines = text.split('\n')
    keywords = ['FY27', 'FY28', 'FY29', 'guidance', 'target', 'margin', 'growth', 'capex', 'capacity', 'revenue', 'profit', 'expansion']
    for i, line in enumerate(lines):
        if any(k.lower() in line.lower() for k in keywords):
            start = max(0, i-1)
            end = min(len(lines), i+2)
            snippet = " ".join(lines[start:end])
            guidance.append(snippet)
            
    unique_g = []
    seen = set()
    for g in guidance:
        clean = re.sub(r'\s+', ' ', g).strip()
        if clean not in seen:
            seen.add(clean)
            unique_g.append(clean)
            
    results[symbol] = {
        'insights': insights_text[:1000], 
        'guidance_snippets': unique_g[:20] 
    }

with open('company_analysis_data.json', 'w') as f:
    json.dump(results, f, indent=2)

print("Data extraction complete.")
